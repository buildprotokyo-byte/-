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
