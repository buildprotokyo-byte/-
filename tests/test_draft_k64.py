"""K-64 の試験(確度に読めた割合など)。**合成のデータだけ。実図面は使わない。**"""

from __future__ import annotations

import json

from draft import stages
from draft.run import run
from tests.test_draft_pipeline import FakeClient, _pdf, machine_output  # noqa: F401  (fixture)


def _item(i: str, page: int, conf: str, basis: str = "図面から読んだ") -> dict:
    return {"id": i, "ページ": page, "確度": conf, "根拠の種類": basis}


def test_high_is_lowered_only_on_poorly_read_pages():
    items = [_item("a", 1, "高"), _item("b", 2, "高"), _item("c", 3, "高"), _item("d", 2, "中"),
             _item("e", 2, "高", "人の回答"), _item("f", 4, "高")]
    reading = {"ページ": {1: {"落ちた率": 0.05}, 2: {"落ちた率": 0.4}, 3: {"落ちた率": None},
                          4: {"落ちた率": 0.15}}}
    out = stages.cap_by_page_readability(items, reading)
    conf = {it["id"]: it["確度"] for it in items}
    # 1 ページ(読めた 95%)と 4 ページ(ちょうど 85%)は高のまま、2 ページ(60%)と測れない 3 ページは中へ
    assert conf == {"a": "高", "b": "中", "c": "中", "d": "中", "e": "高", "f": "高"}
    assert out["高から中に下げた項目"] == 2 and out["下限を下回ったページ"] == [2, 3]
    assert "60%" in next(it for it in items if it["id"] == "b")["確度の上限"]
    assert "測れなかった" in next(it for it in items if it["id"] == "c")["確度の上限"]


def test_floor_is_the_fixed_high_miss_line():
    assert stages.PAGE_READ_FLOOR == 0.85


def test_flag_off_keeps_output_and_flag_on_never_raises(tmp_path, machine_output):  # noqa: F811
    pdf = _pdf(tmp_path / "図面.pdf")
    off, on = tmp_path / "off", tmp_path / "on"
    run([str(pdf), "--out", str(off), "--machine-output", str(machine_output)], client=FakeClient())
    code = run([str(pdf), "--out", str(on), "--machine-output", str(machine_output), "--with-page-confidence"],
               client=FakeClient())
    a = json.loads((off / "下書き.json").read_text(encoding="utf-8"))
    b = json.loads((on / "下書き.json").read_text(encoding="utf-8"))
    assert "確度に読めた割合" not in a and "確度に読めた割合" in b
    assert code == 0 and b["まとめ"]["自動確定"] == 0
    assert b["まとめ"]["確度ごと"]["高"] <= a["まとめ"]["確度ごと"]["高"]
    assert b["まとめ"]["項目の数"] == a["まとめ"]["項目の数"]


def _scan_pdf(path):
    """文字の層も図形も無い、画像だけのページの PDF(合成)。"""
    import pymupdf

    src = _pdf(path.with_name("元.pdf"))
    doc = pymupdf.open()
    with pymupdf.open(src) as s:
        for page in s:
            pix = page.get_pixmap(dpi=50)
            new = doc.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=pix)
    doc.save(path)
    return path


def test_scan_pages_are_unmeasured_not_all_missed(tmp_path, machine_output):  # noqa: F811
    pdf = _scan_pdf(tmp_path / "スキャン.pdf")
    out = tmp_path / "出力"
    client = FakeClient()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--with-page-confidence"],
               client=client)
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert code == 0
    pages = r["読む"]["ページ"]
    assert all(v["落ちた率"] is None and v["読み落としの可能性が高い"] for v in pages.values())
    assert "読み直し" not in client.calls  # 測れないページを全部読み直しに回さない
    assert r["まとめ"]["確度ごと"]["高"] == 0
    assert r["読む"]["読み直した後の落ち"]["落ちた率"] is None


def test_ocr_formats_are_read_and_scaled():
    from draft.ocr import load_ocr

    ours = {"ページ": [{"ページ": 1, "幅": 4000, "語": [{"文字": "洋室1", "位置": [100, 200, 300, 260], "確かさ": 0.9}]}]}
    paddle2 = {"ページ": [{"ページ": 2, "幅": 1000, "結果": [[[[10, 20], [50, 20], [50, 40], [10, 40]], ["WD-1", 0.8]]]}]}
    paddle3 = {"ページ": [{"ページ": 3, "幅": 2000, "結果": {"rec_texts": ["天井", " "], "rec_scores": [0.7, 0.1],
                                                            "rec_polys": [[[0, 0], [10, 0], [10, 5], [0, 5]],
                                                                          [[1, 1], [2, 1], [2, 2], [1, 2]]]}}]}
    a, b, c = load_ocr(ours), load_ocr(paddle2), load_ocr(paddle3)
    assert a[1] == [{"文字": "洋室1", "位置": [50.0, 100.0, 150.0, 130.0], "確かさ": 0.9}]
    assert b[2][0]["文字"] == "WD-1" and b[2][0]["位置"] == [20.0, 40.0, 100.0, 80.0]
    assert [w["文字"] for w in c[3]] == ["天井"]  # 空の語は作らない


