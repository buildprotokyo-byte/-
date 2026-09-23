"""知識の表(`knowledge/`)の読み込みのテスト。

**表の中身はコードに書かない。**会社ごとに違うものなので、差し替えられる
外部ファイルとして読む。リポジトリに置くのは架空の見本だけである。

固定したい約束は 6 つ。

1. **知らない列があったら断る。**黙って読み飛ばすと、半分だけ効いた表になる。
2. **出どころが無ければ断る。**後から「なぜそう決めたか」を辿れなくなる。
3. **知らない種類は断る。**
4. `format_version` が違えば**読まない。**
5. **同梱の見本は、自分が架空だと名乗る。**
6. **読み込むだけで、判定のしかたにはつながっていない。**
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knowledge.table import (
    KNOWLEDGE_FORMAT_VERSION,
    KnowledgeError,
    load_knowledge,
    parse_knowledge,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "knowledge" / "examples" / "synthetic_knowledge.json"


def _payload() -> dict:
    return copy.deepcopy(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def test_the_shipped_example_loads_and_says_it_is_synthetic() -> None:
    table = load_knowledge(EXAMPLE)

    assert table.synthetic is True, "同梱の見本は架空だと名乗ること"
    assert "架空" in table.description
    assert len(table.entries) == 3
    assert {entry.kind for entry in table.entries} == {"数え方", "波及", "問い"}


def test_an_unknown_column_is_refused_not_ignored() -> None:
    payload = _payload()
    payload["entries"][0]["priority"] = "high"

    with pytest.raises(KnowledgeError, match="知らない列"):
        parse_knowledge(payload)


def test_an_entry_without_a_source_is_refused() -> None:
    payload = _payload()
    payload["entries"][0]["source"]["clause"] = ""

    with pytest.raises(KnowledgeError, match="clause"):
        parse_knowledge(payload)


def test_an_unknown_kind_is_refused() -> None:
    payload = _payload()
    payload["entries"][0]["kind"] = "覚え書き"

    with pytest.raises(KnowledgeError, match="知らない kind"):
        parse_knowledge(payload)


def test_a_table_from_another_format_version_is_refused() -> None:
    payload = _payload()
    payload["format_version"] = KNOWLEDGE_FORMAT_VERSION + 1

    with pytest.raises(KnowledgeError, match="format_version"):
        parse_knowledge(payload)


def test_a_knowledge_that_applies_to_nothing_is_refused() -> None:
    payload = _payload()
    payload["entries"][0]["applies_to"] = {}

    with pytest.raises(KnowledgeError, match="applies_to"):
        parse_knowledge(payload)


def test_a_question_with_only_one_answer_is_refused() -> None:
    """**答えが 1 つしかない問いは、聞く意味が無い。**"""
    payload = _payload()
    for entry in payload["entries"]:
        if entry["kind"] == "問い":
            entry["detail"]["answer_options"] = ["はい"]

    with pytest.raises(KnowledgeError, match="answer_options"):
        parse_knowledge(payload)


def test_a_propagation_must_say_whether_it_goes_both_ways() -> None:
    """天井を撤去しても壁には波及しない、のような**非対称**を書けること。"""
    table = load_knowledge(EXAMPLE)
    (propagation,) = table.of_kind("波及")

    assert propagation.detail["symmetric"] is False
    assert propagation.overridden_by == ("特記",)


def test_the_knowledge_table_is_not_wired_into_any_judgement_yet() -> None:
    """**この周では読み込むだけ。**判定の側から呼ばれていないことを固定する。

    呼ばれ始めたときに、このテストが落ちて気づける。**「実装済み」の記録を
    信じず、呼ばれているかで確かめる**という決まりの形である。
    """
    root = Path(__file__).resolve().parent.parent
    watched = ("arbitration", "intake", "estimating", "axes", "killer_question")
    importers: list[str] = []
    for package in watched:
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import knowledge" in text or "from knowledge" in text:
                importers.append(str(path.relative_to(root)))

    assert importers == [], f"判定の側から読み込まれています: {importers}"
