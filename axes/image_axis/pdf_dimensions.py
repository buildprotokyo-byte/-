"""画像軸: 図面に**記入された寸法の数字**を、それが指す2点と一緒に読む。

なぜこのモジュールが要るのか
----------------------------
2026-09-22 の棚卸しで、**図面の寸法の数字を読む手段がリポジトリに1つも
無かった**ことが確かめられた(`docs/principles/principle_conformance_review.md`
B-6)。`axes/image_axis/` にあったのは表(`pdf_tables` / `schedule_tables`)、
面積のラベル、表題欄の縮尺表記だけである。**スキャン画像だけの問題ではなく、
文字データのあるベクターのページでも寸法線の数字は読めていなかった。**

そのために、原則3-1(`docs/principles/start_kit.md`)の2つが実装できなかった。

1. 「図面に書かれた縮尺の表記は当てにしない」。**表記を当てにしないなら、
   代わりに長さの出どころが要る。** おーちゃんの追補(2026-09-22)は
   「基準点が無い場合でも、図面に書かれた寸法の数字から計算できるものは
   計算してよい」である。
2. 「人のクリックや入力も間違う前提とし、図面に書かれた寸法の数字と
   突き合わせて検算し、合わなければ警告する」。**突き合わせる相手**が
   これまで無かった。

このモジュールが守ること
------------------------
1. **数字だけでは寸法にならない。** `3640` という文字が図面のどこかにあっても、
   それが**どの2点の間の長さなのか**が取れなければ意味を持たない。
   寸法線(両端に寸法補助線・矢印・目印のある線)との対応が取れない数字は
   **落とす。既定値で埋めない。**
2. **表題欄の縮尺を一度も読まない。** ここで作る比は
   「記入された数字 ÷ その数字が指す紙の上の距離」だけから出る。
   表題欄の印字とは別のデータ源ではない(同じ PDF)が、**別の手法**である。
3. **単位が書かれていない数字の単位を、当てずっぽうで決めない。**
   日本の建築図面の寸法はほぼ mm だが、`3640` が 3640mm なのか 3640m なのかを
   取り違えると **1000 倍ずれる**(status.md 判断待ち⑥)。ここでは
   「建築図面として成り立つ縮尺になるのはどちらか」だけで見分ける。
   `PLAUSIBLE_SCALE_MIN`〜`PLAUSIBLE_SCALE_MAX` の幅は **1000 倍より狭い**ので、
   mm と m の両方が成り立つことは起こりえない(テストで固定してある)。
   どちらも成り立たない数字は落とす。
4. **1件だけの読みから、そのページの縮尺を主張しない**
   (取り決めのルール3「単一の指標だけを根拠に自動確定させる変更はしない」)。
   `page_scale_from_dimensions()` は、互いに一致する読みが 2 件以上あって
   初めて比を返す。**一致しないときは平均を取らず、何も返さない。**
5. **どの読みも主張していない数値を作らない。** 一致した読みの中から、
   平均にいちばん近い**実在の読み**の値を採る。

この手法は未校正である
----------------------
`METHOD_DIMENSION_TEXT` / `METHOD_DIMENSION_SCALE` はどちらも
`arbitration/method_policies.py` に **`calibrated=False`** で登録してある。
合成 PDF でしか確かめていないので、実図面での誤り率は未知である。

拾えないもの(0 件は「寸法が無い」ではない)
------------------------------------------
- スキャンしただけのページ(文字が画像なので1件も読めない)。
- 文字が寸法線に平行に入っていない図面(寸法線と向きが違う文字は対応が取れない)。
- 寸法線を横切る線があるページ。横切りを両端の目印と区別できないので、
  測る区間が短く取られることがある。**この誤りは1件では気づけない。**
  ページ内の突き合わせ(`page_scale_from_dimensions`)で外れとして出ることを
  当てにしている。実図面でどれだけ起きるかは Codex 側で測る項目である。
- 文字が2桁以下の寸法(`90` など)。通り芯の番号や部屋番号と区別できないので、
  桁数で落としている。
- **表の中の数字。** 建具表の幅・高さ・数量は升目の中にあり、罫線は寸法線では
  ない。放っておくと**罫線の升目の長さを「その数字が指す長さ」として読んで
  しまい**、そのページの縮尺を丸ごと誤る(実際に `tests/
  test_drawing_intake_schedules.py` の通しテストが落ちて見つかった)。
  表の位置は既にある `axes/image_axis/pdf_tables.find_tables()` に聞き、
  その中の数字と罫線は寸法の候補から外す。表の数字は
  `axes/image_axis/schedule_tables.py` が読む。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf

from axes.image_axis.pdf_tables import find_tables
from axes.reading.meaning import (
    PURPOSE_RECEIVED_UNLINKED,
    PURPOSE_UNESTABLISHED,
    Meaning,
)

#: 1 ポイント = 1/72 インチ。
MM_PER_POINT = 25.4 / 72.0

#: 手法ID。図面に記入された寸法の数字をそのまま読んだ値。
#: `arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
METHOD_DIMENSION_TEXT = "pdf_dimension_text"

#: 手法ID。記入された寸法から計算したそのページの縮尺。
#: **この縮尺を入れたのは機械である**(周4)。人が入れた値は
#: `intake/start_kit.py` の `ReferencePoint.entered_by` に入り、**欄の名前が別**。
SET_BY_MACHINE = "機械(図面に記入された寸法から出した比)"

#: **表題欄の印字(`pdf_text_scale`)とは別の手法として名前を分けてある。**
#: 突き合わせる相手にするには、別の名前でなければならない。
METHOD_DIMENSION_SCALE = "pdf_dimension_scale"

#: 寸法線として見る線の最短の長さ(ポイント)。これより短い線は目印・矢印・
#: ハッチングなので、寸法線の候補にしない。
MIN_SEGMENT_LENGTH_PT = 10.0

#: 文字の向きと線の向きが平行とみなす角度の差(度)。
MAX_ANGLE_DEVIATION_DEG = 8.0

#: 目印(寸法補助線・矢印・目印の線)が寸法線に接しているとみなす距離(ポイント)。
ANCHOR_TOUCH_PT = 2.5

#: 目印として数えるための、寸法線との角度の差(度)。これ以下なら寸法線と
#: 同じ向きなので、区間の端を決める目印にはならない。
ANCHOR_MIN_ANGLE_DEG = 20.0

#: 文字が寸法線から離れていてよい距離。文字の高さの何倍か + 定数(ポイント)。
TEXT_GAP_HEIGHT_FACTOR = 1.5
TEXT_GAP_CONSTANT_PT = 2.0

#: 文字を寸法線に射影した位置が、区間の外へはみ出してよい距離(ポイント)。
PROJECTION_MARGIN_PT = 2.0

#: 文字が区間の中央からずれていてよい割合。寸法の数字は区間の中央に置かれる
#: のが作図の習わしなので、大きくずれているものは対応が取れていないとみなす。
TEXT_CENTER_MAX_OFFSET_RATIO = 0.25

#: 建築図面の縮尺として成り立つ分母の範囲。
#:
#: **値そのものを出すためには使わない。** 単位が書かれていない数字が
#: mm なのか m なのか(1000 倍違う)を見分けるためだけに使う。
#: **この幅は未校正である。** 1/5 より大きい(詳細図)図面や 1/600 より小さい
#: 図面の寸法は、ここで落ちる。
PLAUSIBLE_SCALE_MIN = 5.0
PLAUSIBLE_SCALE_MAX = 600.0

#: そのページの縮尺を主張するために要る、互いに一致した読みの数。
MIN_AGREEING_READINGS = 2

#: 単位が書かれていない数字に要る最小の桁数。通り芯の番号(`3`)や
#: 部屋番号と区別するため。**2桁以下の寸法は落とす。**
MIN_BARE_DIGITS = 3

#: 寸法として読む最小の長さ(ミリメートル)。
MIN_DIMENSION_MM = 100.0

#: 単位つきの表記から、ミリメートルへの倍率。
_UNIT_FACTORS: dict[str, float] = {
    "mm": 1.0,
    "ｍｍ": 1.0,
    "cm": 10.0,
    "ｃｍ": 10.0,
    "m": 1000.0,
    "ｍ": 1000.0,
}

#: 寸法の数字とみなす文字列。**行全体がこれに一致しなければ読まない。**
#: `1/50`(縮尺)、`AW-1`(建具番号)、`GL+500`(高さの記号)、`2FL` は
#: ここで一致しないので寸法にならない。
_NUMBER_RE = re.compile(
    r"^(?P<number>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<fraction>\d+))?"
    r"\s*(?P<unit>mm|MM|ｍｍ|cm|CM|ｃｍ|m|M|ｍ)?$"
)

#: 対応が取れなかった理由。テストが文字列で確かめるので、ここに集めておく。
REASON_NO_DIMENSION_LINE = "対応する寸法線(両端に目印のある線)が見つからない"
REASON_AMBIGUOUS = "寸法線の候補が複数あり、どれを指しているか決まらない"
REASON_UNIT_UNDECIDED = (
    "単位の表記が無く、mm でも m でも建築図面の縮尺にならないので単位が決まらない"
)
REASON_INSIDE_TABLE = (
    "表の升目の中の数字なので寸法ではない(罫線は寸法線ではない)。"
    "表の数字は schedule_tables が読む"
)

#: 単位の出どころの表記。根拠としてそのまま残す。
UNIT_FROM_TEXT = "単位が表記されていた"
UNIT_FROM_PLAUSIBLE_SCALE = "単位の表記が無く、縮尺が成り立つ側に読んだ"


class DimensionError(ValueError):
    """呼び出しの引数が受け付けられなかった。"""


# ---------------------------------------------------------------------------
# 値の形
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkippedNumber:
    """数字として読めたが、寸法にしなかったもの。**黙って捨てない。**"""

    text: str
    rect_pt: tuple[float, float, float, float]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "rect_pt": list(self.rect_pt),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DimensionReading:
    """図面に記入された寸法 1 件。**数字と、それが指す2点の両方がある。**"""

    page_index: int
    """0 始まりのページ番号。"""

    text: str
    """図面から読んだ文字そのまま。根拠として残す。"""

    value_mm: float
    """記入された長さ(ミリメートル)。**計算していない。単位だけ直した値。**"""

    unit_source: str
    """単位をどう決めたか。`UNIT_FROM_TEXT` か `UNIT_FROM_PLAUSIBLE_SCALE`。"""

    start_pt: tuple[float, float]
    end_pt: tuple[float, float]
    """その数字が指している2点(ページ座標・ポイント)。

    **人が入れた基準点(`intake/start_kit.py` の `ReferencePoint`)と同じ形**
    である。図面の寸法は「2点と、その実際の長さ」なので、人が物差しを当てた
    ものと突き合わせられる。
    """

    paper_distance_pt: float
    """2点の間の紙の上の距離(ポイント)。"""

    orientation: str
    """``横`` / ``縦`` / ``斜め``。人が入れた基準点の縦横と突き合わせるために持つ。"""

    text_rect_pt: tuple[float, float, float, float]
    """数字が書かれている位置。根拠として残す。"""

    meaning: Meaning
    """意味の4欄(原則2)。**新しく書くコードでは必須**という 2026-09-22 の判断。"""

    @property
    def page_number(self) -> int:
        """1 始まりのページ番号(人に見せる番号)。"""
        return self.page_index + 1

    @property
    def mm_per_point(self) -> float:
        """紙の 1 ポイントが実寸で何ミリか。**この寸法1件だけから出した比。**"""
        return self.value_mm / self.paper_distance_pt

    @property
    def denominator(self) -> float:
        """縮尺の分母。1/50 なら 50.0。**表題欄を読んでいない。**"""
        return self.mm_per_point / MM_PER_POINT

    def provenance(self) -> dict[str, Any]:
        """根拠。ページ番号・座標・元の文字列を落とさない。"""
        return {
            "page_number": self.page_number,
            "source_text": self.text,
            "value_mm": self.value_mm,
            "unit_source": self.unit_source,
            "start_pt": list(self.start_pt),
            "end_pt": list(self.end_pt),
            "paper_distance_pt": round(self.paper_distance_pt, 3),
            "orientation": self.orientation,
            "text_rect_pt": list(self.text_rect_pt),
            "derived_denominator": round(self.denominator, 4),
            "meaning": self.meaning.as_dict(),
            "limitation": (
                "図面に記入された数字をそのまま読んだ値。"
                "寸法線を横切る線があると測る区間が短く取られることがある"
            ),
        }


@dataclass(frozen=True)
class DimensionPage:
    """1 ページぶんの読み取り結果。**読めなかったものもここに残る。**"""

    page_index: int
    readings: tuple[DimensionReading, ...]
    skipped: tuple[SkippedNumber, ...]

    @property
    def page_number(self) -> int:
        return self.page_index + 1


@dataclass(frozen=True)
class DimensionScale:
    """記入された寸法から出した、そのページの縮尺と、その突き合わせの中身。"""

    denominator: float
    """1/50 なら 50.0。**一致した読みの中の実在の1件の値。平均ではない。**"""

    agreeing_count: int
    """互いに一致した読みの件数。"""

    total_count: int
    """そのページで寸法として読めた件数。"""

    outlier_values_mm: tuple[float, ...]
    """一致しなかった読みの値。**捨てずに残す。**"""

    source_text: str
    """比の出どころにした寸法の、図面の文字そのまま。"""

    @property
    def set_by(self) -> str:
        """**この縮尺を入れたのは機械である**(2026-09-25、周4)。

        おーちゃんの決まり: **AI が基準を入れたら、必ずそう記録する。
        人が入れたものと混ぜない。**縮尺の基準となる長さは、本来は人が決める
        作業である(K-34)。人の入力が空のあいだ、機械が代わりに出している。

        **書き換えられる口を持たせない。**定数を返すだけにしてあるので、
        ここに人の名前が入ることはない。人が入れた値は
        `intake/start_kit.py` の `ReferencePoint.entered_by` に入り、
        **欄の名前が別である。**

        **いまのところ、この欄を読んで判定を変える場所は 1 か所も無い**
        (記録するところまでが、2026-09-25 の周4 の範囲)。
        """
        return SET_BY_MACHINE

    @property
    def mm_per_point(self) -> float:
        return self.denominator * MM_PER_POINT

    def provenance(self) -> dict[str, Any]:
        return {
            "denominator": round(self.denominator, 4),
            "agreeing_count": self.agreeing_count,
            "total_count": self.total_count,
            "outlier_values_mm": list(self.outlier_values_mm),
            "source_text": self.source_text,
            "set_by": self.set_by,
            "limitation": (
                "図面に記入された寸法どうしの一致から出した比。"
                "表題欄の印字は読んでいない。**未校正の手法である**"
            ),
        }


# ---------------------------------------------------------------------------
# 1. 線を集める
# ---------------------------------------------------------------------------

Segment = tuple[tuple[float, float], tuple[float, float]]


def _inside(
    rect: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
    margin: float = 1.0,
) -> bool:
    return (
        rect[0] >= outer[0] - margin
        and rect[1] >= outer[1] - margin
        and rect[2] <= outer[2] + margin
        and rect[3] <= outer[3] + margin
    )


def _in_any_table(
    rect: tuple[float, float, float, float],
    tables: Sequence[tuple[float, float, float, float]],
) -> bool:
    return any(_inside(rect, table) for table in tables)


def _collect_segments(page: pymupdf.Page) -> list[Segment]:
    """ページの直線を集める。ベジェ曲線(``"c"``)は見ない。

    同じ線が2度描かれていることがある(閉じた形を作るときなど)ので、
    向きを揃えて重複を落とす。**重複を残すと、同じ線が「候補が複数ある」
    と判定されて読みが落ちる。**
    """
    seen: set[tuple[float, float, float, float]] = set()
    out: list[Segment] = []

    def add(p0: tuple[float, float], p1: tuple[float, float]) -> None:
        a = (round(p0[0], 2), round(p0[1], 2))
        b = (round(p1[0], 2), round(p1[1], 2))
        if a == b:
            return
        key = (*a, *b) if a <= b else (*b, *a)
        if key in seen:
            return
        seen.add(key)
        out.append((a, b) if a <= b else (b, a))

    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                add((item[1].x, item[1].y), (item[2].x, item[2].y))
            elif item[0] == "re":
                rect = item[1]
                corners = [
                    (rect.x0, rect.y0),
                    (rect.x1, rect.y0),
                    (rect.x1, rect.y1),
                    (rect.x0, rect.y1),
                ]
                for index in range(4):
                    add(corners[index], corners[(index + 1) % 4])
    return out


def _length(segment: Segment) -> float:
    (x0, y0), (x1, y1) = segment
    return math.hypot(x1 - x0, y1 - y0)


def _unit(segment: Segment) -> tuple[float, float]:
    (x0, y0), (x1, y1) = segment
    length = _length(segment)
    return ((x1 - x0) / length, (y1 - y0) / length)


def _angle_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2 つの向きの角度の差(度)。**向きの符号は無視する**(0〜90 度)。"""
    dot = min(1.0, abs(a[0] * b[0] + a[1] * b[1]))
    return math.degrees(math.acos(dot))


