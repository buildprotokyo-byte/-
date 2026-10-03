"""周6「文字が線で描かれたページを読む」の測定。

基準は `docs/loop_round6_maker_drawing_criteria.md`(**測る前にコミット済み**)。

この冊子には、**PDF から取り出せる文字が 0 件のページ**がある。線として描かれた
文字なので、いままでの経路からは 1 文字も見えていない。ここでは**画像にしてから
文字を読む**道具を掛け、**囮(墨が 1 画素も無い区画)と必ず並べて**数える。

**本番の経路には繋がない。図面の中身は標準出力に出さない**(`--texts` を付けた
ときだけ、指定したファイルへ書き出す。そのファイルは共有フォルダに置くこと)。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf

#: 区画の分割数(横×縦)。囮と本物の大きさを作りで揃えるために格子で切る。
TILE_COLUMNS = 6
TILE_ROWS = 4

#: 画素が白でないと見なす明るさ。CAD の細線は薄いので高めに取る。
INK_LEVEL = 250

#: 区画を「墨がある」と見なす画素の割合。
INK_FRACTION = 0.0005

#: 囮として取る空白の区画の数(1 ページあたり)。基準の線3。
#: **追記1: このページには空白の区画がほとんど無く、この囮は作れなかった。**
DECOYS_PER_PAGE = 3


def render_shifted(page: pymupdf.Page, rng: random.Random) -> pymupdf.Page:
    """**墨の量はそのまま、図形の位置だけをでたらめに動かした紙**を作る(囮、追記1)。

    1 本ずつの線・曲線・矩形は形も大きさも変えず、**図形のまとまりごと**に
    でたらめな平行移動を掛ける。**文字を組み立てている並びだけが壊れる。**
    **墨は 1 画素も紙の外へ出さない**(外接矩形が紙に収まる範囲でだけ動かす)。
    読み取りが「墨があれば何か返す」のか「並びを見ている」のかが、これで分かれる。
    """
    out = pymupdf.open()
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    shape = new.new_shape()
    width = page.rect.width
    height = page.rect.height
    for drawing in page.get_drawings():
        # **墨を紙の外へ出さない。**図形の外接矩形が紙に収まる範囲でだけ動かす
        # (`benchmarks/measure_closed_faces.py` の囮と同じ決め方)。
        box = drawing["rect"]
        span_x = max(width - box.width, 0.0)
        span_y = max(height - box.height, 0.0)
        dx = rng.uniform(0.0, span_x) - box.x0
        dy = rng.uniform(0.0, span_y) - box.y0
        move = pymupdf.Matrix(1, 0, 0, 1, dx, dy)
        for item in drawing["items"]:
            kind = item[0]
            try:
                if kind == "l":
                    shape.draw_line(item[1] * move, item[2] * move)
                elif kind == "c":
                    shape.draw_bezier(
                        item[1] * move, item[2] * move, item[3] * move, item[4] * move
                    )
                elif kind == "re":
                    shape.draw_rect(item[1] * move)
                elif kind == "qu":
                    shape.draw_quad(item[1] * move)
            except (ValueError, TypeError):
                continue
        shape.finish(
            width=drawing.get("width") or 0.5,
            color=(0, 0, 0),
            fill=None,
            closePath=False,
        )
    shape.commit()
    return new


def render(page: pymupdf.Page, dpi: int) -> np.ndarray:
    """ページを灰色の画像にする。"""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def ink_fraction(tile: np.ndarray) -> float:
    """区画のうち、白でない画素の割合。"""
    if tile.size == 0:
        return 0.0
    return float((tile < INK_LEVEL).sum()) / float(tile.size)


def tiles(image: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
    """画像を格子に切る。戻りは (列, 行, 区画) の一覧。"""
    height, width = image.shape
    out: list[tuple[int, int, np.ndarray]] = []
    for row in range(TILE_ROWS):
        y0 = height * row // TILE_ROWS
        y1 = height * (row + 1) // TILE_ROWS
        for column in range(TILE_COLUMNS):
            x0 = width * column // TILE_COLUMNS
            x1 = width * (column + 1) // TILE_COLUMNS
            out.append((column, row, image[y0:y1, x0:x1]))
    return out


def read(engine: Any, image: np.ndarray) -> list[tuple[str, float]]:
    """画像から文字を読む。戻りは (文字, 確からしさ) の一覧。"""
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    result, _ = engine(image)
    if not result:
        return []
    return [(str(row[1]), float(row[2])) for row in result]


def measure_page(
    engine: Any,
    doc: pymupdf.Document,
    page_index: int,
    *,
    dpi: int,
    seed: int,
) -> dict[str, Any]:
    """1 ページぶん数える。本物(墨のある区画)と囮(空白の区画)を並べて返す。"""
    page = doc.load_page(page_index)
    image = render(page, dpi)

    inked_count = 0
    blank: list[tuple[int, int, np.ndarray]] = []
    inked_texts: list[tuple[int, int, str, float]] = []
    for column, row, tile in tiles(image):
        if ink_fraction(tile) >= INK_FRACTION:
            inked_count += 1
            for text, score in read(engine, tile):
                inked_texts.append((column, row, text, score))
        else:
            blank.append((column, row, tile))

    rng = random.Random(seed + page_index)
    chosen = rng.sample(blank, min(DECOYS_PER_PAGE, len(blank)))
    decoy_texts: list[tuple[int, int, str, float]] = []
    for column, row, tile in chosen:
        for text, score in read(engine, tile):
            decoy_texts.append((column, row, text, score))

    # 追記1 の囮: 墨の量はそのまま、図形の位置だけを動かした紙を同じ設定で読む。
    shifted_page = render_shifted(page, random.Random(seed + page_index))
    shifted_image = render(shifted_page, dpi)
    shifted_inked = 0
    shifted_texts: list[tuple[int, int, str, float]] = []
    for column, row, tile in tiles(shifted_image):
        if ink_fraction(tile) >= INK_FRACTION:
            shifted_inked += 1
            for text, score in read(engine, tile):
                shifted_texts.append((column, row, text, score))

    return {
        "ページ番号": page_index + 1,
        "解像度": dpi,
        "PDF から取り出せる文字": len(page.get_text().strip()),
        "図形の数": len(page.get_drawings()),
        "墨のある区画": inked_count,
        "空白の区画": len(blank),
        "囮に選んだ区画": len(chosen),
        "本物から返った文字の数": len(inked_texts),
        "囮(空白の区画)から返った文字の数": len(decoy_texts),
        "囮(位置を動かした紙)の墨のある区画": shifted_inked,
        "囮(位置を動かした紙)から返った文字の数": len(shifted_texts),
        "_本物": inked_texts,
        "_囮_空白": decoy_texts,
        "_囮_位置": shifted_texts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True, help="ページ番号をカンマ区切りで")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--out", type=Path, help="件数だけを書き出す先")
    parser.add_argument("--texts", type=Path, help="読めた文字を書き出す先(共有フォルダ)")
    args = parser.parse_args(argv)

    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    doc = pymupdf.open(args.pdf)

    pages = []
    for number in args.pages.split(","):
        pages.append(
            measure_page(
                engine, doc, int(number) - 1, dpi=args.dpi, seed=args.seed
            )
        )

    if args.texts:
        args.texts.write_text(
            json.dumps(
                [{k: v for k, v in p.items() if k.startswith("_") or k == "ページ番号"}
                 for p in pages],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    counts = {"ページごと": [
        {k: v for k, v in p.items() if not k.startswith("_")} for p in pages
    ]}
    text = json.dumps(counts, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
