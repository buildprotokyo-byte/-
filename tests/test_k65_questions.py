"""K-65 質問を多用する道。**基準は `docs/k65_question_curve_criteria.md`(測る前にコミット)。**

固定したい約束。

1. **信号は AI の確度を見ない。**確度を「高」にしても 3 状態は動かない。
2. **連鎖は保守的。**深さの上限 3 を越えて「決まった」と言わない。
3. **カードだけで答えられない質問は無効。**数字の入力・自由記述・推奨・通読は通さない。
4. **機械の推す答えをカードに入れない。**
5. **見込み精度は未較正なので出さない。**
6. **答えを戻した再実行で AI を呼ばない。**影響範囲だけ数え直す。
7. **質問数の上限を置かない。**止め線は時間だけ。
"""

from __future__ import annotations

import json

from draft import answers_io, cards, chain, questioning, uncertainty


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


# 1 信号は確度を見ない
# ---------------------------------------------------------------------------


def test_確度を高にしても3状態は動かない() -> None:
    """**これが K-65 でいちばん大事な約束。**自己申告が自分を確定させてはいけない。"""
    low = uncertainty.classify([_item("a", 確度="低")])
    high = uncertainty.classify([_item("a", 確度="高")])

    assert low["3状態の分布"] == high["3状態の分布"]
    assert low["項目ごと"][0]["信号"] == high["項目ごと"][0]["信号"]


def test_根拠が1つなら確定と言わない() -> None:
    rows = uncertainty.classify([_item("a", 要素=["e1"])])["項目ごと"]

    assert rows[0]["3状態"] == uncertainty.UNSETTLED
    assert "根拠の要素が 1 つ以下" in rows[0]["理由"]


def test_信号は9つだけ() -> None:
    assert len(uncertainty.SIGNALS) == 9


def test_数量が無ければ未確定() -> None:
    rows = uncertainty.classify([_item("a", 数量=None)])["項目ごと"]

    assert "数量が取れない" in rows[0]["信号"]
    assert rows[0]["3状態"] == uncertainty.UNSETTLED


def test_3回の読みで割れたら信号が立つ() -> None:
    run1 = [_item("a", 数量=10.0)]
    run2 = [_item("b", 数量=30.0)]
    run3 = [_item("c", 数量=10.0)]

    rows = uncertainty.classify(run1, other_runs=[run2, run3])["項目ごと"]

    assert "3回の読みの割れ" in rows[0]["信号"]


def test_許容差の中なら割れとは言わない() -> None:
    """個数 ±1・連続量 ±5%(K-66 の許容差を 1 か所から使う)。"""
    run1 = [_item("a", 数量=100.0)]
    run2 = [_item("b", 数量=103.0)]

    assert uncertainty.readings_disagree([run1, run2]) == set()


def test_仮説の印は外さない() -> None:
    rows = uncertainty.classify([_item("a", 状態="仮説")])["項目ごと"]

    assert rows[0]["3状態"] == uncertainty.HYPOTHESIS


# 2 連鎖は保守的
# ---------------------------------------------------------------------------


def test_深さの上限を越えたら連鎖とは言わない() -> None:
    items = [_item("a", 工事="撤去", 部位="床"), _item("b", 工事="新設", 部位="床"),
             _item("c", 工事="下地", 部位="床")]
    graph = chain.build(items)

    assert graph.closure(["a"], max_depth=1, kinds=["状態"]) == {"b": 1, "c": 1}
    assert graph.closure(["a"], max_depth=0, kinds=["状態"]) == {}


def test_型が決められない種類の辺は辿らない() -> None:
    """**K-65 追記 1。**同じ科目でつながった辺を辿ると「1 問で全部決まる」という嘘が出る。

    いまのカードの型に「どの科目か」を聞く型は無いので、`科目` の辺を辿る型は 1 つも無い。
    """
    items = [_item(f"a{i}", 工事=f"工事{i}", 場所=f"室{i}") for i in range(10)]
    graph = chain.build(items)

    assert graph.closure(["a0"]) , "科目の辺は張られている(グラフには在る)"
    for card_type in chain.TRAVERSAL:
        assert "科目" not in chain.TRAVERSAL[card_type]
    assert chain.decided_by(graph, ["a0"], card_type="工事の有無")["連鎖"] == []


def test_室が未確定の項目に室の辺を張らない() -> None:
    items = [_item("a", 場所="未確定"), _item("b", 場所="未確定")]
    graph = chain.build(items)

    assert all(kind != "室部位" for children in graph.edges.values() for _c, kind in children)


def test_辺の種類は6つだけ() -> None:
    items = [_item("a"), _item("b", 工事="壁張替", 部位="壁")]
    graph = chain.build(items)
    kinds = {kind for children in graph.edges.values() for _c, kind in children}

    assert kinds <= set(chain.EDGE_KINDS)