def _point_to_segment_distance(
    point: tuple[float, float], segment: Segment
) -> tuple[float, float]:
    """点から線分への距離と、線分の始点からの位置(ポイント)を返す。"""
    (x0, y0), (x1, y1) = segment
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0.0:
        return math.hypot(point[0] - x0, point[1] - y0), 0.0
    t = ((point[0] - x0) * dx + (point[1] - y0) * dy) / length
    clamped = max(0.0, min(length, t))
    px = x0 + dx * clamped / length
    py = y0 + dy * clamped / length
    return math.hypot(point[0] - px, point[1] - py), t


def _crossing_param(base: Segment, other: Segment) -> float | None:
    """`other` が `base` を横切る位置(`base` の始点からのポイント)。

    平行なら None。**交点が両方の線分の内側にあるときだけ**返す。
    寸法線の途中を横切る寸法補助線(連続した寸法の区切り)は、端点が
    どちらの線分の端にも無いので、この判定でしか拾えない。
    """
    (ax0, ay0), (ax1, ay1) = base
    (bx0, by0), (bx1, by1) = other
    rx, ry = ax1 - ax0, ay1 - ay0
    sx, sy = bx1 - bx0, by1 - by0
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-9:
        return None
    t = ((bx0 - ax0) * sy - (by0 - ay0) * sx) / denominator
    u = ((bx0 - ax0) * ry - (by0 - ay0) * rx) / denominator
    base_length = math.hypot(rx, ry)
    other_length = math.hypot(sx, sy)
    if base_length == 0.0 or other_length == 0.0:
        return None
    margin_t = ANCHOR_TOUCH_PT / base_length
    margin_u = ANCHOR_TOUCH_PT / other_length
    if not -margin_t <= t <= 1.0 + margin_t:
        return None
    if not -margin_u <= u <= 1.0 + margin_u:
        return None
    return max(0.0, min(base_length, t * base_length))


