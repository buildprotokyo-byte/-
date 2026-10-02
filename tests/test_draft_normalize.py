"""K-63 3 節(b) 自由記述の工事名を語彙の細目 id に寄せる(draft/normalize.py)の試験。合成の行だけで通す。"""

from __future__ import annotations

import copy
import json

import pytest

from draft import designs
from draft.normalize import DEFAULT_CLUES, OTHER, code_of, load_clues, normalize_rows

CLUES = load_clues()
VOCAB_IDS = {s["id"] for s in designs.load_vocab(designs.DEFAULT_VOCAB)["細目"]}
REMOVAL_IDS = {s["id"] for s in designs.load_vocab(designs.DEFAULT_VOCAB)["細目"] if s["科目"] == "撤去"}


def _code(name: str, **extra) -> str:
    return code_of({"工事項目": name, **extra}, CLUES)


def test_every_clue_points_into_the_vocabulary():
    raw = json.loads(DEFAULT_CLUES.read_text(encoding="utf-8"))
    for group in ("撤去", "そのほか"):
        for _, code in raw[group]:
            assert code in VOCAB_IDS, (group, code)
    assert OTHER in VOCAB_IDS
    assert {code for _, code in raw["撤去"]} <= REMOVAL_IDS


@pytest.mark.parametrize("name", ["天井クロス張替", "天井 クロス 張替え", "天井ビニルクロス貼替", "天井ビニールクロス張替",
                                  "天井 ビニール 貼替"])
def test_ceiling_cloth_is_n05(name):
    assert _code(name) == "N05"


@pytest.mark.parametrize("name", ["壁クロス", "壁クロス張替", "壁 ビニルクロス貼替", "壁ビニールクロス張替", "クロス張替"])
def test_wall_cloth_is_n04(name):
    assert _code(name) == "N04"


@pytest.mark.parametrize("a, b", [
    ("壁ビニールクロス張替", "壁ビニルクロス貼替"),
    ("天井ビニールクロス貼替", "天井ビニルクロス張替"),
    ("ビニール幅木", "ビニル巾木"),
    ("壁 ビニール 貼替", "壁 ビニル 張替"),
    ("天井クロス張替", "天井 クロス 張替"),
    ("天井クロス張替", "天井クロス張替"),  # 全角・半角が混じっても同じ(NFKC)
])
def test_paraphrases_get_the_same_id(a, b):
    assert _code(a) == _code(b) != OTHER


@pytest.mark.parametrize("row", [
    {"工事項目": "天井クロス撤去"},
    {"工事項目": "壁クロス", "区分": "撤去"},
    {"工事項目": "フローリング", "科目": "撤去"},
    {"工事項目": "既存 照明器具 撤去"},
    {"工事項目": "キッチン", "区分": "撤去"},
    {"工事項目": "何か分からないもの", "区分": "撤去"},
    {"工事項目": "天井クロス張替", "区分": "撤去"},
])
def test_removal_rows_get_only_removal_ids(row):
    assert code_of(row, CLUES) in REMOVAL_IDS


def test_removal_ids_are_specific():
    assert _code("天井クロス撤去") == "T03"
    assert _code("壁クロス撤去") == "T02"
    assert _code("床フローリング撤去") == "T01"
    assert _code("照明器具撤去") == "T08"


def test_non_removal_rows_never_get_removal_ids():
    for name in ("天井クロス張替", "壁クロス", "照明器具 交換", "キッチン 新設", "床 フローリング張替"):
        assert _code(name, 区分="張替") not in REMOVAL_IDS


@pytest.mark.parametrize("name", ["", "あいうえお", "諸経費", "設計料"])
def test_unmatched_names_are_x99(name):
    assert _code(name) == OTHER == "X99"


def test_none_and_missing_fields_are_x99():
    assert code_of({}, CLUES) == "X99"
    assert code_of({"工事項目": None, "摘要": None}, CLUES) == "X99"


def test_summary_column_is_used():
    assert code_of({"工事項目": "張替", "摘要": "天井 ビニルクロス"}, CLUES) == "N05"


def test_normalize_rows_does_not_change_the_original_rows():
    rows = [
        {"工事項目": "天井クロス張替", "摘要": "", "数量": 12.5, "単位": "m2", "確度": "中", "状態": "観測"},
        {"工事項目": "壁クロス撤去", "区分": "撤去", "数量": None, "単位": "m2"},
        {"工事項目": "諸経費", "数量": 1, "単位": "式"},
    ]
    before = copy.deepcopy(rows)
    out = normalize_rows(rows)
    assert rows == before
    assert all("細目" not in r for r in rows)
    assert [r["細目"] for r in out] == ["N05", "T02", "X99"]
    for o, b in zip(out, before):
        assert o is not b
        assert {k: v for k, v in o.items() if k != "細目"} == b  # 数量・確度・状態などはそのまま


def test_normalize_rows_accepts_given_clues(tmp_path):
    p = tmp_path / "手がかり.json"
    p.write_text(json.dumps({"撤去": [[[], "T02"]], "そのほか": [[["ビニール"], "N04"]]}, ensure_ascii=False),
                 encoding="utf-8")
    clues = load_clues(p)
    out = normalize_rows([{"工事項目": "天井ビニル貼り"}, {"工事項目": "天井クロス"}], clues)
    assert [r["細目"] for r in out] == ["N04", "X99"]