def test_ocr_only_fills_pages_without_text_layer(tmp_path, machine_output):  # noqa: F811
    pdf = _scan_pdf(tmp_path / "スキャン.pdf")
    # 1 ページ目だけ文字の層がある PDF にする(2・3 ページは画像だけ)
    import pymupdf

    with pymupdf.open(pdf) as doc:
        doc[0].insert_text((50, 50), "文字の層", fontname="japan")
        doc.save(tmp_path / "混在.pdf")
    ocr = tmp_path / "ocr.json"
    ocr.write_text(json.dumps({"ページ": [
        {"ページ": 1, "幅": 2000, "語": [{"文字": "使われない", "位置": [1, 1, 5, 5]}]},
        {"ページ": 2, "幅": 2000, "語": [{"文字": "洋室1", "位置": [90, 90, 300, 130]}]}]}, ensure_ascii=False),
        encoding="utf-8")
    out = tmp_path / "出力"
    client = FakeClient()
    run([str(tmp_path / "混在.pdf"), "--out", str(out), "--machine-output", str(machine_output), "--ocr", str(ocr)],
        client=client)
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["OCR"]["OCR を使ったページ"] == [2]
    assert r["OCR"]["文字の層があるので使わなかったページ"] == [1]
    pending = (out / "AIの答え" / "待っている問い")
    texts = [p.read_text(encoding="utf-8") for p in pending.glob("通読*/指示.md")] if pending.exists() else []
    if texts:  # 鍵の無い道で指示に OCR の語が入る
        assert any("洋室1" in t and "OCR で読んだ文字のページ" in t for t in texts)
    # 渡さなければ今までと同じ(OCR の欄が無い)
    out2 = tmp_path / "出力2"
    run([str(tmp_path / "混在.pdf"), "--out", str(out2), "--machine-output", str(machine_output)], client=FakeClient())
    assert "OCR" not in json.loads((out2 / "下書き.json").read_text(encoding="utf-8"))


def test_ocr_words_go_to_the_reading_request(tmp_path):
    from draft.pages import PageInfo

    page = PageInfo(1, tmp_path / "p1.png", tmp_path / "t.png", "洋室1", 842, 595, False)
    ctx = stages.Context(pdf=tmp_path / "x.pdf", pages=[page], caller=None,
                         ocr={1: [{"文字": "洋室1", "位置": [90.0, 90.0, 300.0, 130.0], "確かさ": 0.9}]})
    data = stages._text_data(ctx, [1])
    assert data["文字の層"]["1"] == "洋室1"
    assert data["OCR で読んだ文字のページ"]["位置つき"]["1"] == [["洋室1", 90, 90, 300, 130]]


def _round_trip(tmp_path, machine_output, answers):  # noqa: F811
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    before = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    path = tmp_path / "答え.json"
    path.write_text(json.dumps(answers(before), ensure_ascii=False), encoding="utf-8")
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--answers", str(path)],
               client=FakeClient())
    return before, json.loads((out / "下書き.json").read_text(encoding="utf-8")), code


def test_answer_with_quantity_changes_rows_and_confidence(tmp_path, machine_output):  # noqa: F811
    def answers(r):
        q = next(it for it in r["理解"]["項目"] if it["状態"] == "問い")
        return {f"項目:{q['id']}": {"選択肢": q["選択肢"][0], "数量": 4, "単位": "枚"}}

    before, after, code = _round_trip(tmp_path, machine_output, answers)
    it = next(i for i in after["理解"]["項目"] if i.get("人の回答"))
    assert (it["数量"], it["単位"], it["確度"], it["状態"], it["根拠の種類"]) == (4.0, "枚", "高", "観測", "人の回答")
    row = next(r for r in after["組み立て"]["内訳の行"] if it["id"] in r["項目"])
    assert row["数量"] == 4.0
    assert after["答えの往復"]["数量・確度が変わった項目"][0]["前"]["数量"] is None
    assert code == 0 and after["まとめ"]["自動確定"] == 0


def test_dont_know_decides_nothing(tmp_path, machine_output):  # noqa: F811
    def answers(r):
        q = next(it for it in r["理解"]["項目"] if it["状態"] == "問い")
        return {f"項目:{q['id']}": "分からない(現地・設計者に確認する)"}

    before, after, _ = _round_trip(tmp_path, machine_output, answers)
    it = next(i for i in after["理解"]["項目"] if "人の回答" in i)
    assert it["状態"] == "問い" and it["確度"] != "高" and it["数量"] is None


def test_quantity_must_be_a_number():
    choice, fields = stages.answer_parts({"選択肢": "洋室1", "数量": "6"})
    assert choice == "洋室1" and "数量" not in fields


