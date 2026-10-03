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
from sameness.terms import default_terms, load_terms


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
    """2026-10-02 に測った 32/36(外れ 4 件まで)。**下がる変更が入ったら落ちる。**

    K-70(b)(c) でおーちゃんが「同じにしない」と決めた 11 対(`K70_RULED_NOT_SAME`)は
    正しい言い換えではなくなったので、**残りの 25 対で外れ 4 件まで**(25 − 4 = 21)を見る。
    決めた 11 対は、逆に **1 件も `○` にならない**ことを確かめる(下の K-70 のテスト)。
    """
    from sameness.decoys import K70_RULED_NOT_SAME

    ruled = set(K70_RULED_NOT_SAME)
    rest = [p for p in paraphrase_pairs() if (p.left, p.right) not in ruled]
    assert len(rest) == 25
    合格 = sum(1 for p in rest if compare(p.left, p.right, level=p.level).hit)
    assert 合格 >= 21


def test_K70_同じにしないと決めた対は丸にならない() -> None:
    """K-70(b)(c): おーちゃんが「同じにしない」と決めた対。**1 件でも `○` なら辞書の分け方が間違い。**"""
    from sameness.decoys import K70_RULED_NOT_SAME

    from sameness.decoys import PARAPHRASES

    levels = {(l, r): lv for l, r, lv in PARAPHRASES}
    丸 = [(l, r) for l, r in K70_RULED_NOT_SAME if compare(l, r, level=levels[(l, r)]).hit]
    assert 丸 == []


@pytest.mark.parametrize("left, right", [
    ("内装工事", "内装仕上工事"), ("造作工事", "大工工事"), ("LGS工事", "軽天・ボード工事"),
    ("美装", "清掃"), ("清掃", "クリーニング"), ("美装", "クリーニング"), ("産廃処分", "発生材処理"),
    ("吸音", "断熱"), ("サッシ工事", "建具工事"), ("床工事", "内装工事"),
    ("諸経費", "現場管理費"), ("諸経費", "一般管理費"), ("諸経費", "共通仮設費"),
    ("電気空調設備工事", "設備工事"), ("共通仮設", "仮設工事"),
])
def test_K70b_足すが同じにしない科目(left: str, right: str) -> None:
    terms = default_terms()
    a, b = terms.find("科目", left), terms.find("科目", right)
    assert a and b, (left, right)
    assert terms.relation("科目", a, b) != "同じ"
    assert compare(left, right, level="科目").value != SAME


@pytest.mark.parametrize("left, right", [
    ("解体工事", "解体・撤去工事"), ("大工工事", "木工事"), ("木工事", "木工・大工工事"),
    ("内装仕上工事", "内装仕上げ工事"), ("建具工事", "建具改修"), ("電気工事", "電気設備工事"),
    ("給排水設備工事", "給排水衛生設備工事"), ("仮設工事", "直接仮設"), ("塗装工事", "塗装改修"),
])
def test_K70a_同じ意味の科目(left: str, right: str) -> None:
    assert compare(left, right, level="科目").value == SAME


def test_K70a_墨出費は墨出し() -> None:
    from sameness.keys import structure_key

    assert structure_key("墨出費").工事の種類 == structure_key("墨出し").工事の種類 == "K02"


@pytest.mark.parametrize("left, right", [
    ("解体", "撤去"), ("撤去", "取外し"), ("取外し", "脱着"), ("設置", "取付"), ("取付", "施工"),
    ("設置", "施工"), ("交換", "更新"), ("更新", "改修"), ("交換", "改修"),
    ("既存利用", "既存流用"), ("既存流用", "残し"), ("既存利用", "残し"), ("移設", "脱着"),
])
def test_K70c_状態の語は別の状態(left: str, right: str) -> None:
    terms = default_terms()
    a, b = terms.find("状態", left), terms.find("状態", right)
    assert a and b and a != b, (left, right, a, b)


def test_K70c_新設と新規は状態コードが共通の候補() -> None:
    terms = default_terms()
    assert terms.find("状態", "新設") == terms.find("状態", "新規") == "JO_新設"


def test_K70c_解体と取外しも撤去の手がかりで細目を引くが状態は別() -> None:
    from sameness.keys import structure_key

    a, b = structure_key("壁ボード撤去"), structure_key("壁ボードの解体")
    assert a.工事の種類 == b.工事の種類 and a.工事の種類.startswith("T")
    assert a.状態 != b.状態
    assert compare("壁ボード撤去", "壁ボードの解体").value != SAME


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


def test_K68_A_材料と部位から細目を引く橋() -> None:
    from sameness.keys import structure_key

    assert structure_key("床クッションフロア張").工事の種類 == structure_key("床ビニル床シート張").工事の種類 == "N01"
    assert structure_key("床長尺シート張").工事の種類 == structure_key("床CF張り").工事の種類 == "N01"
    assert "橋:材料+部位" in structure_key("床ビニル床シート張").由来
    # 材料だけ違えば別の細目(囮の材料の入れ替えが通らない)
    assert structure_key("床フロアタイル張").工事の種類 != structure_key("床クッションフロア張").工事の種類
    # 撤去のときは橋を使わない
    assert "橋:材料+部位" not in structure_key("床長尺シート撤去").由来
