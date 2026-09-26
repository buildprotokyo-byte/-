"""知識のルールを、見積の行の決め手として渡せるか。

K-11 の 1 番。`estimating/decisive.py` には `knowledge_rule_ids` の欄があるのに、
**値を入れる側が 0 か所だった。**そのため決め手の `知識のルール` は
本番経路から一度も出ていない(`docs/reports/2026-09-23_K-09_全体まとめ.md` 2 節)。

基準は `docs/d_knowledge_rule_linkage_criteria.md`(測る前にコミット済み)。

固定したい約束は 9 つ。

1. **規則は「どの知識のルールで数えたか」を書ける。**書いた規則が当たった行は
   決め手に `知識のルール` を持ち、ID がそのまま入る。
2. **書いていない規則の行は今までどおり。**`観測` のままで、知識の札は付かない。
3. **図面からは決まらない行も引用できる。**引用があれば `知識のルール`、
   **無ければ決め手は空**(`観測` に化けさせない)。
4. **版 2 のファイルに新しい欄を書いたら断る。**半分だけ効いた規則を作らない。
5. **空の引用は断る。**「書いたのに空」は、あるように見えて効かない。
6. **知識の表に無い ID を引いた規則は断る。**証拠の無い札を作らせない。
7. **`不採用` の知識は引用できない。**
8. **`候補` の知識は引用できるが、別に数える。**採用済みと混ぜない。
9. **判定は変えない。**自動確定の件数・階層・基づきの内訳は 1 件も動かない。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from estimating.decisive import REASON_KNOWLEDGE_RULE, REASON_OBSERVED
from estimating.mapping import map_quantities
from estimating.quantities import QuantityItem
from estimating.rules import RULES_FORMAT_VERSION, RuleError, parse_rules
from estimating.standing_lines import apply_standing_lines
from knowledge.linkage import LinkageError, check_knowledge_links
from knowledge.table import parse_knowledge

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_EXAMPLE = ROOT / "knowledge" / "examples" / "synthetic_knowledge.json"

#: 見本の知識の表にある ID(架空)。
KNOWN_ID = "synthetic-counting-1"


def _knowledge_payload() -> dict:
    return json.loads(KNOWLEDGE_EXAMPLE.read_text(encoding="utf-8"))


def _rules_payload(knowledge_rule_ids: list[str] | None = None) -> dict:
    rule: dict = {
        "rule_id": "kazari-bashira",
        "kind": "飾り柱",
        "line_items": [{"work_item": "飾り柱 撤去", "unit": "箇所"}],
    }
    if knowledge_rule_ids is not None:
        rule["knowledge_rule_ids"] = knowledge_rule_ids
    return {
        "format_version": 3,
        "ruleset_id": "linkage-test",
        "rules": [rule],
    }


def _standing_payload(knowledge_rule_ids: list[str] | None = None) -> dict:
    standing: dict = {
        "standing_id": "sumidashi",
        "work_item": "墨出し",
        "major_category": "仮設工事",
        "unit": "式",
        "basis": {"kind": "一式", "quantity": 1},
    }
    if knowledge_rule_ids is not None:
        standing["knowledge_rule_ids"] = knowledge_rule_ids
    return {
        "format_version": 3,
        "ruleset_id": "linkage-standing-test",
        "rules": [],
        "standing_lines": [standing],
    }


def _quantity() -> QuantityItem:
    return QuantityItem(
        target="飾り柱::1階",
        value_range=(2.0, 2.0),
        unit="箇所",
        method_id="synthetic",
        axis_id="image",
    )


# ---------------------------------------------------------------------------
# 1・2: 規則の側から渡る
# ---------------------------------------------------------------------------


def test_the_reader_knows_a_newer_format_version() -> None:
    """版が上がっていること。**版を上げずに欄を足さない。**"""
    assert RULES_FORMAT_VERSION >= 3


def test_a_rule_can_say_which_knowledge_rule_it_counted_by() -> None:
    ruleset = parse_rules(_rules_payload([KNOWN_ID]))
    result = map_quantities([_quantity()], ruleset)
    lines = [line for mapping in result.mappings for line in mapping.lines]
    assert lines, "行が 1 行も出ていません"

    kinds = {reason.kind for line in lines for reason in line.decisive}
    assert REASON_KNOWLEDGE_RULE in kinds
    ids = {
        rule_id
        for line in lines
        for reason in line.decisive
        for rule_id in reason.knowledge_rule_ids
    }
    assert ids == {KNOWN_ID}


def test_a_rule_without_a_citation_keeps_the_old_decisive_reason() -> None:
    """**引用を書いていない規則の行は今までどおり。**"""
    ruleset = parse_rules(_rules_payload())
    result = map_quantities([_quantity()], ruleset)
    lines = [line for mapping in result.mappings for line in mapping.lines]
    kinds = {reason.kind for line in lines for reason in line.decisive}
    assert kinds == {REASON_OBSERVED}


# ---------------------------------------------------------------------------
# 3: 図面からは決まらない行
# ---------------------------------------------------------------------------


def test_a_standing_line_can_cite_the_knowledge_it_stands_on() -> None:
    ruleset = parse_rules(_standing_payload([KNOWN_ID]))
    result = apply_standing_lines(ruleset)
    assert result.lines
    line = result.lines[0]
    assert [reason.kind for reason in line.decisive] == [REASON_KNOWLEDGE_RULE]
    assert line.decisive[0].knowledge_rule_ids == (KNOWN_ID,)


def test_a_standing_line_without_a_citation_has_no_decisive_reason() -> None:
    """**空は「決め手が無い」。**図面に現れない行を `観測` に化けさせない。"""
    ruleset = parse_rules(_standing_payload())
    result = apply_standing_lines(ruleset)
    assert result.lines
    assert result.lines[0].decisive == ()


# ---------------------------------------------------------------------------
# 4・5: 書式の検査
# ---------------------------------------------------------------------------


def test_a_version_2_file_that_writes_the_new_field_is_refused() -> None:
    payload = _rules_payload([KNOWN_ID])
    payload["format_version"] = 2
    with pytest.raises(RuleError) as error:
        parse_rules(payload)
    assert "knowledge_rule_ids" in str(error.value)


def test_a_version_2_standing_line_that_writes_the_new_field_is_refused() -> None:
    payload = _standing_payload([KNOWN_ID])
    payload["format_version"] = 2
    with pytest.raises(RuleError) as error:
        parse_rules(payload)
    assert "knowledge_rule_ids" in str(error.value)


def test_an_empty_citation_is_refused() -> None:
    with pytest.raises(RuleError):
        parse_rules(_rules_payload([]))


def test_a_duplicated_citation_is_refused() -> None:
    with pytest.raises(RuleError):
        parse_rules(_rules_payload([KNOWN_ID, KNOWN_ID]))


# ---------------------------------------------------------------------------
# 6・7・8: 知識の表と突き合わせる
# ---------------------------------------------------------------------------


def test_citing_an_id_the_table_does_not_have_is_caught() -> None:
    ruleset = parse_rules(_rules_payload(["そんな知識は無い"]))
    table = parse_knowledge(_knowledge_payload())
    with pytest.raises(LinkageError) as error:
        check_knowledge_links(ruleset, table)
    assert "そんな知識は無い" in str(error.value)


def test_citing_a_rejected_knowledge_entry_is_caught() -> None:
    payload = _knowledge_payload()
    for entry in payload["entries"]:
        if entry["entry_id"] == KNOWN_ID:
            entry["adoption_status"] = "不採用"
    table = parse_knowledge(payload)
    ruleset = parse_rules(_rules_payload([KNOWN_ID]))
    with pytest.raises(LinkageError) as error:
        check_knowledge_links(ruleset, table)
    assert "不採用" in str(error.value)


def test_a_candidate_entry_is_allowed_but_counted_apart() -> None:
    """**`候補` を `採用` と混ぜて数えない。**"""
    table = parse_knowledge(_knowledge_payload())
    ruleset = parse_rules(_rules_payload([KNOWN_ID]))
    report = check_knowledge_links(ruleset, table)
    assert report.cited_candidate == (KNOWN_ID,)
    assert report.cited_adopted == ()


# ---------------------------------------------------------------------------
# 9: 判定は変えない
# ---------------------------------------------------------------------------


def test_wiring_the_citation_changes_no_judgement() -> None:
    """**同じ数量で、引用の有無だけを変える。**判定の数字は動かない。"""
    quantity = _quantity()
    before = map_quantities([quantity], parse_rules(_rules_payload()))
    after = map_quantities([quantity], parse_rules(_rules_payload([KNOWN_ID])))

    def shape(result) -> tuple:
        lines = [line for mapping in result.mappings for line in mapping.lines]
        return (
            len(lines),
            len(result.settled_lines()),
            result.basis_counts_text(),
            tuple((line.tier, line.action, line.is_confirmed_quantity) for line in lines),
        )

    assert shape(before) == shape(after)
    assert len(after.settled_lines()) == 0, "自動確定が増えています(K-11 の条件)"


def test_the_judgement_side_still_does_not_read_the_knowledge_table() -> None:
    """**引用は文字列のまま運ぶ。**突き合わせは `knowledge/` の側にある。

    `tests/test_knowledge_table.py` の「判定の側から読み込まれていない」を
    崩さない形で繋ぐ、という設計をここで固定する。
    """
    watched = ("arbitration", "intake", "estimating", "axes", "killer_question")
    importers: list[str] = []
    for package in watched:
        for path in (ROOT / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import knowledge" in text or "from knowledge" in text:
                importers.append(str(path.relative_to(ROOT)))
    assert importers == [], f"判定の側から読み込まれています: {importers}"


def test_the_bundled_examples_still_load() -> None:
    """**見本を壊していない。**(版を上げたので既存の見本が読めるか)"""
    for name in ("synthetic_rules.json", "synthetic_standing_rules.json"):
        payload = json.loads(
            (ROOT / "estimating" / "examples" / name).read_text(encoding="utf-8")
        )
        parse_rules(copy.deepcopy(payload))