def _anchor_params(base: Segment, others: Sequence[Segment]) -> list[float]:
    """寸法線の候補 `base` の上で、目印が付いている位置を集める。

    目印とは、`base` と**同じ向きでない**線が接している所である。
    寸法補助線でも、45 度の目印でも、矢印を描く線でも同じに拾える。
    **目印が2つ以上無い線は、寸法線として使わない。**
    """
    base_unit = _unit(base)
    base_length = _length(base)
    params: list[float] = []
    for other in others:
        if other == base:
            continue
        if _length(other) < 1e-6:
            continue
        if _angle_between(base_unit, _unit(other)) <= ANCHOR_MIN_ANGLE_DEG:
            continue
        candidates: list[float] = []
        crossing = _crossing_param(base, other)
        if crossing is not None:
            candidates.append(crossing)
        for endpoint in other:
            distance, t = _point_to_segment_distance(endpoint, base)
            if distance <= ANCHOR_TOUCH_PT:
                candidates.append(max(0.0, min(base_length, t)))
        for endpoint in base:
            distance, _ = _point_to_segment_distance(endpoint, other)
            if distance <= ANCHOR_TOUCH_PT:
                _, t = _point_to_segment_distance(endpoint, base)
                candidates.append(max(0.0, min(base_length, t)))
        params.extend(candidates)

    merged: list[float] = []
    for value in sorted(params):
        if not merged or value - merged[-1] > ANCHOR_TOUCH_PT:
            merged.append(value)
    return merged


