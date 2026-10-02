"""K-66 の部品(`sameness/`)を固定する。

**この試験の値打ちは「囮が 1 件も通らない」を固定することにある**(K-66 3 節 a の線)。
通してよいものを増やす変更を入れると、ここが落ちる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sameness import (
    GRAIN,
    HIGHER_LOWER,
    INCOMPARABLE,
    NOT_SAME,
    SAME,
    compare,
    quantity_verdict,
    structure_key,
)
from sameness.decoys import decoy_pairs, paraphrase_pairs
from sameness.normalize import canonical_unit, flatten, room_key
from sameness.terms import load_terms


# ---- (a) 機械で揃える ----

@pytest.mark.parametrize(
    "left,right",
    [
        ("洋室(1)", "洋室1"),
        ("洋室（１）", "洋室1"),
        ("壁クロス貼替え", "壁クロス張替"),
        ("床タイル張り(材工共)", "床タイル張"),
        ("壁のクロス張り 手間のみ", "壁のクロス張"),
        ("ビニル幅木", "ビニル巾木"),
    ],
)
def test_同じ書き方に揃う(left: str, right: str) -> None:
    assert flatten(left) == flatten(right)


@pytest.mark.parametrize("left,right", [("洋室(1)", "洋室2"), ("外壁", "内壁"), ("床", "壁")])
def test_違うものは揃わない(left: str, right: str) -> None:
    assert flatten(left) != flatten(right)


def test_単位の正規形() -> None:
    assert canonical_unit("㎡") == canonical_unit("m2") == canonical_unit("m²") == "m2"
    assert canonical_unit("か所") == canonical_unit("ヶ所") == "箇所"
    assert canonical_unit("m") != canonical_unit("m2")


def test_室名の漢数字() -> None:
    assert room_key("洋室一") == room_key("洋室(1)") == "洋室1"


# ---- (b) 辞書 ----

def test_辞書は差し替えられる(tmp_path: Path) -> None:
    path = tmp_path / "別の辞書.json"
    path.write_text(
        json.dumps({"科目": [{"id": "X", "代表": "ぜんぶ", "語": ["木工事", "電気設備"]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    terms = load_terms(path)
    assert terms.relation("科目", terms.find("科目", "木工事"), terms.find("科目", "電気設備")) == "同じ"
    assert compare("木工事", "電気設備", level="科目", terms=terms).value == SAME


def test_外壁と内壁は壁の下位で互いは別() -> None:
    terms = load_terms()
    assert terms.relation("部位", "BU_壁", "BU_外壁") == "上位"
    assert terms.relation("部位", "BU_外壁", "BU_内壁") == "別"


# ---- (c) 構造のキー ----

def test_キーは4つを取る() -> None:
    key = structure_key("壁のビニルクロス張替")
    assert key.工事の種類 == "N04"
    assert key.部位 == "BU_壁"
    assert key.状態 == "JO_張替"
    assert key.材料 == "ZA_クロス"


def test_手がかりの受け皿は使わない() -> None:
    """`笠木撤去` と `とい撤去` が同じキーになってはいけない(別の工事である)。"""
    assert structure_key("笠木撤去").工事の種類 is None
    assert compare("笠木撤去", "とい撤去").value == INCOMPARABLE


# ---- (1) 判定の 5 値 ----

def test_おーちゃんが挙げた対が通る() -> None:
    assert compare("大工工事", "木工事", level="科目").value == SAME


def test_材料の言い換えは辞書が吸収する() -> None:
    """`ビニルクロス` と `クロス` は平らにしても同じにならない(書き方ではなく語が違う)。

    **揃えるのは辞書の仕事**で、機械で揃える層は触らない。"""
    assert flatten("壁ビニルクロス張替") != flatten("壁クロス張替")
    assert compare("壁ビニルクロス張替", "壁クロス張替").value == SAME


def test_五値がそろう() -> None:
    assert compare("壁クロス張替", "壁ビニルクロス貼替").value == SAME
    assert compare("床仕上の撤去", "壁仕上の撤去").value == NOT_SAME
    assert compare("笠木撤去", "とい撤去").value == INCOMPARABLE
    assert compare("外壁の塗装", "壁の塗装").value == GRAIN
    assert compare({"科目": "内装"}, "壁のクロス張").value == HIGHER_LOWER


def test_格上げは呼ぶ側が渡したときだけ() -> None:
    verdict = compare("外壁の塗装", "壁の塗装")
    assert verdict.value == GRAIN
    assert verdict.promote(totals_match=False, place_match=True).value == GRAIN
    assert verdict.promote(totals_match=True, place_match=True).value == SAME


def test_理由が必ず付く() -> None:
    for left, right in (("大工工事", "木工事"), ("床仕上の撤去", "壁仕上の撤去"), ("笠木撤去", "とい撤去")):
        assert compare(left, right, level="科目" if "工事" in left else "細目").reason


# ---- 数量 ----

def test_数量の許容差() -> None:
    assert quantity_verdict(3, 4, "箇所").hit
    assert not quantity_verdict(3, 5, "箇所").hit
    assert quantity_verdict(10.4, 10.0, "m2").hit
    assert not quantity_verdict(12.0, 10.0, "m2").hit
    assert quantity_verdict(1, 1, "式").value == "判定しない"
    assert quantity_verdict(None, 1, "m2").value == "比較不能"
    assert quantity_verdict(10, 10, "m2", unit_b="m").value == "違う"


# ---- (3) 線: 囮が 1 件も通らない ----

def test_囮は1件も通らない() -> None:
    """K-66 3 節 a の線。**0%。1 件でも通ったら規則が不合格。**"""
    通った = []
    for pair in decoy_pairs():
        verdict = compare(pair.left, pair.right, level=pair.level)
        name_ok = verdict.hit
        if pair.unit:
            name_ok = name_ok and quantity_verdict(pair.left_quantity, pair.right_quantity, pair.unit).hit
        if name_ok:
            通った.append((pair.種類, pair.left, pair.right))
    assert 通った == []


def test_言い換えの合格率は測った値を下回らない() -> None:
    """2026-10-02 に測った 32/36。**下がる変更が入ったら落ちる。**"""
    合格 = sum(1 for p in paraphrase_pairs() if compare(p.left, p.right, level=p.level).hit)
    assert 合格 >= 32


def test_表記を揃える処理は1か所だけ() -> None:
    """**K-66 の条件 1(おーちゃん)**: 名前を揃える処理の 2 つ目を作らない。

    `draft/normalize.py` は K-63(#246)で入った層で、同じ表記のゆれ(ビニール→ビニル、
    貼→張、幅木→巾木)を自分で持っていた。**決める場所が 2 か所あると、
    片方だけ直したときに黙って食い違う。**そこで `sameness.flatten` に寄せた。

    寄せても判定が動かないことは P011 の内訳 2,209 行で測ってある(細目 id の変化 0 件)。
    """
    import re

    import draft.normalize as dn
    from sameness import flatten

    source = Path(dn.__file__).read_text(encoding="utf-8")

    assert dn._flat("ビニール壁紙貼り") == flatten("ビニール壁紙貼り")
    # **`_flat` の中に表記のゆれの表を書き戻していないこと。**
    body = source.split("def _flat")[1].split("\ndef ")[0]
    assert "replace(" not in body
    assert not re.search(r"ビニール|幅木", body.split('"""')[-1])
