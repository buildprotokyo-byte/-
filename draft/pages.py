"""ページを画像と文字の層にする・白紙を見分ける・ページを隠した版を作る(K-61)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

#: 読みの座標の幅(画素)。`benchmarks/erase_check.py` の WIDTH_PX と同じ。
WIDTH_PX = 2000
#: 整理の段に渡す縮小画像の幅。
THUMB_PX = 1000
#: 文字の層の先頭として整理の段に渡す文字数。
TEXT_HEAD_CHARS = 400


@dataclass
class PageInfo:
    number: int
    image: Path
    thumb: Path
    text: str
    width_pt: float
    height_pt: float
    blank: bool

    def as_dict(self) -> dict:
        return {"ページ": self.number, "白紙": self.blank, "文字数": len(self.text)}


def render(pdf_path: str | Path, out_dir: str | Path) -> list[PageInfo]:
    """全ページを幅 2000 画素の PNG と縮小画像にする。**同じ PDF なら同じバイト列になる。**"""
    import pymupdf

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pages: list[PageInfo] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            number = index + 1
            image = out / f"p{number}.png"
            thumb = out / f"p{number}_縮小.png"
            if not image.exists():
                zoom = WIDTH_PX / page.rect.width
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(image)
            if not thumb.exists():
                zoom = THUMB_PX / page.rect.width
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(thumb)
            text = page.get_text("text")
            blank = not text.strip() and not page.get_drawings() and not page.get_images()
            pages.append(PageInfo(number, image, thumb, text, page.rect.width, page.rect.height, blank))
    return pages


def hide_pages(pdf_path: str | Path, pages: Sequence[int], out_pdf: str | Path) -> Path:
    """指定のページを、同じ大きさの白紙に替えた PDF を作る(資料が欠けた案件を試すため)。"""
    import pymupdf

    hidden = set(pages)
    with pymupdf.open(pdf_path) as src:
        dst = pymupdf.open()
        for index in range(src.page_count):
            if index + 1 in hidden:
                rect = src.load_page(index).rect
                dst.new_page(width=rect.width, height=rect.height)
            else:
                dst.insert_pdf(src, from_page=index, to_page=index)
        dst.save(out_pdf)
        dst.close()
    return Path(out_pdf)


def words_in_image(page: Any) -> list[tuple[str, tuple[float, float, float, float]]]:
    """文字の層の語を、`render` が描いた画像(幅 ``WIDTH_PX`` 画素)の座標で返す。

    **回転のあるページでも画像に合わせる**(K-68 C 周 1)。``get_text("words")`` の座標は回転の前のページの座標で、
    描いた画像は回転の後なので、``page.rotation_matrix`` を掛けてから縮尺を掛ける(`benchmarks/erase_check.py` と同じ)。
    """
    import pymupdf

    zoom = WIDTH_PX / page.rect.width
    m = page.rotation_matrix * pymupdf.Matrix(zoom, zoom)
    out = []
    for w in page.get_text("words"):
        r = (pymupdf.Rect(w[:4]) * m).normalize()
        out.append((w[4], (r.x0, r.y0, r.x1, r.y1)))
    return out


def positioned_words(pdf_path: str | Path, number: int) -> list[list[Any]]:
    """文字の層の語を、幅 ``WIDTH_PX`` 画素の画像の座標で返す(``[語, x0, y0, x1, y1]``)。回転のあるページも画像に合う。"""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(number - 1)
        return [[text, round(b[0]), round(b[1]), round(b[2]), round(b[3])] for text, b in words_in_image(page)]
