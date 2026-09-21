"""画像軸: VTracer(ラスター → ベクター変換)のアダプタ。

`6軸_実装詳細設計書.md` の「スキャンした図面をこのパイプラインに通し、線・輪郭を
ベクターデータ化してから絶対ルール軸に渡す」に対応します。

VTracer が返すのは**輪郭(塗りつぶし領域の外周)**であって、線の芯線ではありません。
そのため本モジュールは次の 3 段構えになっています。

1. ``vectorize()``   … VTracer に掛けて SVG を得て、パスを多角形の列として取り出す
2. ``VectorDrawing.to_mask()`` … 取り出した多角形をラスターに戻す(= 正規化された
   二値画像。ノイズが落ち、輪郭が幾何形状に整えられている)
3. ``measure_linework()`` … 芯線化(Zhang-Suen 細線化)して総延長などを測る

2 の「ラスターに戻す」のは遠回りに見えますが、**前処理だけを差し替えて下流の読み取り
アルゴリズムを固定する**ための措置です。こうすると「VTracer を挟んだことによる差」だけ
を切り出して測れます(benchmarks/run_vtracer_eval.py がこれを使います)。

注意: Python バインディングには CLI の ``--preset bw`` がありません。相当する設定は
``colormode="binary"`` です(``BW_PRESET`` にまとめてあります)。
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterator

import cv2
import numpy as np
import vtracer

MM_PER_PIXEL = 10.0
"""benchmarks/synthetic_plans.py と同じスケール。"""


#: CLI の ``--preset bw`` 相当。白黒の線画図面向け。
BW_PRESET: dict[str, Any] = {
    "colormode": "binary",
    "mode": "spline",
    "filter_speckle": 4,
    "corner_threshold": 60,
    "length_threshold": 4.0,
    "splice_threshold": 45,
    "path_precision": 8,
}


# ---------------------------------------------------------------------------
# SVG パスの取り出し
# ---------------------------------------------------------------------------

_PATH_RE = re.compile(r"<path\b([^>]*)/>", re.DOTALL)
_D_RE = re.compile(r'\bd="([^"]*)"')
_TRANSLATE_RE = re.compile(r"translate\(\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*\)")
_TOKEN_RE = re.compile(r"([MmLlCcZzHhVv])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def _tokens(d: str) -> Iterator[tuple[str | None, float | None]]:
    for match in _TOKEN_RE.finditer(d):
        command, number = match.groups()
        yield (command, None) if command else (None, float(number))


def _flatten_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 8,
) -> list[tuple[float, float]]:
    """3 次ベジエを直線で近似する。"""
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        points.append((x, y))
    return points


def parse_svg_paths(svg: str, bezier_steps: int = 8) -> list[list[np.ndarray]]:
    """SVG 文字列から、``<path>`` ごとの輪郭(ring)の列を取り出す。

    戻り値は ``[[外周, 穴, 穴, ...], [外周, ...], ...]`` の形です。1 つの ``<path>``
    の中に ``M ... Z`` が複数入っている(= 外周と穴)ことがあるため、サブパス単位に
    分けて返します。ここを平らに潰すと穴が塗り潰されてしまいます。

    VTracer が出力する範囲(M / L / C / Z と ``translate()``)だけを扱います。
    """
    paths: list[list[np.ndarray]] = []
    for attrs in _PATH_RE.findall(svg):
        d_match = _D_RE.search(attrs)
        if not d_match:
            continue
        dx = dy = 0.0
        translate = _TRANSLATE_RE.search(attrs)
        if translate:
            dx, dy = float(translate.group(1)), float(translate.group(2))

        rings: list[np.ndarray] = []
        points: list[tuple[float, float]] = []
        current = (0.0, 0.0)
        start = (0.0, 0.0)
        command: str | None = None
        buffer: list[float] = []

        def close_ring() -> None:
            nonlocal points
            if len(points) >= 3:
                rings.append(np.array([(x + dx, y + dy) for x, y in points], dtype=np.float64))
            points = []

        def flush() -> None:
            nonlocal current, start, buffer
            if command in ("M", "m"):
                first = True
                while len(buffer) >= 2:
                    x, y = buffer[0], buffer[1]
                    del buffer[:2]
                    current = (x, y) if command == "M" else (current[0] + x, current[1] + y)
                    if first:
                        # 新しいサブパスの開始。直前のサブパスはここで閉じる。
                        close_ring()
                        start = current
                        first = False
                    points.append(current)
            elif command in ("L", "l"):
                while len(buffer) >= 2:
                    x, y = buffer[0], buffer[1]
                    del buffer[:2]
                    current = (x, y) if command == "L" else (current[0] + x, current[1] + y)
                    points.append(current)
            elif command in ("H", "h"):
                while buffer:
                    x = buffer.pop(0)
                    current = (x, current[1]) if command == "H" else (current[0] + x, current[1])
                    points.append(current)
            elif command in ("V", "v"):
                while buffer:
                    y = buffer.pop(0)
                    current = (current[0], y) if command == "V" else (current[0], current[1] + y)
                    points.append(current)
            elif command in ("C", "c"):
                while len(buffer) >= 6:
                    coords = buffer[:6]
                    del buffer[:6]
                    if command == "c":
                        pts = [
                            (current[0] + coords[i], current[1] + coords[i + 1])
                            for i in range(0, 6, 2)
                        ]
                    else:
                        pts = [(coords[i], coords[i + 1]) for i in range(0, 6, 2)]
                    points.extend(_flatten_cubic(current, pts[0], pts[1], pts[2], bezier_steps))
                    current = pts[2]
            buffer = []

        for cmd, number in _tokens(d_match.group(1)):
            if cmd is not None:
                flush()
                if cmd in ("Z", "z"):
                    if points:
                        points.append(start)
                    current = start
                    close_ring()
                    command = None
                else:
                    command = cmd
            else:
                buffer.append(number)  # type: ignore[arg-type]
        flush()
        close_ring()

        if rings:
            paths.append(rings)
    return paths


# ---------------------------------------------------------------------------
# SVG の描画
# ---------------------------------------------------------------------------


def _render_svg(svg: str, width: int, height: int) -> np.ndarray | None:
    """SVG をインク = 255 の二値マスクに描画する。cairosvg が無ければ None。"""
    try:
        import cairosvg
    except ImportError:  # pragma: no cover - 環境依存
        return None
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
        output_height=height,
        background_color="white",
    )
    grey = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if grey is None:  # pragma: no cover - 描画失敗
        return None
    return ((grey < 128).astype(np.uint8)) * 255


# ---------------------------------------------------------------------------
# ベクター図面
# ---------------------------------------------------------------------------


@dataclass
class VectorDrawing:
    """VTracer が出したベクター図面。

    ``paths`` は ``<path>`` ごとの輪郭(ring)の列。1 つの ``<path>`` が外周 1 本と
    穴 0 本以上を持ちます。
    """

    paths: list[list[np.ndarray]]
    width: int
    height: int
    params: dict[str, Any] = field(default_factory=dict)
    svg: str = ""

    @property
    def path_count(self) -> int:
        """``<path>`` の数。ベクター化後に残った「図形の数」に相当する。"""
        return len(self.paths)

    @property
    def rings(self) -> list[np.ndarray]:
        return [ring for path in self.paths for ring in path]

    @property
    def ring_count(self) -> int:
        return len(self.rings)

    @property
    def vertex_count(self) -> int:
        return int(sum(len(ring) for ring in self.rings))

    def to_mask(self) -> np.ndarray:
        """ベクターをラスターに戻す。インク = 255 の uint8 マスク。

        cairosvg が入っていれば SVG をそのまま描画します(推奨)。入っていない場合は
        多角形の塗りつぶしで近似しますが、**1 画素幅の線(開き戸の円弧・窓の細線)が
        落ちる**ことを確認済みなので、計測に使うなら cairosvg を入れてください。
        """
        if self.svg:
            rendered = _render_svg(self.svg, self.width, self.height)
            if rendered is not None:
                return rendered
        return self._to_mask_by_polygon_fill()

    def _to_mask_by_polygon_fill(self) -> np.ndarray:
        """cairosvg が無い場合の近似描画(1 画素幅の線は落ちる)。"""
        mask = np.zeros((self.height, self.width), np.uint8)
        # VTracer の座標は画素の「角」を指す。cv2.fillPoly は両端を含めて塗るので、
        # そのまま渡すと線が 1 画素ずつ太る。0.5 引いて画素中心の座標系に直し、
        # shift で小数精度を保ったまま塗る。
        shift = 4
        scale = 1 << shift
        for path in self.paths:
            layer = np.zeros_like(mask)
            for ring in path:
                ring_layer = np.zeros_like(mask)
                points = np.round((ring - 0.5) * scale).astype(np.int32)
                cv2.fillPoly(ring_layer, [points], 255, shift=shift)
                layer = cv2.bitwise_xor(layer, ring_layer)
            mask = cv2.bitwise_or(mask, layer)
        return mask


def vectorize(image: np.ndarray, **params: Any) -> VectorDrawing:
    """グレースケール画像を VTracer でベクター化する。

    ``params`` は ``vtracer.convert_image_to_svg_py`` にそのまま渡ります。
    既定は ``BW_PRESET``(CLI の ``--preset bw`` 相当)。
    """
    settings = {**BW_PRESET, **params}
    bezier_steps = int(settings.pop("bezier_steps", 8))

    height, width = image.shape[:2]
    tmpdir = tempfile.mkdtemp(prefix="vtracer_")
    png_path = os.path.join(tmpdir, "in.png")
    svg_path = os.path.join(tmpdir, "out.svg")
    try:
        cv2.imwrite(png_path, image)
        vtracer.convert_image_to_svg_py(png_path, svg_path, **settings)
        with open(svg_path, encoding="utf-8") as handle:
            svg = handle.read()
    finally:
        for path in (png_path, svg_path):
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(tmpdir)

    return VectorDrawing(
        paths=parse_svg_paths(svg, bezier_steps=bezier_steps),
        width=width,
        height=height,
        params=settings,
        svg=svg,
    )


# ---------------------------------------------------------------------------
# 芯線化と計測(下流の読み取りアルゴリズム。前処理を差し替えても固定で使う)
# ---------------------------------------------------------------------------


def thin(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen 細線化。インク = 255 の二値マスクを 1 画素幅の芯線にする。

    opencv-contrib(``cv2.ximgproc.thinning``)に依存しないよう自前で持っています。
    """
    image = (mask > 0).astype(np.uint8)
    while True:
        changed = False
        for step in (0, 1):
            padded = np.pad(image, 1)
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]

            neighbours = [p2, p3, p4, p5, p6, p7, p8, p9]
            count = sum(neighbours)
            transitions = sum(
                ((a == 0) & (b == 1)).astype(np.uint8)
                for a, b in zip(neighbours, neighbours[1:] + neighbours[:1])
            )
            if step == 0:
                cond = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)

            remove = (image == 1) & (count >= 2) & (count <= 6) & (transitions == 1) & cond
            if remove.any():
                image[remove] = 0
                changed = True
        if not changed:
            break
    return (image * 255).astype(np.uint8)


