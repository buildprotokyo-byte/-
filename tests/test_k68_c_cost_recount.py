"""K-68 C 周 3: 費用の見立ての数え直しの道具。**合成のデータだけ。**"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks import k68_cost_recount as kc


def test_kind_rule_order_and_unknown():
    assert kc.kind_by_rule("内部仕上表 平面図") == "仕上表"
    assert kc.kind_by_rule("図面リスト 仕上表") == "表紙・図面リスト"
    assert kc.kind_by_rule("ＸＹＺ") == kc.NO_KIND


def test_organize_agreement_with_decoy():
    texts = {1: "仕上表", 2: "平面図", 3: "展開図", 4: "凡例"}
    ai = {1: "仕上表", 2: "平面図", 3: "展開図", 4: "凡例"}
    r = kc.organize_agreement(texts, ai)
    assert r["一致"] == 1.0 and r["囮"] < 1.0


def test_text_matches_rules():
    assert kc.text_matches("洋室 1", "洋室1")
    assert kc.text_matches("CH=2400", "CH=2400m")  # 長さの比 7/8 >= 0.8
    assert not kc.text_matches("CH=2400", "CH=2400mm")  # 7/9 < 0.8
    assert not kc.text_matches("CH", "CH=2400mm")
    assert not kc.text_matches("", "x") and not kc.text_matches("x", "")


def test_reading_share_and_decoy():
    words = {1: [("洋室1", [10, 10, 50, 20])], 2: [("廊下", [10, 10, 50, 20])]}
    reading = {1: {"要素": [{"種類": "文字", "内容": "洋室1", "位置": [10, 10, 50, 20]},
                           {"種類": "線", "内容": "壁", "位置": [0, 0, 100, 1]}]},
               2: {"要素": [{"種類": "文字", "内容": "廊下", "位置": [10, 10, 50, 20]}]}}
    r = kc.reading_share(reading, words)
    assert r["出せる"] == 2 and r["囮で出せる"] == 0 and 0 < r["出せる割合"] < 1 and r["囮"] == 0


def test_finish_share():
    rows = [{"ページ": 1, "室": "洋室1", "仕上": "クロス"}, {"ページ": 1, "室": "廊下", "仕上": "ビニル床"}]
    r = kc.finish_share(rows, {1: "洋室1 クロス 廊下", 2: "なし"})
    assert r["割合"] == 0.5 and r["囮"] == 0.0


def test_stage_costs_skip_missing():
    recs = [{"段": "通読", "答えの出どころ": "置かれた答え", "モデル": "claude-opus-5-5",
             "入力トークンの目安": 1_000_000, "出力トークンの目安": 1_000_000},
            {"段": "通読", "答えの出どころ": "未取得", "モデル": "claude-opus-5-5",
             "入力トークンの目安": 1_000_000, "出力トークンの目安": 0}]
    c = kc.stage_costs(recs)["通読"]
    assert c["回数"] == 2 and c["入力"] == pytest.approx(4.0) and c["出力"] == pytest.approx(20.0)


def test_recount_subtracts_only_passed_stages(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page(width=842, height=595)
    p.insert_text((50, 50), "ABC", fontsize=20)
    doc.new_page(width=842, height=595).insert_text((50, 300), "XYZ", fontsize=20)
    pdf = tmp_path / "a.pdf"
    doc.save(pdf)
    from draft.pages import words_in_image

    with pymupdf.open(pdf) as d:
        box = list(words_in_image(d.load_page(0))[0][1])
    rec = [{"段": s, "答えの出どころ": "置かれた答え", "モデル": "claude-opus-5-5",
            "入力トークンの目安": 1000, "出力トークンの目安": 1000} for s in ("整理", "通読", "理解")]
    res = {"AI を呼んだ記録": {"1回ずつ": rec}, "整理": {"ページ": {"1": {"種類": "平面図"}, "2": {"種類": "平面図"}}},
           "読む": {"読み": {"1": {"要素": [{"種類": "文字", "内容": "ABC", "位置": box}]}}},
           "仕上表": {"原本の行": []}}
    out = kc.recount(pdf, [res])
    v = out["線を通ったか(①で置き換えてよいか)"]
    assert v["整理"] is False and v["理解"] is False and v["仕上表の原本"] is False
    run = out["回ごと"][0]
    assert run["① 普通のプログラム"]["段ごと 前→後"]["理解"][0] == run["① 普通のプログラム"]["段ごと 前→後"]["理解"][1]
    assert all(x == kc.UNMEASURED for x in run["② 専用の小さなモデル"].values())