@dataclass(frozen=True)
class _Span:
    """寸法線の、両端に目印のある1区間。"""

    segment: Segment
    start_param: float
    end_param: float

    @property
    def length(self) -> float:
        return self.end_param - self.start_param

    @property
    def center_param(self) -> float:
        return (self.start_param + self.end_param) / 2.0

    def endpoints(self) -> tuple[tuple[float, float], tuple[float, float]]:
        (x0, y0), _ = self.segment
        ux, uy = _unit(self.segment)
        return (
            (x0 + ux * self.start_param, y0 + uy * self.start_param),
            (x0 + ux * self.end_param, y0 + uy * self.end_param),
        )


def _spans(segments: Sequence[Segment]) -> list[_Span]:
    """目印で区切られた区間を全部集める。

    連続した寸法(``910 | 1820 | 910`` のように1本の線に並ぶもの)を、
    区切りごとの区間として扱えるようにするためである。
    """
    out: list[_Span] = []
    for segment in segments:
        if _length(segment) < MIN_SEGMENT_LENGTH_PT:
            continue
        params = _anchor_params(segment, segments)
        if len(params) < 2:
            # 両端に目印が無い線は寸法線ではない(壁・通り芯・ハッチング)。
            continue
        for index in range(len(params) - 1):
            start, end = params[index], params[index + 1]
            if end - start < MIN_SEGMENT_LENGTH_PT:
                continue
            out.append(_Span(segment=segment, start_param=start, end_param=end))
    return out


