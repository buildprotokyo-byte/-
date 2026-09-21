"""段階0.5(方式A2)の読み取り指示と、読み取り結果の受け取り。

このモジュールが引き受けるのは2つである。

1. **段階0.5(v8 13章)。** 資料は全ページを渡したまま、
   「まず基準寸法・工事対象範囲・現況の3点を確定し、それを前提に
   数量を導く」という**考える順序だけ**を指示する
   (`build_reading_prompt`)。
2. **値の由来の申告(v8 3-3節)。** 数量1つごとに、それが
   「資料に明記された事実をそのまま読んだ値」か
   「読んだ値だけから計算した値」か
   「情報が欠けていたため一般則で埋めた値」かを申告させ、
   `arbitration.axis_quality_firewall.AxisEvidence` の `derivation` に
   そのまま載る形へ直す(`parse_reading_response` /
   `to_orchestrator_evidence`)。

この2つは対で運用する(v8 13-4節)。段階0.5は決まりを落としにくくする
ための工程で、それでも落ちたときに自動確定させないための仕組みが由来の
申告である。**どちらか片方では足りない。**

なぜページを物理的に分けないのか
--------------------------------
トライアル15(`docs/trial15_two_stage_reading_report.md`)で、概要を先に
読んで3要素を確定し、詳細ページだけを次の工程に渡す方式(方式B)は
1段階読み(方式A)を**全条件で下回った**。壊れたのは順序ではなく
**受け渡し様式**で、「巾木は出入口1箇所につき0.90m控除」のような
3要素の受け皿に載らない決まりが工程の境目で捨てられていた。
段階1の出力にその決まりが残っていたかで分けると 20/21 対 2/15。
しかも段階1は36本すべてで「確定できなかった要素は無い」と申告していた。
**工程を分けた時点で、その損失は工程自身に聞いても分からない。**

採用根拠について正直に書いておくこと
------------------------------------
**この形(方式A2)の採用根拠は、2026-09-21 の再測定で弱くなっている。**
難易度1・3では A2 = 54/54 で「方式Aと同等で害が無い」ことが採用根拠
だったが、難易度4(40ページ・現行版と食い違う旧版・但し書きを外した
おとり)では **A2 = 50/54 に対し A = 54/54 で、A2 が初めて A を下回った**
(v8 10章23項②、24項)。外した4問は2セットに集中し、どちらも版の選択を
誤ったうえで範囲と数え方を同時に外している。3要素を先に書き出させることが
版の判断を早く固定させた可能性がある。**扱いはおーちゃんの判断待ちで、
それまでこの実装は残す。**

また、方式Aが天井(98〜100%)に張り付いているため、
**「段階0.5が精度を上げるか」は今も測れていない。** 示せているのは
「方式Bのように工程を分けると下がる現象が実在する」ところまでである。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from arbitration import units as unit_registry
from arbitration.axis_quality_firewall import Derivation

#: 段階0.5で先に確定させる根本要素。**この3つは増やさない。**
#: 受け皿を増やすと「受け皿に載らない情報が捨てられる」問題の形が変わる
#: だけで、境目が無い方式A2では元々何も捨てていない。ここを触るなら
#: トライアル15をやり直すこと。
FOUNDATION_ELEMENTS: tuple[str, str, str] = ("基準寸法", "工事対象範囲", "現況")

#: 読み手に書かせる由来のラベル → `AxisEvidence.derivation` の値。
#: **日本語のラベルで答えさせる。** 読み手に `read` / `assumed` という
#: 英語の識別子を書かせると、意味を取り違えても字面が正しいので気づけない。
DERIVATION_LABELS: dict[str, Derivation] = {
    "資料に書いてあった": "read",
    "読んだ値から計算した": "derived",
    "一般則で補った": "assumed",
}

#: 由来の説明文。プロンプトにそのまま埋める。
_DERIVATION_HELP = """\
- 「資料に書いてあった」… その数量そのものが資料に数値として書かれており、それを写した。
- 「読んだ値から計算した」… 資料に書かれた数値だけを使って計算した。
  このときは「根拠」に、計算に使った値それぞれの由来を上のラベルで並べること。
