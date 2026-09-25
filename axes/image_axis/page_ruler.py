"""画像軸: **1 ページぶんの目盛り**。長さの比が決まれば、どの場所でも測れる。

なぜこのモジュールが要るのか
----------------------------
おーちゃんの言(2026年9月24日、K-29)。

> 一番最初に人間が基準点・基準線・基準の長さを出したら、それがタイル処理になって
> 図面全体に基準線ができます。**そうするとどこを聞かれても、どの面積でも測れる**
> はずですよね。**すべて、どこのどの部分と言われてもその面積が出せるような仕組みを
> 考えないと、寸法を読むということにはなりません。**

リポジトリには、**紙の上の長さを実寸に直す入口が 1 つも無かった。**
縮尺を持つ部品はいくつかある(`pdf_vector_symbols.extract_scale` は表題欄の印字、
`pdf_dimensions.page_scale_from_dimensions` は記入された寸法)が、
**「この矩形は何平米か」を聞ける先が無い。**面積を出す経路はどれも、
室の輪郭を取る処理の中に換算を抱え込んでいた。

このモジュールが持つ値は **1 つだけ**である。``mm_per_point``。
**これが決まれば、そのページのどこでも長さと面積が出る。**

このモジュールが守ること
------------------------
1. **目盛りが無ければ測らない。**縮尺を仮定して数字を出さない。
2. **どの出どころでも ``calibrated`` は ``False``。**人が 1 回入れた値と印字が
   合っただけで階層1(自動確定)に届く道を作らない。
   → `docs/coordination/README.md`、`docs/k29_area_expert_reading_criteria.md` 追記5。
3. **食い違う目盛りを平均しない。**許容差の外なら**どちらも採らず**食い違いとして返す。
   P011 匿名化v2 の 17 ページが実例で、記入寸法と印字が 2.70% 違う。
4. **回転で値は変わらない。**長さは回転で変わらないので ``mm_per_point`` も変わらない。
   回転 0・90・270 の合成の紙で試験に固定してある
   (K-26 で踏んだ「回転前の座標を前提にした値」と同じ轍を踏まない)。
5. **本番の経路には繋がない。**`intake/` からはまだ呼ばれない。

AI が選んだ基準(K-37、2026-09-25)
-----------------------------------
おーちゃんの K-37 やること 2。**人が 2 点を指して実長を入れる作業を、AI が代わる。**
AI が図面の中から基準にすべき寸法線を 1 本選び、機械がその 2 点の距離から比を出す
(`ruler_from_chosen_dimension`)。**出どころは ``SOURCE_AI_REFERENCE`` で、人の入力と混ぜない。**
誰が・なぜ選んだかを ``note`` に残し、理由の無い選択は受け付けない。

検算(`cross_check`)は、同じページのほかの寸法をその比で割り戻す。
ほかが全部そろえば「揃っている」、1 本だけ外れればその寸法を疑い、
2 本以上外れれば基準のほうを疑う。**外れた寸法は捨てずにずれと一緒に返す。**
表記の縮尺は当てにしないが、比べる相手として差を返す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "SOURCE_HUMAN",
    "SOURCE_DIMENSIONS",
    "SOURCE_PRINTED_SCALE",
    "SOURCE_AI_REFERENCE",
    "CHECK_ALL_AGREE",
    "CHECK_ONE_OFF",
    "CHECK_SEVERAL_OFF",
    "PageRuler",
    "CrossCheck",
    "OffDimension",
    "RulerConflict",
    "RulerError",
    "ruler_from_reference_length",
    "ruler_from_page_scale",
    "ruler_from_printed_scale",
    "ruler_from_chosen_dimension",
    "cross_check",
    "agree",
    "reconcile",
    "group_by_agreement",
]

#: 出どころの表記。**根拠としてそのまま残す。**
SOURCE_HUMAN = "人の基準の長さ"
SOURCE_DIMENSIONS = "記入された寸法"
SOURCE_PRINTED_SCALE = "印字された縮尺"
#: AI が図面の中から選んだ 1 本の寸法線(K-37)。**人の基準の長さとは別の出どころ。**
SOURCE_AI_REFERENCE = "AIが選んだ基準の寸法"

_SOURCES = (SOURCE_HUMAN, SOURCE_AI_REFERENCE, SOURCE_DIMENSIONS, SOURCE_PRINTED_SCALE)

#: 検算の結論。
CHECK_ALL_AGREE = "揃っている"
CHECK_ONE_OFF = "1本だけ外れた(その寸法が誤りの疑い)"
CHECK_SEVERAL_OFF = "2本以上外れた(基準が疑わしい)"

#: 1 インチのミリ数と、1 インチのポイント数。PDF の座標は 1/72 インチ。
_MM_PER_INCH = 25.4
_POINTS_PER_INCH = 72.0


class RulerError(ValueError):
    """目盛りが作れない。**推測して作らない。**"""


@dataclass(frozen=True)
class RulerConflict:
    """食い違った目盛り。**平均しない。人に見せる。**"""

    rulers: tuple["PageRuler", ...]
    tolerance: float

    @property
    def spread(self) -> float:
        """いちばん大きい値といちばん小さい値の、中央に対する開き。"""
        values = [ruler.mm_per_point for ruler in self.rulers]
        middle = (max(values) + min(values)) / 2
        return (max(values) - min(values)) / middle if middle else math.inf

    def describe(self) -> str:
        parts = ", ".join(
            f"{ruler.source} {ruler.mm_per_point:.4f}mm/pt" for ruler in self.rulers
        )
        return (
            f"目盛りが食い違っている(許容差 {self.tolerance:.0%}、"
            f"開き {self.spread:.2%}): {parts}"
        )


@dataclass(frozen=True)
class PageRuler:
    """1 ページぶんの目盛り。**長さの比 1 つだけを持つ。**"""

    page_index: int
    """0 始まり。"""

    mm_per_point: float
    """紙の上の 1pt が実寸何 mm か。**これが目盛りの全部である。**"""

    source: str
    """どこから出したか。``SOURCE_*`` のどれか。"""

    note: str = ""
    """出どころの但し書き。人が読む。"""

    calibrated: bool = False
    """**常に ``False``。**

    人の入力も、記入された寸法も、印字された縮尺も、**校正されていない。**
    ここを ``True`` にすると、人が 1 回入れた値と印字が合っただけで
    階層1に届いてしまう。**変えない。**
    """

    def __post_init__(self) -> None:
        if self.mm_per_point <= 0 or not math.isfinite(self.mm_per_point):
            raise RulerError(f"長さの比が正の有限値ではありません: {self.mm_per_point}")
        if self.source not in _SOURCES:
            raise RulerError(f"知らない出どころです: {self.source}")
        if self.calibrated:
            raise RulerError(
                "目盛りを校正済みにはできません。"
                "人が入れた値と印字が合っただけで自動確定に届く道を作らない"
            )

    @property
    def denominator(self) -> float:
        """縮尺の分母(1/50 なら 50)。**印字と比べるためだけに使う。**"""
        return self.mm_per_point * _POINTS_PER_INCH / _MM_PER_INCH

    def length_mm(self, length_pt: float) -> float:
        """紙の上の長さ(pt)を実寸(mm)に直す。"""
        if length_pt < 0:
            raise RulerError("長さは 0 以上である必要があります")
        return length_pt * self.mm_per_point

    def distance_mm(
        self, start: Sequence[float], end: Sequence[float]
    ) -> float:
        """2 点のあいだの実寸(mm)。**回転しても同じ値になる。**"""
        return self.length_mm(_distance(start, end))

    def area_sqm(self, area_pt2: float) -> float:
        """紙の上の面積(pt^2)を実寸の面積(m^2)に直す。"""
        if area_pt2 < 0:
            raise RulerError("面積は 0 以上である必要があります")
        return area_pt2 * (self.mm_per_point**2) / 1_000_000.0

    def rect_mm(
        self, rect_pt: Sequence[float]
    ) -> tuple[float, float]:
        """矩形の幅と高さを実寸(mm)で。``(x0, y0, x1, y1)`` を受ける。"""
        x0, y0, x1, y1 = rect_pt
        return self.length_mm(abs(x1 - x0)), self.length_mm(abs(y1 - y0))

    def rect_area_sqm(self, rect_pt: Sequence[float]) -> float:
        """矩形の面積(m^2)。"""
        width_mm, height_mm = self.rect_mm(rect_pt)
        return width_mm * height_mm / 1_000_000.0

    def polygon_area_sqm(self, points_pt: Sequence[Sequence[float]]) -> float:
        """多角形の面積(m^2)。**閉じていなくてよい**(最後と最初をつなぐ)。"""
        if len(points_pt) < 3:
            raise RulerError("多角形には 3 点以上が必要です")
        total = 0.0
        for index, (x0, y0) in enumerate(points_pt):
            x1, y1 = points_pt[(index + 1) % len(points_pt)]
            total += x0 * y1 - x1 * y0
        return self.area_sqm(abs(total) / 2.0)


def _distance(start: Sequence[float], end: Sequence[float]) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


def ruler_from_reference_length(
    page_index: int,
    start_pt: Sequence[float],
    end_pt: Sequence[float],
    real_mm: float,
    *,
    note: str = "",
) -> PageRuler:
    """**人が指した 2 点と、その実寸**から目盛りを作る。

    おーちゃんの言う「一番最初に人間が基準の長さを出す」ところである。
    **紙の縮尺は見ない。**2 点の紙の上の距離と、人が入れた実寸の比だけで決まる。
    だから**用紙が拡大縮小されていても効く。**
    """
    if real_mm <= 0 or not math.isfinite(real_mm):
        raise RulerError(f"基準の長さが正の有限値ではありません: {real_mm}")
    paper_pt = _distance(start_pt, end_pt)
    if paper_pt <= 0:
        raise RulerError("基準の 2 点が同じ場所です。長さが 0 では比が出ません")
    return PageRuler(
        page_index=page_index,
        mm_per_point=real_mm / paper_pt,
        source=SOURCE_HUMAN,
        note=note or f"人が指した 2 点({paper_pt:.2f}pt)を {real_mm:g}mm と入れた",
    )


def ruler_from_page_scale(page_index: int, scale: object) -> PageRuler:
    """`pdf_dimensions.page_scale_from_dimensions()` の結果から作る。

    ``mm_per_point`` を持つものなら何でも受ける(型の輸入を増やさないため)。
    """
    value = getattr(scale, "mm_per_point", None)
    if value is None:
        raise RulerError("記入された寸法からの縮尺に mm_per_point がありません")
    count = getattr(scale, "reading_count", None)
    note = "図面に記入された寸法どうしが一致した"
    if count is not None:
        note += f"(一致した読み {count} 件)"
    return PageRuler(
        page_index=page_index,
        mm_per_point=float(value),
        source=SOURCE_DIMENSIONS,
        note=note,
    )


def ruler_from_printed_scale(page_index: int, denominator: float) -> PageRuler:
    """表題欄に**印字された縮尺**から作る。

    原則3-1 は「図面に書かれた縮尺の表記は当てにしない」と決めている。
    **だからこれは単独で使うものではなく、ほかの出どころと突き合わせる相手である。**
    """
    if denominator <= 0 or not math.isfinite(denominator):
        raise RulerError(f"縮尺の分母が正の有限値ではありません: {denominator}")
    return PageRuler(
        page_index=page_index,
        mm_per_point=denominator * _MM_PER_INCH / _POINTS_PER_INCH,
        source=SOURCE_PRINTED_SCALE,
        note=f"表題欄の印字 1/{denominator:g}。**当てにしない前提の値**",
    )


def ruler_from_chosen_dimension(
    page_index: int,
    start_pt: Sequence[float],
    end_pt: Sequence[float],
    value_mm: float,
    *,
    text: str,
    chosen_by: str,
    reason: str,
) -> PageRuler:
    """**AI が選んだ 1 本の寸法線**から目盛りを作る(K-37)。

    比は「記入された値 ÷ その 2 点の紙の上の距離」だけで決まる。表題欄の縮尺は見ない。
    ``chosen_by`` と ``reason`` は根拠としてそのまま残す。**どちらかが空なら作らない。**
    """
    if not chosen_by.strip() or not reason.strip():
        raise RulerError("誰が・なぜその寸法線を基準に選んだかが無い選択は受け付けません")
    if value_mm <= 0 or not math.isfinite(value_mm):
        raise RulerError(f"基準の寸法が正の有限値ではありません: {value_mm}")
    paper_pt = _distance(start_pt, end_pt)
    if paper_pt <= 0:
        raise RulerError("基準の寸法線の 2 点が同じ場所です。長さが 0 では比が出ません")
    return PageRuler(
        page_index=page_index,
        mm_per_point=value_mm / paper_pt,
        source=SOURCE_AI_REFERENCE,
        note=f"{chosen_by} が寸法「{text}」({paper_pt:.2f}pt)を基準に選んだ。理由: {reason}",
    )


@dataclass(frozen=True)
class OffDimension:
    """検算で外れた寸法。**捨てずに、ずれと一緒に残す。**"""

    text: str
    value_mm: float
    measured_mm: float
    """基準の比で紙の上の区間を測った長さ。"""

    deviation: float
    """記入された値が、測った長さより何割大きいか(+0.1 なら 10% 大きい)。"""


@dataclass(frozen=True)
class CrossCheck:
    """基準の比で、同じページのほかの寸法を割り戻した結果。"""

    ruler: PageRuler
    tolerance: float
    agreeing: int
    off: tuple[OffDimension, ...]
    printed_difference: float | None = None
    """表記の縮尺に対する、基準から出た縮尺の差(+0.02 なら 2% 大きい)。表記が無ければ ``None``。"""

    @property
    def verdict(self) -> str:
        if not self.off:
            return CHECK_ALL_AGREE
        if len(self.off) == 1:
            return CHECK_ONE_OFF
        return CHECK_SEVERAL_OFF


def cross_check(
    ruler: PageRuler,
    readings: Iterable[object],
    *,
    tolerance: float,
    printed_denominator: float | None = None,
) -> CrossCheck:
    """ほかの寸法(``value_mm``・``start_pt``・``end_pt`` を持つもの)で基準を検算する。

    **既定の許容差は置かない。**判定の許容差(±5%)とは別の、検算のための線を呼ぶ側が渡す。
    """
    if tolerance < 0:
        raise RulerError("許容差は 0 以上である必要があります")
    agreeing = 0
    off: list[OffDimension] = []
    for reading in readings:
        value = float(getattr(reading, "value_mm"))
        measured = ruler.distance_mm(getattr(reading, "start_pt"), getattr(reading, "end_pt"))
        if measured <= 0:
            continue
        deviation = value / measured - 1.0
        if abs(deviation) <= tolerance:
            agreeing += 1
        else:
            off.append(
                OffDimension(
                    text=str(getattr(reading, "text", "")),
                    value_mm=value,
                    measured_mm=measured,
                    deviation=deviation,
                )
            )
    printed = None
    if printed_denominator is not None:
        if printed_denominator <= 0 or not math.isfinite(printed_denominator):
            raise RulerError(f"縮尺の分母が正の有限値ではありません: {printed_denominator}")
        printed = ruler.denominator / printed_denominator - 1.0
    return CrossCheck(
        ruler=ruler,
        tolerance=tolerance,
        agreeing=agreeing,
        off=tuple(off),
        printed_difference=printed,
    )


def agree(first: PageRuler, second: PageRuler, *, tolerance: float) -> bool:
    """2 つの目盛りが許容差の中にあるか。**既定値は置かない。**"""
    if tolerance < 0:
        raise RulerError("許容差は 0 以上である必要があります")
    middle = (first.mm_per_point + second.mm_per_point) / 2
    if middle <= 0:
        return False
    return abs(first.mm_per_point - second.mm_per_point) / middle <= tolerance


def reconcile(
    rulers: Iterable[PageRuler], *, tolerance: float
) -> PageRuler | RulerConflict:
    """複数の出どころを突き合わせる。**食い違えば平均せず、食い違いを返す。**

    揃っていれば、**人の基準の長さ > AI が選んだ基準 > 記入された寸法 > 印字された縮尺**の順に
    1 つを選ぶ。選ぶだけで、値を混ぜない(AI の位置は仮の判断、`docs/provisional_decisions.md` 8 節)。
    """
    items = tuple(rulers)
    if not items:
        raise RulerError("目盛りが 1 つもありません。縮尺を仮定して測らない")
    pages = {ruler.page_index for ruler in items}
    if len(pages) > 1:
        raise RulerError(f"別のページの目盛りは突き合わせません: {sorted(pages)}")
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            if not agree(first, second, tolerance=tolerance):
                return RulerConflict(rulers=items, tolerance=tolerance)
    return min(items, key=lambda ruler: _SOURCES.index(ruler.source))


def group_by_agreement(
    rulers: Sequence[PageRuler], *, tolerance: float
) -> list[list[PageRuler]]:
    """ページをまたいで、**同じ比で足りる組**にまとめる。

    「ページごとに基準の長さが要るのか、同じ縮尺のページで使い回せるのか」を
    数えるための道具。**まとめるだけで、値は作らない。**
    """
    groups: list[list[PageRuler]] = []
    for ruler in sorted(rulers, key=lambda item: item.mm_per_point):
        for group in groups:
            if agree(group[0], ruler, tolerance=tolerance):
                group.append(ruler)
                break
        else:
            groups.append([ruler])
    return groups