def test_連鎖は直接と分けて返す() -> None:
    items = [_item("a"), _item("b")]
    graph = chain.build(items)

    out = chain.decided_by(graph, ["a"])

    assert out["直接"] == ["a"]
    assert "a" not in out["連鎖"]


# 3 カードの決まり
# ---------------------------------------------------------------------------


def _card(**kw):
    base = {"鍵": "k", "型": "工事の有無", "問い": "?", "選択肢": ["ある", "ない"],
            "見る所": [1], "切り抜き": {"ページ": 1}, "直接": ["a"],
            "数字の入力": False, "自由記述": False, "推奨": None}
    base.update(kw)
    return base


def test_数字の入力があるカードは無効() -> None:
    assert "数字の入力欄がある" in cards.invalid_reasons(_card(数字の入力=True))


def test_自由記述と推奨があるカードは無効() -> None:
    assert cards.invalid_reasons(_card(自由記述=True))
    assert cards.invalid_reasons(_card(推奨="たぶん 3 枚"))


def test_見る所が4ページ以上なら無効() -> None:
    """**資料を通読しないと答えられない問いは、カードとして出さない。**"""
    assert any("通読" in r for r in cards.invalid_reasons(_card(見る所=[1, 2, 3, 4])))


def test_選択肢が1つなら無効() -> None:
    assert "選択肢が 2 個未満" in cards.invalid_reasons(_card(選択肢=["ある"]))


def test_切り抜きが無ければ無効() -> None:
    assert "切り抜きが無い" in cards.invalid_reasons(_card(切り抜き=None))


def test_型は8つだけ() -> None:
    """K-65 の 7 つに、K-68 B 周 2 で `どの科目か` を足した(科目も固定の選択肢で聞くため)。"""
    assert len(cards.TYPES) == 8
    assert set(cards.SECONDS) == set(cards.TYPES)


