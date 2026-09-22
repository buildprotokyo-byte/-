"""OCR で読んだ文字から、既存と**同じ規則で**縮尺と面積の記載を拾う。

`axes/image_axis/ocr_text.py` が返すのは「どこに何と書いてあったか」までで、
意味づけはしない。このモジュールが、その語の並びを
`axes/image_axis/pdf_vector_symbols.py` と**同じ正規表現**に掛ける。

なぜ同じ規則を使うのか
----------------------
スキャンのほうだけ規則を緩めると(例: ``専有延床面`` でも面積として拾う)、
**質の悪い読みが甘い判定で通る**ことになる。埋め込み文字より当てにならない
経路に、埋め込み文字より緩い規則を当てるのは逆である。
そのため、判定に使う正規表現は `pdf_vector_symbols.parse_scale_text` と
`parse_area_text` を呼ぶだけにして、こちらには置かない。

手法IDを分けてある理由
----------------------
埋め込み文字から読んだ面積は ``pdf_text_area``、OCR で読んだ面積は
``ocr_text_area`` と、**別の手法として登録する**
(`arbitration/method_policies.py`)。同じ ID にすると、
「印字をそのまま読む手法」の校正の話に OCR の読み違いが混ざる。
OCR は文字を別の字に化けさせるうえ、**その化けを確信度が知らせない**
(`docs/ocr_scanned_pages_report.md` 2節)。

何を返さないか
--------------
- **読めなかったことを 0 や既定値で埋めない。** 面積の記載が読めなければ
  空のリストを返す。それは「図面に書かれていない」ではない。
- **化けた文字を直さない。** 全角と半角の寄せ(`normalize_ocr_text`)だけを
  行い、字の置き換えはしない。
"""

from __future__ import annotations

from dataclasses import dataclass

from axes.image_axis.ocr_text import OcrPage, OcrSpan, normalize_ocr_text
from axes.image_axis.pdf_vector_symbols import parse_area_text, parse_scale_text
from axes.reading.meaning import (
    PHASE_UNKNOWN,
    PURPOSE_UNESTABLISHED,
    Meaning,
)

#: 手法ID。**埋め込み文字の手法とは別物として登録する**
#: (`arbitration/method_policies.py`)。
METHOD_OCR_TEXT_AREA = "ocr_text_area"
METHOD_OCR_TEXT_SCALE = "ocr_text_scale"


@dataclass(frozen=True)
class OcrAreaLabel:
    """OCR で読んだ面積の記載 1 件。"""

    label: str
    """``専有延床面積`` か ``施工床面積``。"""

    value_sqm: float
    """読んだ数値(平方メートル)。**計算はしていない。**"""

    source_text: str
    """読んだままの文字列。幅寄せ前の姿を残す。"""

    page_index: int
    rect_pt: tuple[float, float, float, float] | None
    """**数値が書かれている位置。** 見つからなければ None(0 で埋めない)。"""

    confidence: float
    engines: tuple[str, ...]
    readings: tuple[tuple[str, str], ...]
    cross_checked: bool
    meaning: Meaning
    method_id: str = METHOD_OCR_TEXT_AREA


@dataclass(frozen=True)
class OcrScaleReading:
    """OCR で読んだ縮尺の印字 1 件。

    **これは実寸を出してよいという意味ではない。** 原則 3-1 は
    「図面に書かれた縮尺の表記は当てにしない。図面そのものがスキャンや縮小で
    歪んでいる可能性がある」であり、**スキャンのページはその前提そのもの**
    である。この読みは、人が入れた基準点との突き合わせ(検算)の相手として使う。
    """

    denominator: float
    source_text: str
    page_index: int
    rect_pt: tuple[float, float, float, float] | None
    confidence: float
    engines: tuple[str, ...]
    readings: tuple[tuple[str, str], ...]
    cross_checked: bool
    meaning: Meaning
    method_id: str = METHOD_OCR_TEXT_SCALE


def read_area_labels(page: OcrPage) -> list[OcrAreaLabel]:
    """OCR の読みから面積の記載を拾う。書かれていなければ空。

    ラベルと数値は別の語として返ってくることが多い(実測)。行に組んでから
    既存の正規表現に掛ける。
    """
    out: list[OcrAreaLabel] = []
    for _, line in page.lines():
        normalized = normalize_ocr_text(line)
        for label, value, matched, raw_value in parse_area_text(normalized):
            span = _span_for(page, raw_value)
            out.append(
                OcrAreaLabel(
                    label=label,
                    value_sqm=value,
                    source_text=_source_text(page, line, matched),
                    page_index=page.page_index,
                    rect_pt=span.rect_pt if span is not None else None,
                    confidence=span.confidence if span is not None else 0.0,
                    engines=page.engines,
                    readings=span.readings if span is not None else (),
                    cross_checked=page.cross_checked,
                    meaning=Meaning(
                        what=label,
                        where=f"ページ{page.page_index + 1}に印字された{label}の記載",
                        # スキャンの文字だけでは現況か計画かは決まらない。
                        # 人がページの種類を宣言していれば入口の側で上書きする。
                        phase=PHASE_UNKNOWN,
                        purpose_link=PURPOSE_UNESTABLISHED,
                    ),
                )
            )
    return out


def read_scale(page: OcrPage) -> OcrScaleReading | None:
    """OCR の読みから縮尺の印字を拾う。読めなければ None。"""
    for _, line in page.lines():
        normalized = normalize_ocr_text(line)
        scale = parse_scale_text(normalized)
        if scale is None:
            continue
        span = _span_for(page, scale.source_text)
        return OcrScaleReading(
            denominator=scale.denominator,
            source_text=_source_text(page, line, scale.source_text),
            page_index=page.page_index,
            rect_pt=span.rect_pt if span is not None else None,
            confidence=span.confidence if span is not None else 0.0,
            engines=page.engines,
            readings=span.readings if span is not None else (),
            cross_checked=page.cross_checked,
            meaning=Meaning(
                what="縮尺の印字",
                where=f"ページ{page.page_index + 1}に印字された縮尺",
                phase=PHASE_UNKNOWN,
                purpose_link=PURPOSE_UNESTABLISHED,
            ),
        )
    return None


def _span_for(page: OcrPage, fragment: str) -> OcrSpan | None:
    """一致した文字列を含む語を探す。**無ければ None**(座標を作らない)。"""
    target = normalize_ocr_text(fragment).replace(" ", "")
    if not target:
        return None
    for span in page.spans:
        if target and target in span.normalized.replace(" ", ""):
            return span
    # 語をまたいで一致した場合(``縮尺`` と ``1/50`` が別の語)は、
    # 数値のほうを含む語を優先して探す。
    for span in page.spans:
        stripped = span.normalized.replace(" ", "")
        if stripped and stripped in target:
            return span
    return None


def _source_text(page: OcrPage, line: str, matched: str) -> str:
    """根拠として残す文字列。**読んだままの行**を添える。

    幅寄せ後の文字列だけを残すと、図面に戻ったときに「こうは書いていない」
    ことになる。一致した部分と、読んだままの行の両方を残す。
    """
    return f"{matched}(読んだまま: {line})"