- 「一般則で補った」… **資料にその情報が無かったため、一般的な決まりや慣習を当てはめた。**
  当てはめた内容が正しいと思える場合でも、資料に書かれていないならこちらである。"""


class ReadingResponseError(Exception):
    """読み手の回答を採用できないこと。

    ``code`` は呼び出し側が分岐に使う識別子で、``detail`` は人が読む説明。
    **黙って既定値で埋めない。** 由来が書かれていない回答を
    「たぶん読んだ値だろう」と補うと、この仕組み全体が無意味になる。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class QuantityRequest:
    """読み手に答えさせる数量ひとつ。"""

    #: 突合せに使う対象名(`AxisEvidence.target` になる)。
    target: str
    #: 読み手に見せる設問文。
    text: str
    #: 読み手に答えさせる単位の表記。`arbitration.units` の別名表にある
    #: ものに限る。読み手が単位を選べると、㎡と平米が別物になる。
    unit: str

    def __post_init__(self) -> None:
        if not self.target or not self.text:
            raise ValueError("target と text は空にできません")
        # 未知の単位はここで弾く。プロンプトを組んでから気づくと、
        # 読み手を1往復走らせたあとで捨てることになる。
        unit_registry.canonical_unit(self.unit)


@dataclass(frozen=True)
class ReadingRequest:
    """1回の読み取りで渡す資料と、答えさせる数量の一式。"""

    #: 資料のページ。**並び順のまま、全ページを1回で渡す**(方式A2)。
    documents: tuple[str, ...]
    quantities: tuple[QuantityRequest, ...]

    def __post_init__(self) -> None:
        if not self.documents:
            raise ValueError("資料が1ページもありません")
        if not self.quantities:
            raise ValueError("答えさせる数量が1つもありません")
        targets = [q.target for q in self.quantities]
        if len(set(targets)) != len(targets):
            raise ValueError("同じ target を2回答えさせることはできません")


def answer_schema(request: ReadingRequest) -> str:
    """読み手に守らせる回答の形(JSON)を組み立てる。"""
    foundations = ", ".join(f'"{name}": "<文章>"' for name in FOUNDATION_ELEMENTS)
    quantities = ",\n    ".join(
        '"%s": {"値": <数値>, "下限": <数値>, "上限": <数値>, '
        '"由来": "<%s>", "根拠": [], "根拠となった記述": "<文章>"}'
        % (q.target, " / ".join(DERIVATION_LABELS))
        for q in request.quantities
    )
    return (
        "{\n  "
        + foundations
        + ',\n  "数量": {\n    '
        + quantities
        + "\n  }\n}"
    )


def build_reading_prompt(request: ReadingRequest) -> str:
    """段階0.5(方式A2)の読み取り指示を組み立てる。

    **資料は1つの塊のまま、全ページを並び順どおりに渡す。** 概要と詳細に
    分けたり、詳細を読む段で概要を取り上げたりしない(v8 13-2節)。
    指示するのは考える順序だけである。
    """
    pages = "\n".join(request.documents)
    questions = "\n".join(
        f"{index}. [{q.target}] {q.text}(単位: {q.unit})"
        for index, q in enumerate(request.quantities, start=1)
    )
    elements = "\n".join(
        f"{index}. {name}"
        for index, name in enumerate(FOUNDATION_ELEMENTS, start=1)
    )
    return f"""あなたは建築改修工事の積算担当です。
次の資料一式(全{len(request.documents)}ページ)を、並び順どおりに読んでください。

===== 資料ここから =====
{pages}
===== 資料ここまで =====

まず、次の3点を資料から書き出してください。

{elements}

そのうえで、この3点を前提として設問に答えてください。

設問:
{questions}

各数量について、次のことを必ず守ってください。

* 幅を持たせて答えること。確実に分かる場合は下限と上限を同じ値にしてください。
* **その値をどうやって出したかを「由来」に申告すること。**
{_DERIVATION_HELP}
* 資料に書かれていない情報を、書かれていたかのように答えないこと。
  一般則で補ったなら、その値がどれだけ確からしく思えても「一般則で補った」と
  申告してください。**申告された値は人が確認するだけで、捨てられはしません。**
* どうしても答えられない数量は、その項目ごと書かないでください。

回答は次の形の JSON オブジェクトだけを出力してください。前後に説明文を書かないでください。
{answer_schema(request)}
"""


