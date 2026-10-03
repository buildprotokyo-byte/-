"""K-73 作業1・2: 旗オンの見方と、内訳の足し上げを入れた採点(原則 11)。

**合成の正解と合成の下書きだけ**で確かめる(正解はクラウドで開かない)。基準は `docs/k73_flags_sum_criteria.md`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import pc_kit
from estimating import summed_scoring as ss

SECRET = "ひみつの品名"


def _row(name, unit, q, ids, place="", cat="内装", kubun=""):
    return {"科目": cat, "区分": kubun, "工事項目": name, "摘要": "", "場所": place, "数量": q, "単位": unit,
            "項目": ids, "メモ": ""}


# --------------------------------------------------------------------------- 3.1 足し上げ(正解を使わない)


def test_同じ鍵の行は室をまたいで足し上げ_内訳は残る():
    rows = [_row("コンセント新設", "個", 1, ["a"], "洋室1", "電気"), _row("コンセント新設", "箇所", 2, ["b"], "洋室2", "電気")]
    groups = ss.group_rows(rows)
    assert len(groups) == 1  # 個 と 箇所 は新の同値で同じ単位
    g = groups[0]
    assert g.total == 3 and g.status == ss.TOTAL
    assert [p["場所"] for p in g.parts()] == ["洋室1", "洋室2"]
    line = ss.group_line(g)
    assert line["数量"] == 3 and line["内訳の行の番号"] == [0, 1] and len(line["内訳"]) == 2


def test_未取得が1つでもあれば合計を出さない_0にしない():
    rows = [_row("コンセント新設", "個", 1, ["a"], "洋室1", "電気"), _row("コンセント新設", "個", None, ["b"], "洋室2", "電気")]
    g = ss.group_rows(rows)[0]
    assert g.total is None and g.status == ss.HAS_MISSING and g.missing == 1
    assert ss.group_line(g)["数量"] is None
    assert ss.group_line(g)["合計の状態"] == "未取得あり"


@pytest.mark.parametrize("u1,u2", [("m", "m2"), ("個", "本"), ("式", "個"), ("組", "個")])
def test_単位が違えば別の組(u1, u2):
    rows = [_row("ソフト巾木交換", u1, 1, ["a"]), _row("ソフト巾木交換", u2, 2, ["b"])]
    assert len(ss.group_rows(rows)) == 2


def test_工事の種類が取れない行は品名が丸ごと同じときだけまとめる():
    rows = [_row(f"{SECRET}X", "式", 1, ["a"]), _row(f"{SECRET}X", "式", 1, ["b"]), _row(f"{SECRET}Y", "式", 1, ["c"])]
    assert sorted(len(g.rows) for g in ss.group_rows(rows)) == [1, 2]


def test_出力側の数():
    rows = [_row("コンセント新設", "個", 1, ["a"], "洋室1", "電気"), _row("コンセント新設", "個", None, ["b"], "洋室2", "電気"),
            _row("ソフト巾木交換", "m", 3, ["c"], "洋室1"), _row("ソフト巾木交換", "m", 4, ["d"], "洋室2")]
    c = ss.output_counts(rows)
    assert c["組の数"] == 2 and c["2 行以上の組"] == 2 and c["合計が出た組"] == 1 and c["未取得ありの組"] == 1
    assert c["未取得ありの組に入った数量のある行"] == 1 and c["うち 2 行以上を足した組"] == 1


def test_囮は同じ単位の中で入れ替え_未取得の位置は動かさない():
    rows = [_row("a", "個", 1, []), _row("b", "個", 2, []), _row("c", "個", None, []), _row("d", "m", 5, [])]
    d = ss.decoy_rows(rows)
    assert d[2]["数量"] is None and d[3]["数量"] == 5
    assert sorted(r["数量"] for r in d[:2]) == [1, 2]
    assert rows[0]["数量"] == 1  # 元は変えない


# --------------------------------------------------------------------------- 2.1 旗の見方


def _flag_draft():
    rows = [_row("床フロアシート張替", "m2", None, ["u1"], "洗面室"),      # 埋める
            _row("床フロアシート張替", "m2", 5.0, ["u2"], "トイレ"),       # 数量があるので書き換えない
            _row("スイッチ交換", "個", None, ["u3", "u4"], "廊下", "電気"),  # 2 部品から → 使わない
            _row("ガス種", "個", None, ["u5"]),                            # またがる足したもの → 使わない
            _row("建具", "箇所", None, ["u6"])]
    add = lambda i, part_q, unit, rel, st="推論", conf="低": {"id": i, "工事": "x", "場所": "", "数量": part_q, "単位": unit,
                                                           "状態": st, "確度": conf, "理解の項目": rel, "数量が増える": True}
    parts = {
        "縮尺で長さを測る": {"足したもの": [add("f-len-001", 3.62, "m2", ["u1"]), add("f-len-002", 5.5, "m2", ["u2"])]},
        "記号を室ごとに数える": {"足したもの": [add("f-sym-001", 2.0, "個", ["u3"], "仮説"), add("f-sym-002", 1.0, "個", ["u5", "u6"]),
                                         add("f-sym-003", 4.0, "個", ["zz"]),
                                         dict(add("f-sym-004", 9.0, "個", ["u6"]), 数量が増える=False)]},
        "分かれ道を AI に選択肢で聞く": {"足したもの": [add("f-br-001", 3.0, "個", ["u4"], "推論", "中")]},
    }
    items = [{"id": f"u{n}", "確度": "中", "状態": "推論", "検算": [], "根拠の種類": "図面から読んだ", "理由": ""}
             for n in range(1, 7)]
    return {"まとめ": {"自動確定": 0}, "理解": {"項目": items}, "読む": {"ページ": {}},
            "機械の検算": {"行ごとの要確認": [[] for _ in rows]},
            "組み立て": {"内訳の行": rows}, "旗の部品": {"部品": parts}}


def test_旗オフは組み立ての行そのまま():
    d = _flag_draft()
    view = ss.flag_view(d, "旗オフ")
    assert view["行"] == d["組み立て"]["内訳の行"]


def test_旗オン埋めるは数量の無い行だけに入れ_元は変えない():
    d = _flag_draft()
    view = ss.flag_view(d, "旗オン(埋める)")
    rows = view["行"]
    assert rows[0]["数量"] == 3.62 and rows[0]["旗で埋めた"] and "f-len-001" in rows[0]["項目"]
    assert rows[1]["数量"] == 5.0  # 書き換えない
    assert rows[2]["数量"] is None  # 部品をまたぐ
    assert rows[3]["数量"] is None and rows[4]["数量"] is None  # またがる足したもの
    assert len(rows) == 6 and rows[5]["数量"] == 4.0  # 結べなかったものは後ろに
    c = view["数"]
    assert (c["数量が入った行"], c["数量のある行に結ばれた(書き換えない)"], c["部品をまたいで使わなかった行"]) == (1, 1, 1)
    assert c["2 行以上にまたがって使わなかった足したもの"] == 1 and c["結べずに後ろに足した行"] == 1
    assert d["組み立て"]["内訳の行"][0]["数量"] is None  # 元の下書きは変えない


def test_旗オン足すは後ろに足すだけ():
    d = _flag_draft()
    view = ss.flag_view(d, "旗オン(足す)")
    assert view["行"][:5] == d["組み立て"]["内訳の行"]
    assert len(view["行"]) == 5 + 6  # 数量が増える 6 件


def test_旗が足したものは確度高と観測にならない():
    items = ss.flag_items(_flag_draft())
    assert {v["確度"] for v in items.values()} <= {"中", "低"}
    assert {v["状態"] for v in items.values()} <= {"推論", "仮説"}


# --------------------------------------------------------------------------- 3.2 正解との突き合わせ(合成)


def _gold(tmp_path: Path) -> Path:
    items = [{"code": c, "work_item": "除く式", "unit": "式", "major_category": "仮設工事", "quantity": 1,
              "amount": 100, "expected_source_type": "standard_rule"} for c in pc_kit.EXCLUDED_CODES]
    add = lambda code, name, unit, cat, q, amt, src: items.append(
        {"code": code, "work_item": name, "unit": unit, "major_category": cat, "quantity": q, "amount": amt,
         "expected_source_type": src})
    add("G100", "コンセント新設", "ヶ所", "電気設備工事", 3, 500, "symbol_count")       # 1: 室ごと 1+2 で合う
    add("G101", "ソフト巾木", "m", "内装仕上工事", 20.0, 400, "geometry_derived")      # 3: 粒度の格上げ 12+8
    add("G102", "スイッチ交換", "ヶ所", "電気設備工事", 4, 300, "symbol_count")         # 1: 未取得あり
    add("G103", "墨出し", "式", "仮設工事", 1, 50, "standard_rule")                     # 4
    for n in range(93 - 4):
        add(f"G{200 + n}", f"{SECRET}{n}", "式", "仮設工事", 1, 10, "explicit_text")
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"expected_items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path: Path) -> Path:
    items = [{"id": i, "確度": "高", "状態": "観測", "検算": [], "根拠の種類": "図面から読んだ", "理由": "", "ページ": 1}
             for i in "abcdefg"]
    items[6]["理由"] = "会社のルールが要る"
    rows = [
        _row("コンセント新設", "個", 1, ["a"], "洋室1", "電気"),
        _row("コンセント新設", "個", 2, ["b"], "洋室2", "電気"),
        _row("巾木交換", "m", 12.0, ["c"], "洋室1", "内装仕上工事"),
        _row("巾木交換", "m", 8.0, ["d"], "洋室2", "内装仕上工事"),
        _row("スイッチ交換", "個", 4, ["e"], "廊下", "電気"),
        _row("スイッチ交換", "個", None, ["f"], "洗面室", "電気"),
        _row("墨出し", "式", 1, ["g"], cat="仮設"),
    ]
    draft = {"まとめ": {"自動確定": 0}, "理解": {"項目": items}, "読む": {"ページ": {}},
             "機械の検算": {"行ごとの要確認": [[] for _ in rows]},
             "組み立て": {"内訳の行": rows, "段階ごとの出力": {m: {"行の番号": list(range(len(rows)))}
                                                     for m in pc_kit.MODES}}}
    run = tmp_path / "run"
    run.mkdir()
    (run / "下書き.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return run


def test_k71に足し上げを入れると室ごとの行の合計で比べる(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    run = _run(tmp_path)
    old = pc_kit.k71(run, gold)
    new = pc_kit.k71(run, gold, summed=True)
    # 旧: 1 行 ↔ 1 行。コンセントは 1 個の行が当たって「違う」、スイッチは 4 個の行が当たって「合う」
    assert old["性質ごと"]["性質1"]["新: 合う"] == 1
    # 新: コンセントは 1+2=3 で合う、スイッチは未取得ありで比べない(0 として足さない)
    p1 = new["性質ごと"]["性質1"]
    assert p1["新: 合う"] == 1 and p1["新: 比較不能(未取得)"] == 1
    assert p1["うち未取得あり(足し上げで合計を出さなかった)"] == 1
    assert new["足し上げ"]["格上げで当たった"] == 1  # 巾木 12+8=20 が △粒度 → ○
    assert new["性質ごと"]["性質3"]["新: 合う"] == 1 and old["性質ごと"]["性質3"]["新: 合う"] == 0
    assert new["性質ごと"]["性質4・5"]["理由で合格"] == 1
    assert SECRET not in json.dumps(new, ensure_ascii=False)


def test_格上げは合計が線の外なら当たりにしない(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    run = _run(tmp_path)
    d = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    d["組み立て"]["内訳の行"][3]["数量"] = 30.0  # 12 + 30 = 42 は 20 の ±15% の外
    (run / "下書き.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    new = pc_kit.k71(run, gold, summed=True)
    assert new["足し上げ"]["格上げで当たった"] == 0
    assert new["性質ごと"]["性質3"]["当たった"] == 0


def test_格上げは未取得ありなら合計を出さず当たりにしない(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    run = _run(tmp_path)
    d = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    d["組み立て"]["内訳の行"][3]["数量"] = None
    (run / "下書き.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    new = pc_kit.k71(run, gold, summed=True)
    assert new["足し上げ"]["格上げで当たった"] == 0
    assert new["足し上げ"]["格上げの候補が未取得ありで止まった"] == 1


def test_性質45は内訳の全部に理由があるときだけ合格():
    by_reason = {0: ["未確定"], 1: []}
    line = {"内訳の行の番号": [0, 1]}
    assert ss.all_reasons(line, [{}, {}], lambda r, n: by_reason[n]) == []
    assert ss.all_reasons({"内訳の行の番号": [0]}, [{}], lambda r, n: by_reason[n]) == ["未確定"]


def test_k73は旗オフとオンと足し上げの旧新を並べる(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    run = _run(tmp_path)
    d = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    d["組み立て"]["内訳の行"][5]["項目"] = ["f"]
    d["旗の部品"] = {"部品": {"記号を室ごとに数える": {"足したもの": [
        {"id": "f-sym-001", "工事": "スイッチ交換", "場所": "洗面室", "数量": 0.0, "単位": "個", "状態": "仮説", "確度": "低",
         "理解の項目": ["f"], "数量が増える": True}]}}}
    (run / "下書き.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = pc_kit.k73(run, gold)
    assert set(out["見方"]) == set(ss.VIEWS)
    table = {(r["見方"], r["採点"]): r for r in out["要約"]}
    assert len(table) == 6
    # 旗で洗面室のスイッチが 0 個と入り、足し上げで 4+0=4 になって合う(旗オフでは未取得あり)
    assert table[("旗オフ", "新(足し上げ)")]["個数: 合う"] == 1
    assert table[("旗オン(埋める)", "新(足し上げ)")]["個数: 合う"] == 2
    assert table[("旗オン(埋める)", "新(足し上げ)")]["格上げで当たった"] == 1
    assert "囮(新、数量を入れ替え)" in out["見方"]["旗オフ"]
    assert out["自動確定"] == 0


def test_k73出力側は正解を使わない(tmp_path):
    out = pc_kit.k73_output_side(_run(tmp_path))
    assert out["見方"]["旗オフ"]["足し上げ"]["組の数"] == 4
    assert out["見方"]["旗オフ"]["足し上げ"]["未取得ありの組"] == 1


def test_k73コマンド(tmp_path):
    errata = tmp_path / "正誤表_区分.json"
    errata.write_text(json.dumps({"行": []}), encoding="utf-8")
    run = _run(tmp_path)
    code = pc_kit.main(["k73", "--golden", str(_gold(tmp_path)), "--run", str(run),
                        "--errata", str(errata), "--out", str(tmp_path / "pc2.json")])
    assert code == 0
    out = json.loads((tmp_path / "pc2.json").read_text(encoding="utf-8"))
    assert out["回ごと"][0]["対象"].endswith("run")
    assert pc_kit.main(["k73-出力側", "--run", str(run)]) == 0


# --------------------------------------------------------------------------- 作業1 旗オンの出力を作る道具


def test_旗オンの出力は旗の部品以外の欄を変えない():
    from benchmarks import k73_flags_on as fo

    before = {"まとめ": {"自動確定": 0}, "組み立て": {"内訳の行": [{"数量": None}]}}
    after = dict(before, 旗の部品={"部品": {}})
    assert fo.unchanged(before, after) == []
    changed = dict(after, 組み立て={"内訳の行": [{"数量": 1}]})
    assert fo.unchanged(before, changed) == ["組み立て"]


def test_旗オンの道具はページ番号の鍵を数に戻す():
    from benchmarks import k73_flags_on as fo

    draft = {"整理": {"ページ": {"1": {"種類": "平面図"}}}, "読む": {"読み": {"2": {}}, "ページ": {"3": {}}},
             "理解": {}, "仕上表": {}, "組み立て": {}}
    org, reading, *_ = fo.restore(draft)
    assert list(org["ページ"]) == [1] and list(reading["読み"]) == [2] and list(reading["ページ"]) == [3]
