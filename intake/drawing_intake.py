"""本番の入口: 図面 PDF のパスを受け取り、読めた数量を仲裁層まで流す。

2026-09-22 時点で、このリポジトリの PDF を読む部品
(`axes/image_axis/pdf_pages.py`、`axes/image_axis/pdf_vector_symbols.py`)は
**評価スクリプトとテストからしか呼ばれていなかった。** `app.py` は空で、
実図面の情報が `arbitration/inference_orchestrator.py` まで届く経路が
どこにも無かった。このモジュールはその1本目の経路である。

**新しい認識技術は足していない。** 既にある部品を1本につないだだけで、
読める数量は増えていない。

この入口が守っていること
------------------------
1. **ラスターのページを黙って捨てない。** ベクター(CAD 由来)のページだけを
   抽出に回し、スキャンされたページは「未対応」として `PageOutcome` に残す。
   捨ててしまうと、34 ページのうち何ページが読めなかったのかが
   どこにも残らない。
2. **縮尺が読めないページで長さを出さない。** `extract_scale()` が None を
   返したページでは、開き戸の抽出そのものを行わない(扉幅の実寸が出せない)。
3. **ページをまたいで足し算しない。** 開き戸の件数はページごとの対象として
   別々に出す。同じ案件の図面には既存平面図と新設平面図が両方あり、
   足すと同じ建具を二重に数える。どのページが既存でどれが新設かは
   図面の文字からは決まらないので、ここでは判断しない。
4. **面積の選択を勝手に決めない。** 専有延床面積と施工床面積のどちらを
   使うかは案件ごとに人へ1回だけ聞き(`intake/case_answers.py`)、
   回答が無い間はその数量を出さない。
5. **根拠を落とさない。** ページ番号・座標・元の文字列を
   `AxisEvidence.evidence["provenance"]` に載せて仲裁層へ渡す。

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from arbitration.inference_orchestrator import InferenceOrchestrator, OrchestrationResult
from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from axes.image_axis.pdf_pages import ContentKind, rasterize
from axes.image_axis.pdf_vector_symbols import (
    METHOD_DOOR_ARC,
    METHOD_TEXT_AREA,
    DrawingScale,
    extract_scale,
    find_area_labels,
    find_door_arcs,
)
from intake.case_answers import (
    AREA_BASIS_DEPENDENT_ITEMS,
    AREA_BASIS_OPTIONS,
    QUESTION_AREA_BASIS,
    AnswerStore,
    PendingQuestion,
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

#: ラスター化の解像度。**この経路は画像処理をしない**(ベクター図形と
#: 埋め込み文字しか使わない)ので、低くてよい。既定を 200dpi のままにすると
#: A0 相当の図面 34 ページで数 GB の配列を作ることになる。
#: ページの種別判定・用紙寸法・埋め込み文字は dpi に依存しない。
CLASSIFY_DPI = 72

PageStatus = Literal["processed", "unsupported_raster", "unsupported_empty"]


class IntakeError(Exception):
    """入口の設定が受け付けられなかった。"""


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

    def __post_init__(self) -> None:
        if not self.case_id:
            raise IntakeError("case_id は空にできません")
        if self.dpi <= 0:
            raise IntakeError("dpi は正の整数である必要があります")


@dataclass(frozen=True)
class PageOutcome:
    """1 ページをどう扱ったか。**読めなかったページもここに残る。**"""

    page_number: int
    """1 始まり。人に見せる番号に合わせる。"""

    content_kind: ContentKind
    status: PageStatus
    scale: DrawingScale | None = None
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
    provenance: dict[str, Any] = field(default_factory=dict)
    """ページ番号・座標・元の文字列。**仲裁層まで一緒に運ぶ。**"""


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

    @property
    def confirmed_targets(self) -> tuple[str, ...]:
        """自動で確定した対象。**2026-09-22 時点では常に空になる。**"""
        return tuple(item.target for item in self.decisions if item.confirmed)

    @property
    def unsupported_pages(self) -> tuple[int, ...]:
        """未対応として記録したページ番号(1 始まり)。"""
        return tuple(page.page_number for page in self.pages if not page.processed)

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
        ]
        for decision in self.decisions:
            lines.append(
                f"  - {decision.target}: 階層{decision.tier} / {decision.action}"
            )
        for question in self.pending_questions:
            lines.append(f"  - 未回答: {question.question}")
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


def read_drawing(
    config: IntakeConfig,
    *,
    answers: AnswerStore | None = None,
    orchestrator: InferenceOrchestrator | None = None,
) -> IntakeResult:
    """図面 PDF を読み、読めた数量を仲裁層に通して結果を返す。

    引数に取るのは**設定だけ**で、図面のパスは `config` からしか入らない。
    正解データ(見積明細・ゴールデン)はここでは一切読まない。
    """
    pdf_path = Path(config.pdf_path)
    if not pdf_path.exists():
        raise IntakeError(f"図面 PDF が見つかりません: {pdf_path}")

    store = answers if answers is not None else AnswerStore(config.answers_path)
    fingerprint = file_fingerprint(pdf_path)
    # source_id は案件の中で一意であればよい。**ファイル名は入れない。**
    source_id = f"{config.case_id}::drawing"

    pages, findings = _extract(pdf_path, config)
    findings, pending = _apply_area_basis(
        findings, case_id=config.case_id, store=store
    )

    engine = orchestrator if orchestrator is not None else InferenceOrchestrator(
        method_policies=dict(DEFAULT_METHOD_POLICIES),
        source_registry={source_id: fingerprint},
    )
    decisions = tuple(
        _decide(engine, finding, case_id=config.case_id, source_id=source_id,
                fingerprint=fingerprint)
        for finding in findings
    )
    return IntakeResult(
        case_id=config.case_id,
        source_id=source_id,
        source_fingerprint=fingerprint,
        pages=pages,
        findings=tuple(findings),
        decisions=decisions,
        pending_questions=tuple(pending),
    )


# ---------------------------------------------------------------------------
# 1. ページを分けて、ベクターのページだけを次へ回す
# ---------------------------------------------------------------------------


def _extract(
    pdf_path: Path, config: IntakeConfig
) -> tuple[tuple[PageOutcome, ...], list[DrawingFinding]]:
    outcomes: list[PageOutcome] = []
    #: ラベルごとに、読めた値とその根拠を集める。ページをまたいで同じ記載が
    #: あるのは普通なので、ここでまとめる。**別のデータ源として数えない。**
    area_hits: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    findings: list[DrawingFinding] = []

    page_count = _page_count(pdf_path)
    indices = range(page_count) if config.pages is None else config.pages
    for index in indices:
        if not 0 <= index < page_count:
            raise IntakeError(f"ページ {index} は存在しません(全 {page_count} ページ)")
        # 1 ページずつ読む。全ページ分の画像を同時に持つと、A0 相当の図面では
        # 配列だけで数 GB になる。ここで要るのは種別と埋め込み文字だけ。
        page = rasterize(pdf_path, dpi=config.dpi, pages=range(index, index + 1))[0]
        notes: list[str] = []

        if page.content_kind == "raster":
            outcomes.append(
                PageOutcome(
                    page_number=index + 1,
                    content_kind=page.content_kind,
                    status="unsupported_raster",
                    notes=("スキャン画像のページ。この経路では読めない(未対応)",),
                )
            )
            continue
        if page.content_kind == "empty":
            outcomes.append(
                PageOutcome(
                    page_number=index + 1,
                    content_kind=page.content_kind,
                    status="unsupported_empty",
                    notes=("描画オブジェクトが無いページ",),
                )
            )
            continue

        if page.content_kind == "mixed":
            notes.append(
                "ベクターとラスターが同居するページ。**貼られた画像の中身は読んでいない**"
            )

        scale = extract_scale(pdf_path, index)
        if scale is None:
            notes.append("縮尺が読めないため、実寸に依存する抽出(開き戸)は行わない")

        for label in find_area_labels(pdf_path, index):
            area_hits.setdefault(label.label, []).append(
                (
                    label.value_sqm,
                    {
                        "page_number": index + 1,
                        "source_text": label.source_text,
                        "rect_pt": list(label.rect_pt) if label.rect_pt else None,
                    },
                )
            )

        if scale is not None:
            arcs = find_door_arcs(pdf_path, index, scale)
            if arcs:
                findings.append(
                    DrawingFinding(
                        # ページごとに別の対象にする。足すと既存と新設を
                        # 二重に数えるため(モジュール冒頭 3)。
                        target=f"開き戸::ページ{index + 1}",
                        value_range=(float(len(arcs)), float(len(arcs))),
                        unit=COUNT_UNIT,
                        method_id=METHOD_DOOR_ARC,
                        provenance={
                            "page_number": index + 1,
                            "scale": f"1/{scale.denominator:g}",
                            "scale_source_text": scale.source_text,
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
                                "円弧を描かない引戸・折戸は拾えない。"
                                "0 件は「建具が無い」ではない"
                            ),
                        },
                    )
                )
            else:
                notes.append("開き戸の円弧は 0 件(引戸・折戸はこの手法では拾えない)")

        outcomes.append(
            PageOutcome(
                page_number=index + 1,
                content_kind=page.content_kind,
                status="processed",
                scale=scale,
                notes=tuple(notes),
            )
        )

    findings.extend(_area_findings(area_hits))
    return tuple(outcomes), findings


def _page_count(pdf_path: Path) -> int:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def _area_findings(
    area_hits: Mapping[str, list[tuple[float, dict[str, Any]]]]
) -> list[DrawingFinding]:
    """ラベルごとに 1 件の数量にまとめる。

    同じラベルが複数ページに違う値で書かれていたときは、**どちらかを選ばず
    レンジにする。** 図面が2つの値を主張しているという事実をそのまま残すほうが、
    片方を黙って採るより安全である(レンジが広ければ仲裁層が確定しない)。
    """
    out: list[DrawingFinding] = []
    for label, hits in sorted(area_hits.items()):
        values = [value for value, _ in hits]
        provenance: dict[str, Any] = {"occurrences": [meta for _, meta in hits]}
        if min(values) != max(values):
            provenance["note"] = (
                "同じラベルの記載がページによって違う値だったため、"
                "どちらも捨てずにレンジにした"
            )
        out.append(
            DrawingFinding(
                target=label,
                value_range=(min(values), max(values)),
                unit=AREA_UNIT,
                method_id=METHOD_TEXT_AREA,
                provenance=provenance,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 2. 面積の選択(案件ごとに1回だけ人に聞く)
# ---------------------------------------------------------------------------


def _apply_area_basis(
    findings: list[DrawingFinding], *, case_id: str, store: AnswerStore
) -> tuple[list[DrawingFinding], list[PendingQuestion]]:
    """人の回答があれば `施工対象床面積` を足し、無ければ質問を残す。

    **回答が無いときに片方を既定として採らない。** 図面のどこにも
    書かれていないことを一般則で埋めるのは、トライアル15 で実際に
    誤答を生んだ形そのものである。
    """
    by_label = {item.target: item for item in findings if item.unit == AREA_UNIT}
    available = tuple(option for option in AREA_BASIS_OPTIONS if option in by_label)
    if not available:
        # 面積の記載が1つも読めていないなら、聞くことがまだ無い。
        return findings, []

    answer = store.get(case_id, QUESTION_AREA_BASIS)
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
        return findings, [
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
        ]

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
    )


# ---------------------------------------------------------------------------
# 3. 仲裁層へ渡す
# ---------------------------------------------------------------------------


def to_orchestrator_request(
    finding: DrawingFinding,
    *,
    case_id: str,
    source_id: str,
    fingerprint: str,
) -> dict[str, Any]:
    """1 対象ぶんの要求を作る。

    **対象ごとに 1 件ずつ**にする。複数の対象を 1 つの要求に混ぜると
    `AxisQualityFirewall.assess()` が例外を投げる
    (`axes/reading/protocol.orchestrator_requests()` と同じ理由)。

    強度と校正状態はここで名乗るが、実際に効くのは
    `arbitration/method_policies.py` の上限まで引き下げた後の値である。
    この経路の2手法はどちらも未校正なので、名乗りに関わらず参考情報になる。
    """
    return {
        "trace_id": f"{case_id}::intake::{finding.target}",
        "element_id": finding.target,
        "evidence": [
            {
                "target": finding.target,
                "count_range": [finding.value_range[0], finding.value_range[1]],
                "unit": finding.unit,
                "source_id": source_id,
                "source_fingerprint": fingerprint,
                "axis_id": AXIS_ID,
                "method_id": finding.method_id,
                "strength": "strong" if finding.method_id == METHOD_TEXT_AREA else "weak",
                "status": "confident",
                # 未校正であることを入口の側でも名乗る。登録簿が上限として
                # 効くので二重だが、呼び出し側だけを読んだ人が
                # 「校正済みとして渡している」と誤解しないため。
                "calibrated": False,
                # 図面に印字された数値と、図面に描かれた図形をそのまま読んだ値。
                # 一般則で埋めた値はこの経路に1件も無い。
                "derivation": "read",
                "provenance": finding.provenance,
            }
        ],
        "relations": [],
    }


def _decide(
    engine: InferenceOrchestrator,
    finding: DrawingFinding,
    *,
    case_id: str,
    source_id: str,
    fingerprint: str,
) -> TargetDecision:
    result: OrchestrationResult = engine.process(
        to_orchestrator_request(
            finding, case_id=case_id, source_id=source_id, fingerprint=fingerprint
        )
    )
    decision = result.decision
    reason_codes = tuple(
        code for event in result.events for code in event.reason_codes
    )
    return TargetDecision(
        target=finding.target,
        trace_id=result.trace_id,
        tier=decision.tier if decision is not None else 3,
        action=decision.action if decision is not None else "requires_review",
        confirmed_range=decision.confirmed_range if decision is not None else None,
        reasons=decision.reasons if decision is not None else (),
        reason_codes=reason_codes,
        is_invalid=result.is_invalid,
    )
