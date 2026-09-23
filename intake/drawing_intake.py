"""本番の入口: 図面 PDF のパスを受け取り、読めた数量を仲裁層まで流す。

2026-09-22 時点で、このリポジトリの PDF を読む部品
(`axes/image_axis/pdf_pages.py`、`axes/image_axis/pdf_vector_symbols.py`)は
**評価スクリプトとテストからしか呼ばれていなかった。** `app.py` は空で、
実図面の情報が `arbitration/inference_orchestrator.py` まで届く経路が
どこにも無かった。このモジュールはその1本目の経路である。

**最初の版では新しい認識技術を足していない。** 既にある部品を1本に
つないだだけだった。2026-09-22 に、そこへ**建具表と内装仕上表を表として
読む経路**を足した(`axes/image_axis/schedule_tables.py`、v8 15章)。
引戸・折戸は円弧を描かないので図形からは原理的に拾えないが、**建具表の
種別の文字が読めれば拾える。** 内装仕上表のほうは室名と仕上げの対応を
返すだけで、**数量にはならない**(室の輪郭を取る実装がまだ無い)。

この入口が守っていること
------------------------
1. **ラスターのページを黙って捨てない。** 既定では、ベクター(CAD 由来)の
   ページだけを抽出に回し、スキャンされたページは「未対応」として
   `PageOutcome` に残す。捨ててしまうと、34 ページのうち何ページが
   読めなかったのかがどこにも残らない。
   **2026-09-22 に、スキャンのページを OCR で読む道を足した**
   (`IntakeConfig.ocr`、`axes/image_axis/ocr_text.py`、v8 17章)。
   **渡さなければ今までどおり「未対応」のままで、振る舞いは変わらない。**
   渡したときは、図面に文字として書かれている面積の記載を証拠として出す。
   ただし OCR は文字を別の字に化けさせ、**その化けを確信度が知らせない**
   (`docs/ocr_scanned_pages_report.md`)ので、埋め込み文字とは別の手法
   (``ocr_text_area`` / ``ocr_text_scale``)として未校正で登録してある。
2. **縮尺が読めないページで長さを出さない。** `extract_scale()` が None を
   返したページでは、開き戸の抽出そのものを行わない(扉幅の実寸が出せない)。
3. **ページをまたいで足し算しない。** 開き戸の件数はページごとの対象として
   別々に出す。同じ案件の図面には既存平面図と新設平面図が両方あり、
   足すと同じ建具を二重に数える。どのページが既存でどれが新設かは
   図面の文字からは決まらないので、ここでは判断しない。建具表の数量も
   同じで、同じ建具番号が別のページで違う数量ならレンジにする。
3-2. **建具表の数量と開き戸の検出数を、ここで突き合わせない。**
   `建具数量::<建具番号>` と `開き戸::ページN` は別々の対象として出す。
   数が合うかどうかの判定は仲裁層の仕事である。
4. **面積の選択を勝手に決めない。** 専有延床面積と施工床面積のどちらを
   使うかは案件ごとに人へ1回だけ聞き(`intake/case_answers.py`)、
   回答が無い間はその数量を出さない。
5. **根拠を落とさない。** ページ番号・座標・元の文字列を
   `AxisEvidence.evidence["provenance"]` に載せて仲裁層へ渡す。
6. **表から読んだ寸法の単位を勝手に決めない。** 建具表の `1650` は
   まず mm だが、単位が表に書かれていなければ mm に直さない
   (`schedule_tables` 冒頭)。

この入口を通しても確定はしない(2026-09-22 時点)
------------------------------------------------
階層1(自動確定)は**独立した強いデータ源が2つ以上**要る
(`arbitration/axis_quality_firewall.py`)。図面 PDF は1ファイルで1つの
データ源なので、そこから何種類の手法で読んでも独立数は 1 にしかならない。
加えて、この経路の2手法はどちらも実測校正を通っていないため
(`arbitration/method_policies.py`)、そもそもハード制約に入らない。
**結果として、この入口だけでは全項目が階層3(人の確認)になる。**
これは実装の不具合ではなく、実図面で確定できた数量が0件だという
既知の事実(`docs/real_drawing_eval_report.md`)と同じことを、
本番の経路の上で再現している。

Web 画面はまだ作らない。`app.py` は空のままである。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from arbitration.axis_quality_firewall import CENTER_TOLERANCES
from arbitration.inference_orchestrator import InferenceOrchestrator, OrchestrationResult
from arbitration.method_policies import (
    DEFAULT_METHOD_POLICIES,
    METHOD_HUMAN_REFERENCE_POINT,
)
from arbitration.provisional_audit import (
    ACTION_TIERS,
    AUDITABLE_TIERS,
    AuditCandidate,
    TierAuditPolicy,
    TieredAuditPlan,
    plan_tiered_audit,
)
from axes.image_axis.ocr_readings import (
    METHOD_OCR_TEXT_AREA,
    METHOD_OCR_TEXT_SCALE,
    read_area_labels,
    read_scale,
)
from axes.image_axis.ocr_text import (
    DEFAULT_MIN_CONFIDENCE,
    OCR_DPI,
    OcrBackend,
    OcrPage,
    recognize_page,
)
from axes.image_axis.pdf_pages import ContentKind, rasterize
from axes.image_axis.pdf_vector_symbols import (
    METHOD_DOOR_ARC,
    METHOD_TEXT_AREA,
    METHOD_TEXT_SCALE,
    MM_PER_POINT,
    DrawingScale,
    extract_scale,
    find_area_labels,
    find_door_arcs,
)
from axes.image_axis.schedule_tables import (
    METHOD_DOOR_SCHEDULE,
    DoorScheduleRow,
    FinishScheduleRow,
    is_arc_blind,
    read_door_schedules,
    read_finish_schedules,
)
from intake.case_answers import (
    AREA_BASIS_DEPENDENT_ITEMS,
    AREA_BASIS_OPTIONS,
    QUESTION_AREA_BASIS,
    Answer,
    AnswerStore,
    PendingQuestion,
)
from axes.reading.meaning import PHASE_UNKNOWN, PURPOSE_UNESTABLISHED, Meaning
from intake.start_kit import (
    DOOR_ARC_EXPECTED_PAGE_KINDS,
    PageDeclaration,
    PagePairing,
    ReferencePoint,
    StartKit,
    ConditionSurvey,
    Purpose,
)

#: この入口が属する軸。図面の図形と印字を読むので画像軸。
AXIS_ID = "image"

#: 面積の単位表記。`arbitration/units.py` が cm2 の整数へ正規化する。
AREA_UNIT = "㎡"

#: 建具の件数の単位表記。同じく `count` へ正規化される。
COUNT_UNIT = "箇所"

#: 人の回答を待って初めて出す対象。図面の2つの記載のどちらを使うかが
#: 決まらない限り、この数量は出さない。
TARGET_WORK_FLOOR_AREA = "施工対象床面積"

#: 長さの単位表記。`arbitration/units.py` が mm の整数へ正規化する。
LENGTH_UNIT = "mm"

#: 人が宣言したページの種類と、実際に読めた中身が食い違ったときの
#: `PendingDecision.kind`。
#:
#: **食い違っても読み取りは止めない。** 止めるかわりに、読みを「仮説に
#: 基づく」側へ落として(由来を ``assumed`` にして)自動確定から外し、
#: この記録で人の判断へ回す(原則4の条件3・原則5)。
PAGE_KIND_DISAGREEMENT = "page_kind_disagreement"

#: 建具表から読んだ数量の対象名の頭。建具番号ごとに 1 つの対象にする。
#:
#: **開き戸の円弧から数えた `開き戸::ページN` とは別の対象にしてある。**
#: 同じページに建具表と平面図があっても、数が合うかどうかをここで判定しない。
TARGET_DOOR_QUANTITY_PREFIX = "建具数量::"

#: 本番で抜き取り検査を走らせる割合。**階層ごとに分けて数える。**
#:
#: 階層2の 30%・最小5件は既存の値で、変えていない。
#:
#: **階層1の 10%・最小3件には実測の裏づけが無い。** 根拠にしたのは
#: 「階層1は人が一度も見ないまま通る階層なので、完全に無検査にはしない」の
#: 1点だけである。階層1は独立した強い軸2つの一致と中心値の検査を通っている
#: ので誤りの事前確率は階層2より低い**はず**だが、見逃したときの重さは
#: 階層2より大きい(階層2は抜き取られれば人が見る)。**どちらの効きが
#: 大きいかは測っていない。** おーちゃんの承認のもと、**後で測って調整する
#: 前提の初期値**として入れている(v8 10章28項、
#: `docs/audit_production_integration_design.md` 2-3節・7節)。
#:
#: **この既定を部品側(`arbitration/provisional_audit.DEFAULT_TIER_POLICIES`)
#: に置かないのは意図的である。** 値が決まっていない段階で他の呼び出しにも
#: 効いてしまうため、本番の入口だけが名乗る。
PRODUCTION_AUDIT_POLICIES: Mapping[int, TierAuditPolicy] = MappingProxyType({
    1: TierAuditPolicy(sampling_rate=0.10, minimum_sample=3),
    2: TierAuditPolicy(sampling_rate=0.30, minimum_sample=5),
})

#: 縮尺の読みが一致しているとみなす許容差。
#:
#: **新しい数値を作らない。** ファイアウォールが長さの中心値に使っている
#: `CENTER_TOLERANCES["mm"]`(暫定の ±5%)をそのまま借りる。2箇所に別の値を
#: 置くと、v8 10章12項で許容誤差を本決めしたときに片方だけ残る。
#: 呼び出し側は `read_drawing(..., scale_tolerance=...)` で差し替えられる。
SCALE_AGREEMENT_TOLERANCE: Fraction = CENTER_TOLERANCES["mm"].relative or Fraction(1, 20)

#: ラスター化の解像度。**この経路は画像処理をしない**(ベクター図形と
#: 埋め込み文字しか使わない)ので、低くてよい。既定を 200dpi のままにすると
#: A0 相当の図面 34 ページで数 GB の配列を作ることになる。
#: ページの種別判定・用紙寸法・埋め込み文字は dpi に依存しない。
CLASSIFY_DPI = 72

#: ページをどう扱ったか。``processed_ocr`` は**スキャンのページを OCR で
#: 読んだ**状態で、ベクターのページの ``processed`` と分けてある。
#: 読めた中身の確かさが違うので、集計のときに同じ数に混ぜない。
PageStatus = Literal[
    "processed", "processed_ocr", "unsupported_raster", "unsupported_empty"
]


class IntakeError(Exception):
    """入口の設定が受け付けられなかった。"""


@dataclass(frozen=True)
class OcrSettings:
    """スキャンのページを読むときの設定。**渡さなければ OCR は動かない。**

    `backends` に 2 つ以上のエンジンを渡すと、**両方が同じに読んだ語だけ**が
    通る(`axes/image_axis/ocr_text.recognize_page`)。実測では、中国語・英語の
    モデルが数字に強くて日本語の語を化けさせ、日本語のモデルはその逆だった
    (`docs/ocr_scanned_pages_report.md`)。1 つだけ渡すこともできるが、
    その読みは突き合わせを通っていないことが証拠に残る。
    """

    backends: tuple[OcrBackend, ...] = ()
    dpi: int = OCR_DPI
    min_confidence: float = DEFAULT_MIN_CONFIDENCE

    def __post_init__(self) -> None:
        if not self.backends:
            raise IntakeError(
                "OcrSettings にエンジンが 1 つも入っていません。"
                "OCR を使わないなら IntakeConfig.ocr を None のままにしてください"
            )


@dataclass(frozen=True)
class IntakeConfig:
    """入口の設定。**図面のパスはここからしか入らない。**

    実図面・見積明細・そこから作った正解ファイルはリポジトリに置かない
    決まりなので、パスは常に外から渡す。既定値は持たせない。
    """

    pdf_path: Path
    case_id: str
    #: 人の回答を保存する JSON のパス(リポジトリの外)。None なら保存しない。
    answers_path: Path | None = None
    #: 処理するページ(0 始まり)。None なら全ページ。
    pages: range | None = None
    dpi: int = CLASSIFY_DPI
    #: 人が最初に決める前提(`intake/start_kit.py`)。**任意。**
    #: 与えられなければ、今までどおり自動で処理する。
    start_kit: StartKit | None = None
    #: スキャン(ラスター)のページを OCR で読むための設定。**任意。**
    #: None なら、ラスターのページは今までどおり「未対応」として記録される。
    ocr: OcrSettings | None = None
    #: 抜き取り検査の記録を書き出す JSON のパス(リポジトリの外)。
    #: **None なら書かない。既定値は持たせない**(案件の情報なので、
    #: `intake/case_answers.AnswerStore` と同じ約束)。
    audit_log_path: Path | None = None
    #: 階層ごとの抜き取り率。既定は `PRODUCTION_AUDIT_POLICIES`。
    audit_policies: Mapping[int, TierAuditPolicy] | None = None

    def __post_init__(self) -> None:
        if not self.case_id:
            raise IntakeError("case_id は空にできません")
        if self.dpi <= 0:
            raise IntakeError("dpi は正の整数である必要があります")


@dataclass(frozen=True)
class ScaleReading:
    """1 ページについて得られた縮尺の読み 1 件と、その出どころ。"""

    denominator: float
    """1/50 なら 50.0。"""

    origin: str
    """``表題欄の印字`` / ``人が入れた基準点(横)`` など。"""

    detail: str
    """根拠。印字なら読んだ文字列、基準点なら 2 点と実際の長さ。"""

    is_human_input: bool = False


@dataclass(frozen=True)
class PendingDecision:
    """人の判断を待つことになった食い違い。**黙って片方を採らない。**"""

    kind: str
    """``scale_disagreement`` / ``area_basis_conflict`` / ``page_kind_disagreement``。"""

    detail: str
    observed: tuple[tuple[str, float], ...] = ()
    page_number: int | None = None


@dataclass(frozen=True)
class PageOutcome:
    """1 ページをどう扱ったか。**読めなかったページもここに残る。**"""

    page_number: int
    """1 始まり。人に見せる番号に合わせる。"""

    content_kind: ContentKind
    status: PageStatus
    scale: DrawingScale | None = None
    """このページで使うことにした縮尺。**食い違ったときは None。**"""

    #: このページで得られた縮尺の読みを全部。突き合わせの材料として残す。
    scale_readings: tuple[ScaleReading, ...] = ()
    #: 人が宣言したページの種類と現況/計画/解体の区別(宣言が無ければ None)。
    declaration: PageDeclaration | None = None
    #: 起きたことの記録(縮尺が読めない、ラスターの中身は読んでいない、など)。
    notes: tuple[str, ...] = ()

    @property
    def processed(self) -> bool:
        return self.status == "processed"


@dataclass(frozen=True)
class DrawingFinding:
    """図面から読めた数量 1 件と、その根拠。"""

    target: str
    value_range: tuple[float, float]
    """宣言した単位のままの値。1 点で読めた値は下限=上限になる。"""

    unit: str
    method_id: str
    #: この読みがどのデータ源から来たか。``"drawing"`` は図面 PDF、
    #: ``"start_kit"`` は人が入れた前提。**独立性の判定が変わるので、
    #: 同じ source にまとめない。**
    source_kind: Literal["drawing", "start_kit"] = "drawing"
    axis_id: str = AXIS_ID
    strength: str = "strong"
    """名乗る強度。実際に効くのは登録簿の上限まで引き下げた後の値。"""

    derivation: str = "read"
    derivation_basis: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    """ページ番号・座標・元の文字列。**仲裁層まで一緒に運ぶ。**"""

    meaning: Meaning | None = None
    """この値の意味の 4 欄(`axes/reading/meaning.py`)。

    **新しく書いた経路(OCR)では必ず入る**(おーちゃんの回答9、2026-09-22)。
    既存の経路(埋め込み文字・建具表・開き戸)はまだ None で、修正のついでに
    順に入れていく。**None は「意味が無い」ではなく「まだ付けていない」。**
    """


@dataclass(frozen=True)
class TargetDecision:
    """1 対象に対する仲裁層の判定(人が読む形に畳んだもの)。"""

    target: str
    trace_id: str
    tier: int
    action: str
    confirmed_range: tuple[int, int] | None
    """**正規形単位の整数**(面積なら cm²)。確定していなければ None。"""

    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...]
    is_invalid: bool

    @property
    def confirmed(self) -> bool:
        return self.action == "auto_confirm" and self.confirmed_range is not None


@dataclass(frozen=True)
class IntakeResult:
    """入口の出力。**確定したものと、しなかった理由の両方が入る。**"""

    case_id: str
    source_id: str
    source_fingerprint: str
    pages: tuple[PageOutcome, ...]
    findings: tuple[DrawingFinding, ...]
    decisions: tuple[TargetDecision, ...]
    pending_questions: tuple[PendingQuestion, ...]
    #: 読みが食い違って人の判断を待つことになったもの。
    pending_decisions: tuple[PendingDecision, ...] = ()
    #: 人が入れた現況と計画の対応。**差分の計算はまだしていない。**
    page_pairings: tuple[PagePairing, ...] = ()

    #: スキャンのページを OCR に掛けた結果。**読めなかったもの・エンジンどうしで
    #: 食い違ったものもここに残る。** OCR を渡していなければ空。
    ocr_pages: tuple[OcrPage, ...] = ()
    #: 人が与えた目的(方向性と資料の指定だけ)。与えられなければ None。
    #:
    #: **弱い手がかりとしてだけ扱う。** 図面の中身との突き合わせはまだ無い
    #: (食い違いを人への確認事項として出す経路は未実装)。
    purpose: Purpose | None = None

    #: 現況の把握の状態の申告。**未申告なら None で、「不明」として扱う。**
    condition_survey: ConditionSurvey | None = None

    #: 前提を入れた人。前提を案件の前提に直すときに使う。
    start_kit_entered_by: str = ""

    door_schedule_rows: tuple[DoorScheduleRow, ...] = ()
    """建具表から読んだ行。**数量が読めなかった行もここには残る。**"""

    audit_plan: TieredAuditPlan | None = None
    """抜き取り検査で抜いた対象。**照合はしていないので的中率は無い。**

    正解データの記入者が決まるまで、この一覧は「人が確かめるべき対象」で
    ある(`docs/audit_production_integration_design.md` 3-3節)。
    """

    finish_schedule_rows: tuple[FinishScheduleRow, ...] = ()
    """内装仕上表から読んだ「室名・部位・仕上」の対応。

    **これは数量ではない。** 室の輪郭を取る実装がこのリポジトリに無いので、
    仕上げから面積は出せない(`docs/real_drawing_eval_report.md`)。輪郭が
    取れるようになったとき、その面積が何の仕上げの数量なのかを決めるための
    材料として残している。
    """

    def arc_blind_doors(self) -> tuple[str, ...]:
        """**円弧では拾えない種別**として建具表に書かれていた建具番号。

        引戸・折戸は円弧を描かないので `find_door_arcs()` の範囲外である。
        ここに出る建具は、図形からは 1 件も拾えていない建具である。
        種別が読めなかった建具は**入れない**(「拾えている」とも
        「拾えていない」とも言えないため)。
        """
        marks: list[str] = []
        for row in self.door_schedule_rows:
            if is_arc_blind(row.kind) and row.mark not in marks:
                marks.append(row.mark)
        return tuple(marks)

    @property
    def confirmed_targets(self) -> tuple[str, ...]:
        """自動で確定した対象。**2026-09-22 時点では常に空になる。**"""
        return tuple(item.target for item in self.decisions if item.confirmed)

    @property
    def unsupported_pages(self) -> tuple[int, ...]:
        """未対応として記録したページ番号(1 始まり)。"""
        return tuple(page.page_number for page in self.pages if not page.processed)

    def audit_line(self) -> str:
        """抜き取り検査の 1 行。

        **母集団が0件のときは「監査対象なし」と書く。**「0件監査、全件一致」
        とは書かない。0件の状態と「監査して全部当たった」状態は、まったく
        違う(`arbitration/provisional_audit.py` の 2 番目の穴)。
        """
        if self.audit_plan is None:
            return "抜き取り検査: 行っていない"
        # **引き継ぎの約束**: 母集団が 0 でなくなり、最初の 1 件を検査したら
        # その結果を報告する(`arbitration/provisional_audit.
        # FIRST_AUDIT_MUST_BE_REPORTED`)。
        parts = []
        for tier in self.audit_plan.audited_tiers:
            plan = self.audit_plan.plans[tier]
            parts.append(
                f"階層{tier} 母集団{plan.population_size}件"
                f"→{plan.sample_size}件抽出"
            )
        tail = (
            "(照合待ち: 正解が無いので的中率は出せない)"
            if self.audit_plan.has_population
            else "(監査対象なし)"
        )
        return "抜き取り検査: " + " / ".join(parts) + tail

    def summary(self) -> str:
        """人が読む要約。報告にそのまま貼れる形にする。"""
        lines = [
            f"案件: {self.case_id}",
            f"図面の指紋(sha256): {self.source_fingerprint}",
            f"ページ: 全 {len(self.pages)} / 抽出に回した {sum(1 for p in self.pages if p.processed)}"
            f" / 未対応 {len(self.unsupported_pages)}",
            f"読めた数量: {len(self.findings)} 件",
            f"自動確定: {len(self.confirmed_targets)} 件",
            f"人への質問: {len(self.pending_questions)} 件",
            f"判断待ち: {len(self.pending_decisions)} 件",
            f"建具表から読んだ行: {len(self.door_schedule_rows)} 件"
            f"(うち円弧では拾えない種別 {len(self.arc_blind_doors())} 件)",
            f"内装仕上表から読んだ対応: {len(self.finish_schedule_rows)} 件"
            "(**数量ではない**)",
            self.audit_line(),
        ]
        for decision in self.decisions:
            lines.append(
                f"  - {decision.target}: 階層{decision.tier} / {decision.action}"
            )
        for question in self.pending_questions:
            lines.append(f"  - 未回答: {question.question}")
        for pending in self.pending_decisions:
            lines.append(f"  - 判断待ち: {pending.detail}")
        return "\n".join(lines)


def file_fingerprint(path: Path | str) -> str:
    """ファイルの内容そのものの sha256。

    **ファイル名やパスは混ぜない。** 名前は匿名化の過程で変わるうえ、
    案件名が入っていることがある。指紋は独立性の判定に使うので、
    同じ中身が同じ値になることのほうが大事である。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def start_kit_fingerprint(start_kit: StartKit) -> str:
    """人が入れた前提そのものの sha256。

    図面とは**別のデータ源**なので、独立性の判定に使える指紋を別に持つ。
    前提を書き換えれば指紋も変わるので、「どの前提で出した数量か」が後から辿れる。
    """
    payload = json.dumps(
        {
            "reference_points": [
                {
                    "page_number": point.page_number,
                    "axis": point.axis,
                    "point_a_pt": list(point.point_a_pt),
                    "point_b_pt": list(point.point_b_pt),
                    "actual_length_mm": point.actual_length_mm,
                    "entered_by": point.entered_by,
                }
                for point in start_kit.reference_points
            ],
            "page_declarations": [
                {
                    "page_number": item.page_number,
                    "kind": item.kind,
                    "phase": item.phase,
                }
                for item in start_kit.page_declarations
            ],
            "page_pairings": [
                {"existing_page": item.existing_page, "planned_page": item.planned_page}
                for item in start_kit.page_pairings
            ],
            "area_basis": start_kit.area_basis,
            # **新しい前提を足したらここにも足すこと。** ここに載せ忘れると、
            # 中身の違う前提が同じ指紋になり、「どの前提で出した数量か」が
            # 辿れなくなる(tests/test_start_kit_purpose_condition.py が見張る)。
            "purpose": (
                None
                if start_kit.purpose is None
                else {
                    "direction": start_kit.purpose.direction,
                    "source_documents": [
                        {"label": d.label, "page_number": d.page_number}
                        for d in start_kit.purpose.source_documents
                    ],
                }
            ),
            "condition_survey": (
                None
                if start_kit.condition_survey is None
                else {
                    "overall": start_kit.condition_survey.overall,
                    "ranges": [
                        {"description": r.description, "awareness": r.awareness}
                        for r in start_kit.condition_survey.ranges
                    ],
                }
            ),
            "entered_by": start_kit.entered_by,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_drawing(
    config: IntakeConfig,
    *,
    answers: AnswerStore | None = None,
    orchestrator: InferenceOrchestrator | None = None,
    scale_tolerance: Fraction = SCALE_AGREEMENT_TOLERANCE,
) -> IntakeResult:
    """図面 PDF を読み、読めた数量を仲裁層に通して結果を返す。

    引数に取るのは**設定だけ**で、図面のパスは `config` からしか入らない。
    正解データ(見積明細・ゴールデン)はここでは一切読まない。

    `config.start_kit` に人が決めた前提が入っていれば使う。**無くても動く。**
    """
    pdf_path = Path(config.pdf_path)
    if not pdf_path.exists():
        raise IntakeError(f"図面 PDF が見つかりません: {pdf_path}")

    start_kit = config.start_kit if config.start_kit is not None else StartKit()
    page_count = _page_count(pdf_path)
    # 存在しないページ番号への前提は、黙って読み飛ばさずに止める。
    start_kit.validate_against(page_count)

    store = answers if answers is not None else AnswerStore(config.answers_path)
    fingerprint = file_fingerprint(pdf_path)
    # source_id は案件の中で一意であればよい。**ファイル名は入れない。**
    source_id = f"{config.case_id}::drawing"
    human_source_id = f"{config.case_id}::start_kit"
    human_fingerprint = start_kit_fingerprint(start_kit)

    pages, findings, pending_decisions, door_rows, finish_rows, ocr_pages = _extract(
        pdf_path, config, start_kit, page_count, scale_tolerance
    )
    findings, pending_questions, area_pending = _apply_area_basis(
        findings, case_id=config.case_id, store=store, start_kit=start_kit
    )
    pending_decisions.extend(area_pending)

    engine = orchestrator if orchestrator is not None else InferenceOrchestrator(
        method_policies=dict(DEFAULT_METHOD_POLICIES),
        source_registry={
            source_id: fingerprint,
            human_source_id: human_fingerprint,
        },
    )
    sources = {
        "drawing": (source_id, fingerprint),
        "start_kit": (human_source_id, human_fingerprint),
    }
    groups = _group_by_target(findings)
    decisions = tuple(
        _decide(engine, group, case_id=config.case_id, sources=sources)
        for group in groups
    )

    # **抽出だけを行う。正解には触らない。** 照合は `score_audit_plan()` が
    # 別に行う(`docs/audit_production_integration_design.md` 3-1節)。
    audit_plan = plan_tiered_audit(
        _audit_candidates(decisions, groups),
        policies=(
            config.audit_policies
            if config.audit_policies is not None
            else PRODUCTION_AUDIT_POLICIES
        ),
        seed=audit_seed(config.case_id, fingerprint),
        case_id=config.case_id,
        source_fingerprint=fingerprint,
        start_kit_fingerprint=human_fingerprint,
    )
    if config.audit_log_path is not None:
        append_audit_record(config.audit_log_path, audit_plan)

    return IntakeResult(
        case_id=config.case_id,
        source_id=source_id,
        source_fingerprint=fingerprint,
        pages=pages,
        findings=tuple(findings),
        decisions=decisions,
        pending_questions=tuple(pending_questions),
        pending_decisions=tuple(pending_decisions),
        page_pairings=tuple(start_kit.page_pairings),
        purpose=start_kit.purpose,
        condition_survey=start_kit.condition_survey,
        start_kit_entered_by=start_kit.entered_by,
        door_schedule_rows=tuple(door_rows),
        finish_schedule_rows=tuple(finish_rows),
        ocr_pages=tuple(ocr_pages),
        audit_plan=audit_plan,
    )


def audit_seed(case_id: str, source_fingerprint: str) -> int:
    """抜き取りの乱数シードを、案件と図面の中身から決める。

    **時刻も実行回数も混ぜない。** 同じ図面をもう一度読んだときに、人に
    見せる一覧が入れ替わってはいけない。人が 3 件を確かめている最中に
    再実行して別の 3 件が出てきたら、確かめた分が捨てられる。

    母集団そのものが変われば抽出は変わる(新しい建具が読めるようになった、
    など)。これは避けられないので、記録に母集団の件数を残して、変わった
    ことが分かるようにしている。
    """
    digest = hashlib.sha256(f"{case_id}::{source_fingerprint}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _audit_candidates(
    decisions: Sequence[TargetDecision],
    groups: Sequence[Sequence[DrawingFinding]],
) -> list[AuditCandidate]:
    """判定と読みから、抜き取り検査の母集団を組み立てる。

    **階層1(自動確定)と階層2(仮採用)だけを入れる。** 階層3は人が必ず
    見るので抜き取る意味が無く、混ぜると的中率が薄まる。

    `arbitration.provisional_audit.collect_audit_population()` を使わずに
    ここで組み立てているのは、あちらが `FirewallDecision` と `AxisEvidence`
    の形を前提にしているためである。`TargetDecision` と `DrawingFinding` は
    たまたま同じ属性名を持つが、**たまたま通ることに頼らない。**

    **2026-09-22 時点では、この関数は常に空を返す。** 図面 PDF は 1 つの
    データ源なので独立した強い軸が 2 つ揃わず、全対象が階層3になる。
    これは不具合ではなく、母集団0件が正しい出力である。
    """
    candidates: list[AuditCandidate] = []
    for decision, group in zip(decisions, groups):
        tier = ACTION_TIERS.get(decision.action)
        if tier is None or tier not in AUDITABLE_TIERS:
            continue
        if decision.tier != tier:
            # 階層番号と action の食い違いを黙って受け入れると、集計が
            # 実際とは違う階層に入る。
            raise IntakeError(
                f"'{decision.target}' は action={decision.action!r}(階層{tier})"
                f"なのに tier={decision.tier} と申告されています"
            )
        if decision.confirmed_range is None:
            raise IntakeError(
                f"'{decision.target}' は階層{tier}ですが確定範囲がありません。"
                "自動採用した範囲が無ければ検査のしようがありません"
            )
        first = group[0]
        provenance = dict(first.provenance)
        provenance.setdefault("reading_count", len(group))
        # **どのページを見ればいいのかを、必ず 1 つの欄で答えられるようにする。**
        # 読みの種類によって根拠の形が違い(面積は `occurrences` の中、開き戸は
        # 直下)、人に渡す一覧でそこを探させることになるため。
        provenance["page_numbers"] = _pages_in_provenance(group)
        candidates.append(AuditCandidate(
            target=decision.target,
            adopted_range=decision.confirmed_range,
            unit=first.unit,
            # 拡大監査の単位。系統誤差は手法から出るので手法 ID を使う。
            category=first.method_id or first.axis_id or decision.target,
            axis_id=first.axis_id,
            method_id=first.method_id,
            tier=tier,
            provenance=provenance,
        ))
    return candidates


def _pages_in_provenance(
    findings: Sequence[DrawingFinding],
) -> list[int]:
    """読みの根拠に出てくるページ番号を集めて並べる。

    根拠の形は読みの種類ごとに違う(面積は ``occurrences`` の中に、開き戸は
    直下に ``page_number`` を持つ)。**形の違いを人に探させない。**
    """
    pages: set[int] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            number = value.get("page_number")
            if isinstance(number, int):
                pages.add(number)
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)

    for finding in findings:
        _walk(finding.provenance)
    return sorted(pages)


def append_audit_record(path: Path | str, plan: TieredAuditPlan) -> None:
    """抜き取り検査の記録を JSON に追記する。

    **案件の情報なのでリポジトリには置かない。** 書き出す先は呼び出し側が
    渡したパスだけで、既定値を持たない(`intake/case_answers.AnswerStore`
    と同じ約束)。

    **母集団0件の回も残す。** 残さないと「検査していない」と区別がつかない。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise IntakeError(
                f"抜き取り検査の記録が読めません: {target} ({error})"
            ) from error
        if not isinstance(loaded, list):
            raise IntakeError(
                f"抜き取り検査の記録は配列である必要があります: {target}"
            )
        records = loaded
    records.append(plan.as_log_dict())
    target.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _group_by_target(
    findings: Sequence[DrawingFinding],
) -> list[list[DrawingFinding]]:
    """同じ対象の読みをひとまとめにする。

    **1 回の判定に渡せるのは同じ対象の証拠だけ**で、混ぜると
    `AxisQualityFirewall.assess()` が例外を投げる。逆に、同じ対象の読みを
    別々の要求に分けてしまうと、独立した読み同士の突き合わせが起きない。
    """
    grouped: dict[str, list[DrawingFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.target, []).append(finding)
    return list(grouped.values())


# ---------------------------------------------------------------------------
# 1. ページを分けて、ベクターのページだけを次へ回す
# ---------------------------------------------------------------------------


def _extract(
    pdf_path: Path,
    config: IntakeConfig,
    start_kit: StartKit,
    page_count: int,
    scale_tolerance: Fraction,
) -> tuple[
    tuple[PageOutcome, ...],
    list[DrawingFinding],
    list[PendingDecision],
    list[DoorScheduleRow],
    list[FinishScheduleRow],
    list[OcrPage],
]:
    outcomes: list[PageOutcome] = []
    #: ラベルごとに、読めた値とその根拠を集める。ページをまたいで同じ記載が
    #: あるのは普通なので、ここでまとめる。**別のデータ源として数えない。**
    #:
    #: 鍵に手法IDを入れてあるのは、**埋め込み文字から読んだ面積と、スキャンを
    #: OCR で読んだ面積を混ぜないため。** 同じ対象(``専有延床面積``)の
    #: 別々の読みとして仲裁層へ渡り、そこで突き合わせられる。
    area_hits: dict[
        tuple[str, str], list[tuple[float, dict[str, Any], Meaning | None]]
    ] = {}
    #: 建具番号ごとに、読めた数量とその根拠。同じ建具番号が複数ページに
    #: 出てくることがある(建具表が分割されている、既存と新設で別紙)。
    #: **足さない。** 値が食い違えばレンジにして、判断は仲裁層へ回す。
    door_quantity_hits: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    door_rows: list[DoorScheduleRow] = []
    finish_rows: list[FinishScheduleRow] = []
    findings: list[DrawingFinding] = []
    pending: list[PendingDecision] = []
    ocr_pages: list[OcrPage] = []

    indices = range(page_count) if config.pages is None else config.pages
    for index in indices:
        if not 0 <= index < page_count:
            raise IntakeError(f"ページ {index} は存在しません(全 {page_count} ページ)")
        # 1 ページずつ読む。全ページ分の画像を同時に持つと、A0 相当の図面では
        # 配列だけで数 GB になる。ここで要るのは種別と埋め込み文字だけ。
        page = rasterize(pdf_path, dpi=config.dpi, pages=range(index, index + 1))[0]
        page_number = index + 1
        declaration = start_kit.declaration_for(page_number)
        notes: list[str] = []

        if page.content_kind == "raster":
            if config.ocr is None:
                outcomes.append(
                    PageOutcome(
                        page_number=page_number,
                        content_kind=page.content_kind,
                        status="unsupported_raster",
                        declaration=declaration,
                        notes=(
                            "スキャン画像のページ。この経路では読めない(未対応)",
                        ),
                    )
                )
                continue
            outcomes.append(
                _read_scanned_page(
                    pdf_path,
                    index,
                    page_number=page_number,
                    settings=config.ocr,
                    declaration=declaration,
                    reference_points=start_kit.reference_points_for(page_number),
                    tolerance=scale_tolerance,
                    findings=findings,
                    pending=pending,
                    area_hits=area_hits,
                    ocr_pages=ocr_pages,
                )
            )
            continue
        if page.content_kind == "empty":
            outcomes.append(
                PageOutcome(
                    page_number=page_number,
                    content_kind=page.content_kind,
                    status="unsupported_empty",
                    declaration=declaration,
                    notes=("描画オブジェクトが無いページ",),
                )
            )
            continue

        if page.content_kind == "mixed":
            notes.append(
                "ベクターとラスターが同居するページ。**貼られた画像の中身は読んでいない**"
            )

        printed = extract_scale(pdf_path, index)
        reference_points = start_kit.reference_points_for(page_number)
        scale, readings, disagreement = _resolve_scale(
            page_number=page_number,
            printed=printed,
            reference_points=reference_points,
            tolerance=scale_tolerance,
        )
        if disagreement is not None:
            pending.append(disagreement)
            notes.append(
                "縮尺の読みが食い違ったため、このページでは実寸に依存する抽出を行わない"
            )
        elif scale is None:
            notes.append("縮尺が読めないため、実寸に依存する抽出(開き戸)は行わない")
        elif any(reading.is_human_input for reading in readings):
            notes.append("人が入れた基準点から求めた縮尺を使った")

        findings.extend(
            _reference_dimension_findings(reference_points, printed, page_number)
        )

        # 表の読み取りは縮尺に依存しない。印字された文字を読むだけなので、
        # 縮尺が読めないページでも、読みが食い違ったページでも表は読める。
        door_schedule_count, finish_schedule_count = _read_schedules(
            pdf_path,
            index,
            notes=notes,
            door_rows=door_rows,
            finish_rows=finish_rows,
            door_quantity_hits=door_quantity_hits,
        )

        for label in find_area_labels(pdf_path, index):
            area_hits.setdefault((label.label, METHOD_TEXT_AREA), []).append(
                (
                    label.value_sqm,
                    {
                        "page_number": page_number,
                        "source_text": label.source_text,
                        "rect_pt": list(label.rect_pt) if label.rect_pt else None,
                    },
                    None,
                )
            )

        if scale is not None:
            # **宣言でページを外さない。** 人が「建具表」と宣言していても
            # 円弧は探す(原則4の条件3「図面の読み方を縛らない」)。
            # 宣言と食い違ったときの扱いは `_door_arc_findings` の中。
            findings.extend(
                _door_arc_findings(
                    pdf_path,
                    index,
                    page_number,
                    scale,
                    declaration,
                    notes,
                    pending=pending,
                    door_schedule_count=door_schedule_count,
                    finish_schedule_count=finish_schedule_count,
                )
            )

        outcomes.append(
            PageOutcome(
                page_number=page_number,
                content_kind=page.content_kind,
                status="processed",
                scale=scale,
                scale_readings=readings,
                declaration=declaration,
                notes=tuple(notes),
            )
        )

    findings.extend(_area_findings(area_hits))
    findings.extend(_door_quantity_findings(door_quantity_hits))
    return tuple(outcomes), findings, pending, door_rows, finish_rows, ocr_pages


def _read_scanned_page(
    pdf_path: Path,
    index: int,
    *,
    page_number: int,
    settings: OcrSettings,
    declaration: PageDeclaration | None,
    reference_points: Sequence[ReferencePoint],
    tolerance: Fraction,
    findings: list[DrawingFinding],
    pending: list[PendingDecision],
    area_hits: dict[tuple[str, str], list[tuple[float, dict[str, Any], Meaning | None]]],
    ocr_pages: list[OcrPage],
) -> PageOutcome:
    """スキャン(ラスター)のページ 1 枚を OCR で読む。

    **このページからは実寸(長さ)を出さない。** 止めているのではなく、
    出す対象が無いからである。スキャンのページには図形データが入っていないので、
    ベクターのページで拾っている開き戸の円弧のような「図面上の長さ」が
    そもそも取れない。読んだ縮尺の印字は、**人が入れた基準点との
    突き合わせ(検算)にだけ**使い、合わなければ警告を残してどちらも採らない
    (原則 3-1: 図面に書かれた縮尺の表記は当てにしない)。

    なお、印字の縮尺だけに頼って長さを出す場合は「仮説に基づく」として出す、
    というのがおーちゃんの指示(2026-09-22)だが、**この経路にはその対象が無い。**
    画像から長さを測る実装(罫線・輪郭の検出)が入った時点で、その決まりが効く。

    出すのは、図面に**文字として書かれている**面積の記載だけである。
    建具表を表として読む経路は、罫線を画像から見つける実装がまだ無いので
    動かない(次の段)。
    """
    ocr_page = recognize_page(
        pdf_path,
        index,
        backends=settings.backends,
        dpi=settings.dpi,
        min_confidence=settings.min_confidence,
    )
    ocr_pages.append(ocr_page)

    notes: list[str] = [
        f"スキャン画像のページを OCR で読んだ(エンジン: {'、'.join(ocr_page.engines)})",
        *ocr_page.notes,
        "このページからは実寸(長さ)を出していない。"
        "スキャンには図形データが無く、長さを出す対象がそもそも無いため。"
        "印字の縮尺は、人が入れた基準点との突き合わせにだけ使った",
    ]

    printed = read_scale(ocr_page)
    scale, readings, disagreement = _resolve_scale(
        page_number=page_number,
        printed=(
            DrawingScale(denominator=printed.denominator, source_text=printed.source_text)
            if printed is not None
            else None
        ),
        reference_points=reference_points,
        tolerance=tolerance,
        printed_origin="スキャンを OCR で読んだ表題欄の印字",
    )
    if disagreement is not None:
        pending.append(disagreement)
        notes.append(
            "人が入れた基準点と、OCR で読んだ印字の縮尺が食い違った。"
            "どちらも採っていない"
        )
    elif printed is None:
        notes.append("縮尺の印字は読めなかった(**書かれていないという意味ではない**)")
    if reference_points:
        notes.append(
            "人が入れた基準点は、このページでは縮尺の突き合わせにだけ使った"
            "(スキャンなので実寸の抽出はしない)"
        )

    phase = declaration.phase if declaration is not None else PHASE_UNKNOWN
    if phase not in {"現況", "計画", "解体"}:
        phase = PHASE_UNKNOWN

    for label in read_area_labels(ocr_page):
        area_hits.setdefault((label.label, METHOD_OCR_TEXT_AREA), []).append(
            (
                label.value_sqm,
                {
                    "page_number": page_number,
                    "source_text": label.source_text,
                    "rect_pt": list(label.rect_pt) if label.rect_pt else None,
                    "engines": list(label.engines),
                    "cross_checked": label.cross_checked,
                    "confidence": round(label.confidence, 3),
                    "readings": [list(item) for item in label.readings],
                    "read_by": "ocr",
                    "limitation": (
                        "スキャンを OCR で読んだ値。文字が別の字に化けても"
                        "確信度では止まらない"
                    ),
                },
                Meaning(
                    what=label.meaning.what,
                    where=label.meaning.where,
                    phase=phase,
                    purpose_link=PURPOSE_UNESTABLISHED,
                ),
            )
        )

    if ocr_page.conflicts:
        notes.append(
            f"エンジンどうしで読みが食い違った語が {len(ocr_page.conflicts)} 件ある。"
            "食い違った語は数量にしていない"
        )

    return PageOutcome(
        page_number=page_number,
        content_kind="raster",
        status="processed_ocr",
        scale=scale,
        scale_readings=readings,
        declaration=declaration,
        notes=tuple(notes),
    )


def _read_schedules(
    pdf_path: Path,
    index: int,
    *,
    notes: list[str],
    door_rows: list[DoorScheduleRow],
    finish_rows: list[FinishScheduleRow],
    door_quantity_hits: dict[str, list[tuple[float, dict[str, Any]]]],
) -> tuple[int, int]:
    """1 ページぶんの建具表・内装仕上表を読み、結果を呼び出し側の器に足す。

    返すのは、そのページで**表として読めた**建具表と内装仕上表の数である。
    人が宣言したページの種類と突き合わせる材料に使う(原則5)。
    **0 件は「表が無い」ではない。** 罫線で組まれていない表とスキャンの
    ページは、この経路では読めない。
    """
    door_schedules = read_door_schedules(pdf_path, index)
    if not door_schedules:
        notes.append(
            "建具表は見つからなかった(罫線で組まれた表として読めるものが無い)。"
            "**建具が無いという意味ではない**"
        )
    for schedule in door_schedules:
        door_rows.extend(schedule.rows)
        if schedule.unit_source is None:
            notes.append(
                "建具表の寸法は単位が書かれていないため、mm に直していない"
            )
        if schedule.skipped_rows:
            notes.append(
                f"建具表で読まなかった行が {len(schedule.skipped_rows)} 行ある"
            )
        for row in schedule.rows:
            if row.quantity is None:
                continue
            door_quantity_hits.setdefault(row.mark, []).append(
                (float(row.quantity), row.provenance())
            )

    finish_schedules = read_finish_schedules(pdf_path, index)
    if not finish_schedules:
        notes.append(
            "内装仕上表は見つからなかった(罫線で組まれた表として読めるものが無い)"
        )
    for schedule in finish_schedules:
        finish_rows.extend(schedule.rows)

    return len(door_schedules), len(finish_schedules)


def _door_quantity_findings(
    hits: Mapping[str, list[tuple[float, dict[str, Any]]]]
) -> list[DrawingFinding]:
    """建具番号ごとに 1 件の数量にまとめる。**足さない。**

    同じ建具番号が別のページで違う数量になっていたら、どちらかを選ばず
    レンジにする。合計するのは、既存と新設の建具表が両方あるときに
    二重に数えることになる。
    """
    out: list[DrawingFinding] = []
    for mark, occurrences in sorted(hits.items()):
        values = [value for value, _ in occurrences]
        provenance: dict[str, Any] = {
            "occurrences": [meta for _, meta in occurrences],
            "limitation": (
                "建具表に書かれた数量をそのまま読んだ値。"
                "表に載っていない建具は拾えない"
            ),
        }
        if min(values) != max(values):
            provenance["note"] = (
                "同じ建具番号の数量がページによって違ったため、"
                "どちらも捨てずにレンジにした(足していない)"
            )
        out.append(
            DrawingFinding(
                target=f"{TARGET_DOOR_QUANTITY_PREFIX}{mark}",
                value_range=(min(values), max(values)),
                unit=COUNT_UNIT,
                method_id=METHOD_DOOR_SCHEDULE,
                provenance=provenance,
            )
        )
    return out


def _page_count(pdf_path: Path) -> int:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


# ---------------------------------------------------------------------------
# 2. 縮尺: 印字と、人が入れた基準点を突き合わせる
# ---------------------------------------------------------------------------


def _resolve_scale(
    *,
    page_number: int,
    printed: DrawingScale | None,
    reference_points: Sequence[ReferencePoint],
    tolerance: Fraction,
    printed_origin: str = "表題欄の印字",
) -> tuple[DrawingScale | None, tuple[ScaleReading, ...], PendingDecision | None]:
    """このページで使う縮尺を決める。食い違えば**使わずに判断待ちにする。**

    読みは2種類ある。

    - 表題欄の印字(`extract_scale`)。**用紙の拡大縮小を保証しない。**
      A3 の図面を A0 で出しても印字は 1/50 のままで、実効の縮尺は約 1/18 になる
      (P011 で実際に起きた。`docs/real_drawing_eval_report.md`)。
    - 人が入れた基準点から求めた比。紙の上の距離と実寸の比なので、
      拡大縮小があっても正しく出る。

    食い違ったときに**どちらかを選ばない。** 選べる根拠がこの場に無いし、
    間違ったほうを選ぶと、もっともらしい長さが下流に入る。
    """
    readings: list[ScaleReading] = []
    if printed is not None:
        readings.append(
            ScaleReading(
                denominator=printed.denominator,
                origin=printed_origin,
                detail=printed.source_text,
            )
        )
    for point in sorted(reference_points, key=lambda item: item.axis):
        readings.append(
            ScaleReading(
                denominator=point.derived_denominator,
                origin=f"人が入れた基準点({point.label})",
                detail=(
                    f"{point.point_a_pt} - {point.point_b_pt} "
                    f"= 紙の上 {point.paper_distance_pt:.2f}pt / 実寸 {point.actual_length_mm:g}mm"
                ),
                is_human_input=True,
            )
        )

    if not readings:
        return None, (), None

    if not _all_agree([reading.denominator for reading in readings], tolerance):
        return (
            None,
            tuple(readings),
            PendingDecision(
                kind="scale_disagreement",
                page_number=page_number,
                detail=(
                    f"ページ {page_number} の縮尺の読みが"
                    f"許容差(±{float(tolerance) * 100:.3g}%)を超えて食い違っています。"
                    "どちらを使うかは人が決める必要があります"
                ),
                observed=tuple(
                    (reading.origin, reading.denominator) for reading in readings
                ),
            ),
        )

    # 一致しているときは、**人が入れた基準点のほうを使う。** 印字と同じ値を
    # 指しているうえ、用紙の拡大縮小の影響を受けないため。平均は取らない
    # (平均した分母は、どの読みも主張していない数値になる)。
    chosen = next(
        (reading for reading in readings if reading.is_human_input), readings[0]
    )
    return (
        DrawingScale(denominator=chosen.denominator, source_text=chosen.detail),
        tuple(readings),
        None,
    )


def _all_agree(values: Sequence[float], tolerance: Fraction) -> bool:
    """すべての値が、互いに許容差の中に収まっているか。

    比較は平均に対する相対差で行う。**値が2つより多いときは総当たりで見る。**
    最大と最小だけを見ると、間に挟まった値の食い違いを見落とす場合がある。
    """
    if len(values) < 2:
        return True
    limit = float(tolerance)
    for index, first in enumerate(values):
        for second in values[index + 1 :]:
            mean = (first + second) / 2.0
            if mean <= 0:
                return False
            if abs(first - second) / mean > limit:
                return False
    return True


def _mm_range(value: float) -> tuple[int, int]:
    """ミリの値を、1mm きざみで表せるレンジに直す。

    `arbitration/units.py` は 1mm より細かい値を受け付けない(黙って丸めない
    ため)。ここで四捨五入して 1 点にすると、丸めた結果が「読んだ値」として
    下流に入る。**その値を含む 1mm 幅のレンジ**にして、丸めたことを残す。
    """
    return (math.floor(value), math.ceil(value))


def _reference_dimension_findings(
    reference_points: Sequence[ReferencePoint],
    printed: DrawingScale | None,
    page_number: int,
) -> list[DrawingFinding]:
    """人が指した2点の間の長さを、2つの読みとして証拠にする。

    - 人が入れた実寸(`human_reference_point`)
    - 同じ2点を、**表題欄の印字した縮尺**で実寸に直した値(`pdf_text_scale`)

    この2つは、縮尺についてだけ独立している。**2点の座標は共有している**ので、
    座標の取り違えは両方に同じように効き、この突き合わせでは捕まらない。
    その但し書きは `provenance` に残す。
    """
    out: list[DrawingFinding] = []
    shared_note = (
        "この読みと相手の読みは2点の座標を共有している。"
        "独立なのは縮尺の部分だけで、座標の取り違えは両方に同じように効く"
    )
    for point in reference_points:
        target = f"基準寸法::ページ{page_number}::{point.label}"
        base_provenance = {
            "page_number": page_number,
            "point_a_pt": list(point.point_a_pt),
            "point_b_pt": list(point.point_b_pt),
            "paper_distance_pt": round(point.paper_distance_pt, 3),
            "independence_note": shared_note,
        }
        out.append(
            DrawingFinding(
                target=target,
                value_range=_mm_range(point.actual_length_mm),
                unit=LENGTH_UNIT,
                method_id=METHOD_HUMAN_REFERENCE_POINT,
                source_kind="start_kit",
                provenance={
                    **base_provenance,
                    "entered_by": point.entered_by,
                    "stated_length_mm": point.actual_length_mm,
                    "derived_denominator": round(point.derived_denominator, 4),
                },
            )
        )
        if printed is not None:
            from_print = point.paper_distance_pt * MM_PER_POINT * printed.denominator
            out.append(
                DrawingFinding(
                    target=target,
                    value_range=_mm_range(from_print),
                    unit=LENGTH_UNIT,
                    method_id=METHOD_TEXT_SCALE,
                    derivation="derived",
                    derivation_basis=("read",),
                    provenance={
                        **base_provenance,
                        "printed_scale": f"1/{printed.denominator:g}",
                        "printed_scale_source_text": printed.source_text,
                        "computed_length_mm": round(from_print, 3),
                    },
                )
            )
    return out


def _door_arc_findings(
    pdf_path: Path,
    index: int,
    page_number: int,
    scale: DrawingScale,
    declaration: PageDeclaration | None,
    notes: list[str],
    *,
    pending: list[PendingDecision],
    door_schedule_count: int = 0,
    finish_schedule_count: int = 0,
) -> list[DrawingFinding]:
    """そのページの開き戸の円弧を数える。**人の宣言では止めない。**

    人が宣言したページの種類は**弱い手がかり**であって、読み取りの範囲では
    ない(原則4の条件3)。宣言が「平面図」以外でも円弧は探し、出た件数は
    そのまま出す(原則5「前提が揃うまで止めるのではなく、何に基づくかを
    区別して出す」)。

    宣言と読み取りが食い違ったとき(「建具表」と宣言されたページで円弧が
    出たとき)にすることは 3 つある。

    1. **読みを捨てない。** 値は出す。
    2. 由来を ``assumed`` にする。表の罫線や姿図の記号を開き戸と
       取り違えているかもしれない、という**仮説の上に乗っている**ため。
       見積の行では「仮説に基づく」になり、階層1(自動確定)には上がらない。
    3. 食い違いを `PendingDecision` に残して人へ回す。同じページで建具表が
       実際に表として読めていれば、それも材料として書く
       (**人の宣言のほうが正しい場合もある**)。

    円弧が 0 件のときは食い違いを立てない。**捨てられた読みが無い**ので、
    正しい宣言のたびに人へ質問が飛ぶのを避ける。
    """
    arcs = find_door_arcs(pdf_path, index, scale)
    declared_kind = declaration.kind if declaration is not None else None
    unexpected_here = (
        declared_kind is not None
        and declared_kind not in DOOR_ARC_EXPECTED_PAGE_KINDS
    )
    if not arcs:
        notes.append("開き戸の円弧は 0 件(引戸・折戸はこの手法では拾えない)")
        if unexpected_here:
            notes.append(
                f"人が「{declared_kind}」と宣言したページでも円弧は探した"
                "(宣言は読み取りの範囲を決めない)。結果は 0 件で、"
                "宣言と食い違わなかった"
            )
        return []

    conflict = unexpected_here
    if conflict:
        notes.append(
            f"人が「{declared_kind}」と宣言したページだが、開き戸の円弧が "
            f"{len(arcs)} 件出た。**宣言を理由に捨てていない。** ただし表の罫線や"
            "姿図の記号を取り違えている可能性があるので、この値は"
            "「仮説に基づく」として出し、食い違いを人の判断へ回した"
        )
        observed: list[tuple[str, float]] = [("開き戸の円弧", float(len(arcs)))]
        if door_schedule_count:
            observed.append(("読めた建具表", float(door_schedule_count)))
        if finish_schedule_count:
            observed.append(("読めた内装仕上表", float(finish_schedule_count)))
        detail = (
            f"ページ{page_number}は人が「{declared_kind}」と宣言しているが、"
            f"開き戸の円弧が {len(arcs)} 件読めた。"
            "宣言が誤っている(実際は平面図、または平面図が同居している)のか、"
            "表の罫線・姿図の記号を円弧として拾ったのか、"
            "図面を見て決めてください。"
        )
        if door_schedule_count:
            detail += (
                f"なお、このページでは建具表も {door_schedule_count} 件"
                "表として読めている(宣言どおりの可能性がある)。"
            )
        else:
            detail += (
                "なお、このページでは建具表を表として読めていない"
                "(**表が無いという意味ではない**。罫線で組まれていない表と"
                "スキャンのページは、この経路では読めない)。"
            )
        pending.append(
            PendingDecision(
                kind=PAGE_KIND_DISAGREEMENT,
                detail=detail,
                observed=tuple(observed),
                page_number=page_number,
            )
        )

    # ページごとに別の対象にする。足すと既存と新設を二重に数えるため。
    # 人が現況/計画/解体を宣言していれば、対象の名前に残す。
    phase = declaration.phase if declaration is not None else None
    target = (
        f"開き戸::{phase}::ページ{page_number}"
        if phase is not None and phase != "不明"
        else f"開き戸::ページ{page_number}"
    )
    return [
        DrawingFinding(
            target=target,
            value_range=(float(len(arcs)), float(len(arcs))),
            unit=COUNT_UNIT,
            method_id=METHOD_DOOR_ARC,
            strength="weak",
            # 宣言と食い違った読みは「仮説に基づく」側に落とす。
            # `assumed` は `arbitration/` が階層1へ上げない由来である。
            derivation="assumed" if conflict else "read",
            provenance={
                "page_number": page_number,
                "scale": f"1/{scale.denominator:g}",
                "scale_source_text": scale.source_text,
                "phase": phase,
                "declared_page_kind": declared_kind,
                "declaration_conflict": conflict,
                "door_schedules_read_on_page": door_schedule_count,
                "arcs": [
                    {
                        "center_pt": list(arc.center_pt),
                        "rect_pt": list(arc.rect_pt),
                        "width_mm": round(arc.width_mm, 1),
                        "swept_degrees": round(arc.swept_degrees, 1),
                    }
                    for arc in arcs
                ],
                "limitation": (
                    "円弧を描かない引戸・折戸は拾えない。0 件は「建具が無い」ではない"
                ),
            },
        )
    ]


def _area_findings(
    area_hits: Mapping[
        tuple[str, str], list[tuple[float, dict[str, Any], Meaning | None]]
    ]
) -> list[DrawingFinding]:
    """ラベルと手法ごとに 1 件の数量にまとめる。

    同じラベルが複数ページに違う値で書かれていたときは、**どちらかを選ばず
    レンジにする。** 図面が2つの値を主張しているという事実をそのまま残すほうが、
    片方を黙って採るより安全である(レンジが広ければ仲裁層が確定しない)。

    **埋め込み文字から読んだ値と、スキャンを OCR で読んだ値はまとめない。**
    手法が違うので、同じ対象に対する別々の読みとして仲裁層へ渡す。
    まとめてしまうと、質の違う 2 つの読みが 1 本のレンジに溶けて、
    突き合わせが起きなくなる。
    """
    out: list[DrawingFinding] = []
    for (label, method_id), hits in sorted(area_hits.items()):
        values = [value for value, _, _ in hits]
        provenance: dict[str, Any] = {"occurrences": [meta for _, meta, _ in hits]}
        if min(values) != max(values):
            provenance["note"] = (
                "同じラベルの記載がページによって違う値だったため、"
                "どちらも捨てずにレンジにした"
            )
        meaning = _merge_meanings([item for _, _, item in hits])
        if meaning is not None:
            provenance["meaning"] = meaning.as_dict()
        provenance.update(_shared_reading_provenance([meta for _, meta, _ in hits]))
        out.append(
            DrawingFinding(
                target=label,
                value_range=(min(values), max(values)),
                unit=AREA_UNIT,
                method_id=method_id,
                # OCR で読んだ値は、文字化けを確信度が知らせないので弱い軸として
                # 名乗る(登録簿の上限も weak)。埋め込み文字はこれまでどおり。
                strength="weak" if method_id == METHOD_OCR_TEXT_AREA else "strong",
                provenance=provenance,
                meaning=meaning,
            )
        )
    return out


def _merge_meanings(meanings: Sequence[Meaning | None]) -> Meaning | None:
    """複数ページの読みをまとめたときの意味。

    **ページによって現況/計画の宣言が違ったら ``不明`` に落とす。** どちらかを
    選ぶと、宣言されていないほうの意味を名乗ることになる。
    """
    present = [item for item in meanings if item is not None]
    if not present:
        return None
    first = present[0]
    phases = {item.phase for item in present}
    wheres = sorted({item.where for item in present})
    return Meaning(
        what=first.what,
        where="、".join(wheres),
        phase=first.phase if len(phases) == 1 else PHASE_UNKNOWN,
        purpose_link=first.purpose_link,
    )


def _shared_reading_provenance(metas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """読みに共通して載せる、OCR まわりの根拠。埋め込み文字のときは空。"""
    engines: list[str] = []
    cross_checked: list[bool] = []
    for meta in metas:
        if "engines" in meta:
            for engine in meta["engines"]:
                if engine not in engines:
                    engines.append(engine)
        if "cross_checked" in meta:
            cross_checked.append(bool(meta["cross_checked"]))
    if not engines:
        return {}
    return {
        "engines": tuple(engines),
        # 1 ページでも突き合わせていない読みが混ざっていれば False。
        "cross_checked": all(cross_checked) if cross_checked else False,
    }


# ---------------------------------------------------------------------------
# 3. 面積の選択(案件ごとに1回だけ人に聞く)
# ---------------------------------------------------------------------------


def _apply_area_basis(
    findings: list[DrawingFinding],
    *,
    case_id: str,
    store: AnswerStore,
    start_kit: StartKit,
) -> tuple[list[DrawingFinding], list[PendingQuestion], list[PendingDecision]]:
    """人の回答があれば `施工対象床面積` を足し、無ければ質問を残す。

    回答は2つの経路で入る。保存済みの回答(`AnswerStore`)と、
    スタートキットに書かれた回答である。**両方あって食い違うときは、
    どちらも採らずに判断待ちにする。**

    **回答が無いときに片方を既定として採らない。** 図面のどこにも
    書かれていないことを一般則で埋めるのは、トライアル15 で実際に
    誤答を生んだ形そのものである。
    """
    by_label = {item.target: item for item in findings if item.unit == AREA_UNIT}
    available = tuple(option for option in AREA_BASIS_OPTIONS if option in by_label)
    if not available:
        # 面積の記載が1つも読めていないなら、聞くことがまだ無い。
        return findings, [], []

    stored = store.get(case_id, QUESTION_AREA_BASIS)
    from_kit = start_kit.area_basis
    if stored is not None and from_kit is not None and stored.answer != from_kit:
        return (
            findings,
            [],
            [
                PendingDecision(
                    kind="area_basis_conflict",
                    detail=(
                        "保存済みの回答とスタートキットの指定が食い違っています"
                        f"(保存済み: {stored.answer} / スタートキット: {from_kit})。"
                        "どちらを使うかは人が決める必要があります"
                    ),
                    observed=(
                        (f"保存済み({stored.answered_by})", 0.0),
                        ("スタートキット", 0.0),
                    ),
                )
            ],
        )

    answer = stored
    if answer is None and from_kit is not None:
        answer = Answer(
            question_id=QUESTION_AREA_BASIS,
            answer=from_kit,
            answered_by=start_kit.entered_by or "スタートキット",
            answered_at="",
        )

    if answer is None or answer.answer not in by_label:
        observed = tuple(
            (label, by_label[label].value_range[0]) for label in available
        )
        blocks = (TARGET_WORK_FLOOR_AREA,) + AREA_BASIS_DEPENDENT_ITEMS
        detail = (
            f"回答 {answer.answer!r} に対応する記載が図面から読めていない"
            if answer is not None
            else "未回答"
        )
        return (
            findings,
            [
                PendingQuestion(
                    question_id=QUESTION_AREA_BASIS,
                    case_id=case_id,
                    question=(
                        "この案件の数量は、専有延床面積と施工床面積のどちらを基準に"
                        f"拾いますか({detail})"
                    ),
                    options=AREA_BASIS_OPTIONS,
                    observed=observed,
                    blocks=blocks,
                )
            ],
            [],
        )

    chosen = by_label[answer.answer]
    provenance = dict(chosen.provenance)
    provenance["area_basis"] = {
        "answer": answer.answer,
        "answered_by": answer.answered_by,
        "answered_at": answer.answered_at,
        "reused_for": list(AREA_BASIS_DEPENDENT_ITEMS),
    }
    return (
        findings
        + [
            DrawingFinding(
                target=TARGET_WORK_FLOOR_AREA,
                value_range=chosen.value_range,
                unit=chosen.unit,
                method_id=chosen.method_id,
                provenance=provenance,
            )
        ],
        [],
        [],
    )


# ---------------------------------------------------------------------------
# 4. 仲裁層へ渡す
# ---------------------------------------------------------------------------


def to_orchestrator_request(
    findings: Sequence[DrawingFinding],
    *,
    case_id: str,
    sources: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    """1 対象ぶんの要求を作る。

    **対象ごとに 1 件ずつ**にする。複数の対象を 1 つの要求に混ぜると
    `AxisQualityFirewall.assess()` が例外を投げる
    (`axes/reading/protocol.orchestrator_requests()` と同じ理由)。
    逆に、同じ対象の読みは 1 つの要求にまとめる。別々に分けると、
    独立した読み同士の突き合わせがそもそも起きない。

    `sources` は `{"drawing": (source_id, 指紋), "start_kit": (...)}`。
    人の入力を図面とは別のデータ源として渡すために分けてある。

    強度と校正状態はここで名乗るが、実際に効くのは
    `arbitration/method_policies.py` の上限まで引き下げた後の値である。
    この経路の手法はどれも未校正なので、名乗りに関わらず参考情報になる。
    """
    if not findings:
        raise IntakeError("証拠が 1 件もない要求は作れません")
    targets = {finding.target for finding in findings}
    if len(targets) != 1:
        raise IntakeError(f"1 つの要求に複数の対象が混ざっています: {sorted(targets)}")
    target = findings[0].target

    evidence: list[dict[str, Any]] = []
    for finding in findings:
        source_id, fingerprint = sources[finding.source_kind]
        entry: dict[str, Any] = {
            "target": finding.target,
            "count_range": [finding.value_range[0], finding.value_range[1]],
            "unit": finding.unit,
            "source_id": source_id,
            "source_fingerprint": fingerprint,
            "axis_id": finding.axis_id,
            "method_id": finding.method_id,
            "strength": finding.strength,
            "status": "confident",
            # 未校正であることを入口の側でも名乗る。登録簿が上限として
            # 効くので二重だが、呼び出し側だけを読んだ人が
            # 「校正済みとして渡している」と誤解しないため。
            "calibrated": False,
            "derivation": finding.derivation,
            "provenance": finding.provenance,
        }
        if finding.derivation == "derived":
            entry["derivation_basis"] = list(finding.derivation_basis)
        evidence.append(entry)

    return {
        "trace_id": f"{case_id}::intake::{target}",
        "element_id": target,
        "evidence": evidence,
        "relations": [],
    }


def _decide(
    engine: InferenceOrchestrator,
    findings: Sequence[DrawingFinding],
    *,
    case_id: str,
    sources: Mapping[str, tuple[str, str]],
) -> TargetDecision:
    result: OrchestrationResult = engine.process(
        to_orchestrator_request(findings, case_id=case_id, sources=sources)
    )
    decision = result.decision
    reason_codes = tuple(
        code for event in result.events for code in event.reason_codes
    )
    return TargetDecision(
        target=findings[0].target,
        trace_id=result.trace_id,
        tier=decision.tier if decision is not None else 3,
        action=decision.action if decision is not None else "requires_review",
        confirmed_range=decision.confirmed_range if decision is not None else None,
        reasons=decision.reasons if decision is not None else (),
        reason_codes=reason_codes,
        is_invalid=result.is_invalid,
    )
