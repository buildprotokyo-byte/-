"""合成平面図の生成器(劣化レベル: clean / light / medium / heavy / extreme)。

トライアル4・6・10で使った合成図面は Codex 側で生成したもので、このリポジトリには
残っていません。そのため同等の条件(格子状に整列した壁・開き戸・窓を持つ平面図に、
段階的な劣化を加える)を再現する生成器をここに置き、段階Aの比較はすべてこの
生成器が出す図面の上で行います。**トライアル4・6・10の画像そのものではない**点は
報告書側にも明記します。

正解値(ground truth)は画像と同時に返すので、読み取り結果との突き合わせに使えます。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

DegradationLevel = Literal["clean", "light", "medium", "heavy", "extreme"]

#: 図面のスケール。1 ピクセル = 10 mm(= 1 cm)として扱う。
MM_PER_PIXEL = 10.0


@dataclass(frozen=True)
class WallSegment:
    """壁の芯線。座標はピクセル、長さはミリメートル。"""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def length_mm(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1) * MM_PER_PIXEL

    @property
    def orientation(self) -> Literal["h", "v"]:
        return "h" if abs(self.x2 - self.x1) >= abs(self.y2 - self.y1) else "v"


@dataclass(frozen=True)
class Room:
    name: str
    x: int
    y: int
    w: int
    h: int

    @property
    def area_mm2(self) -> float:
        return self.w * self.h * MM_PER_PIXEL * MM_PER_PIXEL

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)


@dataclass(frozen=True)
class Symbol:
    """記号(開き戸・窓)の正解位置。box は (x1, y1, x2, y2)。

    ``room`` は、その記号が属する部屋の名前(開き戸なら戸が開く側の部屋)。
    絶対ルール軸に渡す際の包含関係の正解値になります。
    """

    kind: Literal["door", "window"]
    box: tuple[int, int, int, int]
    room: str = ""

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass
class SyntheticPlan:
    """合成平面図 1 枚と、その正解値。"""

    name: str
    level: DegradationLevel
    image: np.ndarray
    rooms: list[Room] = field(default_factory=list)
    walls: list[WallSegment] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)

    # --- 正解値のサマリー ------------------------------------------------
    @property
    def room_count(self) -> int:
        return len(self.rooms)

    @property
    def door_count(self) -> int:
        return sum(1 for s in self.symbols if s.kind == "door")

    @property
    def window_count(self) -> int:
        return sum(1 for s in self.symbols if s.kind == "window")

    @property
    def total_wall_length_mm(self) -> float:
        return sum(w.length_mm for w in self.walls)

    @property
    def total_floor_area_mm2(self) -> float:
        return sum(r.area_mm2 for r in self.rooms)

    def save(self, path: str) -> None:
        cv2.imwrite(path, self.image)


# ---------------------------------------------------------------------------
# 図面の作図
# ---------------------------------------------------------------------------

_WALL_THICKNESS = 4
_INK = 0
_PAPER = 255


def _draw_wall(canvas: np.ndarray, wall: WallSegment, gaps: list[tuple[int, int]]) -> None:
    """壁を描く。gaps は開口部(戸・窓)として塗り残す区間 [(start, end), ...]。

    区間は水平壁なら x、垂直壁なら y の値で指定する。
    """
    cv2.line(canvas, (wall.x1, wall.y1), (wall.x2, wall.y2), _INK, _WALL_THICKNESS)
    for start, end in gaps:
        if wall.orientation == "h":
            cv2.rectangle(
                canvas,
                (start, wall.y1 - _WALL_THICKNESS),
                (end, wall.y1 + _WALL_THICKNESS),
                _PAPER,
                -1,
            )
        else:
            cv2.rectangle(
                canvas,
                (wall.x1 - _WALL_THICKNESS, start),
                (wall.x1 + _WALL_THICKNESS, end),
                _PAPER,
                -1,
            )


def _draw_door(
    canvas: np.ndarray, wall: WallSegment, start: int, end: int, side: int, room: str
) -> Symbol:
    """開き戸記号(戸の板 + 開き勝手の円弧)を描き、正解ボックスを返す。

    ``side`` は戸が開く向き。水平な壁なら -1 が上、+1 が下。垂直な壁なら
    -1 が左、+1 が右。``room`` はその側にある部屋の名前。
    """
    span = end - start
    if wall.orientation == "h":
        y = wall.y1
        hinge = (start, y)
        # 戸の板: 蝶番から壁に垂直に立ち上がる
        cv2.line(canvas, hinge, (start, y + side * span), _INK, 2)
        # 開き勝手の円弧(1/4円)
        angles = (-90, 0) if side < 0 else (0, 90)
        cv2.ellipse(canvas, hinge, (span, span), 0, angles[0], angles[1], _INK, 1)
        top = y - span - 4 if side < 0 else y - 4
        bottom = y + 4 if side < 0 else y + span + 4
        box = (start - 4, top, end + 4, bottom)
    else:
        x = wall.x1
        hinge = (x, start)
        cv2.line(canvas, hinge, (x + side * span, start), _INK, 2)
        angles = (0, 90) if side > 0 else (90, 180)
        cv2.ellipse(canvas, hinge, (span, span), 0, angles[0], angles[1], _INK, 1)
        left = x - 4 if side > 0 else x - span - 4
        right = x + span + 4 if side > 0 else x + 4
        box = (left, start - 4, right, end + 4)
    return Symbol("door", box, room)  # type: ignore[arg-type]


def _draw_window(canvas: np.ndarray, wall: WallSegment, start: int, end: int, room: str) -> Symbol:
    """窓記号(開口部を跨ぐ細い二重線)を描き、正解ボックスを返す。"""
    if wall.orientation == "h":
        y = wall.y1
        cv2.line(canvas, (start, y - 3), (end, y - 3), _INK, 1)
        cv2.line(canvas, (start, y + 3), (end, y + 3), _INK, 1)
        box = (start - 2, y - 6, end + 2, y + 6)
    else:
        x = wall.x1
        cv2.line(canvas, (x - 3, start), (x - 3, end), _INK, 1)
        cv2.line(canvas, (x + 3, start), (x + 3, end), _INK, 1)
        box = (x - 6, start - 2, x + 6, end + 2)
    return Symbol("window", box, room)  # type: ignore[arg-type]


_CANVAS_H, _CANVAS_W = 520, 760

# 外周(x: 60..700, y: 60..460)と内壁(x=380 の縦壁, y=260 の横壁)
_OX1, _OY1, _OX2, _OY2 = 60, 60, 700, 460
_MID_X, _MID_Y = 380, 260

_WALLS = [
    WallSegment(_OX1, _OY1, _OX2, _OY1),  # 上
    WallSegment(_OX1, _OY2, _OX2, _OY2),  # 下
    WallSegment(_OX1, _OY1, _OX1, _OY2),  # 左
    WallSegment(_OX2, _OY1, _OX2, _OY2),  # 右
    WallSegment(_MID_X, _OY1, _MID_X, _OY2),  # 縦の間仕切り
    WallSegment(_OX1, _MID_Y, _OX2, _MID_Y),  # 横の間仕切り
]

# 開口部の割り付け: (壁のindex, 種別, 開始, 終了, 開く向き, 属する部屋)
# 開き戸は 4 室に 1 つずつ行き渡るよう配置している。
_OPENINGS: list[tuple[int, str, int, int, int, str]] = [
    (0, "window", 150, 230, 0, "室1"),   # 上外壁の窓
    (0, "window", 480, 560, 0, "室2"),   # 上外壁の窓
    (1, "window", 200, 280, 0, "室3"),   # 下外壁の窓
    (2, "door", 150, 200, +1, "室1"),    # 左外壁の玄関戸(室1 側へ開く)
    (3, "window", 300, 380, 0, "室4"),   # 右外壁の窓
    (4, "door", 120, 170, +1, "室2"),    # 縦間仕切りの戸(室2 側へ開く)
    (4, "door", 320, 370, +1, "室4"),    # 縦間仕切りの戸(室4 側へ開く)
    (5, "door", 180, 230, +1, "室3"),    # 横間仕切りの戸(室3 側へ開く)
]

_ROOMS = [
    Room("室1", _OX1, _OY1, _MID_X - _OX1, _MID_Y - _OY1),
    Room("室2", _MID_X, _OY1, _OX2 - _MID_X, _MID_Y - _OY1),
    Room("室3", _OX1, _MID_Y, _MID_X - _OX1, _OY2 - _MID_Y),
    Room("室4", _MID_X, _MID_Y, _OX2 - _MID_X, _OY2 - _MID_Y),
]


def _draw_symbols(canvas: np.ndarray) -> list[Symbol]:
    symbols: list[Symbol] = []
    for idx, kind, start, end, side, room in _OPENINGS:
        wall = _WALLS[idx]
        if kind == "door":
            symbols.append(_draw_door(canvas, wall, start, end, side, room))
        else:
            symbols.append(_draw_window(canvas, wall, start, end, room))
    return symbols


def symbol_ink_masks() -> dict[int, np.ndarray]:
    """記号ごとの、線そのものの位置を示すマスク(記号の index → uint8 マスク)。

    記号を 1 つずつ白紙に描いて得ます。記号ボックスで切り出すと壁のインクが混ざり、
    「記号が読めたか」の判定が鈍るため、線の位置だけを取り出せるようにしています。
    """
    masks: dict[int, np.ndarray] = {}
    for index, (idx, kind, start, end, side, room) in enumerate(_OPENINGS):
        canvas = np.full((_CANVAS_H, _CANVAS_W), _PAPER, np.uint8)
        wall = _WALLS[idx]
        if kind == "door":
            _draw_door(canvas, wall, start, end, side, room)
        else:
            _draw_window(canvas, wall, start, end, room)
        masks[index] = ((canvas < 128).astype(np.uint8)) * 255
    return masks


def wall_ink_mask() -> np.ndarray:
    """壁だけを描いたときのインク位置(開口部は抜いた状態)。"""
    canvas = np.full((_CANVAS_H, _CANVAS_W), _PAPER, np.uint8)
    gaps_by_wall: dict[int, list[tuple[int, int]]] = {}
    for idx, _kind, start, end, _side, _room in _OPENINGS:
        gaps_by_wall.setdefault(idx, []).append((start, end))
    for idx, wall in enumerate(_WALLS):
        _draw_wall(canvas, wall, gaps_by_wall.get(idx, []))
    return ((canvas < 128).astype(np.uint8)) * 255


def _build_clean_plan() -> SyntheticPlan:
    """格子状に整列した 4 室の平面図を作る(トライアル10 と同じ「格子前提」条件)。"""
    canvas = np.full((_CANVAS_H, _CANVAS_W), _PAPER, np.uint8)

    gaps_by_wall: dict[int, list[tuple[int, int]]] = {}
    for idx, _kind, start, end, _side, _room in _OPENINGS:
        gaps_by_wall.setdefault(idx, []).append((start, end))

    for idx, wall in enumerate(_WALLS):
        _draw_wall(canvas, wall, gaps_by_wall.get(idx, []))

    symbols = _draw_symbols(canvas)

    return SyntheticPlan(
        name="grid_4rooms",
        level="clean",
        image=canvas,
        rooms=list(_ROOMS),
        walls=list(_WALLS),
        symbols=symbols,
    )


# ---------------------------------------------------------------------------
# 劣化の付与
# ---------------------------------------------------------------------------

_DEGRADATION_PARAMS = {
    # level: (ガウスノイズσ, ぼかしカーネル, 塩胡椒ノイズ率, 回転角, 線の欠落率, コントラスト)
    "light": (6.0, 3, 0.000, 0.0, 0.00, 1.00),
    "medium": (14.0, 3, 0.004, 0.6, 0.03, 0.85),
    "heavy": (26.0, 5, 0.012, 1.6, 0.10, 0.65),
    # 記号の線がどこで失われ始めるかを見るための、さらに厳しい条件。
    "extreme": (34.0, 7, 0.020, 2.4, 0.28, 0.45),
}


def _degrade(image: np.ndarray, level: DegradationLevel, seed: int) -> np.ndarray:
    if level == "clean":
        return image.copy()

    sigma, blur_k, sp_rate, angle, dropout, contrast = _DEGRADATION_PARAMS[level]
    rng = np.random.default_rng(seed)
    out = image.astype(np.float32)

    # 1. 線の欠落(かすれ): インク画素の一部を紙色に戻す
    if dropout > 0:
        ink = out < 128
        drop = rng.random(out.shape) < dropout
        out[ink & drop] = _PAPER

    # 2. 回転(スキャン時の傾き)
    if angle != 0.0:
        h, w = out.shape
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        out = cv2.warpAffine(
            out, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=float(_PAPER)
        )

    # 3. ぼかし(スキャン解像度・ピント)
    out = cv2.GaussianBlur(out, (blur_k, blur_k), 0)

    # 4. コントラスト低下(紙の黄ばみ・薄い印字)
    if contrast != 1.0:
        out = _PAPER - (_PAPER - out) * contrast

    # 5. ガウスノイズ
    out += rng.normal(0.0, sigma, out.shape)

    # 6. 塩胡椒ノイズ
    if sp_rate > 0:
        mask = rng.random(out.shape)
        out[mask < sp_rate / 2] = 0.0
        out[mask > 1.0 - sp_rate / 2] = float(_PAPER)

    return np.clip(out, 0, 255).astype(np.uint8)


def make_plan(level: DegradationLevel = "clean", seed: int = 0) -> SyntheticPlan:
    """指定した劣化レベルの合成平面図を返す。正解値は劣化前の座標のまま。

    medium / heavy では図面をわずかに回転させるため、正解座標と画素の位置は
    厳密には一致しません(実スキャンと同じ条件)。長さ・面積・個数の比較には
    影響しない範囲に収めています。
    """
    plan = _build_clean_plan()
    return SyntheticPlan(
        name=plan.name,
        level=level,
        image=_degrade(plan.image, level, seed),
        rooms=plan.rooms,
        walls=plan.walls,
        symbols=plan.symbols,
    )


ALL_LEVELS: tuple[DegradationLevel, ...] = ("clean", "light", "medium", "heavy")
"""標準の評価で使う劣化レベル。

``extreme`` は含めていません。あの条件では二値化の結果がノイズで埋まり(インク率が
正解の 8 倍になる)、測っているのが図面ではなくノイズになるためです。劣化の上限を
確かめたいときだけ ``make_plan("extreme")`` で個別に使ってください。
"""
