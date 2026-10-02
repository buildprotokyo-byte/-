"""K-68 B 質問の作り方を直す。**基準は `docs/k68_b_questions_criteria.md`(周ごとに測る前にコミット)。**

合成のデータだけを使う(実案件も正解も使わない)。
"""

from __future__ import annotations

import copy

from draft import uncertainty


def _item(item_id: str, **kw):
    base = {
        "id": item_id, "ページ": 1, "要素": ["e1", "e2"], "位置": [], "囲み": [0, 0, 10, 10],
        "読み取った値": "", "何": "もの", "部位": "床", "場所": "洋室1", "区分": "新設",
        "工事": "床張替", "科目": "内装", "品番": "", "数量": 1.0, "単位": "m2", "式": "",
        "状態": "観測", "確度": "中", "根拠の種類": "図面から読んだ", "理由": "", "選択肢": [],
        "検算": [],
    }
    base.update(kw)
    return base


# 周 1 3 回の割れを K-66 の鍵で揃える
# ---------------------------------------------------------------------------


def test_既定の鍵はK66の鍵() -> None:
    assert uncertainty.DEFAULT_KEY_RULE == "新"
    from sameness.rows import agreement_key

    it = _item("a", 工事="壁クロス張替", 部位="壁")
    assert uncertainty.key_of(it) == agreement_key(it)


def test_言い換えは新規則では割れと言わない() -> None:
    """旧規則では文字が違うので割れ。**新規則では構造のキーが同じなので割れない。**"""
    run1 = [_item("a", 工事="壁クロス張替", 部位="壁", 数量=20.0)]
    run2 = [_item("b", 工事="壁ビニルクロス貼替", 部位="壁", 数量=20.0)]

    assert uncertainty.readings_disagree([run1, run2], rule="旧")
    assert not uncertainty.readings_disagree([run1, run2], rule="新")


def test_新規則でも別の工事_別の室_許容差の外_欠けた回は割れ() -> None:
    base = _item("a", 工事="壁クロス張替", 部位="壁", 数量=20.0)
    for other in (_item("b", 工事="床フローリング張", 部位="床", 数量=20.0),
                  _item("b", 工事="壁クロス張替", 部位="壁", 場所="洋室2", 数量=20.0),
                  _item("b", 工事="壁クロス張替", 部位="壁", 数量=30.0)):
        assert uncertainty.readings_disagree([[base], [other]], rule="新"), other
    assert uncertainty.readings_disagree([[base], []], rule="新"), "出てこなかった回も割れ"


def test_同じ回の中で同じ鍵の2行は足してから比べる() -> None:
    """K-66 の鍵は品名の文字を持たないので、1 回の中で 2 行が同じ鍵になることがある。"""
    run1 = [_item("a", 工事="壁クロス張替", 部位="壁", 数量=10.0),
            _item("a2", 工事="壁ビニルクロス貼替", 部位="壁", 数量=5.0)]
    run2 = [_item("b", 工事="壁クロス張替", 部位="壁", 数量=15.0)]

    assert not uncertainty.readings_disagree([run1, run2], rule="新")


def test_同じ回の中で数量の有る行と無い行が混ざったら割れ_無い数量を0にしない() -> None:
    run1 = [_item("a", 工事="壁クロス張替", 部位="壁", 数量=10.0),
            _item("a2", 工事="壁ビニルクロス貼替", 部位="壁", 数量=None)]
    run2 = [_item("b", 工事="壁クロス張替", 部位="壁", 数量=10.0)]

    assert uncertainty.readings_disagree([run1, run2], rule="新")


def test_鍵の和を一緒に返す() -> None:
    """**一致の割合を書くときは鍵の和を必ず並べる**(K-68 の決まり)。"""
    run1 = [_item("a", 工事="壁クロス張替", 部位="壁")]
    run2 = [_item("b", 工事="壁クロス張替", 部位="壁"), _item("c", 工事="床フローリング張", 部位="床")]

    out = uncertainty.classify(run1, other_runs=[run2])
    assert out["鍵の和"] == 2
    assert out["全部の回に出た鍵"] == 1
    assert out["鍵の規則"] == "新"


def test_旧規則はK65のまま残る() -> None:
    run1 = [_item("a", 数量=10.0)]
    run2 = [_item("b", 数量=30.0)]

    assert uncertainty.readings_disagree([run1, run2], rule="旧") == {uncertainty.item_key(run1[0])}


def test_鍵を替えても項目の値は書き換えない() -> None:
    items = [_item("a", 工事="壁クロス張替", 部位="壁")]
    other = [[_item("b", 工事="壁ビニルクロス貼替", 部位="壁")]]
    before = copy.deepcopy(items)

    uncertainty.classify(items, other_runs=other, rule="新")
    assert items == before


def test_鍵の規則は2つだけ() -> None:
    import pytest

    with pytest.raises(ValueError):
        uncertainty.key_of(_item("a"), "どちらでもない")


def test_K66の囮は新規則で割れと言う() -> None:
    """合成の囮(部位・状態・材料の入れ替えと数量違い)。**見逃しの数は測定で書く。**

    ここでは部位と状態の入れ替え、数量違いが割れになることだけ固定する。
    """
    from sameness.decoys import decoy_pairs

    for pair in decoy_pairs():
        if pair.種類 not in ("1 部位の入れ替え", "2 状態の入れ替え", "5 数量違い"):
            continue
        unit = pair.unit or "m2"
        # 区分・科目の欄は空にする(欄があると、K-66 の鍵は品名より欄を先に読むため)。
        left = _item("a", 工事=pair.left, 部位="", 区分="", 科目="", 数量=pair.left_quantity or 10.0, 単位=unit)
        right = _item("b", 工事=pair.right, 部位="", 区分="", 科目="", 数量=pair.right_quantity or 10.0,
                      単位=unit)
        assert uncertainty.readings_disagree([[left], [right]], rule="新"), pair
