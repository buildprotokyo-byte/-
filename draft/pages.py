"""ページを画像と文字の層にする・白紙を見分ける・ページを隠した版を作る(K-61)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

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
