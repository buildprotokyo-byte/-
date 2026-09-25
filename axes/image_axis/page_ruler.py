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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "SOURCE_HUMAN",
    "SOURCE_DIMENSIONS",
    "SOURCE_PRINTED_SCALE",
    "PageRuler",
    "RulerConflict",
    "RulerError",
    "ruler_from_reference_length",
    "ruler_from_page_scale",
    "ruler_from_printed_scale",
    "agree",
    "reconcile",
    "group_by_agreement",
]

#: 出どころの表記。**根拠としてそのまま残す。**
SOURCE_HUMAN = "人の基準の長さ"
SOURCE_DIMENSIONS = "記入された寸法"
SOURCE_PRINTED_SCALE = "印字された縮尺"

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
        if self.source not in (SOURCE_HUMAN, SOURCE_DIMENSIONS, SOURCE_PRINTED_SCALE):
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

    揃っていれば、**人の基準の長さ > 記入された寸法 > 印字された縮尺**の順に
    1 つを選ぶ。選ぶだけで、値を混ぜない。
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
    order = (SOURCE_HUMAN, SOURCE_DIMENSIONS, SOURCE_PRINTED_SCALE)
    return min(items, key=lambda ruler: order.index(ruler.source))


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
