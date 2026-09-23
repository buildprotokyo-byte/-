"""知識の表(`knowledge/`)の読み込みのテスト。

**表の中身はコードに書かない。**会社ごとに違うものなので、差し替えられる
外部ファイルとして読む。リポジトリに置くのは架空の見本だけである。

固定したい約束は 9 つ。

1. **知らない列があったら断る。**黙って読み飛ばすと、半分だけ効いた表になる。
2. **出どころが無ければ断る。**後から「なぜそう決めたか」を辿れなくなる。
3. **知らない種類は断る。**
4. `format_version` が違えば**読まない。**版 1 の表には、足す列を名指しして断る。
5. **同梱の見本は、自分が架空だと名乗る。**
6. **読み込むだけで、判定のしかたにはつながっていない。**
7. **5 つの列(発行元・出典の版・年・確認日・拘束力・採否の状態)が無ければ断る。**
   採否の状態を読み込みが勝手に埋めることもしない(K-04 6 番)。
8. **採否の状態を `採用` / `不採用` に変えるのはおーちゃんだけ。**
   コードがこの列を書き換えていないことを固定する。
9. **`数え方` の単位は 1 つだけ。**複合は単位ごとに 1 件へ分ける(K-06 1 番)。
"""

from __future__ import annotations

import ast
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
    assert KNOWLEDGE_FORMAT_VERSION == 2
    for entry in table.entries:
        assert entry.adoption_status == "候補", "見本は候補のまま。採否はおーちゃんが決める"
        assert entry.source.publisher and entry.source.edition
        assert entry.source.checked_on == "2026-09-23"
        assert entry.source.binding in ("法令", "行政基準", "業界指針", "任意資料")


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


# ---------------------------------------------------------------------------
# format_version 2: 5 つの列(K-04 6 番)と単位は 1 つ(K-06 1 番)
# ---------------------------------------------------------------------------

_SOURCE_COLUMNS = ("publisher", "edition", "checked_on", "binding")


def test_a_version_1_table_is_refused_and_told_which_columns_to_add() -> None:
    """**版 1 の表は読まない。**何を足せば読めるかを、列の名前で言う。"""
    payload = _payload()
    payload["format_version"] = 1
    for entry in payload["entries"]:
        del entry["adoption_status"]
        for column in _SOURCE_COLUMNS:
            del entry["source"][column]

    with pytest.raises(KnowledgeError) as caught:
        parse_knowledge(payload)

    message = str(caught.value)
    assert "format_version" in message
    for column in (*_SOURCE_COLUMNS, "adoption_status"):
        assert column in message, f"足す列 {column} を名指ししていません: {message}"


@pytest.mark.parametrize("column", _SOURCE_COLUMNS)
def test_a_source_without_each_new_column_is_refused(column: str) -> None:
    payload = _payload()
    del payload["entries"][0]["source"][column]

    with pytest.raises(KnowledgeError, match=column):
        parse_knowledge(payload)


@pytest.mark.parametrize("column", ("publisher", "edition"))
def test_an_empty_publisher_or_edition_is_refused(column: str) -> None:
    payload = _payload()
    payload["entries"][0]["source"][column] = "  "

    with pytest.raises(KnowledgeError, match=column):
        parse_knowledge(payload)


def test_an_entry_without_adoption_status_is_refused_not_filled_in() -> None:
    """**読み込みは採否の状態を埋めない。**書く側が `候補` と書く。"""
    payload = _payload()
    del payload["entries"][0]["adoption_status"]

    with pytest.raises(KnowledgeError, match="adoption_status"):
        parse_knowledge(payload)


@pytest.mark.parametrize("value", ["公共基準", "業界GL", "", "法令・行政基準"])
def test_an_unknown_binding_is_refused(value: str) -> None:
    payload = _payload()
    payload["entries"][0]["source"]["binding"] = value

    with pytest.raises(KnowledgeError, match="binding"):
        parse_knowledge(payload)


@pytest.mark.parametrize("value", ["保留", "採用候補", "", "adopted"])
def test_an_unknown_adoption_status_is_refused(value: str) -> None:
    payload = _payload()
    payload["entries"][0]["adoption_status"] = value

    with pytest.raises(KnowledgeError, match="adoption_status"):
        parse_knowledge(payload)


@pytest.mark.parametrize("status", ["候補", "採用", "不採用"])
def test_each_adoption_status_is_read_as_written(status: str) -> None:
    """**読むだけ。**書かれた値をそのまま持つ(変えるのはおーちゃん)。"""
    payload = _payload()
    payload["entries"][0]["adoption_status"] = status

    table = parse_knowledge(payload)

    assert table.entries[0].adoption_status == status


@pytest.mark.parametrize(
    "value",
    ["2026-02-30", "2026/09/23", "2026-9-23", "20260923", "2026-09-23T00:00", "令和8年9月23日", 20260923],
)
def test_a_checked_on_that_is_not_a_real_yyyy_mm_dd_date_is_refused(value: object) -> None:
    payload = _payload()
    payload["entries"][0]["source"]["checked_on"] = value

    with pytest.raises(KnowledgeError, match="checked_on"):
        parse_knowledge(payload)


