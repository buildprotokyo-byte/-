"""スタートキット: 人が最初に決める前提を、関数の入力として受け取る。

2026-09-22 のおーちゃんの依頼。**画面(クリックの操作)は作らない。**
関数の入力として受け取るところまでである。

何を受け取るか
--------------
1. **基準点** … ページ番号、図面上の2点の座標、その間の実際の長さ(mm)。
   縦・横それぞれ1組まで。
2. **ページの種類** … 平面図/展開図/建具表/仕上表/設備図など、および
   現況/計画/解体の区別。**弱い手がかりとしてだけ扱い、読み取りの範囲は
   決めない**(原則4の条件3)。宣言と読み取りが食い違えば、読みを捨てずに
   食い違いとして人の判断へ回す。
3. **現況と計画の対応** … どのページ同士が同じ範囲を表すか。
4. **目的** … 方向性の自由記述と、資料の指定だけ(原則3-2)。
   **詳細は求めない。** 人に詳細を設定させると自動積算の意味を失う。
   方向性は**弱い手がかりとしてだけ**扱う。
5. **現況の把握の状態** … 正確な現況ではなく、**どの程度把握しているか**の申告だけ
   (原則3-3)。混在のときは範囲ごとに申告する。**未申告は「不明」として扱う。**

扱いのルール(依頼のとおり)
--------------------------
- **前提は必須にしない。** 与えられなければ今までどおり自動で処理する。
  目的も現況の申告も同じで、**未入力でも止めない**(2026-09-22 の位置づけの定め直し。
  最上位の原則は実行時の関門ではない)。
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

#: 開き戸の円弧が**出てくると見込まれる**ページの種類。
#:
#: **これは探索の範囲ではない。** 円弧の探索は、宣言があってもなくても
#: 全ページで行う(原則4の条件3「図面の読み方を縛らない」)。この並びは、
#: 読めた円弧が人の宣言と食い違っているかを判定するためだけに使う。
#: 食い違ったときも読みは捨てず、食い違いとして人の判断へ回す。
#:
#: 2026-09-22 まではこの並びが**探索そのものを止めていた。**
#: 人が「建具表」と宣言するとそのページの開き戸は黙って 0 件になり、
#: 宣言が誤っていても気づく手段が無かった。
DOOR_ARC_EXPECTED_PAGE_KINDS: tuple[PageKind, ...] = ("平面図",)


#: 現況をどの程度把握しているかの選択肢(原則3-3の原文のまま)。
CONDITION_AWARENESS: tuple[str, ...] = (
    "明確に分かる",
    "おおむね分かるが確実ではない",
    "全く分からない、または図面で表現されていない",
    "部分によって混在する",
)

#: 未申告のときに使う値。**既定で「分かっている」に倒さない。**
AWARENESS_UNKNOWN = "全く分からない、または図面で表現されていない"

#: 範囲ごとの申告が要る値。
AWARENESS_MIXED = "部分によって混在する"


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
class SourceDocument:
    """やりたいことが最も濃く書かれている資料の指し示し。

    **中身はここに持たない。** 人が指すのは「どれか」だけで、読むのは AI である
    (原則3-2「やりたいことが最も濃く書かれている資料を選ぶ」)。
    """

    label: str
    """資料の呼び名(基本仕様書、イメージパース、要望書など)。"""

    page_number: int | None = None
    """同じ PDF の中のページなら、その番号(1 始まり)。別の資料なら None。"""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise StartKitError("資料には呼び名を付けてください")
        if self.page_number is not None and self.page_number < 1:
            raise StartKitError("ページ番号は 1 以上にしてください(1 始まり)")


@dataclass(frozen=True)
class Purpose:
    """人が最初に与える目的。**方向性の自由記述と、資料の指定だけ。**

    原則3-2: スタートキットの段階では人も詳細は分からない。**詳細は求めない。**
    詳細な目的は、AI が図面を読んだ後に組み立て、人が確認する(**二段階目はまだ無い**)。
    """

    direction: str = ""
    """方向性の自由記述(例: 戸建ての水回りリフォーム)。"""

    source_documents: tuple[SourceDocument, ...] = ()

    #: この手がかりの強さ。**方向性は弱い手がかりとしてだけ扱う**(原則3-2)。
    #: 定数として持つのは、読む側が強く扱わないことをコードから確かめられるようにするため。
    strength: str = "weak"

    def __post_init__(self) -> None:
        if not self.direction.strip() and not self.source_documents:
            raise StartKitError(
                "目的は、方向性の記述か資料の指定のどちらかが要ります"
                "(与えていないことと、空を与えたことは別なので、"
                "与えないときは目的そのものを渡さないでください)"
            )
        if self.strength != "weak":
            raise StartKitError(
                "方向性は弱い手がかりとしてだけ扱う決まりです(原則3-2)"
            )


@dataclass(frozen=True)
class ConditionRange:
    """現況の把握の状態が、範囲によって違うときの 1 範囲。"""

    description: str
    """どこの範囲か。人が書いた言葉のまま(例: 1階の水回り)。"""

    awareness: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise StartKitError("範囲には、どこのことかを書いてください")
        if self.awareness not in CONDITION_AWARENESS:
            raise StartKitError(f"把握の状態が不正です: {self.awareness}")
        if self.awareness == AWARENESS_MIXED:
            raise StartKitError(
                "範囲の中でまた「混在」とは申告できません"
                "(いつまでも決まらないため、範囲を分けてください)"
            )


@dataclass(frozen=True)
class ConditionSurvey:
    """現況をどの程度把握しているかの申告。**正確な現況は求めない。**

    原則3-3: 人に正確な現況を用意させるルールにしてはならない。
    分からない部分は、AI が図面から推論するか、仮説を置いて進む。
    """

    overall: str
    ranges: tuple[ConditionRange, ...] = ()

    def __post_init__(self) -> None:
        if self.overall not in CONDITION_AWARENESS:
            raise StartKitError(f"把握の状態が不正です: {self.overall}")
        if self.overall == AWARENESS_MIXED and not self.ranges:
            raise StartKitError(
                "「部分によって混在する」と申告したときは、範囲ごとに申告してください"
            )
        if self.overall != AWARENESS_MIXED and self.ranges:
            raise StartKitError(
                "範囲ごとの申告は「部分によって混在する」のときだけです"
            )

    @property
    def awareness_overall(self) -> str:
        return self.overall


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
    #: 目的(方向性の自由記述と資料の指定だけ)。**未入力でも止めない。**
    purpose: Purpose | None = None
    #: 現況の把握の状態の申告。**未申告は「不明」として扱う。**
    condition_survey: ConditionSurvey | None = None
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
            or self.purpose
            or self.condition_survey
        )

    def effective_condition_awareness(self) -> str:
        """現況の把握の状態。**未申告は「不明」として扱う。**

        既定を「明確に分かる」に倒すと、誰も申告していないのに現況が分かって
        いることになり、差分(工事内容)が黙って作られてしまう。
        """
        if self.condition_survey is None:
            return AWARENESS_UNKNOWN
        return self.condition_survey.awareness_overall

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