# ---------------------------------------------------------------------------
# 2. 数字を集める
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NumberText:
    text: str
    rect_pt: tuple[float, float, float, float]
    direction: tuple[float, float]
    value: float
    has_unit: bool
    unit_factor: float
    digit_count: int

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.rect_pt
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    @property
    def height(self) -> float:
        x0, y0, x1, y1 = self.rect_pt
        # 90 度回転した文字では幅と高さが入れ替わる。短いほうが字の高さ。
        return min(x1 - x0, y1 - y0)


def _number_texts(page: pymupdf.Page) -> list[_NumberText]:
    """ページの文字から、寸法の数字になりうる行だけを位置と向きつきで集める。

    **行全体が数字(と単位)でなければ読まない。** 図面の文字は
    `1/50`(縮尺)・`AW-1`(建具番号)・`GL+500`(高さ)・`2FL` のように、
    数字を含むだけのものが多い。
    """
    out: list[_NumberText] = []
    for block in page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            text = "".join(span.get("text", "") for span in line.get("spans", ())).strip()
            if not text:
                continue
            match = _NUMBER_RE.match(text)
            if match is None:
                continue
            digits = match.group("number").replace(",", "")
            fraction = match.group("fraction")
            raw = float(digits + ("." + fraction if fraction else ""))
            unit = match.group("unit")
            has_unit = unit is not None
            factor = _UNIT_FACTORS[unit.lower()] if unit is not None else 1.0
            x0, y0, x1, y1 = line["bbox"]
            direction = tuple(float(value) for value in line.get("dir", (1.0, 0.0)))
            if math.hypot(*direction) < 1e-9:
                direction = (1.0, 0.0)
            out.append(
                _NumberText(
                    text=text,
                    rect_pt=(float(x0), float(y0), float(x1), float(y1)),
                    direction=(direction[0], direction[1]),
                    value=raw,
                    has_unit=has_unit,
                    unit_factor=factor,
                    digit_count=len(digits),
                )
            )
    return out


