"""入口(`intake/`)の結果を、当てはめの入力(`QuantityItem`)に直す。

**`intake/` には手を入れない。** 変換をこちら側に置くのは、入口を他の
スレッドが同時に触っているのと、数量の出どころが将来ふえるためである。

ここが守ること
--------------
1. **入口の判定をそのまま運ぶ。** 階層と `action` と確定した値は
   `IntakeResult.decisions` から対象名で引き、作り直さない。判定が
   見つからない数量は `action` を None のままにする(確定扱いにしない)。
   **由来(`read` / `derived` / `assumed`)も同じく運ぶ。** これを写して
   いなかったので、一般則で補った値と図面から読んだ値が、見積の行の上では
   見分けられなかった(`docs/principles/principle_conformance_review.md` D-13)。
2. **属性はページで食い違ったら付けない。** 同じ建具番号の種別がページに
   よって違ったとき、片方を選ぶと選ばなかったほうの規則が黙って外れる。
   付けずに `notes` に残し、規則が当たらない側に倒す。
3. **内装仕上表の行から数量を作らない。** 室の輪郭を取る実装がこの
   リポジトリに無いので、仕上げから面積は出せない。
4. **人が入れた前提は、案件の前提(`estimating/case_premises.py`)に直す。**
   目的も現況の申告も、性質としては案件の前提なので、**別の仕組みを2つ作らない。**
   変換がこちら側にあるのは今までどおりで、`intake/` は `estimating/` を
   import しない(向きを逆にすると循環する)。
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from axes.image_axis.schedule_tables import DoorScheduleRow
from estimating.case_premises import (
    SOURCE_HUMAN,
    SOURCE_HYPOTHESIS,
    CasePremise,
)
from estimating.decisive import (
    NOT_OBTAINED_AREA,
    NOT_OBTAINED_SYMBOL,
    NotObtained,
)
from estimating.quantities import TARGET_SEPARATOR, QuantityItem, split_target

#: 建具表の行から属性として渡す欄。**印字された文字列のまま渡す。**
#: 単位が表に書かれていない寸法を mm に直さないのは入口と同じ約束。
DOOR_ATTRIBUTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("種別", "kind"),
    ("幅", "width_text"),
    ("高さ", "height_text"),
)

#: 建具番号ごとの数量の対象名の頭。入口の `TARGET_DOOR_QUANTITY_PREFIX` と
#: 同じ文字列だが、**`intake/` を import しないためにここで持つ**
#: (この層が入口の実装に縛られないようにする)。値がずれたときは
#: `tests/test_estimate_line_mapping.py` の通しテストが気づく。
DOOR_QUANTITY_KIND = "建具数量"

#: 人が入れた前提から来た読みの `source_kind`。入口の `DrawingFinding` と
#: 同じ文字列だが、**`intake/` を import しないためにここで持つ。**
SOURCE_KIND_START_KIT = "start_kit"


def attributes_for_door_mark(
    mark: str, rows: Sequence[DoorScheduleRow]
) -> tuple[dict[str, str], tuple[str, ...]]:
    """建具番号に対応する属性と、付けなかったことの記録を返す。

    **食い違った欄は付けない。** 読めなかった欄も付けない。
    """
    attributes: dict[str, str] = {}
    notes: list[str] = []
    matching = [row for row in rows if row.mark == mark]
    for name, field_name in DOOR_ATTRIBUTE_FIELDS:
        values = [
            value
            for value in (getattr(row, field_name, None) for row in matching)
            if isinstance(value, str) and value.strip()
        ]
        if not values:
            continue
        distinct = list(dict.fromkeys(values))
        if len(distinct) > 1:
            notes.append(
                f"建具 {mark} の {name} がページによって食い違うため属性にしない: "
                + "、".join(distinct)
            )
            continue
        attributes[name] = distinct[0]
    return attributes, tuple(notes)


def _methods_of(evidence_ids: Sequence[str]) -> tuple[str, ...]:
    """証拠の識別子から手法の名前だけを取り出す。

    識別子は `source_id::axis_id::method_id::target`
    (`arbitration/axis_quality_firewall.AxisEvidence.evidence_id`)。
    **対象名にも `::` が入る**ので、区切りは 3 回までにする。
    """
    out: list[str] = []
    for evidence_id in evidence_ids:
        parts = evidence_id.split("::", 3)
        if len(parts) >= 3 and parts[2] and parts[2] not in out:
            out.append(parts[2])
    return tuple(out)


def _decisive_evidence(finding, decision) -> dict:  # noqa: ANN001
    """決め手の**証拠**を、仲裁層が出した記録から取り出す。

    **ここで判定をやり直さない。** 一致したかどうかは仲裁層が解いた結果
    (`solve_is_consistent`)を読むだけで、範囲を見比べ直したりはしない。

    いまの本番経路で名乗れるのは、実際には
    **「観測」と、スタートキットが入っているときの「人の回答」**だけである。
    知識のルール(図面に現れない行の決まり・波及)と要約資料(メニュー)は**経路そのものが
    まだ無い**ので、ここも空のままになる。**見た目のために埋めない。**
    """
    source_kind = getattr(finding, "source_kind", "drawing")

    # **一致は、矛盾なく解けたときだけ。** ハードで効いた読みを先に見て、
    # 全部が参考情報に落ちている(未校正の手法)ときは参考情報のほうを見る。
    paths: tuple[str, ...] = ()
    independent: bool | None = None
    if decision is not None and getattr(decision, "solve_is_consistent", False):
        hard = _methods_of(tuple(getattr(decision, "hard_evidence_ids", ()) or ()))
        if len(hard) >= 2:
            paths = hard
            independent = (
                getattr(decision, "independent_strong_source_count", 0) >= 2
            )
        else:
            advisory = _methods_of(
                tuple(getattr(decision, "advisory_evidence_ids", ()) or ())
            )
            if len(advisory) >= 2:
                paths = advisory
                independent = (
                    getattr(decision, "independent_advisory_source_count", 0) >= 2
                )

    # 人が入れた前提から出た数量は、**どの入力への回答か**が分かる。
    question_id = ""
    if source_kind == SOURCE_KIND_START_KIT:
        question_id = f"start_kit::{getattr(finding, 'method_id', '')}"

    return {
        "agreeing_paths": paths,
        "paths_independent": independent,
        "question_id": question_id,
    }


def quantities_from_intake(result) -> tuple[QuantityItem, ...]:
    """`IntakeResult` を `QuantityItem` の並びに直す。

    引数の型を注釈で縛らないのは、この層が `intake/` に依存しないため。
    必要なのは `findings` / `decisions` / `door_schedule_rows` だけである。
    """
    decisions: Mapping[str, object] = {
        decision.target: decision for decision in getattr(result, "decisions", ())
    }
    door_rows: Sequence[DoorScheduleRow] = tuple(
        getattr(result, "door_schedule_rows", ())
    )

    out: list[QuantityItem] = []
    for finding in getattr(result, "findings", ()):
        kind, key = split_target(finding.target)
        attributes: dict[str, str] = {}
        notes: list[str] = []
        if kind == DOOR_QUANTITY_KIND and key:
            # 鍵は `WD-01` か `WD-01::現況` である(入口が 2026-09-23 から
            # 現況/計画ごとに別の対象にしている)。**属性を引くのは建具番号のほう。**
            # 現況/計画は `Meaning.phase` で運ぶので、ここで切り出した文字は使わない。
            attributes, notes_tuple = attributes_for_door_mark(
                key.split(TARGET_SEPARATOR, 1)[0], door_rows
            )
            notes.extend(notes_tuple)

        decision = decisions.get(finding.target)
        out.append(
            QuantityItem(
                target=finding.target,
                value_range=tuple(finding.value_range),
                unit=finding.unit,
                method_id=finding.method_id,
                source_kind=getattr(finding, "source_kind", "drawing"),
                axis_id=getattr(finding, "axis_id", "image"),
                derivation=getattr(finding, "derivation", "read"),
                derivation_basis=tuple(getattr(finding, "derivation_basis", ()) or ()),
                tier=getattr(decision, "tier", None),
                action=getattr(decision, "action", None),
                confirmed_range=getattr(decision, "confirmed_range", None),
                **_decisive_evidence(finding, decision),
                attributes=attributes,
                # **意味の4欄は運ぶだけ。ここで作らない。**
                # 付いていない読み(埋め込み文字の面積)は None のまま残す。
                # 既定値で埋めると「まだ付けていない」が「不明と分かっている」に化ける。
                meaning=getattr(finding, "meaning", None),
                provenance=dict(getattr(finding, "provenance", {}) or {}),
                notes=tuple(notes),
            )
        )
    return tuple(out)


def quantities_from_findings(
    findings: Iterable, *, door_rows: Sequence[DoorScheduleRow] = ()
) -> tuple[QuantityItem, ...]:
    """仲裁層を通していない読みだけから数量を作る(試験・検討用)。

    **`action` は None のまま**なので、この入口から作った数量は
    どれも確定しない。
    """

    class _Bare:
        findings = ()
        decisions = ()
        door_schedule_rows = ()

    holder = _Bare()
    holder.findings = tuple(findings)  # type: ignore[assignment]
    holder.door_schedule_rows = tuple(door_rows)  # type: ignore[assignment]
    return quantities_from_intake(holder)


# ---------------------------------------------------------------------------
# 人が入れた前提 → 案件の前提
# ---------------------------------------------------------------------------

#: 現況の把握が申告されていないときに使う値。`intake/start_kit.py` と同じ文字列だが、
#: **`intake` を import しないためにここで持つ**(`DOOR_QUANTITY_KIND` と同じ理由)。
#: 値がずれたときは `tests/test_start_kit_purpose_condition.py` が気づく。
AWARENESS_UNKNOWN = "全く分からない、または図面で表現されていない"

PREMISE_PURPOSE = "目的の方向性"
PREMISE_CONDITION = "現況の把握の状態"


def premises_from_intake(result) -> tuple[CasePremise, ...]:
    """入口の結果から、案件の前提を組み立てる。

    いま作るのは目的と現況の申告の2つだけである。面積の基準や基準点も性質としては
    案件の前提だが、**それらを前提として立てると判定が変わる**ので、別の変更で扱う。

    2つの約束がある。

    1. **目的は、無いなら無い。** 「目的が不明」という前提をでっち上げない。
       目的は何を読むか・どの行が対象かの推定に効くだけで、**無くても処理は止まらない。**
    2. **現況の申告は、無くても前提を作る。** 未申告は「不明」として扱うと決まって
       いるが、**黙って不明で進めない。** 仮説として記録し、何が分かれば要らなく
       なるかを書いておく。
    """
    out: list[CasePremise] = []
    entered_by = getattr(result, "start_kit_entered_by", "") or "スタートキット"

    purpose = getattr(result, "purpose", None)
    if purpose is not None:
        parts: list[str] = []
        if purpose.direction.strip():
            parts.append(f"方向性: {purpose.direction.strip()}")
        if purpose.source_documents:
            named = "、".join(
                document.label
                + (f"(ページ{document.page_number})" if document.page_number else "")
                for document in purpose.source_documents
            )
            parts.append(f"やりたいことが濃く書かれている資料: {named}")
        out.append(
            CasePremise(
                premise_id="purpose::direction",
                kind=PREMISE_PURPOSE,
                statement=(
                    "。".join(parts)
                    + "。**弱い手がかりとしてだけ扱う**(図面の中身と食い違ったら、"
                    "そのこと自体を人に聞き返す)"
                ),
                source=SOURCE_HUMAN,
                entered_by=entered_by,
            )
        )

    survey = getattr(result, "condition_survey", None)
    if survey is not None:
        statement = f"人の申告: 現況は「{survey.overall}」"
        if survey.ranges:
            statement += "。範囲ごと: " + "、".join(
                f"{item.description} は「{item.awareness}」" for item in survey.ranges
            )
        out.append(
            CasePremise(
                premise_id="condition::survey",
                kind=PREMISE_CONDITION,
                statement=statement,
                source=SOURCE_HUMAN,
                entered_by=entered_by,
            )
        )
    else:
        out.append(
            CasePremise(
                premise_id="condition::unstated",
                kind=PREMISE_CONDITION,
                statement=(
                    "現況の把握の状態が申告されていないため、"
                    f"「{AWARENESS_UNKNOWN}」として扱う"
                ),
                source=SOURCE_HYPOTHESIS,
                resolved_by="人が現況の把握の状態を申告する",
                alternatives=(
                    "明確に分かる",
                    "おおむね分かるが確実ではない",
                    "部分によって混在する",
                ),
            )
        )

    return tuple(out)


#: 面積の数量の対象名の頭。入口の `TARGET_WORK_FLOOR_AREA` と同じ文字列。
AREA_TARGET_KINDS: tuple[str, ...] = ("施工対象床面積", "専有延床面積")


def not_obtained_from_intake(result) -> tuple[NotObtained, ...]:  # noqa: ANN001
    """**入口の側にしか分からない「取れなかった理由」を作る。**

    おーちゃんの指示(66周目)の 4 つのうち、この層が根拠を持てるのは 2 つ。

    - **記号が読めない** … 図形データとして読めなかったページ。
      そのページからは記号を 1 つも数えられない。
    - **面積が出せない** … 面積の数量が 1 件も出ていない、または面積の
      数え方が食い違って人の判断待ちになっている。

    **残りの 2 つ(ルールが無い・現地確認が必要)は当てはめの側が作る**
    (`estimating/mapping.MappingResult.not_obtained()`)。
    **推測で埋めない。** 根拠が無い理由は作らない。
    """
    out: list[NotObtained] = []

    for page in getattr(result, "pages", ()):
        status = getattr(page, "status", "")
        if status in ("unsupported_raster", "unsupported_empty"):
            out.append(
                NotObtained(
                    reason=NOT_OBTAINED_SYMBOL,
                    target=f"ページ{getattr(page, 'page_number', '?')}",
                    detail=status,
                )
            )

    findings = tuple(getattr(result, "findings", ()))
    has_area = any(
        split_target(finding.target)[0] in AREA_TARGET_KINDS for finding in findings
    )
    if not has_area:
        out.append(
            NotObtained(
                reason=NOT_OBTAINED_AREA,
                target="面積",
                detail="面積の数量が 1 件も出ていない",
            )
        )
    for pending in getattr(result, "pending_decisions", ()):
        if getattr(pending, "kind", "") == "area_basis_conflict":
            out.append(
                NotObtained(
                    reason=NOT_OBTAINED_AREA,
                    target="面積",
                    detail=getattr(pending, "detail", ""),
                )
            )
    return tuple(out)
