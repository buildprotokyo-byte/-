"""スキャンされたページから文字を読む層(`axes/image_axis/ocr_text.py`)の試験。

この層が守るべきこと(本物の OCR を実測して決めた。
`docs/ocr_scanned_pages_report.md` 2節)は 6 つ。

1. **スキャンのページを画像にして OCR に渡し、結果をページ座標(ポイント)で返す。**
   既存の読み取り経路(`pdf_tables`・`pdf_vector_symbols`)がポイントで
   位置を持っているので、ここだけ画素のままにすると突き合わせられない。
2. **確信度の低い読みは既定値で埋めず落とし、落としたことを残す。**
3. **確信度は文字化けの見張りにならない。** 実測で `種別` → `种别`(確信度
   0.99)、`引戸` → `引户`(0.97)。だから確信度だけを根拠にしない設計にする。
4. **エンジンを 2 つ渡したときは、両方の読みが一致した語だけを通す。**
   食い違った語は捨てずに `conflicts` に残す。**どちらかを選ばない。**
   実測では、中国語モデルは数字を正しく読んで日本語の語を化けさせ、
   日本語モデルは語を正しく読んで数字を化けさせた。片方だけを信じると、
   もっともらしい誤りが黙って下流に入る。
5. **読めた語が 0 件であることを「文字が無い」と言わない。**
6. **エンジンが 1 つも無ければ例外にする。** 黙って空を返すと、
   「OCR を掛けたが何も無かった」と区別できない。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from axes.image_axis.ocr_text import (
    OcrUnavailable,
    OcrWord,
    normalize_ocr_text,
    recognize_page,
)
from axes.image_axis.pdf_pages import rasterize
from tests.scan_fixtures import (
    SCAN_DPI,
    ScriptedOcrBackend,
    build_vector_pdf,
    scan_pdf,
    trace_words,
    traced_backend,
)

TITLE_BLOCK = (
    ((900.0, 780.0), "縮尺 1/50"),
    ((900.0, 800.0), "専有延床面積 95.54 ㎡"),
    ((900.0, 820.0), "施工床面積 90.61 ㎡"),
)

TABLE = {
    "origin": (80.0, 120.0),
    "col_widths": (110.0, 90.0, 80.0, 80.0, 70.0),
    "row_height": 24.0,
    "rows": (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "2"),
        ("WD-02", "折戸", "1200", "2000", "1"),
    ),
    "caption": "建具表",
}


@pytest.fixture()
def scanned(tmp_path: Path) -> tuple[Path, Path]:
    """(元図のベクター PDF, それをスキャンした PDF) を返す。"""
    source = build_vector_pdf(
        tmp_path / "vector.pdf", title_block=TITLE_BLOCK, table=TABLE
    )
    scanned_path = scan_pdf(source, tmp_path / "scanned.pdf", dpi=SCAN_DPI)
    return source, scanned_path


# ---------------------------------------------------------------------------
# 前提: 合成したスキャンが、実案件のスキャンと同じ形になっていること
# ---------------------------------------------------------------------------


def test_synthetic_scan_has_no_text_and_is_raster(scanned: tuple[Path, Path]) -> None:
    """合成したスキャンは ``raster`` 判定で、埋め込み文字が 1 文字も無い。

    ここが崩れると、以降の試験は「スキャンのページ」を試していない。
    """
    source, scanned_path = scanned
    before = rasterize(source, dpi=72)[0]
    after = rasterize(scanned_path, dpi=72)[0]

    assert before.content_kind in {"vector", "mixed"}
    assert before.text_span_count > 0

    assert after.content_kind == "raster"
    assert after.text == ""
    assert after.text_span_count == 0


# ---------------------------------------------------------------------------
# 1. 位置をページ座標で返す
# ---------------------------------------------------------------------------


def test_spans_come_back_in_page_points(scanned: tuple[Path, Path]) -> None:
    source, scanned_path = scanned
    backend = traced_backend(source, name="traced", dpi=SCAN_DPI)

    page = recognize_page(scanned_path, 0, backends=[backend], dpi=SCAN_DPI)

    assert backend.calls == 1
    assert page.engines == ("traced",)
    marks = {span.text: span for span in page.spans}
    assert "WD-01" in marks

    # 元図の「WD-01」は表の 1 行目、左端の列にある。ページ座標で
    # そこを指していることを、元図から取った位置と比べて確かめる。
    expected = next(
        word.rect_pt for word in trace_words(source) if word.text == "WD-01"
    )
    got = marks["WD-01"].rect_pt
    assert all(abs(a - b) < 1.0 for a, b in zip(got, expected)), (got, expected)


def test_dpi_does_not_change_the_reported_position(scanned: tuple[Path, Path]) -> None:
    """解像度を変えても、返ってくる位置(ポイント)は変わらない。"""
    source, scanned_path = scanned
    low = recognize_page(
        scanned_path,
        0,
        backends=[traced_backend(source, name="a", dpi=150)],
        dpi=150,
    )
    high = recognize_page(
        scanned_path,
        0,
        backends=[traced_backend(source, name="a", dpi=400)],
        dpi=400,
    )
    low_marks = {span.text: span.rect_pt for span in low.spans}
    high_marks = {span.text: span.rect_pt for span in high.spans}
    assert "1650" in low_marks and "1650" in high_marks
    assert all(
        abs(a - b) < 1.0 for a, b in zip(low_marks["1650"], high_marks["1650"])
    )


# ---------------------------------------------------------------------------
# 2〜3. 確信度
# ---------------------------------------------------------------------------


def test_low_confidence_words_are_dropped_and_recorded(
    scanned: tuple[Path, Path]
) -> None:
    source, scanned_path = scanned

    def mutate(text: str) -> tuple[str, float] | str:
        if text == "1650":
            return ("1650", 0.20)
        return text

    page = recognize_page(
        scanned_path,
        0,
        backends=[traced_backend(source, name="a", dpi=SCAN_DPI, mutate=mutate)],
        dpi=SCAN_DPI,
        min_confidence=0.5,
    )

    assert "1650" not in {span.text for span in page.spans}
    dropped = [item for item in page.dropped if item.text == "1650"]
    assert len(dropped) == 1
    assert dropped[0].confidence == pytest.approx(0.20)
    assert "確信度" in dropped[0].reason


def test_high_confidence_does_not_mean_correct(scanned: tuple[Path, Path]) -> None:
    """**確信度は文字化けを止めない。** 実測した化け方をそのまま通す。

    この試験は「こちらの層は確信度で誤りを止められない」ことを固定する。
    止まらないと分かっているからこそ、下流で未校正の手法として扱う。
    """
    source, scanned_path = scanned

    def mutate(text: str) -> tuple[str, float] | str:
        if text == "種別":
            return ("种别", 0.99)  # 実測で起きた化け方(中国語モデル)
        return text

    page = recognize_page(
        scanned_path,
        0,
        backends=[traced_backend(source, name="a", dpi=SCAN_DPI, mutate=mutate)],
        dpi=SCAN_DPI,
    )
    texts = {span.text for span in page.spans}
    assert "种别" in texts
    assert "種別" not in texts


# ---------------------------------------------------------------------------
# 4. エンジンを 2 つ渡したときの突き合わせ
# ---------------------------------------------------------------------------


def test_two_engines_must_agree_before_a_span_is_returned(
    scanned: tuple[Path, Path]
) -> None:
    """実測した「中国語モデルは数字に強く、日本語モデルは語に強い」を再現する。

    どちらか一方しか読めていない語は `conflicts` に落ち、**通らない。**
    """
    source, scanned_path = scanned

    def chinese(text: str) -> str:
        return {"種別": "种别", "引戸": "引户", "折戸": "折户"}.get(text, text)

    def japanese(text: str) -> str:
        return {"1650": "16.0", "1200": "１２０", "95.54": "ｓｓ・ｓ４"}.get(text, text)

    page = recognize_page(
        scanned_path,
        0,
        backends=[
            traced_backend(source, name="zh", dpi=SCAN_DPI, mutate=chinese),
            traced_backend(source, name="ja", dpi=SCAN_DPI, mutate=japanese),
        ],
        dpi=SCAN_DPI,
    )

    agreed = {span.text for span in page.spans}
    # 両方が同じに読んだ語だけが通る。
    assert "WD-01" in agreed
    assert "2000" in agreed
    # 片方だけが正しく読んだ語は通らない。**正しいほうを選ばない。**
    assert "1650" not in agreed
    assert "引戸" not in agreed
    assert "引户" not in agreed

    conflicted = {
        frozenset(text for _, text in conflict.readings) for conflict in page.conflicts
    }
    assert frozenset({"1650", "16.0"}) in conflicted
    assert frozenset({"引戸", "引户"}) in conflicted

    assert page.engines == ("zh", "ja")
    assert page.cross_checked is True


def test_a_word_only_one_engine_saw_is_a_conflict(scanned: tuple[Path, Path]) -> None:
    """片方のエンジンが語を丸ごと落としたときも、黙って採用しない。

    実測で `高さ` の見出しが中国語モデルから丸ごと消えた。
    """
    source, scanned_path = scanned

    def drops_takasa(text: str) -> str | None:
        return None if text == "高さ" else text

    page = recognize_page(
        scanned_path,
        0,
        backends=[
            traced_backend(source, name="a", dpi=SCAN_DPI, mutate=drops_takasa),
            traced_backend(source, name="b", dpi=SCAN_DPI),
        ],
        dpi=SCAN_DPI,
    )
    assert "高さ" not in {span.text for span in page.spans}
    reasons = [
        conflict.reason
        for conflict in page.conflicts
        if any(text == "高さ" for _, text in conflict.readings)
    ]
    assert reasons and "読み" in reasons[0]


def test_agreement_is_judged_after_width_folding(scanned: tuple[Path, Path]) -> None:
    """全角と半角の違いだけなら食い違いとしない。**文字の違いは食い違いとする。**"""
    source, scanned_path = scanned

    def fullwidth(text: str) -> str:
        return text.translate(str.maketrans("0123456789-.", "０１２３４５６７８９－．"))

    page = recognize_page(
        scanned_path,
        0,
        backends=[
            traced_backend(source, name="a", dpi=SCAN_DPI),
            traced_backend(source, name="b", dpi=SCAN_DPI, mutate=fullwidth),
        ],
        dpi=SCAN_DPI,
    )
    agreed = {span.normalized for span in page.spans}
    assert "1650" in agreed
    assert "WD-01" in agreed


def test_normalize_keeps_the_original_characters(scanned: tuple[Path, Path]) -> None:
    """幅寄せは数字・英字・記号だけ。漢字や ``㎡`` は触らない。"""
    assert normalize_ocr_text("１６５０") == "1650"
    assert normalize_ocr_text("９５．５４") == "95.54"
    assert normalize_ocr_text("ＷＤ－０１") == "WD-01"
    assert normalize_ocr_text("１／５０") == "1/50"
    # 単位記号と日本語はそのまま。NFKC を丸ごと掛けると ㎡ が m2 になる。
    assert normalize_ocr_text("95.54㎡") == "95.54㎡"
    assert normalize_ocr_text("種別") == "種別"


def test_single_engine_spans_say_so(scanned: tuple[Path, Path]) -> None:
    """エンジンが 1 つのときは、突き合わせていないことが読みに残る。"""
    source, scanned_path = scanned
    page = recognize_page(
        scanned_path, 0, backends=[traced_backend(source, name="only", dpi=SCAN_DPI)],
        dpi=SCAN_DPI,
    )
    assert page.cross_checked is False
    assert all(span.engines == ("only",) for span in page.spans)
    assert any("突き合わせ" in note for note in page.notes)


# ---------------------------------------------------------------------------
# 5〜6. 何も読めなかったとき / エンジンが無いとき
# ---------------------------------------------------------------------------


def test_empty_result_is_not_called_an_empty_page(scanned: tuple[Path, Path]) -> None:
    _, scanned_path = scanned
    page = recognize_page(
        scanned_path, 0, backends=[ScriptedOcrBackend("empty", [])], dpi=SCAN_DPI
    )
    assert page.spans == ()
    assert any("文字が無い" in note for note in page.notes)


def test_no_backend_is_an_error_not_an_empty_page(scanned: tuple[Path, Path]) -> None:
    _, scanned_path = scanned
    with pytest.raises(OcrUnavailable):
        recognize_page(scanned_path, 0, backends=[], dpi=SCAN_DPI)


def test_backend_receives_a_grayscale_image_of_the_scanned_page(
    scanned: tuple[Path, Path]
) -> None:
    """渡される画像は、スキャンのページをそのままラスター化したもの。"""
    _, scanned_path = scanned
    seen: dict[str, object] = {}

    class Recorder:
        name = "recorder"
        model_id = "recorder"

        def recognize(self, image: np.ndarray, *, dpi: int):
            seen["shape"] = image.shape
            seen["dtype"] = image.dtype
            seen["dpi"] = dpi
            return [OcrWord(text="x", rect_px=(0.0, 0.0, 10.0, 10.0), confidence=0.9)]

    recognize_page(scanned_path, 0, backends=[Recorder()], dpi=SCAN_DPI)
    assert seen["dtype"] == np.uint8
    assert len(seen["shape"]) == 2  # グレースケール
    assert seen["dpi"] == SCAN_DPI