# ---------------------------------------------------------------------------
# 3. 数字と寸法線を対応させる
# ---------------------------------------------------------------------------


def _matching_spans(number: _NumberText, spans: Sequence[_Span]) -> list[_Span]:
    """その数字が指していると考えられる区間を集める。

    条件は 4 つ。

    1. 数字の向きと線の向きが平行(`MAX_ANGLE_DEVIATION_DEG` 以内)
    2. 数字が線から離れすぎていない(字の高さを基準にする)
    3. 数字を線に射影した位置が、区間の中にある
    4. その位置が区間の中央から大きくずれていない(作図の習わし)
    """
    center = number.center
    max_gap = TEXT_GAP_HEIGHT_FACTOR * number.height + TEXT_GAP_CONSTANT_PT
    out: list[_Span] = []
    for span in spans:
        if _angle_between(number.direction, _unit(span.segment)) > MAX_ANGLE_DEVIATION_DEG:
            continue
        distance, param = _point_to_segment_distance(center, span.segment)
        if distance > max_gap:
            continue
        if not (
            span.start_param - PROJECTION_MARGIN_PT
            <= param
            <= span.end_param + PROJECTION_MARGIN_PT
        ):
            continue
        if abs(param - span.center_param) > TEXT_CENTER_MAX_OFFSET_RATIO * span.length:
            continue
        out.append(span)
    return out


def _pick_span(matches: Sequence[_Span]) -> _Span | None:
    """候補の中から1つ選ぶ。**長さが食い違う候補が残っていたら選ばない。**

    連続した寸法では、区切りの細かい区間だけが候補になるので1つに決まる。
    寸法線が2本重なっている所では長さの違う候補が残るので、
    **どちらかを選ばずに落とす。**
    """
    if not matches:
        return None
    shortest = min(matches, key=lambda span: span.length)
    for span in matches:
        if abs(span.length - shortest.length) > ANCHOR_TOUCH_PT:
            return None
    return shortest


