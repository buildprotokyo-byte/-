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


# 周 2 状態・有無・まとめ方・科目は固定の選択肢+「どれでもない」
# ---------------------------------------------------------------------------

from draft import answers_io, cards, chain, questioning  # noqa: E402


def _q(item_id: str, **kw):
    base = {"鍵": f"項目:{item_id}", "種類": "決められなかった所", "科目": "内装", "問い": "?",
            "選択肢": ["AI が書いた候補 1", "AI が書いた候補 2"], "見る所": [1], "関係する項目": [item_id],
            "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}
    base.update(kw)
    return base


def _cards_for(items, questions):
    graph = chain.build(items)
    classified = uncertainty.classify(items)
    return cards.build_cards(questions, items, graph, classified)


def test_4つの型は固定の選択肢とどれでもないだけ() -> None:
    items = [_item("a", 状態="問い"),  # 工事の有無
             _item("b", 状態="問い", 工事="床撤去"),  # 状態
             _item("c", 状態="問い", 科目="未確定")]  # どの科目か
    built = _cards_for(items, [_q("a"), _q("b"), _q("c")])
    by_type = {c["型"]: c for c in built["カード"]}

    assert set(by_type) == {"工事の有無", "状態", "どの科目か"}
    for type_, card in by_type.items():
        assert card["選択肢"] == [*cards.FIXED_OPTIONS[type_], cards.NONE_OF_THESE]
        assert not any("AI が書いた" in o for o in card["選択肢"])
        assert card["数字の入力"] is False and card["自由記述"] is False and card["推奨"] is None


def test_まとめ方の固定の選択肢() -> None:
    assert cards.fixed_options("まとめ方") == ["1 行にまとめる", "分けて数える", cards.NONE_OF_THESE]


def test_固定の型で選択肢が違えば無効() -> None:
    card = {"鍵": "項目:a", "型": "状態", "問い": "?", "選択肢": ["撤去", "新設"], "見る所": [1],
            "切り抜き": {"ページ": 1}, "直接": ["a"]}
    assert "固定の選択肢と違う" in cards.invalid_reasons(card)


def test_読めなかった所は段の閉じた選択肢のまま() -> None:
    from draft.stages import UNREADABLE_OPTIONS

    q = {"鍵": "読めない:1:1", "種類": "読めなかった所", "科目": "未確定", "問い": "?",
         "選択肢": list(UNREADABLE_OPTIONS), "見る所": [1], "関係する項目": [],
         "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}
    built = _cards_for([_item("a")], [q])

    assert built["カード"][0]["選択肢"] == list(UNREADABLE_OPTIONS)


def test_抜き取りと枠の問いも有無の固定の選択肢() -> None:
    assert list(cards.PRESENCE_OPTIONS) == cards.fixed_options("工事の有無")
    frames = cards.frame_cards({"枠": [{"枠": "左官", "状態": "記載が見当たらない",
                                       "分からないこと": {"理由": ["記載が見当たらない"], "候補ページ": [3]}}]})
    assert frames[0]["選択肢"] == cards.fixed_options("工事の有無")


def test_科目の型は連鎖を辿らない() -> None:
    assert chain.TRAVERSAL["どの科目か"] == ()


def _apply_one(type_item, choice):
    items = [type_item]
    built = _cards_for(items, [_q(type_item["id"])])
    card = built["カード"][0]
    understanding = {"項目": copy.deepcopy(items)}
    finish = {"照らし合わせ": []}
    out = answers_io.apply(understanding, finish, [card], {card["鍵"]: choice})
    return card, understanding["項目"][0], out


def test_固定の選択肢の答えは黙って捨てない() -> None:
    """**どの選択肢を答えても、決める・外す・書くだけ・分からない のどれかになる。**"""
    for item in (_item("a", 状態="問い"), _item("b", 状態="問い", 工事="床撤去"),
                 _item("c", 状態="問い", 科目="未確定")):
        card, _it, _out = _apply_one(item, "ある")
        for choice in card["選択肢"]:
            _card, it, out = _apply_one(item, choice)
            assert not out["戻せなかった答え"], (card["型"], choice)
            assert it.get("人の回答"), (card["型"], choice)


def test_ないは消さずに外す印を付ける() -> None:
    _card, it, out = _apply_one(_item("a", 状態="問い"), "ない")

    assert it["外す"] == "人の回答: ない"
    assert out["固定の選択肢で外した項目"] == ["a"]


def test_科目の答えは科目欄だけを決める() -> None:
    _card, it, _out = _apply_one(_item("c", 状態="問い", 科目="未確定", 数量=None), "内装")

    assert it["科目"] == "内装"
    assert it["根拠の種類"] == "人の回答"
    assert it["数量"] is None, "数量は決めない(未取得を 0 にしない)"


def test_どれでもないは何も決めない() -> None:
    _card, it, _out = _apply_one(_item("a", 状態="問い"), cards.NONE_OF_THESE)

    assert it["状態"] == "問い"
    assert "外す" not in it


def test_抜き取りの答えで確定の項目を書き換えない() -> None:
    items = [_item("a")]
    card = cards.spot_check_cards(["a"], items)[0]
    understanding = {"項目": copy.deepcopy(items)}
    answers_io.apply(understanding, {"照らし合わせ": []}, [card], {card["鍵"]: "ない"})

    assert "外す" not in understanding["項目"][0]
    assert understanding["項目"][0]["抜き取りの回答"] == "ない"


def test_室が未確定なら科目より先に室を聞く() -> None:
    built = _cards_for([_item("a", 状態="問い", 場所="未確定", 科目="未確定")], [_q("a")])

    assert built["カード"][0]["型"] == "どの室・部位か"


def test_仕様書と図面の問いはAIの選択肢のまま() -> None:
    """固定の選択肢にするのは 4 つの型だけ(仕様書と図面のどちらを採るか は原本の文字が選択肢)。"""
    built = _cards_for([_item("a", 状態="問い")], [_q("a", 種類="原本との違い")])

    assert built["カード"][0]["型"] == "仕様書と図面のどちらを採るか"
    assert built["カード"][0]["選択肢"][:2] == ["AI が書いた候補 1", "AI が書いた候補 2"]
