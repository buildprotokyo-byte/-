"""画像軸: CAD 由来の PDF から、縮尺と建具記号をベクター図形として取り出す。

**このモジュールが使えるのは「CAD から出力された PDF」だけである。**
紙をスキャンしただけの PDF には線も文字も図形データとして入っていないので、
`rasterize()` + 画像処理に頼るしかない。同じ案件でも、匿名化の過程で
全ページを画像化すると、このモジュールは何も返さなくなる
(実例は `docs/real_drawing_eval_report.md` 2 節)。

だから `extract_scale()` も `find_door_arcs()` も、**取り出せなかったときは
None か空を返し、推測はしない。**

**なぜベクターから取るのか**

同じ図面の記号を、ラスター画像のテンプレート照合で探した結果は
**的中 0 件**だった(報告書 4 節・11 節)。原因は精度ではなく、探すべき形の
出どころが無いことと、相関スコアが正解を区別しないことである。
ベクターが手に入るなら、円弧はベジェ曲線としてそのまま入っているので、
照合する必要がそもそも無い。

**何で開き戸と判定しているか**

外接矩形の大きさと縦横比では足りなかった。斜めに振れる建具(廊下に対して
45 度に付く WIC の扉など)は、円弧だけを取り出すと外接矩形が細長くなり、
縦横比で落ちてしまう(実測: 既存平面図の 15 件中 2 件がこれで漏れた)。
代わりに**円弧そのものの幾何**を見る。

1. ベジェ曲線の通過点が 1 つの円に十分よく乗ること(円でないものを落とす)
2. その円を**何度ぶん**なぞっているか … 開き戸の振りは 90 度前後。
   円(360 度)や楕円はここで落ちる。
3. 半径が実寸で建具の幅として成り立つこと

この 3 つはどれも**回転に依存しない**ので、斜めの建具も同じに拾える。
"""


from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

#: 1 ポイント = 1/72 インチ。
MM_PER_POINT = 25.4 / 72.0

#: 表題欄の縮尺表記。`1/50（A3）` `1:100` `1/30 (A1)` などを拾う。
#: 全角のスラッシュ・コロン・括弧も受ける(図面の表題欄は全角が混ざる)。
_SCALE_RE = re.compile(r"1\s*[/:／：]\s*(\d{1,4})")

#: 開き戸の円弧とみなす実寸の範囲(ミリメートル)。半径 = 扉の幅。
#: 住宅の建具は概ね 600〜1000mm。両開きの 1 枚が 600mm 程度まで下がるので
#: 下限を 400mm、上限は玄関の大きめの扉を見込んで 1400mm にしてある。
DOOR_ARC_MIN_MM = 400.0
DOOR_ARC_MAX_MM = 1400.0

#: 開き戸の振りとみなす中心角の範囲(度)。図面上は 90 度で描かれる。
#: 円(360 度)・半円(180 度)はここで落ちる。
DOOR_ARC_MIN_DEGREES = 55.0
DOOR_ARC_MAX_DEGREES = 125.0

#: 円への当てはまりの許容誤差。半径に対する比で見る。
#: これを超えるものは楕円・自由曲線とみなす。
#:
#: 実測で決めた値。P011 の既存平面図にある本物の建具 15 件の残差は
#: 最大 0.00033(半径の 0.033%)。一方、四分楕円を短径/長径 = 0.4 まで
#: 潰しても残差は 0.025 にしかならない。**当初の 0.03 では楕円を 1 つも
#: 落とせていなかった**(テストを壊す試験で発覚)。本物の 15 倍の余裕を
#: 取って 0.005 にしてある。
_CIRCLE_TOLERANCE = 0.005

#: 1 本のベジェ曲線から取る標本点の数。
_SAMPLES_PER_CURVE = 8


def _sample_curves(items: list) -> list[tuple[float, float]]:
    """ベジェ曲線(``"c"``)の通過点を標本として取り出す。

    直線(``"l"``)は見ない。扇形として閉じている建具では直線は半径であって、
    円周上に無いから。
    """
    points: list[tuple[float, float]] = []
    for item in items:
        if item[0] != "c":
            continue
        p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
        for index in range(_SAMPLES_PER_CURVE + 1):
            t = index / _SAMPLES_PER_CURVE
            u = 1.0 - t
            x = u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x
            y = u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y
            points.append((x, y))
    return points