def test_a_leap_day_is_a_real_date() -> None:
    payload = _payload()
    payload["entries"][0]["source"]["checked_on"] = "2028-02-29"

    assert parse_knowledge(payload).entries[0].source.checked_on == "2028-02-29"


def _counting(payload: dict) -> dict:
    return next(e for e in payload["entries"] if e["kind"] == "数え方")


@pytest.mark.parametrize(
    "unit", ["個・組", "個、組", "個,組", "個,組", "個/組", "個/組", "個等", "個 組", "個　組"]
)
def test_a_compound_counting_unit_is_refused_and_told_to_split(unit: str) -> None:
    """**単位は 1 つだけ。**複合は単位ごとに 1 件へ分ける(K-06 1 番)。"""
    payload = _payload()
    _counting(payload)["detail"]["unit"] = unit

    with pytest.raises(KnowledgeError, match="分けて") as caught:
        parse_knowledge(payload)

    assert "unit" in str(caught.value)


@pytest.mark.parametrize("unit", ["個", "組", "箇所", "㎡", "m", "本"])
def test_a_single_counting_unit_is_read_as_written(unit: str) -> None:
    """**書き方は今のまま(自由な言葉)。**正規形(count/mm…)には直さない。"""
    payload = _payload()
    _counting(payload)["detail"]["unit"] = unit

    (counting,) = parse_knowledge(payload).of_kind("数え方")

    assert counting.detail["unit"] == unit


def test_an_empty_counting_unit_is_refused() -> None:
    payload = _payload()
    _counting(payload)["detail"]["unit"] = ""

    with pytest.raises(KnowledgeError, match="unit"):
        parse_knowledge(payload)


def test_a_compound_threshold_unit_is_refused() -> None:
    payload = _payload()
    _counting(payload)["detail"]["threshold"]["unit"] = "㎡・m"

    with pytest.raises(KnowledgeError, match="threshold.unit"):
        parse_knowledge(payload)


def test_a_compound_extent_unit_is_refused() -> None:
    payload = _payload()
    propagation = next(e for e in payload["entries"] if e["kind"] == "波及")
    propagation["detail"]["extent"]["unit"] = "mm/m"

    with pytest.raises(KnowledgeError, match="extent.unit"):
        parse_knowledge(payload)


# ---------------------------------------------------------------------------
# 採否の状態は、コードが書き換えない(K-04 6 番「AI が変えないでください」)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {".git", "tests", ".venv", "venv", "node_modules", "__pycache__"}


def _repo_files(suffix: str) -> list[Path]:
    return [
        path
        for path in ROOT.rglob(f"*{suffix}")
        if not (set(path.relative_to(ROOT).parts[:-1]) & _SKIP_DIRS)
    ]


def test_only_the_loader_mentions_adoption_status_in_code() -> None:
    """**`adoption_status` の名前を口にするコードは、読み込みだけ。**

    ほかのモジュールがこの列の名前を書いた時点で落ちる。キーへの代入・
    `dataclasses.replace(adoption_status=...)`・`setattr` の文字列、の
    どれでも名前が要るので、ここで捕まる。
    """
    mentions = [
        str(path.relative_to(ROOT))
        for path in _repo_files(".py")
        if "adoption_status" in path.read_text(encoding="utf-8")
    ]

    assert mentions == ["knowledge/table.py"], f"採否の状態に触れているコード: {mentions}"


def test_the_loader_only_lists_adopted_and_rejected_as_allowed_values() -> None:
    """読み込みの中で `採用` / `不採用` の文字列が出てくるのは、読める値の一覧だけ。

    既定値を埋める・値を書き換える、のどちらも、この文字列を別の場所に
    書かないとできない。`.get("adoption_status", 既定値)` の形も断る。
    """
    tree = ast.parse((ROOT / "knowledge" / "table.py").read_text(encoding="utf-8"))

    allowed_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "ADOPTION_STATUSES" for t in targets):
                allowed_ids.update(id(n) for n in ast.walk(node.value))

    stray = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value in ("採用", "不採用")
        and id(node) not in allowed_ids
    ]
    assert allowed_ids, "ADOPTION_STATUSES が見つかりません"
    assert stray == [], f"読める値の一覧の外に 採用/不採用 が書かれています(行 {stray})"

    defaulted = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("get", "setdefault", "pop")
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "adoption_status"
        and (len(node.args) > 1 or node.func.attr == "setdefault")
    ]
    assert defaulted == [], f"採否の状態に既定値を埋めています(行 {defaulted})"


def test_every_knowledge_file_in_the_repo_stays_a_candidate() -> None:
    """**リポジトリに置く知識の表は、全件 `候補` のまま。**

    リポジトリに置いてよいのは架空の見本と測定用の書き直しだけで、
    どちらもおーちゃんが採否を決めた表ではない。
    """
    checked = 0
    for path in _repo_files(".json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            continue
        for entry in payload["entries"]:
            if isinstance(entry, dict) and "adoption_status" in entry:
                checked += 1
                assert entry["adoption_status"] == "候補", (
                    f"{path.relative_to(ROOT)} の {entry.get('entry_id')} が "
                    f"{entry['adoption_status']} になっています"
                )

    assert checked >= 3, "見本の 3 件すら見つかっていません"
