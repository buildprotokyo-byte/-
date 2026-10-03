"""K-71 作業1: 性質ごとの線・単位の同値・性質の振り分け・理由で合格・確度の測り方・段階 3。

**合成の正解と合成の下書きだけ**で、道具が正しく数えることを確かめる(正解はクラウドで開かない)。
基準は `docs/k71_scoring_lines_criteria.md`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import pc_kit
from draft import scorecard
from estimating import row_nature
from sameness import quantity_verdict, quantity_verdicts

SECRET = "ひみつの品名"


# --------------------------------------------------------------------------- 1 節 線と単位の同値


def test_旧の線は今までどおり():
    assert quantity_verdict(3, 4, "箇所").hit
    assert not quantity_verdict(10.6, 10.0, "m2").hit  # ±5%
    assert quantity_verdict(3, 3, "箇所", unit_b="個").value == "違う"  # 旧は単位の同値なし


@pytest.mark.parametrize("a,b", [("箇所", "個"), ("ヶ所", "台"), ("カ所", "枚"), ("ケ所", "個"), ("台", "枚")])
def test_新の単位の同値(a, b):
    assert quantity_verdict(3, 3, a, unit_b=b, side="新", nature=1).value == "合う"
    assert quantity_verdict(3, 3, a, unit_b=b).value == "違う"


@pytest.mark.parametrize("a,b", [("本", "個"), ("組", "箇所"), ("セット", "台"), ("式", "個"), ("m", "m2"), ("ｍ", "㎡")])
def test_新でも同じにしない単位(a, b):
    assert quantity_verdict(3, 3, a, unit_b=b, side="新", nature=1).value == "違う"


def test_新の線_性質1は10パーセントか1の大きいほう():
    assert quantity_verdict(4, 3, "個", side="新", nature=1).value == "合う"  # 少ないときは ±1
    assert quantity_verdict(5, 3, "個", side="新", nature=1).value == "違う"
    assert quantity_verdict(33, 30, "個", side="新", nature=1).value == "合う"  # ±10% = 3
    assert quantity_verdict(34, 30, "個", side="新", nature=1).value == "違う"


def test_新の線_性質2は10パーセントで合う30パーセントで近い():
    assert quantity_verdict(11, 10, "m2", side="新", nature=2).value == "合う"
    assert quantity_verdict(13, 10, "m2", side="新", nature=2).value == "近い"
    assert quantity_verdict(13.1, 10, "m2", side="新", nature=2).value == "違う"
    assert not quantity_verdict(13, 10, "m2", side="新", nature=2).hit  # 近いは合うに数えない


def test_新の線_性質3は15パーセント_456は数量を見ない():
    assert quantity_verdict(11.5, 10, "m2", side="新", nature=3).value == "合う"
    assert quantity_verdict(11.6, 10, "m2", side="新", nature=3).value == "違う"
    assert quantity_verdict(9, 1, "式", side="新", nature=4).value == "理由で見る"
    assert quantity_verdict(9, 1, "式", side="新", nature=5).value == "理由で見る"
    assert quantity_verdict(9, 1, "箇所", side="新", nature=6).value == "有無だけ"
    assert quantity_verdict(1, 1, "式", side="新").value == "判定しない"  # 性質が無ければ単位で
    assert quantity_verdict(None, 1, "個", side="新", nature=1).value == "比較不能"


def test_旧と新を並べて返す():
    both = quantity_verdicts(10.6, 10, "m2", nature=2)
    assert both["旧"].value == "違う" and both["新"].value == "合う"


def test_許容の数字はquantityの外に置かない():
    """K-71 作業1 b: 許容と単位の同値は quantity_verdict の 1 か所だけ。"""
    for path in ("benchmarks/pc_kit.py", "estimating/row_nature.py", "draft/scorecard.py"):
        text = Path(path).read_text(encoding="utf-8")
        assert "0.15 *" not in text and "0.10 *" not in text and "NEW_COUNT_EQUIVALENT" not in text, path


# --------------------------------------------------------------------------- 2 節 振り分け


@pytest.mark.parametrize("row,source,want", [
    ({"工事項目": "配線の切り回し", "単位": "式"}, "propagation", 6),
    ({"工事項目": "なにか", "単位": "式"}, "standard_rule", 4),
    ({"工事項目": "墨出し", "単位": "式"}, None, 4),
    ({"工事項目": "駐車場代", "単位": "日"}, None, 4),
    ({"工事項目": "荷上げ費", "単位": "式"}, None, 4),
    ({"工事項目": "なにか", "単位": "式", "科目": "諸経費"}, None, 4),
    ({"工事項目": "大工手間", "単位": "人工"}, None, 5),
    ({"工事項目": "取付手間", "単位": "箇所"}, None, 5),  # 揃える前の字で「手間」を探す
    ({"工事項目": "なにか", "単位": "一式"}, "symbol_count", 5),
    ({"工事項目": "壁石膏ボード張り", "単位": "㎡"}, "geometry_derived", 3),
    ({"工事項目": "クロス見切", "単位": "m"}, None, 3),
    ({"工事項目": "コンセント新設", "単位": "ヶ所"}, "explicit_text", 1),
    ({"工事項目": "床フロアタイル張り", "単位": "m2"}, None, 2),
    ({"工事項目": "なにか", "単位": ""}, "symbol_count", 1),
    ({"工事項目": "なにか", "単位": ""}, "geometry_derived", 2),
    ({"工事項目": "なにか", "単位": "kg"}, "explicit_text", "未分類"),
])
def test_振り分けの順(row, source, want):
    got = row_nature.classify(row, source)
    assert got.value == want, got.reason
    assert got.reason


def test_ボードでも個数の単位なら性質1():
    assert row_nature.classify({"工事項目": "点検口ボード", "単位": "箇所"}).value == 1


# --------------------------------------------------------------------------- 正誤表


def _errata() -> dict:
    printed = [f"G{n:03d}" for n in range(300, 314)]
    prop = [f"G{n:03d}" for n in range(320, 329)]
    rows = [{"G": g, "訂正前": "explicit_text", "訂正後": "explicit_text"} for g in printed]
    rows.append({"G": "G330", "訂正前": "explicit_text", "訂正後": "explicit_text", "注記": "辿れない"})
    rows += [{"G": g, "訂正前": "explicit_text", "訂正後": "propagation"} for g in prop]
    return {"説明": "合成", "行": rows}


def test_正誤表の件数は24から15と9():
    counts = row_nature.errata_counts(_errata())
    assert counts == {"訂正前": {"explicit_text": 24}, "訂正後": {"explicit_text": 15, "propagation": 9}}


def test_正誤表に名前の欄があれば止める():
    bad = {"行": [{"G": "G001", "訂正前": "a", "訂正後": "b", "品名": SECRET}]}
    with pytest.raises(ValueError):
        row_nature.errata_map(bad)


def test_正誤表は訂正前と同じ行だけ直す():
    rows = [{"code": "G320", "src": "explicit_text"}, {"code": "G321", "src": "propagation"},
            {"code": "G322", "src": "symbol_count"}, {"code": "G999", "src": "explicit_text"}]
    out = row_nature.apply_errata(rows, _errata(), code_col="code", source_col="src")
    assert out["置き換えた行"] == 1 and out["すでに訂正後だった行"] == 1
    assert out["区分が訂正前とも訂正後とも違った行"] == 1
    assert [r["src"] for r in rows] == ["propagation", "propagation", "symbol_count", "explicit_text"]
    again = row_nature.apply_errata(rows, _errata(), code_col="code", source_col="src")
    assert again["置き換えた行"] == 0  # 2 回当てても変わらない


# --------------------------------------------------------------------------- 3 節 理由


def _item(i, **kw):
    base = {"id": i, "確度": "高", "状態": "観測", "検算": [], "根拠の種類": "図面から読んだ", "理由": "", "ページ": 1}
    base.update(kw)
    return base


def test_理由の4種類():
    by_id = {"a": _item("a", 状態="問い"), "b": _item("b", 検算=["食い違い"]),
             "c": _item("c", 理由="寸法が無い"), "d": _item("d", 理由="原価表が要る"), "e": _item("e")}
    r = lambda ids, **kw: dict({"項目": ids, "数量": 1, "メモ": ""}, **kw)
    assert row_nature.row_reasons(r(["a"]), by_id) == ["未確定"]
    assert row_nature.row_reasons(r(["b"]), by_id) == ["要確認"]
    assert row_nature.row_reasons(r(["e"]), by_id, ["機械と食い違い"]) == ["要確認"]
    assert row_nature.row_reasons(r(["c"], 数量=None), by_id) == ["根拠が図面に無い"]
    assert row_nature.row_reasons(r(["c"]), by_id) == []  # 数量を出しているなら理由の文だけでは「無い」にしない
    assert "会社ルールが要る" in row_nature.row_reasons(r(["d"]), by_id)
    assert row_nature.row_reasons(r(["e"]), by_id) == []
    assert row_nature.row_reasons(r(["e"], 数量=None), by_id) == []  # 未取得でも理由の文が無ければ理由なし


# --------------------------------------------------------------------------- pc_kit k71(合成)


def _gold(tmp_path: Path) -> Path:
    items = [{"code": c, "work_item": "除く式", "unit": "式", "major_category": "仮設工事", "quantity": 1,
              "amount": 100, "expected_source_type": "standard_rule"} for c in pc_kit.EXCLUDED_CODES]
    add = lambda code, name, unit, cat, q, amt, src: items.append(
        {"code": code, "work_item": name, "unit": unit, "major_category": cat, "quantity": q, "amount": amt,
         "expected_source_type": src})
    add("G100", "壁クロス張替", "㎡", "内装仕上工事", 20.0, 1000, "geometry_derived")        # 3
    add("G101", "コンセント新設", "ヶ所", "電気設備工事", 3, 500, "symbol_count")           # 1
    add("G102", "床フロアタイル張替", "m2", "内装仕上工事", 10.0, 800, "geometry_derived")  # 2
    add("G103", "墨出し", "式", "仮設工事", 1, 50, "standard_rule")                         # 4
    add("G104", "大工手間", "人工", "木工事", 2, 300, "explicit_text")                      # 5
    add("G105", "配線切り回し", "式", "電気設備工事", 1, 200, "explicit_text")              # 正誤表で 6
    add("G106", f"{SECRET}A", "kg", "仮設工事", 5, 10, "explicit_text")                    # 未分類
    for n in range(93 - 7):
        add(f"G{200 + n}", f"{SECRET}{n}", "式", "仮設工事", 1, 10, "explicit_text")
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"expected_items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path: Path) -> Path:
    items = [_item("a"), _item("b"), _item("c", 状態="問い", 確度="低"), _item("d", 理由="会社のルールが要る"),
             _item("e"), _item("f"), _item("g", 理由="寸法が図面に無い")]
    row = lambda name, unit, q, ids, cat="内装": {"科目": cat, "区分": "", "工事項目": name, "摘要": "",
                                                 "場所": "", "数量": q, "単位": unit, "項目": ids, "メモ": ""}
    rows = [
        row("壁クロス張替", "㎡", 22.5, ["a"]),            # 性質3 ±15% → 合う(旧 ±5% は違う)
        row("コンセント新設", "個", 4, ["b"]),              # 性質1 ±1 → 合う。単位は 個 と ヶ所(新で同じ)
        row("床フロアタイル張替", "m2", 12.5, ["c"]),      # 性質2 → 近い(理由つき: 未確定)
        row("墨出し", "式", 1, ["d"], cat="仮設"),          # 性質4 → 理由で合格(会社ルール)
        row("大工手間", "人工", 2, ["e"], cat="木工"),      # 性質5 → 理由なし(根拠なしの数量)
        row("配線切り回し", "式", None, ["g"], cat="電気"),  # 性質6 → 有無で当たり
        row("正解に無い何か", "箇所", 3, ["f"]),           # 高だが正解に無い行(誤りに数えない)
    ]
    draft = {"まとめ": {"自動確定": 0}, "理解": {"項目": items}, "読む": {"ページ": {}},
             "機械の検算": {"行ごとの要確認": [[] for _ in rows]},
             "組み立て": {"内訳の行": rows, "段階ごとの出力": {m: {"行の番号": list(range(len(rows)))}
                                                     for m in pc_kit.MODES}}}
    run = tmp_path / "run"
    run.mkdir()
    (run / "下書き.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return run


def _errata_for_gold() -> dict:
    return {"行": [{"G": "G105", "訂正前": "explicit_text", "訂正後": "propagation"}]}


def test_k71_性質ごとに数える(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS, errata=_errata_for_gold())
    assert gold.errata_result["置き換えた行"] == 1
    table = pc_kit.nature_table(gold, pc_kit.natures_of(gold))
    assert table["件数"]["1"] == 1 and table["件数"]["2"] == 1 and table["件数"]["3"] == 1
    assert table["件数"]["4"] == 1 and table["件数"]["6"] == 1
    assert table["件数"]["5"] == 1 + 86  # 大工手間と、式の 86 行
    assert table["未分類の数"] == 1 and table["合計"] == 93
    assert table["未分類の理由(1 行ずつ)"] == ["G106: 単位 kg は数える・測るのどちらでもなく、区分 explicit_text でも決まらない"]

    out = pc_kit.k71(_run(tmp_path), gold)
    per = out["性質ごと"]
    assert per["性質1"]["新: 合う"] == 1 and per["性質1"]["旧: 合う(±1 / ±5%、単位の同値なし)"] == 0
    assert per["性質3"]["新: 合う"] == 1 and per["性質3"]["旧: 合う(±1 / ±5%、単位の同値なし)"] == 0
    assert per["性質2"]["新: 合う"] == 0 and per["性質2"]["新: 近い(±30%)"] == 1
    assert per["性質2"]["合否の割合(新: 合う ÷ 正解の行)"] == {"値": 0.0, "分子": 0, "分母": 1}
    p45 = per["性質4・5"]
    assert (p45["理由で合格"], p45["理由なし"], p45["出していない"]) == (1, 1, 86)
    assert p45["理由なしのうち数量を出していた(根拠なしの数量)"] == 1
    assert per["性質6"]["名前で当たった"] == 1

    conf = out["確度"]
    assert conf["「高」の行"] == 6  # c は低
    assert conf["当たらなかった(正解に無い行。誤りに数えない)"] == 1
    assert conf["名前・有無・状態の的中"]["値"] == 1.0
    assert conf["数量の的中(性質1〜3、新の線)"] == {"値": 1.0, "分子": 2, "分母": 2}
    assert conf["数量の的中(性質1〜3、旧の線)"]["分子"] == 0

    table3 = {r["名前"]: r for r in out["段階3の表"]}
    assert table3["未分類の行"]["いまの値"] == 1 and table3["未分類の行"]["合否"] == "未達"
    assert table3["性質4・5 理由で合格"]["いまの値"] == round(1 / 88, 4)  # 性質4 が 1 行、性質5 が 87 行
    assert out["段階3の判定"]["判定"] != "合格"
    assert out["旧と新"]["細目の数量が合った件数"]["新"] == 2
    assert out["旧と新"]["細目の数量が合った件数"]["旧"] == 1  # 旧は 人工 を ±5% で見て合う(新は性質5 で見ない)
    assert out["自動確定"] == 0
    assert SECRET not in json.dumps(out, ensure_ascii=False)


def test_k71_単位が違う組の診断(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS, errata=_errata_for_gold())
    run = _run(tmp_path)
    draft = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    draft["組み立て"]["内訳の行"][1]["単位"] = "本"  # 個数でも同値の外
    (run / "下書き.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    diag = pc_kit.k71(run, gold)["診断: 数量が合わなかった細目の単位の組(出力 → 正解)"]
    assert diag["旧の正規形"] == {"本 → 箇所": 1}
    assert diag["新の同値を通した後"] == {"本 → 個(個・箇所・台・枚)": 1}


def test_k71_金額は参考単価を借りて被覆と近さ(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS, errata=_errata_for_gold())
    money = pc_kit.k71(_run(tmp_path), gold)["金額(仮)"]
    inner = [r for r in money["科目ごと"] if r["拾えた行"] == 2][0]  # 内装仕上: クロス 1000 + 床 800
    assert inner["被覆"] == 1.0
    # 推し量り = 22.5×50 + 12.5×80 = 2125、正解 1800 → 比 1.1806(±30% の中、±15% の外)
    assert inner["近さ"] == {"±15%": False, "±30%": True, "比": round(2125 / 1800, 4)}
    temp = [r for r in money["科目ごと"] if r["正解の行"] > 80][0]
    assert temp["近さ"] == "被覆が低いので出さない"
    assert SECRET not in json.dumps(money, ensure_ascii=False)
    assert "仮設" not in json.dumps(money, ensure_ascii=False)  # 科目の名前は番号に置き換える


def test_k71_コマンドは正誤表が無ければ止める(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        pc_kit.load_errata(None)


def test_k71_コマンド全体(tmp_path, capsys):
    errata = tmp_path / "正誤表_区分.json"
    errata.write_text(json.dumps(_errata_for_gold(), ensure_ascii=False), encoding="utf-8")
    code = pc_kit.main(["k71", "--golden", str(_gold(tmp_path)), "--run", str(_run(tmp_path)),
                        "--errata", str(errata), "--out", str(tmp_path / "pc2.json")])
    assert code == 0
    out = json.loads((tmp_path / "pc2.json").read_text(encoding="utf-8"))
    assert out["正誤表(機械形だけから数えた件数)"]["訂正後"] == {"propagation": 1}
    assert len(out["回ごと"]) == 1 and out["性質の振り分け"]["合計"] == 93


def test_k71_自動確定が出たら2で止まる(tmp_path):
    run = _run(tmp_path)
    draft = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    draft["まとめ"]["自動確定"] = 1
    (run / "下書き.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    errata = tmp_path / "e.json"
    errata.write_text(json.dumps(_errata_for_gold()), encoding="utf-8")
    assert pc_kit.main(["k71", "--golden", str(_gold(tmp_path)), "--run", str(run), "--errata", str(errata)]) == 2


# --------------------------------------------------------------------------- 出力側の数・段階 3


def test_出力側の数(tmp_path):
    draft = json.loads((_run(tmp_path) / "下書き.json").read_text(encoding="utf-8"))
    out = row_nature.output_counts(draft)
    after = out["後(K-71 の基準)"]
    assert out["前(K-69 の数え方)"]["行の数"] == 7
    assert after["理由つきで「分からない」(数量が未取得で理由つき)"] == 1
    assert after["根拠なしの数量の合計"] == 4  # クロス・コンセント・大工手間・正解に無い行
    assert after["確度「高」の行"] == 6


def test_段階3は性質ごとの行で未取得を0にしない():
    card = scorecard.build()
    names = [r["名前"] for r in card["行"] if r["段階"] == 3]
    assert "性質1 個数(±10%、少ないとき±1)" in names and "性質4・5 理由で合格" in names
    assert "未分類の行" in names and "性質6 波及の有無" in names
    assert all("未取得" in str(r["いまの値"]) for r in card["行"] if r["段階"] == 3)


def test_段階3は未分類があると合格と言わない():
    values = {name: (0 if name == "未分類の行" else 1.0) for stage, name, *_ in scorecard.GOLDEN_ROWS}
    assert scorecard.build(golden=values)["段階ごと"]["段階3 下書き"]["判定"] == "合格"
    values["未分類の行"] = 2
    assert scorecard.build(golden=values)["段階ごと"]["段階3 下書き"]["判定"] != "合格"
    del values["性質3 派生(±15%)"]
    verdict = scorecard.build(golden={**values, "未分類の行": 0})["段階ごと"]["段階3 下書き"]["判定"]
    assert "合格とは言わない" in verdict