def _fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """標本点に円を当てる。返すのは (中心x, 中心y, 半径)。当たらなければ None。

    Kasa 法(``x^2 + y^2 = a x + b y + c`` の最小二乗)。行列を組まずに
    3 元の正規方程式をそのまま解く。
    """
    if len(points) < 3:
        return None
    n = float(len(points))
    sx = sy = sxx = syy = sxy = sz = sxz = syz = 0.0
    for x, y in points:
        z = x * x + y * y
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        sz += z
        sxz += x * z
        syz += y * z
    # 未知数 (a, b, c) の 3x3 連立方程式。
    matrix = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
    rhs = [sxz, syz, sz]
    solution = _solve3(matrix, rhs)
    if solution is None:
        return None
    a, b, c = solution
    cx, cy = a / 2.0, b / 2.0
    radius_squared = c + cx * cx + cy * cy
    if radius_squared <= 0:
        return None
    return cx, cy, math.sqrt(radius_squared)


def _solve3(matrix: list[list[float]], rhs: list[float]) -> tuple[float, float, float] | None:
    """3 元 1 次連立方程式をガウスの消去法で解く。解けなければ None。"""
    rows = [list(matrix[i]) + [rhs[i]] for i in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for target in range(3):
            if target == column:
                continue
            factor = rows[target][column] / rows[column][column]
            for k in range(column, 4):
                rows[target][k] -= factor * rows[column][k]
    return tuple(rows[i][3] / rows[i][i] for i in range(3))  # type: ignore[return-value]


def _swept_degrees(points: list[tuple[float, float]], cx: float, cy: float) -> float:
    """中心から見て、標本点が何度ぶんに広がっているか。

    角度を並べて**いちばん大きい隙間**を探し、360 度からそれを引く。
    円を 1 周する図形では隙間がほとんど無いので 360 度に近くなる。
    """
    angles = sorted(math.degrees(math.atan2(y - cy, x - cx)) % 360.0 for x, y in points)
    if len(angles) < 2:
        return 0.0
    gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append(angles[0] + 360.0 - angles[-1])
    return 360.0 - max(gaps)


@dataclass(frozen=True)
class DrawingScale:
    """図面の縮尺と、その根拠。"""

    denominator: float
    """1/50 なら 50.0。"""

    source_text: str
    """表題欄から実際に読んだ文字列。根拠として残す。"""

    @property
    def mm_per_point(self) -> float:
        """紙の 1 ポイントが実寸で何ミリか。"""
        return MM_PER_POINT * self.denominator

    def mm_per_pixel(self, dpi: int) -> float:
        """その dpi でラスター化したときの、1 ピクセルの実寸(ミリメートル)。"""
        if dpi <= 0:
            raise ValueError("dpi は正の整数である必要があります")
        return 25.4 / dpi * self.denominator


@dataclass(frozen=True)
class DoorArc:
    """開き戸の振りを表す円弧 1 本。"""

    rect_pt: tuple[float, float, float, float]
    """ページ座標(ポイント)の外接矩形 (x0, y0, x1, y1)。"""

    width_mm: float
    """実寸のおおよその扉幅。**当てた円の半径**であって、外接矩形ではない。

    斜めに振れる建具では外接矩形が細長くなるので、そこから幅は取れない。
    """

    swept_degrees: float
    """その円を何度ぶんなぞっているか。開き戸なら 90 度前後。根拠として残す。"""

    center_pt: tuple[float, float]
    """振りの中心(= 吊元)のページ座標(ポイント)。"""

    def rect_px(self, dpi: int) -> tuple[int, int, int, int]:
        """その dpi でラスター化した画像の座標に直す。"""
        factor = dpi / 72.0
        x0, y0, x1, y1 = self.rect_pt
        return (int(x0 * factor), int(y0 * factor), int(x1 * factor), int(y1 * factor))


def extract_scale(pdf_path: str | Path, page_index: int) -> DrawingScale | None:
    """表題欄の文字列から縮尺を読む。読めなければ **None**。

    縮尺が読めないまま紙のスケールを実寸として使うと、1/50 の図面では
    50 倍ずれた長さが下流に入る。ここで推測しないのはそのため。
    """
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        text = doc.load_page(page_index).get_text("text")

    match = _SCALE_RE.search(text)
    if match is None:
        return None
    denominator = float(match.group(1))
    if denominator <= 0:
        return None
    return DrawingScale(denominator=denominator, source_text=match.group(0))


def find_door_arcs(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    min_mm: float = DOOR_ARC_MIN_MM,
    max_mm: float = DOOR_ARC_MAX_MM,
    min_degrees: float = DOOR_ARC_MIN_DEGREES,
    max_degrees: float = DOOR_ARC_MAX_DEGREES,
) -> list[DoorArc]:
    """開き戸の振り(四分円)をベクター図形から拾う。

    判定は 3 つ。どれも**回転に依存しない**ので、斜めに付く建具も同じに拾える。

    1. ベジェ曲線の通過点が 1 つの円に乗ること(残差が半径の 3% 以内)
    2. その円を ``min_degrees``〜``max_degrees`` ぶんなぞっていること
       … 円(360 度)・半円(180 度)・自由曲線をここで落とす
    3. 半径が実寸で建具の幅として成り立つ範囲にあること

    **引戸・折戸は円弧を描かないので、ここでは拾えない。** 拾えないことと
    「建具が無い」ことは別なので、呼び出し側で混同しないこと。
    """
    if min_mm <= 0 or max_mm <= min_mm:
        raise ValueError("実寸の範囲が不正です")
    if not 0 < min_degrees < max_degrees <= 360:
        raise ValueError("中心角の範囲が不正です")

    out: list[DoorArc] = []
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        page = doc.load_page(page_index)
        for drawing in page.get_drawings():
            points = _sample_curves(drawing["items"])
            if len(points) < 3:
                continue
            fit = _fit_circle(points)
            if fit is None:
                continue
            cx, cy, radius = fit
            if radius <= 0:
                continue
            residual = max(abs(math.hypot(x - cx, y - cy) - radius) for x, y in points)
            if residual > radius * _CIRCLE_TOLERANCE:
                continue
            swept = _swept_degrees(points, cx, cy)
            if not min_degrees <= swept <= max_degrees:
                continue
            radius_mm = radius * scale.mm_per_point
            if not min_mm <= radius_mm <= max_mm:
                continue
            rect = drawing["rect"]
            out.append(
                DoorArc(
                    rect_pt=(rect.x0, rect.y0, rect.x1, rect.y1),
                    width_mm=radius_mm,
                    swept_degrees=swept,
                    center_pt=(cx, cy),
                )
            )
    # 呼び出し順に依存しないよう、位置で並べておく。
    out.sort(key=lambda a: (round(a.rect_pt[1], 1), round(a.rect_pt[0], 1)))
    return out


#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
#: 名前を変えると登録簿の上限が効かなくなる(未登録手法として weak に落ちるので
#: 安全側ではあるが、黙って別物になる)ので、変えるときは登録簿も一緒に直すこと。
METHOD_TEXT_AREA = "pdf_text_area"
METHOD_DOOR_ARC = "pdf_vector_door_arc"

#: 面積の記載を拾う正規表現。`専有延床面積 95.54 ㎡` のような並びを想定。
#: ラベルと数値の間には改行が入ることがある(表題欄が表組みになっている図面)。
_AREA_RE = re.compile(r"(専有延床面積|施工床面積)\s*\n?\s*([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class AreaLabel:
    """図面に**文字として書かれている**面積の記載 1 件。"""

    label: str
    """``専有延床面積`` か ``施工床面積``。"""

    value_sqm: float
    """読んだ数値(平方メートル)。図面の文字そのままで、計算していない。"""

    source_text: str
    """実際に一致した文字列。根拠としてそのまま残す。"""

    page_index: int
    """0 始まりのページ番号。"""

    rect_pt: tuple[float, float, float, float] | None
    """数値が書かれている位置(ページ座標・ポイント)。見つからなければ None。

    ``page.search_for()`` は表示上の文字列を探すので、改行や字送りの都合で
    当たらないことがある。**当たらなかったことを 0 や原点で埋めない。**
    """


def find_area_labels(pdf_path: str | Path, page_index: int) -> list[AreaLabel]:
    """ページの埋め込み文字から、面積の記載を位置つきで拾う。書かれていなければ空。

    **図面に書いてある数値をそのまま読むだけで、面積を計算はしない。**
    室の輪郭から面積を出す実装はこのリポジトリに無い
    (`docs/real_drawing_eval_report.md`)。ここで拾えるのは、設計者が
    図面に書き込んだ面積の記載だけである。

    スキャンしただけのページには文字が入っていないので、何も返さない。
    """
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        page = doc.load_page(page_index)
        text = page.get_text("text")
        out: list[AreaLabel] = []
        for match in _AREA_RE.finditer(text):
            label, raw_value = match.group(1), match.group(2)
            hits = page.search_for(raw_value)
            rect = (
                (hits[0].x0, hits[0].y0, hits[0].x1, hits[0].y1) if hits else None
            )
            out.append(
                AreaLabel(
                    label=label,
                    value_sqm=float(raw_value),
                    source_text=match.group(0),
                    page_index=page_index,
                    rect_pt=rect,
                )
            )
    return out
