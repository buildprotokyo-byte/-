"""テスト用の「スキャンされた図面」を合成で組み立てる道具。

**実図面は使わない。** 実案件の図面・見積明細は匿名化済みでもリポジトリに
置かない決まりなので、スキャンのページはここで合成する。

作り方は実物と同じ順序をたどる。

1. ベクターの PDF を組む(文字は PyMuPDF 内蔵の日本語フォントで入れる。
   システムに日本語フォントが入っていない CI でも同じ絵になる)
2. それをラスター画像にする(= 紙に出して読み取った状態)
3. その画像 1 枚だけを貼った PDF を作る

3 の PDF は `axes/image_axis/pdf_pages.rasterize()` が ``raster`` と判定し、
`page.get_text()` は空文字を返す。**P011 の 34 ページと同じ形である。**

OCR の試験体
------------
本物の OCR エンジンは CI に入っていない(`requirements.txt` の OCR の節)。
そこで、**2 の段階で捨てた「文字がどこに何と書かれていたか」を使って
OCR の出力を組み立てる試験体**を用意する(`traced_backend`)。

- 位置は本物と同じ(ベクターの PDF から取った座標を画素に直したもの)
- 文字は `mutate` で自由に壊せる(本物の OCR が実際に起こした化け方を
  そのまま再現できる)

**この試験体は「OCR が当たる」ことの証拠にはならない。** 当たったときと
外したときに、こちら側の経路がどう振る舞うかを固定するためのものである。
本物のエンジンを使った確認は `tests/test_ocr_rapidocr_integration.py`
(エンジンが入っていなければ skip)と `benchmarks/run_ocr_synthetic_eval.py`。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import pymupdf

from axes.image_axis.ocr_text import OcrWord

#: 合成スキャンの既定の解像度。実案件のスキャン図面はおおむね 200〜300dpi。
SCAN_DPI = 200


def draw_scanned_table(
    page: pymupdf.Page,
    *,
    origin: tuple[float, float],
    col_widths: Sequence[float],
    row_height: float,
    rows: Sequence[Sequence[str | None]],
    caption: str | None = None,
    fontsize: float = 10.0,
) -> None:
    """罫線で組んだ表を描く。`tests/test_pdf_tables.draw_table` と同じ形。"""
    x0, y0 = origin
    if caption is not None:
        page.insert_text(
            pymupdf.Point(x0, y0 - 8.0), caption, fontname="japan", fontsize=fontsize + 1
        )
    total_width = sum(col_widths)
    for index in range(len(rows) + 1):
        y = y0 + index * row_height
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x0 + total_width, y))
    x = x0
    for width in list(col_widths) + [0.0]:
        page.draw_line(
            pymupdf.Point(x, y0), pymupdf.Point(x, y0 + row_height * len(rows))
        )
        x += width
    for row_index, row in enumerate(rows):
        x = x0
        for col_index, text in enumerate(row):
            if text:
                page.insert_text(
                    pymupdf.Point(x + 4.0, y0 + row_index * row_height + row_height - 7.0),
                    text,
                    fontname="japan",
                    fontsize=fontsize,
                )
            x += col_widths[col_index]


def build_vector_pdf(
    path: Path,
    *,
    title_block: Iterable[tuple[tuple[float, float], str]] = (),
    table: dict | None = None,
    width: float = 1190.0,
    height: float = 842.0,
) -> Path:
    """スキャン前の元図(ベクター)を作る。"""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    for (x, y), text in title_block:
        page.insert_text(pymupdf.Point(x, y), text, fontname="japan", fontsize=11)
    if table is not None:
        draw_scanned_table(page, **table)
    doc.save(path)
    doc.close()
    return path


def scan_pdf(
    source: Path,
    destination: Path,
    *,
    dpi: int = SCAN_DPI,
    rotate_degrees: float = 0.0,
    noise_sigma: float = 0.0,
    page_index: int = 0,
    seed: int = 0,
) -> Path:
    """元図を「紙に出してスキャンした 1 枚の画像」に変えた PDF を作る。

    `rotate_degrees` と `noise_sigma` は実物のスキャンの傾きと粒状感。
    既定は 0 で、**歪みの無い理想的なスキャン**になる。
    """
    from PIL import Image

    with pymupdf.open(source) as doc:
        page = doc.load_page(page_index)
        rect = pymupdf.Rect(page.rect)
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width
        ).copy()

    pil = Image.fromarray(image)
    if rotate_degrees:
        pil = pil.rotate(rotate_degrees, resample=Image.BICUBIC, fillcolor=255)
    array = np.asarray(pil).astype(np.float32)
    if noise_sigma:
        array = array + np.random.default_rng(seed).normal(0.0, noise_sigma, array.shape)
    array = np.clip(array, 0.0, 255.0).astype(np.uint8)

    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")

    out = pymupdf.open()
    scanned = out.new_page(width=rect.width, height=rect.height)
    scanned.insert_image(scanned.rect, stream=buffer.getvalue())
    out.save(destination)
    out.close()
    return destination


@dataclass(frozen=True)
class TracedWord:
    """元図に書かれていた文字 1 つと、その位置(ページ座標・ポイント)。"""

    text: str
    rect_pt: tuple[float, float, float, float]


def trace_words(source: Path, page_index: int = 0) -> list[TracedWord]:
    """元図(ベクター)から「どこに何と書かれていたか」を取り出す。

    **スキャン後の PDF からは取れない。** スキャンの側には文字が無いので、
    これはテストが答えを知っているという意味であり、実行時には無い情報である。
    """
    with pymupdf.open(source) as doc:
        page = doc.load_page(page_index)
        out: list[TracedWord] = []
        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            out.append(
                TracedWord(text=text, rect_pt=(float(x0), float(y0), float(x1), float(y1)))
            )
    return out


class ScriptedOcrBackend:
    """決められた語をそのまま返す試験体。位置も文字もテストが指定する。"""

    def __init__(self, name: str, words: Sequence[OcrWord]) -> None:
        self.name = name
        self.model_id = "scripted"
        self._words = tuple(words)
        self.calls = 0

    def recognize(self, image: np.ndarray, *, dpi: int) -> Sequence[OcrWord]:
        self.calls += 1
        return self._words


def traced_backend(
    source: Path,
    *,
    name: str,
    dpi: int = SCAN_DPI,
    page_index: int = 0,
    confidence: float = 0.95,
    mutate: Callable[[str], tuple[str, float] | str | None] | None = None,
) -> ScriptedOcrBackend:
    """元図の文字をそのままの位置で返す試験体を作る。

    `mutate` は語ごとに呼ばれ、
    ``None`` を返すとその語は**読めなかった**ことになり(本物の OCR は
    語を丸ごと落とす。実測で「高さ」の見出しが消えた)、
    ``str`` を返すと化けた文字として、``(str, float)`` を返すと
    化けた文字と確信度として扱われる。
    """
    scale = dpi / 72.0
    words: list[OcrWord] = []
    for traced in trace_words(source, page_index):
        text, score = traced.text, confidence
        if mutate is not None:
            result = mutate(traced.text)
            if result is None:
                continue
            if isinstance(result, tuple):
                text, score = result
            else:
                text = result
        x0, y0, x1, y1 = traced.rect_pt
        words.append(
            OcrWord(
                text=text,
                rect_px=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                confidence=score,
            )
        )
    return ScriptedOcrBackend(name, words)
