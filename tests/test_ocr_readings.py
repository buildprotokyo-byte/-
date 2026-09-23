"""OCR で読んだ文字から、既存と同じ規則で縮尺・面積を拾う層の試験。

守りたいのは 5 つ。

1. **埋め込み文字のときと同じ正規表現で読む。** スキャンのほうだけ規則を
   緩めると、質の悪い読みが甘い判定で通ることになる。
2. **全角と半角の違いは吸収し、文字の違いは吸収しない。**
   実測で OCR は ``95.54`` を ``95．54``(全角の点)で返した。一方
   ``専有延床面`` のように**字が落ちた読みは、拾わない。**
3. **位置を残す。** 人が図面を見に行けるように、数値の位置を返す。
4. **どのエンジンがどう読んだかを残す。**
5. **意味の 4 欄を必ず付ける。**
"""

from __future__ import annotations

import pytest

from axes.image_axis.ocr_readings import (
    METHOD_OCR_TEXT_AREA,
    METHOD_OCR_TEXT_SCALE,
    read_area_labels,
    read_scale,
)
from axes.image_axis.ocr_text import OcrPage, OcrSpan


def _span(text: str, rect: tuple[float, float, float, float], engines=("zh", "ja")) -> OcrSpan:
    from axes.image_axis.ocr_text import normalize_ocr_text

    return OcrSpan(
        text=text,
        normalized=normalize_ocr_text(text),
        rect_pt=rect,
        confidence=0.9,
        engines=tuple(engines),
        readings=tuple((engine, text) for engine in engines),
    )


def _page(*spans: OcrSpan, engines=("zh", "ja")) -> OcrPage:
    return OcrPage(
        page_index=3,
        dpi=300,
        engines=tuple(engines),
        models=tuple(f"{engine}-model" for engine in engines),
        spans=tuple(spans),
        conflicts=(),
        dropped=(),
        min_confidence=0.5,
    )


def test_area_label_is_read_when_the_label_and_the_number_are_separate_spans() -> None:
    page = _page(
        _span("専有延床面積", (900.0, 770.0, 980.0, 782.0)),
        _span("95.54", (985.0, 770.0, 1020.0, 782.0)),
        _span("㎡", (1022.0, 770.0, 1032.0, 782.0)),
    )
    labels = read_area_labels(page)
    assert len(labels) == 1
    label = labels[0]
    assert label.label == "専有延床面積"
    assert label.value_sqm == pytest.approx(95.54)
    assert label.method_id == METHOD_OCR_TEXT_AREA
    # 位置は**数値そのもの**を指す。
    assert label.rect_pt == (985.0, 770.0, 1020.0, 782.0)
    assert label.engines == ("zh", "ja")
    assert label.meaning.what == "専有延床面積"
    assert "ページ4" in label.meaning.where


def test_fullwidth_digits_are_accepted_and_the_raw_text_is_kept() -> None:
    page = _page(
        _span("専有延床面積", (900.0, 770.0, 980.0, 782.0)),
        _span("９５．５４", (985.0, 770.0, 1020.0, 782.0)),
    )
    labels = read_area_labels(page)
    assert labels[0].value_sqm == pytest.approx(95.54)
    # 読んだままの文字も残す(根拠として図面に戻れるように)。
    assert "９５．５４" in labels[0].source_text


def test_a_dropped_character_is_not_guessed_back() -> None:
    """実測で起きた ``専有延床面`` (積が落ちた)は**拾わない。**

    似ているから拾う、という寄せ方をすると、別のラベルを取り違える。
    """
    page = _page(
        _span("専有延床面", (900.0, 770.0, 980.0, 782.0)),
        _span("95.54", (985.0, 770.0, 1020.0, 782.0)),
    )
    assert read_area_labels(page) == []


def test_two_labels_on_two_lines_are_both_read() -> None:
    page = _page(
        _span("専有延床面積", (900.0, 770.0, 980.0, 782.0)),
        _span("95.54", (985.0, 770.0, 1020.0, 782.0)),
        _span("施工床面積", (900.0, 790.0, 980.0, 802.0)),
        _span("90.61", (985.0, 790.0, 1020.0, 802.0)),
    )
    labels = read_area_labels(page)
    assert {label.label for label in labels} == {"専有延床面積", "施工床面積"}
    assert {round(label.value_sqm, 2) for label in labels} == {95.54, 90.61}


def test_scale_notation_is_read_with_the_same_rule_as_the_vector_path() -> None:
    page = _page(_span("縮尺 1/50", (900.0, 750.0, 960.0, 762.0)))
    reading = read_scale(page)
    assert reading is not None
    assert reading.denominator == pytest.approx(50.0)
    assert reading.method_id == METHOD_OCR_TEXT_SCALE
    assert reading.rect_pt == (900.0, 750.0, 960.0, 762.0)
    assert reading.meaning.what == "縮尺の印字"


def test_a_broken_scale_reading_is_not_repaired() -> None:
    """実測で起きた ``縮尺１50``(斜線が消えた)は読まない。

    ここで ``1/50`` と直すと、``150`` と書かれた図面まで縮尺にしてしまう。
    """
    page = _page(_span("縮尺１50", (900.0, 750.0, 960.0, 762.0)))
    assert read_scale(page) is None


def test_nothing_is_read_from_an_empty_page() -> None:
    page = _page()
    assert read_scale(page) is None
    assert read_area_labels(page) == []


def test_readings_say_whether_the_engines_were_cross_checked() -> None:
    single = _page(
        _span("専有延床面積", (900.0, 770.0, 980.0, 782.0), engines=("zh",)),
        _span("95.54", (985.0, 770.0, 1020.0, 782.0), engines=("zh",)),
        engines=("zh",),
    )
    label = read_area_labels(single)[0]
    assert label.cross_checked is False
    assert label.engines == ("zh",)