def _resolve_unit(
    number: _NumberText, paper_distance_pt: float
) -> tuple[float, str] | None:
    """その数字の実寸(ミリメートル)と、単位の決め方を返す。決まらなければ None。

    単位が書かれていればそれに従う。書かれていなければ、**建築図面として
    成り立つ縮尺になるほうに読む。** mm と m は 1000 倍違い、成り立つ縮尺の幅は
    1000 倍より狭いので、両方が成り立つことは起こりえない。
    """
    if paper_distance_pt <= 0.0:
        return None

    def denominator_for(value_mm: float) -> float:
        return value_mm / paper_distance_pt / MM_PER_POINT

    if number.has_unit:
        value_mm = number.value * number.unit_factor
        if value_mm < MIN_DIMENSION_MM:
            return None
        if not PLAUSIBLE_SCALE_MIN <= denominator_for(value_mm) <= PLAUSIBLE_SCALE_MAX:
            return None
        return value_mm, UNIT_FROM_TEXT

    if number.digit_count < MIN_BARE_DIGITS:
        return None
    plausible = [
        value_mm
        for value_mm in (number.value, number.value * 1000.0)
        if value_mm >= MIN_DIMENSION_MM
        and PLAUSIBLE_SCALE_MIN <= denominator_for(value_mm) <= PLAUSIBLE_SCALE_MAX
    ]
    if len(plausible) != 1:
        return None
    return plausible[0], UNIT_FROM_PLAUSIBLE_SCALE


def _orientation(segment: Segment) -> str:
    ux, uy = _unit(segment)
    if abs(ux) >= math.cos(math.radians(MAX_ANGLE_DEVIATION_DEG)):
        return "横"
    if abs(uy) >= math.cos(math.radians(MAX_ANGLE_DEVIATION_DEG)):
        return "縦"
    return "斜め"


def read_dimensions(
    pdf_path: str | Path,
    page_index: int,
    *,
    phase: str = "不明",
    purpose_received: bool = False,
) -> DimensionPage:
    """1 ページから、記入された寸法を読む。読めなければ空。

    `phase` は人がそのページについて宣言した現況/計画/解体である
    (`intake/start_kit.py` の `PageDeclaration.phase`)。**宣言が無ければ
    ``"不明"`` のまま**で、ここで推測はしない。意味の4欄(原則2)に入る。

    `purpose_received` は、人が目的(`intake/start_kit.py` の `Purpose`)を
    渡しているかである。**渡されていても、その目的と個々の寸法を結び付ける
    経路はまだ無い**(原則3-2の二段階目が未実装)ので、意味の4欄の
    `purpose_link` には「受け皿が無い」のか「結び付けが無い」のかを分けて
    印だけを置く。**方向性の自由記述をここに写さない。**
    """
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        page = doc.load_page(page_index)
        segments = _collect_segments(page)
        numbers = _number_texts(page)

    # 表の位置は、既にある検出器に聞く(自前で罫線を探し直さない)。
    tables = tuple(region.rect_pt for region in find_tables(pdf_path, page_index))
    if tables:
        # 表の罫線を寸法線の候補から外す。**外さないと升目の長さを
        # 「その数字が指す長さ」として読んでしまう。**
        segments = [
            segment
            for segment in segments
            if not _in_any_table(
                (
                    min(segment[0][0], segment[1][0]),
                    min(segment[0][1], segment[1][1]),
                    max(segment[0][0], segment[1][0]),
                    max(segment[0][1], segment[1][1]),
                ),
                tables,
            )
        ]
    spans = _spans(segments)

    readings: list[DimensionReading] = []
    skipped: list[SkippedNumber] = []
    for number in numbers:
        if _in_any_table(number.rect_pt, tables):
            skipped.append(
                SkippedNumber(
                    text=number.text,
                    rect_pt=number.rect_pt,
                    reason=REASON_INSIDE_TABLE,
                )
            )
            continue
        matches = _matching_spans(number, spans)
        if not matches:
            skipped.append(
                SkippedNumber(
                    text=number.text,
                    rect_pt=number.rect_pt,
                    reason=REASON_NO_DIMENSION_LINE,
                )
            )
            continue
        span = _pick_span(matches)
        if span is None:
            skipped.append(
                SkippedNumber(
                    text=number.text, rect_pt=number.rect_pt, reason=REASON_AMBIGUOUS
                )
            )
            continue
        resolved = _resolve_unit(number, span.length)
        if resolved is None:
            skipped.append(
                SkippedNumber(
                    text=number.text,
                    rect_pt=number.rect_pt,
                    reason=REASON_UNIT_UNDECIDED,
                )
            )
            continue
        value_mm, unit_source = resolved
        start, end = span.endpoints()
        readings.append(
            DimensionReading(
                page_index=page_index,
                text=number.text,
                value_mm=value_mm,
                unit_source=unit_source,
                start_pt=start,
                end_pt=end,
                paper_distance_pt=span.length,
                orientation=_orientation(span.segment),
                text_rect_pt=number.rect_pt,
                meaning=Meaning(
                    what="図面に記入された寸法",
                    where=(
                        f"ページ{page_index + 1} "
                        f"({start[0]:.1f},{start[1]:.1f})-({end[0]:.1f},{end[1]:.1f})"
                    ),
                    phase=phase,
                    # 目的と個々の寸法を結び付ける経路がまだ無い。作った文字列で
                    # 埋めず、目的が渡されているかどうかだけを分けて印を置く。
                    purpose_link=(
                        PURPOSE_RECEIVED_UNLINKED
                        if purpose_received
                        else PURPOSE_UNESTABLISHED
                    ),
                ),
            )
        )

    # 何度読んでも同じ順番になるようにする(決定性を壊さない)。
    readings.sort(
        key=lambda reading: (
            round(reading.text_rect_pt[1], 1),
            round(reading.text_rect_pt[0], 1),
            reading.value_mm,
        )
    )
    skipped.sort(key=lambda item: (round(item.rect_pt[1], 1), round(item.rect_pt[0], 1)))
    return DimensionPage(
        page_index=page_index, readings=tuple(readings), skipped=tuple(skipped)
    )