@dataclass(frozen=True)
class ReadingFinding:
    """読み手が返した数量ひとつを、検証して取り込んだもの。"""

    target: str
    #: 宣言された単位の刻みに正規化した整数レンジ。
    count_range: tuple[int, int]
    #: 正規形の単位名(count / mm / cm2 / yen)。
    unit: str
    derivation: Derivation
    derivation_basis: tuple[Derivation, ...] = ()
    #: 読み手が挙げた根拠の記述。人が確認するときに使う。
    quoted: str = ""

    @property
    def effective_derivation(self) -> Derivation:
        """根拠まで遡った由来。

        `AxisEvidence.effective_derivation` と同じ規則。ここでも計算して
        おくのは、軸の側で「一般則を1回計算に通すと読んだ事実に化ける」
        かどうかを、ファイアウォールへ渡す前に見られるようにするため。
        """
        if self.derivation == "derived" and "assumed" in self.derivation_basis:
            return "assumed"
        return self.derivation


@dataclass(frozen=True)
class ParsedReading:
    """読み手の回答を検証して取り込んだ結果。"""

    foundations: dict[str, str] = field(default_factory=dict)
    findings: tuple[ReadingFinding, ...] = ()
    #: 採用できなかった数量と、その理由。**黙って落とさない。**
    #: ここが空でないまま先へ進むと、読み手が答えたのに消えた数量に
    #: 誰も気づけない。
    rejected: tuple[tuple[str, str], ...] = ()

    @property
    def assumed_targets(self) -> tuple[str, ...]:
        """一般則で埋めたと申告された対象。人が見るべき一覧。"""
        return tuple(
            f.target for f in self.findings if f.effective_derivation == "assumed"
        )


def _require_mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadingResponseError(code, detail)
    return value


def parse_reading_response(
    payload: str | bytes | Mapping[str, Any], request: ReadingRequest
) -> ParsedReading:
    """読み手の回答を検証して取り込む。

    **由来が書かれていない数量は採用しない。** 既定値を当てて通すと、
    由来の申告そのものが意味を失う(`AxisEvidence.derivation` に既定値を
    置かない理由と同じ)。採用しなかった数量は `rejected` に理由付きで残す。
    """
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            data = json.loads(payload)
        except (ValueError, TypeError) as error:
            raise ReadingResponseError("response_not_json", str(error)) from None
    else:
        data = payload
    data = _require_mapping(data, "response_not_object", type(data).__name__)

    foundations: dict[str, str] = {}
    missing_elements: list[str] = []
    for name in FOUNDATION_ELEMENTS:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            foundations[name] = value.strip()
        else:
            missing_elements.append(name)
    if missing_elements:
        # 段階0.5の要は「3点を先に確定すること」なので、それが欠けた回答は
        # 数量だけ拾って通してはいけない。指示が守られていない証拠である。
        raise ReadingResponseError(
            "foundation_elements_missing", "、".join(missing_elements)
        )

    quantities = _require_mapping(
        data.get("数量", {}), "quantities_not_object", repr(data.get("数量"))
    )

    findings: list[ReadingFinding] = []
    rejected: list[tuple[str, str]] = []
    asked = {q.target: q for q in request.quantities}

    for target in sorted(set(quantities) - set(asked)):
        rejected.append((target, "設問に無い対象が返ってきた"))

    for question in request.quantities:
        raw = quantities.get(question.target)
        if raw is None:
            rejected.append((question.target, "回答が無い"))
            continue
        try:
            findings.append(_parse_one(raw, question))
        except ReadingResponseError as error:
            rejected.append((question.target, str(error)))

    return ParsedReading(
        foundations=foundations,
        findings=tuple(findings),
        rejected=tuple(rejected),
    )


def _parse_one(raw: Any, question: QuantityRequest) -> ReadingFinding:
    item = _require_mapping(raw, "answer_not_object", repr(raw))

    label = item.get("由来")
    if not isinstance(label, str) or label not in DERIVATION_LABELS:
        # ここで既定値に落とさないことが、この仕組みの全部である。
        raise ReadingResponseError("derivation_not_declared", repr(label))
    derivation = DERIVATION_LABELS[label]

    basis_raw = item.get("根拠", [])
    if not isinstance(basis_raw, (list, tuple)):
        raise ReadingResponseError("basis_not_list", repr(basis_raw))
    basis: list[Derivation] = []
    for entry in basis_raw:
        if not isinstance(entry, str) or entry not in DERIVATION_LABELS:
            raise ReadingResponseError("unknown_basis_label", repr(entry))
        basis.append(DERIVATION_LABELS[entry])
    if derivation == "derived" and not basis:
        # 根拠なしの「計算した」を許すと、一般則で埋めた値を計算値と名乗る
        # だけで通せてしまう。ファイアウォール側と同じ規則をここでも敷く。
        raise ReadingResponseError("derived_requires_basis", question.target)
    if derivation != "derived" and basis:
        raise ReadingResponseError("basis_not_allowed", question.target)

    lower = item.get("下限", item.get("値"))
    upper = item.get("上限", item.get("値"))
    if lower is None or upper is None:
        raise ReadingResponseError("value_missing", question.target)
    try:
        unit, count_range = unit_registry.normalise_range(
            question.unit, lower, upper
        )
    except unit_registry.UnitError as error:
        raise ReadingResponseError(error.code, error.detail) from None

    quoted = item.get("根拠となった記述", "")
    return ReadingFinding(
        target=question.target,
        count_range=count_range,
        unit=unit,
        derivation=derivation,
        derivation_basis=tuple(basis),
        quoted=quoted if isinstance(quoted, str) else "",
    )