@dataclass(frozen=True)
class LineworkMeasurement:
    """芯線から測った、図面全体の線の量。"""

    total_length_mm: float
    skeleton_pixels: int
    ink_pixels: int
    component_count: int
    mean_stroke_width_px: float


def measure_linework(
    mask: np.ndarray,
    mm_per_pixel: float = MM_PER_PIXEL,
    region: np.ndarray | None = None,
) -> LineworkMeasurement:
    """インクマスクから、線の総延長・連結成分数・平均線幅を測る。

    総延長は芯線の画素をたどって測ります(斜め方向は √2 として数える)ので、
    軸に平行な線でも傾いた線でも大きく外れません。

    ``region`` を渡すと、細線化は画像全体で行ったうえで、**長さの集計だけ**を
    その領域内に限ります(領域の境界で線を切ってから細線化すると、切り口が
    芯線を歪めるため)。
    """
    binary = (mask > 0).astype(np.uint8)
    ink_pixels = int(binary.sum())
    component_count = int(cv2.connectedComponents(binary, connectivity=8)[0]) - 1

    skeleton = (thin(mask) > 0).astype(np.uint8)
    skeleton_pixels = int(skeleton.sum())
    if region is not None:
        skeleton = skeleton & (region > 0).astype(np.uint8)
        skeleton_pixels = int(skeleton.sum())

    padded = np.pad(skeleton, 1)
    # 各芯線画素から、右・右下・下・左下の 4 方向だけを見れば、隣接ペアを
    # 重複なく 1 回ずつ数えられる。
    straight = int(
        (skeleton & padded[1:-1, 2:]).sum() + (skeleton & padded[2:, 1:-1]).sum()
    )
    diagonal = int(
        (skeleton & padded[2:, 2:]).sum() + (skeleton & padded[2:, :-2]).sum()
    )
    total_length_px = straight + diagonal * float(np.sqrt(2.0))

    mean_stroke_width = (ink_pixels / skeleton_pixels) if skeleton_pixels else 0.0

    return LineworkMeasurement(
        total_length_mm=total_length_px * mm_per_pixel,
        skeleton_pixels=skeleton_pixels,
        ink_pixels=ink_pixels,
        component_count=component_count,
        mean_stroke_width_px=mean_stroke_width,
    )


def binarize_classical(image: np.ndarray, median_ksize: int = 3) -> np.ndarray:
    """比較対象となる古典的前処理: メディアンフィルタ + 大津の二値化。

    VTracer の効果を測るためのベースラインです。メディアンフィルタを入れてあるのは、
    塩胡椒ノイズを落とさないベースラインでは比較が不公平になるためです。
    """
    smoothed = cv2.medianBlur(image, median_ksize) if median_ksize > 1 else image
    _, binary = cv2.threshold(smoothed, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary
