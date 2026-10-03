"""K-68 C 周 4: OCR の速さを測る道具。**偽のエンジンだけ(PaddleOCR は入れない)。**"""

from __future__ import annotations

import json
import time

import pymupdf
import pytest

from benchmarks import k68_ocr_speed as sp
from draft.ocr import load_ocr


def _pdf(path):
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page(width=842, height=595)
        if i < 2:
            page.draw_rect(pymupdf.Rect(50, 50, 400, 300))  # 文字の層は無い(スキャンの代わり)
    doc.save(path)
    return path


def _v2(path):
    return [[[[10, 10], [110, 10], [110, 40], [10, 40]], ["洋室1", 0.98]],
            [[[200, 10], [300, 10], [300, 40], [200, 40]], ["CH=2400", 0.9]]]


def _v3(path):
    return {"rec_texts": ["洋室1"], "rec_scores": [0.97], "rec_polys": [[[10, 10], [110, 10], [110, 40], [10, 40]]]}


@pytest.mark.parametrize("engine,words", [(_v2, 2), (_v3, 1)])
def test_output_loads_in_the_pipeline_form(tmp_path, engine, words):
    pdf = _pdf(tmp_path / "a.pdf")
    calls = []

    def fake(path):
        calls.append(path.name)
        return [] if "白紙の囮" in path.name else engine(path)

    res = sp.measure(pdf, lambda: (fake, "偽"), tmp_path / "w")
    out = tmp_path / "ocr.json"
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    loaded = load_ocr(out)
    assert sorted(loaded) == [1, 2, 3]
    assert all(len(loaded[n]) == words for n in loaded)
    m = res["まとめ"]
    assert m["ページ数"] == 3 and m["白紙の囮の語の数"] == 0
    assert m["墨のあるページ"] == 2 and m["語が取れたページ(墨のあるページのうち)"] == 2
    assert m["文字の層の無いページ"] == 2
    # 慣らしの 1 回 + 3 ページ + 囮
    assert calls[0] == "p1.png" and len(calls) == 5


def test_time_excludes_loading_and_warmup(tmp_path):
    pdf = _pdf(tmp_path / "a.pdf")
    state = {"n": 0}

    def make():
        time.sleep(0.2)  # 読み込み
        return engine, "偽"

    def engine(path):
        state["n"] += 1
        time.sleep(0.15 if state["n"] == 1 else 0.02)  # 1 回目(慣らし)だけ遅い
        return _v2(path)

    res = sp.measure(pdf, make, tmp_path / "w")
    m = res["まとめ"]
    assert m["読み込みの秒"] >= 0.2 and m["慣らしの秒"] >= 0.15
    assert all(0.02 <= r["秒"] < 0.15 for r in res["ページ"])
    assert m["合計の秒(読み込み・慣らしを除く)"] == pytest.approx(sum(r["秒"] for r in res["ページ"]), abs=0.01)