def to_orchestrator_evidence(
    parsed: ParsedReading,
    *,
    source_id: str,
    source_fingerprint: str,
    axis_id: str,
    method_id: str,
    strength: str = "strong",
    calibrated: bool = True,
    model_confidence: float | None = None,
) -> list[dict[str, Any]]:
    """取り込んだ読み取りを `InferenceOrchestrator` が受け取る形にする。

    申告された由来をそのまま載せるだけで、**ここで強度を落とすことはしない。**
    「一般則で埋めた値は階層1に使わせない」は
    `AxisQualityFirewall.is_hard_eligible` が一箇所で決めている。
    同じ判断を2箇所に置くと、片方だけ直したときに緩いほうが残る。
    """
    evidence: list[dict[str, Any]] = []
    for finding in parsed.findings:
        entry: dict[str, Any] = {
            "target": finding.target,
            "count_range": list(finding.count_range),
            "unit": finding.unit,
            "source_id": source_id,
            "source_fingerprint": source_fingerprint,
            "axis_id": axis_id,
            "method_id": method_id,
            "strength": strength,
            "status": "confident",
            "calibrated": calibrated,
            "derivation": finding.derivation,
        }
        if finding.derivation == "derived":
            entry["derivation_basis"] = list(finding.derivation_basis)
        if model_confidence is not None:
            entry["model_confidence"] = model_confidence
        evidence.append(entry)
    return evidence


def orchestrator_requests(
    parsed: ParsedReading,
    *,
    trace_prefix: str,
    source_id: str,
    source_fingerprint: str,
    axis_id: str,
    method_id: str,
    **evidence_options: Any,
) -> list[dict[str, Any]]:
    """対象ごとに1件ずつ、`InferenceOrchestrator.process()` に渡す要求を作る。

    **1回の読み取りは複数の数量を返すが、入口は1要素ずつしか扱えない。**
    `to_orchestrator_evidence()` の結果をまとめて1つの要求に入れると、
    `AxisQualityFirewall.assess()` が
    「1回の判定では同一targetの証拠だけを渡してください」で例外を投げる。
    検証エラーとしてではなく例外として落ちるので、呼び出し側が自分で
    分けるのに任せると踏みやすい。ここで分けておく。
    """
    evidence = to_orchestrator_evidence(
        parsed,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        axis_id=axis_id,
        method_id=method_id,
        **evidence_options,
    )
    by_target: dict[str, list[dict[str, Any]]] = {}
    for entry in evidence:
        by_target.setdefault(str(entry["target"]), []).append(entry)
    return [
        {
            "trace_id": f"{trace_prefix}::{target}",
            "element_id": target,
            "evidence": entries,
            "relations": [],
        }
        for target, entries in by_target.items()
    ]


def foundation_summary(parsed: ParsedReading) -> str:
    """確定した3要素を人が読む形に整える(報告・引き継ぎ用)。"""
    return "\n".join(
        f"{name}: {parsed.foundations.get(name, '(未確定)')}"
        for name in FOUNDATION_ELEMENTS
    )


def describe_sequence(quantities: Sequence[QuantityRequest]) -> str:
    """段階0.5がこの読み取りに何を指示したかの1行説明(記録用)。"""
    return (
        f"段階0.5(方式A2): 全ページを1回で渡し、"
        f"{'・'.join(FOUNDATION_ELEMENTS)}の3点を先に確定させたうえで"
        f"{len(quantities)}件の数量を答えさせ、値ごとに由来を申告させた"
    )
