"""スタートキット: 人が最初に決める前提を、関数の入力として受け取る。

2026-09-22 のおーちゃんの依頼。**画面(クリックの操作)は作らない。**
関数の入力として受け取るところまでである。

何を受け取るか
--------------
1. **基準点** … ページ番号、図面上の2点の座標、その間の実際の長さ(mm)。
   縦・横それぞれ1組まで。
2. **ページの種類** … 平面図/展開図/建具表/仕上表/設備図など、および
   現況/計画/解体の区別。
3. **現況と計画の対応** … どのページ同士が同じ範囲を表すか。

扱いのルール(依頼のとおり)
--------------------------
- **前提は必須にしない。** 与えられなければ今までどおり自動で処理する。
- 基準点から求めた縮尺は、図面の縮尺表記とは**独立した読み**として扱い、
  突き合わせる。食い違えば判断待ちとして記録する。
- **人の入力も、それだけを根拠に自動確定させない。** 人が入れた値は
  `arbitration/method_policies.py` で未校正として登録してあるので、
  `is_hard_eligible` が False になりハード制約には入らない。

独立性についての但し書き(重要)
------------------------------
基準点から求めた縮尺と、表題欄に印字された縮尺は、**2点の座標を共有している。**
独立なのは「その2点の間が実際に何ミリか」を決める部分だけで、座標の取り違えは
両方に同じように効く。突き合わせで捕まえられるのは縮尺の食い違いであって、
座標の誤りではない。この但し書きは証拠の根拠(`provenance`)にも残す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from axes.image_axis.pdf_vector_symbols import MM_PER_POINT
from intake.case_answers import AREA_BASIS_OPTIONS

#: 基準点の向き。縦・横それぞれ1組まで受ける。
ReferenceAxis = Literal["horizontal", "vertical"]

#: ページの種類。図面の呼び名をそのまま使う。判断できないものは "その他"。
PageKind = Literal["平面図", "展開図", "建具表", "仕上表", "設備図", "その他"]

#: 現況/計画/解体の区別。人が決められなければ "不明" のままにする。
#: **"不明" を「計画」とみなして進めない。**
PagePhase = Literal["現況", "計画", "解体", "不明"]

PAGE_KINDS: tuple[PageKind, ...] = (
    "平面図", "展開図", "建具表", "仕上表", "設備図", "その他",
)
PAGE_PHASES: tuple[PagePhase, ...] = ("現況", "計画", "解体", "不明")

#: 開き戸の円弧を探すページの種類。種類が宣言されていないページは
#: 今までどおり全部探す(前提を必須にしないため)。
DOOR_ARC_PAGE_KINDS: tuple[PageKind, ...] = ("平面図",)


class StartKitError(Exception):
    """人が入れた前提が、そのままでは使えない形だった。

    **黙って直さない。** 座標や長さの取り違えは、直した結果が
    もっともらしい数値になるので、後から気づけない。
    """


@dataclass(frozen=True)
class ReferencePoint:
    """人が図面上で指した2点と、その間の実際の長さ。

    座標は **PDF のページ座標(ポイント)** で、`find_door_arcs()` が返す
    `rect_pt` / `center_pt` と同じ系である。
    """

    page_number: int
    """1 始まり。人が見ているページ番号に合わせる。"""

    axis: ReferenceAxis
    point_a_pt: tuple[float, float]
    point_b_pt: tuple[float, float]
    actual_length_mm: float
    """その2点の間が、実際の建物で何ミリか。"""

    entered_by: str = ""
    """誰が入れたか。人の入力であることを根拠に残すため。"""

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise StartKitError("ページ番号は 1 以上にしてください(1 始まり)")
        if self.axis not in ("horizontal", "vertical"):
            raise StartKitError(f"基準点の向きが不正です: {self.axis}")
        if self.actual_length_mm <= 0:
            raise StartKitError("実際の長さは正の数にしてください")
        if self.paper_distance_pt <= 0:
            raise StartKitError("2点が同じ位置です。離れた2点を指してください")
        dx = abs(self.point_b_pt[0] - self.point_a_pt[0])
        dy = abs(self.point_b_pt[1] - self.point_a_pt[1])
        # 向きの申告と、実際に指した2点の向きが食い違っていたら止める。
        # 縦の寸法を横として入れる取り違えは、縮尺をそのまま間違える。
        if self.axis == "horizontal" and dy > dx:
            raise StartKitError(
                "横として入れられた2点が、縦に長く離れています"
                f"(横 {dx:.1f}pt / 縦 {dy:.1f}pt)"
            )
        if self.axis == "vertical" and dx > dy:
            raise StartKitError(
                "縦として入れられた2点が、横に長く離れています"
                f"(横 {dx:.1f}pt / 縦 {dy:.1f}pt)"
            )

    @property
    def paper_distance_pt(self) -> float:
        """紙の上での2点間の距離(ポイント)。"""
        return math.hypot(
            self.point_b_pt[0] - self.point_a_pt[0],
            self.point_b_pt[1] - self.point_a_pt[1],
        )

    @property
    def derived_denominator(self) -> float:
        """この基準点から求まる縮尺の分母(1/50 なら 50.0)。

        紙の上の距離を実寸に直す比なので、**用紙が拡大縮小して印刷されていても
        正しく出る。** 表題欄の印字はこれを保証しない(A3 の図面を A0 で出すと
        印字は 1/50 のままで、実効の縮尺は違う)。
        """
        return self.actual_length_mm / (self.paper_distance_pt * MM_PER_POINT)

    @property
    def label(self) -> str:
        return "横" if self.axis == "horizontal" else "縦"


@dataclass(frozen=True)
class PageDeclaration:
    """このページが何の図面で、現況・計画・解体のどれかを人が決めたもの。"""

    page_number: int
    kind: PageKind
    phase: PagePhase = "不明"

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise StartKitError("ページ番号は 1 以上にしてください(1 始まり)")
        if self.kind not in PAGE_KINDS:
            raise StartKitError(f"ページの種類が不正です: {self.kind}")
        if self.phase not in PAGE_PHASES:
            raise StartKitError(f"現況/計画/解体の区別が不正です: {self.phase}")


@dataclass(frozen=True)
class PagePairing:
    """現況のページと計画のページが同じ範囲を表すという対応。

    **この対応から差分(撤去・新設)を計算する実装はまだ無い。** 対応を
    受け取って記録するところまでで、数量にはしていない。
    """

    existing_page: int
    planned_page: int

    def __post_init__(self) -> None:
        if self.existing_page < 1 or self.planned_page < 1:
            raise StartKitError("ページ番号は 1 以上にしてください(1 始まり)")
        if self.existing_page == self.planned_page:
            raise StartKitError(
                f"同じページ({self.existing_page})を現況と計画の対応にはできません"
            )


@dataclass(frozen=True)
class StartKit:
    """人が最初に決める前提のひとまとめ。**すべて任意。**"""

    reference_points: tuple[ReferencePoint, ...] = ()
    page_declarations: tuple[PageDeclaration, ...] = ()
    page_pairings: tuple[PagePairing, ...] = ()
    #: 専有延床面積と施工床面積のどちらを使うか。`intake/case_answers.py` の
    #: 質問に人がここで答えられるようにしたもの。
    area_basis: str | None = None
    entered_by: str = ""

    def __post_init__(self) -> None:
        seen: set[tuple[int, str]] = set()
        for point in self.reference_points:
            key = (point.page_number, point.axis)
            if key in seen:
                # 「縦・横それぞれ1組まで」。2組あるとどちらを使うかを
                # 黙って決めることになる。
                raise StartKitError(
                    f"ページ {point.page_number} の{point.label}の基準点が2組あります"
                )
            seen.add(key)

        declared: set[int] = set()
        for declaration in self.page_declarations:
            if declaration.page_number in declared:
                raise StartKitError(
                    f"ページ {declaration.page_number} の種類が2つ宣言されています"
                )
            declared.add(declaration.page_number)

        if self.area_basis is not None and self.area_basis not in AREA_BASIS_OPTIONS:
            raise StartKitError(
                f"面積の選択 {self.area_basis!r} は選択肢 {list(AREA_BASIS_OPTIONS)} にありません"
            )

    @property
    def is_empty(self) -> bool:
        """前提が1つも与えられていないか。空なら今までどおりに処理する。"""
        return not (
            self.reference_points
            or self.page_declarations
            or self.page_pairings
            or self.area_basis
        )

    def reference_points_for(self, page_number: int) -> tuple[ReferencePoint, ...]:
        return tuple(
            point for point in self.reference_points if point.page_number == page_number
        )

    def declaration_for(self, page_number: int) -> PageDeclaration | None:
        for declaration in self.page_declarations:
            if declaration.page_number == page_number:
                return declaration
        return None

    def validate_against(self, page_count: int) -> None:
        """宣言されたページ番号が、その PDF に実在するかを確かめる。

        存在しないページ番号を黙って読み飛ばすと、人が入れた前提が
        **1つも効いていないのに効いたように見える。**
        """
        def check(page_number: int, what: str) -> None:
            if page_number > page_count:
                raise StartKitError(
                    f"{what}のページ {page_number} は存在しません(全 {page_count} ページ)"
                )

        for point in self.reference_points:
            check(point.page_number, "基準点")
        for declaration in self.page_declarations:
            check(declaration.page_number, "ページの種類")
        for pairing in self.page_pairings:
            check(pairing.existing_page, "現況と計画の対応(現況側)")
            check(pairing.planned_page, "現況と計画の対応(計画側)")