def test_決めると確定するを言い分ける() -> None:
    """**K-65 追記 2。**欄が埋まることと、3 状態が確定に移ることは別。

    P011 で 32 問すべてに答えても確定に移ったのは 1 項目だったので、
    メーターは「決める」と書き、但し書きを必ず付ける。
    """
    graph = chain.build([_item("a")])
    classified = uncertainty.classify([_item("a")])
    built = cards.build_cards(
        [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
          "選択肢": ["ある", "ない"], "見る所": [1], "関係する項目": ["a"],
          "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}],
        [_item("a")], graph, classified)
    meter = built["カード"][0]["メーター"]

    assert "決める項目数" in meter
    assert "確定する項目数" not in meter
    assert "「確定」になることではない" in meter["但し書き"]


def test_見込み精度は出さない() -> None:
    """**未較正のものを画面に出さない。**出すのは構造から出る数字だけ。"""
    graph = chain.build([_item("a")])
    classified = uncertainty.classify([_item("a")])
    built = cards.build_cards(
        [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
          "選択肢": ["ある", "ない"], "見る所": [1], "関係する項目": ["a"],
          "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}],
        [_item("a")], graph, classified)

    meter = built["カード"][0]["メーター"]
    assert meter["見込み精度の変化"].startswith("出さない")
    assert meter["回答時間の見積は較正済みか"] is False


def test_機械の推す答えはカードに入らない() -> None:
    graph = chain.build([_item("a", 数量=3.0)])
    classified = uncertainty.classify([_item("a", 数量=3.0)])
    built = cards.build_cards(
        [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
          "選択肢": ["3 枚", "4 枚"], "見る所": [1], "関係する項目": ["a"],
          "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}],
        [_item("a", 数量=3.0)], graph, classified)
    card = built["カード"][0]

    assert card["推奨"] is None
    assert all("推" not in r.get("工事", "") for r in card["競っている読みの候補"])
    assert "機械の推す答え" in "".join(built["但し書き"])


def test_原価表が無ければ金額ではなく項目数で出す() -> None:
    rows = cards.cumulative([{"鍵": "k", "メーター": {"決める項目数": {"合計": 1},
                                                 "回答時間の見積(秒)": 20,
                                                 "決める金額": "未取得(原価表なし)"},
                              "連鎖": {"直接": ["a"], "連鎖": []}}], total=None, item_total=4)

    assert rows[0]["割合の中身"] == "項目数(金額ではない)"
    assert rows[0]["決める項目の割合"] == 0.25


def test_止め線は時間だけで数の上限は無い() -> None:
    assert cards.STOP_LINES == (300, 600, 900)
    rows = cards.cumulative([{"鍵": f"k{i}", "メーター": {"決める項目数": {"合計": 1},
                                                     "回答時間の見積(秒)": 100,
                                                     "決める金額": "未取得"},
                              "連鎖": {"直接": [f"a{i}"], "連鎖": []}} for i in range(12)],
                            total=None, item_total=12)

    assert cards.within(rows, 300) == 3
    assert len(rows) == 12  # **上限で切っていない**


# 5 答えを戻す口
# ---------------------------------------------------------------------------


def test_同じ鍵に違う答えは矛盾() -> None:
    found = answers_io.contradictions(
        [{"鍵": "k", "選択肢": "ある"}, {"鍵": "k", "選択肢": "ない"}],
        [_card()], [_item("a")])

    assert [c["型"] for c in found] == ["同じ鍵に違う答え"]


def test_入らないと採るの両方は矛盾() -> None:
    found = answers_io.contradictions(
        [{"鍵": "k1", "選択肢": "この室・部位は今回の工事に入らない"},
         {"鍵": "k2", "選択肢": "図面の読みのとおり: 床タイル"}],
        [_card(鍵="k1", 室="洋室1", 部位="床"), _card(鍵="k2", 室="洋室1", 部位="床")],
        [_item("a")])

    assert [c["型"] for c in found] == ["入らないと採るの両方"]


def test_矛盾の型は3つだけ() -> None:
    assert len(answers_io.CONTRADICTIONS) == 3


def test_影響範囲は案件全体にならない() -> None:
    items = [_item(f"a{i}", 工事=f"工事{i}", 場所=f"室{i}", 部位="床") for i in range(10)]
    graph = chain.build(items)

    out = answers_io.affected(graph, [{"鍵": "k", "選択肢": "ある"}],
                              [_card(鍵="k", 直接=["a0"])])

    assert out["数え直した割合"] is not None
    assert out["数え直した割合"] < 1.0


def test_変わった項目を一覧にする() -> None:
    items = [_item("a", 数量=1.0)]
    before = answers_io.snapshot(items)
    items[0]["数量"] = 3.0

    out = answers_io.changed(before, items)

    assert out[0]["変わった欄"]["数量"] == {"前": 1.0, "後": 3.0}


def test_答えのファイルは読めない行を落として理由を残す(tmp_path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"回答": [{"鍵": "k", "選択肢": "ある", "秒": 12},
                                       {"選択肢": "ある"}, {"鍵": "k2"}]},
                               ensure_ascii=False), encoding="utf-8")

    out = answers_io.load(path)

    assert out["答えた数"] == 1
    assert [b["理由"] for b in out["読めなかった行"]] == ["鍵が無い", "選択肢が無い"]


def test_間違いの検出割合は0でもそのまま出す() -> None:
    out = answers_io.wrong_answer_detection([{"鍵": "k"}], ["k"], [])

    assert out["見つけた割合"] == 0.0
    assert "見つけられない間違いがある" in out["但し書き"]


# 全体
# ---------------------------------------------------------------------------


def test_数量の候補が無いカードは捨てる_数字を打たせない() -> None:
    """**数量の値が 1 つも無いときは、数字の入力に逃げずにカードを捨てる。**

    選択肢が「どれでもない」1 つだけになるので `選択肢が 2 個未満` で無効。
    """
    items = [_item("a", 数量=None)]
    out = questioning.build(
        {"候補": [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
                  "選択肢": ["ある", "ない"], "見る所": [1], "関係する項目": ["a"],
                  "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}]},
        {"項目": items}, None)

    assert out["カード"] == []
    assert out["捨てたカード"][0]["型"] == "数量"
    assert "選択肢が 2 個未満" in out["捨てたカード"][0]["理由"]


def test_質問の段はAIを呼ばない() -> None:
    """**カードを作るのは機械だけ。**(KQ に答えるために図面を読み返さない)"""
    items = [_item("a", 数量=3.0, 場所="未確定"), _item("b", 工事="壁撤去", 部位="壁")]
    out = questioning.build(
        {"候補": [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
                  "選択肢": ["洋室1", "洋室2"], "見る所": [1], "関係する項目": ["a"],
                  "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}]},
        {"項目": items}, None)

    assert out["聞かない(影響小)"] == ["項目:a"], "1 項目しか決まらないので影響小(原価表なしの線)"
    assert "質問数の上限は置いていない(時間の止め線だけ)" in out["但し書き"]


def test_連鎖で2つ以上決まるなら聞く() -> None:
    """**影響小の線は「連鎖で決まる項目が 1 個だけ」**(原価表が無い案件)。"""
    items = [_item("a", 場所="未確定", 工事="床張替"), _item("b", 場所="未確定", 工事="床張替")]
    out = questioning.build(
        {"候補": [{"鍵": "項目:a", "種類": "決められなかった所", "科目": "内装", "問い": "?",
                  "選択肢": ["洋室1", "洋室2"], "見る所": [1], "関係する項目": ["a", "b"],
                  "位置": [{"ページ": 1, "位置": [0, 0, 1, 1]}]}]},
        {"項目": items}, None)

    assert [c["型"] for c in out["カード"]] == ["どの室・部位か"]
    assert out["聞かない(影響小)"] == []