# ---------------------------------------------------------------------------
# 4. ページの中で突き合わせる
# ---------------------------------------------------------------------------


def _agree(first: float, second: float, tolerance: float) -> bool:
    mean = (first + second) / 2.0
    if mean <= 0.0:
        return False
    return abs(first - second) / mean <= tolerance


def page_scale_from_dimensions(
    page: DimensionPage,
    *,
    tolerance: float,
    min_agreeing: int = MIN_AGREEING_READINGS,
) -> DimensionScale | None:
    """そのページの寸法どうしを突き合わせて、縮尺を出す。出せなければ None。

    **`tolerance` に既定値を置かない。** 許容差はこのリポジトリで1つに
    決めるべき値で(v8 10章12項が未決)、ここに既定を置くと2箇所目の
    基準値になってしまう。呼び出し側(`intake/`)が渡す。

    出せない場合は3つある。どれも**推測せずに None を返す。**

    - 寸法が `min_agreeing` 件より少ない … 1件だけの読みから主張しない
    - 互いに一致する組がいちばん多いものが決まらない … どちらも採らない
    - 一致した件数が `min_agreeing` に届かない
    """
    if tolerance < 0.0:
        raise DimensionError("許容差は 0 以上である必要があります")
    readings = page.readings
    if len(readings) < min_agreeing:
        return None

    denominators = [reading.denominator for reading in readings]
    groups: list[list[int]] = []
    for index, value in enumerate(denominators):
        members = [
            other
            for other, candidate in enumerate(denominators)
            if _agree(value, candidate, tolerance)
        ]
        if all(
            _agree(denominators[a], denominators[b], tolerance)
            for a in members
            for b in members
        ):
            groups.append(members)
    if not groups:
        return None

    largest = max(len(group) for group in groups)
    if largest < min_agreeing:
        return None
    best = [group for group in groups if len(group) == largest]
    # 同じ大きさの組が2つ以上あって、互いに一致しないなら決められない。
    reference = denominators[best[0][0]]
    if any(not _agree(reference, denominators[group[0]], tolerance) for group in best):
        return None

    members = best[0]
    mean = sum(denominators[index] for index in members) / len(members)
    # **平均は採らない。** 平均にいちばん近い「実在の読み」を採る。
    chosen = min(members, key=lambda index: abs(denominators[index] - mean))
    outliers = tuple(
        readings[index].value_mm
        for index in range(len(readings))
        if index not in set(members)
    )
    return DimensionScale(
        denominator=denominators[chosen],
        agreeing_count=len(members),
        total_count=len(readings),
        outlier_values_mm=outliers,
        source_text=readings[chosen].text,
    )


def describe(page: DimensionPage) -> str:
    """人が読める表にする。報告書にそのまま貼るための出力。"""
    lines = [
        f"ページ {page.page_number}: 寸法 {len(page.readings)} 件 / "
        f"読まなかった数字 {len(page.skipped)} 件",
        "| 文字 | 実寸(mm) | 紙(pt) | 向き | 縮尺の分母 | 単位の決め方 |",
        "|---|---|---|---|---|---|",
    ]
    for reading in page.readings:
        lines.append(
            f"| {reading.text} | {reading.value_mm:g} "
            f"| {reading.paper_distance_pt:.2f} | {reading.orientation} "
            f"| {reading.denominator:.2f} | {reading.unit_source} |"
        )
    return "\n".join(lines)