def test_out_of_scope_answer_removes_rows(tmp_path, machine_output):  # noqa: F811
    def answers(r):
        c = next(c for c in r["仕上表"]["照らし合わせ"] if c["照らし合わせ"] in ("違う", "近い", "原本のみ", "ひな型のみ")
                 and any(stages._norm_room(it["場所"]) == stages._norm_room(c["室"]) and it["部位"] == c["部位"]
                         for it in r["理解"]["項目"]))
        return {f"仕上:{stages._norm_room(c['室'])}:{c['部位']}": "この室・部位は今回の工事に入らない"}

    before, after, _ = _round_trip(tmp_path, machine_output, answers)
    gone = after["答えの往復"]["内訳から外した項目"]
    assert gone and all(not (set(r["項目"]) & set(gone)) for r in after["組み立て"]["内訳の行"])
    assert len(after["組み立て"]["外した行"]) == len(gone)


def test_original_answer_does_not_confirm_the_drawing_reading(tmp_path, machine_output):  # noqa: F811
    keys = {}

    def answers(r):
        c = next(c for c in r["仕上表"]["照らし合わせ"] if c["照らし合わせ"] in ("違う", "近い")
                 and any(stages._norm_room(it["場所"]) == stages._norm_room(c["室"]) and it["部位"] == c["部位"]
                         for it in r["理解"]["項目"]))
        keys["k"] = f"仕上:{stages._norm_room(c['室'])}:{c['部位']}"
        return {keys["k"]: f"原本(仕上表)のとおり: {c['原本']}"}

    before, after, _ = _round_trip(tmp_path, machine_output, answers)
    assert keys["k"] in after["答えの往復"]["戻した鍵"]
    assert after["答えの往復"]["数量・確度が変わった項目"] == []
    assert before["まとめ"]["確度ごと"] == after["まとめ"]["確度ごと"]


# --- 周 5: 原価表の入力口 ---------------------------------------------------------------


def test_cost_table_formats(tmp_path):
    from draft.cost_table import load_cost_table

    csv_path = tmp_path / "原価表.csv"
    csv_path.write_text("工事項目,品番,単位,単価,数量\n建具,WD-1,枚,\"12,000\",3\n清掃,,式,5000,\n", encoding="utf-8")
    t = load_cost_table(csv_path)
    assert t["形"] == "CSV" and t["行"][0] == {"工事": "建具", "品番": "WD-1", "単位": "枚", "単価": 12000.0,
                                               "数量": 3.0, "科目": ""}
    assert t["行"][1]["数量"] is None  # 読めない数量は 0 にしない
    assert load_cost_table({"WD-1": 100})["形"] == "単価だけ"
    assert load_cost_table({"行": [{"工事": "床", "単価": "x"}]})["行"][0]["単価"] is None
    assert load_cost_table(None) is None


def test_unit_mismatch_does_not_match():
    from draft.cost_table import match

    t = {"行": [{"工事": "床", "品番": "", "単位": "㎡", "単価": 10.0, "数量": 5.0, "科目": ""}]}
    assert match(t, "", "床", "㎡") is not None and match(t, "", "床", "m") is None and match(t, "", "床", "") is not None


def test_question_amount_uses_table_quantity_only_for_order():
    from draft.cost_table import question_amount

    t = {"行": [{"工事": "床", "品番": "", "単位": "㎡", "単価": 10.0, "数量": 5.0, "科目": ""}]}
    items = {"a": {"工事": "床", "品番": "", "単位": "㎡", "数量": None}, "b": {"工事": "床", "品番": "", "単位": "㎡", "数量": 2}}
    amount, why = question_amount({"関係する項目": ["a"]}, items, t)
    assert amount == 50.0 and "並べ方のためだけ" in why and items["a"]["数量"] is None
    assert question_amount({"関係する項目": ["b"]}, items, t)[0] == 20.0
    assert question_amount({"関係する項目": ["a"]}, items, None) == (None, "原価表 未取得")


def test_cost_table_orders_questions_and_compares_without_rewriting(tmp_path, machine_output):  # noqa: F811
    pdf = _pdf(tmp_path / "図面.pdf")
    base = tmp_path / "なし"
    run([str(pdf), "--out", str(base), "--machine-output", str(machine_output)], client=FakeClient())
    r0 = json.loads((base / "下書き.json").read_text(encoding="utf-8"))
    works = sorted({it["工事"] for it in r0["理解"]["項目"] if it["工事"]})
    table = [{"工事": w, "単位": "", "単価": 1000 * (i + 1), "数量": 7} for i, w in enumerate(works)]
    path = tmp_path / "原価表.json"
    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "あり"
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--cost-table", str(path)],
               client=FakeClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert code == 0 and r["まとめ"]["自動確定"] == 0 and r["まとめ"]["原価表"].startswith("あり")
    assert "金額の大きい順" in r["質問"]["並べ方"]
    for mode, qs in r["質問"]["段階ごと"].items():
        known = [q["金額"] for q in qs if q["金額"] is not None]
        assert known == sorted(known, reverse=True)
        assert all(q["金額"] is not None for q in qs[:len(known)])  # 未取得は後ろ
    # 内訳の数量は原価表で書き換えない
    assert [x["数量"] for x in r["組み立て"]["内訳の行"]] == [x["数量"] for x in r0["組み立て"]["内訳の行"]]
    cmp_ = r["組み立て"]["原価表との比べ"]
    assert "正解として使わない" in cmp_["注"] and cmp_["並べた行"]
    assert "原価表との比べ" not in r0["組み立て"]
