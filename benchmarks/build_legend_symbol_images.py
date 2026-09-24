"""凡例の「記号」の升目を **1 件ずつ画像に切り出す**(K-21)。

なぜ画像か
----------
K-20 で分かったのは、**凡例の名前の行 86 行のうち 19 行は記号の升目に文字が無い**
ということである。丸や四角の絵だけで描かれている。**文字で引き当てる道では
原理的に取れない。**その 19 件に、建築の知識で名前が付くかどうかを測りたい。
測るには、読む側に**絵を見せる**しかない。

この道具が守ること
------------------
1. **答えが画像に写り込まないこと。**切り出す幅は「記号」の列の x の幅ぴったりで、
   **名前の列には決して掛からない。**(升目が横に潰れている行でも、列の見出しの
   矩形から x を取るので幅は変わらない。)
2. **並びを混ぜること。**群の並びや行の順から答えを当てられないようにする。
   種は基準に書いた ``20260924`` で固定。
3. **囮を混ぜること。**凡例に無いものを 10 件混ぜる(罫線だけの空の升目 5・
   凡例に無い合成の図形 5)。**囮に名前が付いたら、その条件の名前は疑う。**

出すもの
--------
* ``--sheets`` … 12 件ずつ並べた紙(コンタクトシート)の PNG。**読む側に渡すのはこれだけ。**
* ``--answers`` … 通し番号と正解(凡例が刷っている名前)の対応。
  **図面から取った文字なので、リポジトリに置かない。**共有フォルダのパスを渡すこと。

実行::

    python -m benchmarks.build_legend_symbol_images --pdf <図面.pdf> --page 22 \\
        --sheets <作業用フォルダ> --answers <共有フォルダ>/answers.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import pymupdf

from axes.image_axis.pdf_tables import find_tables
from benchmarks.build_legend_lookup import (
    _GROUP_HEADING,
    _name_without_note,
    _norm,
    _usable_code,
    _usable_name,
)

#: 並べ替えの種。**基準に書いた値。結果を見てから変えない。**
SHUFFLE_SEED = 20260924

#: 1 枚に並べる件数。
PER_SHEET = 12

#: 紙の升目 1 つの大きさ(pt)。
TILE_W, TILE_H = 300.0, 230.0

#: 絵を置く枠(pt)。下に番号を書く余白を残す。
BOX_W, BOX_H = 286.0, 176.0

#: 切り出しの拡大率。細い線が消えない程度に大きく取る。
CROP_ZOOM = 8.0

#: 紙を PNG にするときの拡大率。
SHEET_ZOOM = 2.0

#: 囮の数(罫線だけの空の升目 / 凡例に無い合成の図形)。
BLANK_DECOYS, DRAWN_DECOYS = 5, 5


def name_rows(pdf_path: Path | str, page_number: int) -> list[dict[str, Any]]:
    """「名称 / 記号」の表から、**名前の行を 1 行ずつ**返す。

    K-20 の :func:`benchmarks.build_legend_lookup.symbol_rows_from_tables` と
    **同じ数え方**で群を決める。数が食い違ったら、どちらかが壊れている。

    返す ``rect`` は「**記号の列の x の幅 × その行の y の幅**」である。
    行ごとの升目の矩形をそのまま使うと、升目が横に潰れている行(幅 6pt ほど)で
    絵が切れる。列の見出しの矩形から x を取ればその心配が無く、しかも
    **名前の列に掛からないことが列の定義から保証される。**
    """
    rows: list[dict[str, Any]] = []
    for table_index, region in enumerate(find_tables(pdf_path, page_number - 1)):
        texts = region.texts()
        head = next(
            (
                index
                for index, row in enumerate(texts[:2])
                if {"名称", "記号"} <= {_norm(cell) for cell in row}
            ),
            None,
        )
        if head is None:
            continue
        header = [_norm(cell) for cell in texts[head]]
        name_column = header.index("名称")
        code_column = header.index("記号")
        column = region.rows[head][code_column].rect_pt
        group: str | None = None
        for row_index in range(head + 1, region.row_count):
            row = region.rows[row_index]
            raw_name = row[name_column].text.strip()
            if not raw_name:
                continue
            heading = _GROUP_HEADING.match(raw_name)
            if heading:
                group = heading.group(1).strip()
                continue
            code = next((c.text for c in row[name_column + 1 :] if c.text.strip()), "")
            name = _name_without_note(raw_name)
            if not code:
                kind = "群B 図形だけ"
            elif not _usable_name(name):
                kind = "群C 決まらない"
            elif not _usable_code(code):
                kind = "群C 決まらない"
            else:
                kind = "群A 文字あり"
            span = row[name_column].rect_pt
            rows.append(
                {
                    "table_index": table_index,
                    "row_index": row_index,
                    "kind": kind,
                    "name": name,
                    "code": code.strip(),
                    "group": group,
                    "rect": (column[0], span[1], column[2], span[3]),
                }
            )
    return rows


def blank_cells(pdf_path: Path | str, page_number: int, wanted: int) -> list[dict[str, Any]]:
    """**罫線だけで中身が無い升目**を囮として集める。

    「名称 / 記号」の表の、見出しが空の列(記号の右にある余りの列)から取る。
    **正解は「凡例に無い」である。**ここに名前が付いたら、読む側は絵が無くても
    名前を作っている。
    """
    found: list[dict[str, Any]] = []
    for table_index, region in enumerate(find_tables(pdf_path, page_number - 1)):
        texts = region.texts()
        head = next(
            (
                index
                for index, row in enumerate(texts[:2])
                if {"名称", "記号"} <= {_norm(cell) for cell in row}
            ),
            None,
        )
        if head is None:
            continue
        header = [_norm(cell) for cell in texts[head]]
        spare = [i for i, cell in enumerate(header) if not cell and i > header.index("記号")]
        for row_index in range(head + 1, region.row_count):
            row = region.rows[row_index]
            for column_index in spare:
                cell = row[column_index]
                x0, y0, x1, y1 = cell.rect_pt
                if cell.text.strip() or cell.merged_with_above:
                    continue
                if x1 - x0 < 10.0 or y1 - y0 < 8.0:
                    continue
                found.append(
                    {
                        "kind": "囮 空の升目",
                        "name": None,
                        "code": "",
                        "group": None,
                        "table_index": table_index,
                        "row_index": row_index,
                        "rect": cell.rect_pt,
                    }
                )
                if len(found) >= wanted:
                    return found
    return found


def drawn_decoys(count: int) -> list[tuple[dict[str, Any], pymupdf.Pixmap]]:
    """**凡例に無い図形**を描いて囮にする。実図面から取らない。

    正解は「凡例に無い」。読む側がこれに設備の名前を付けたら、**絵から名前を
    作っている**ということになる。
    """
    shapes: list[tuple[dict[str, Any], pymupdf.Pixmap]] = []
    with pymupdf.open() as doc:
        for index in range(count):
            page = doc.new_page(width=50, height=30)
            middle = pymupdf.Point(25, 15)
            if index == 0:
                page.draw_polyline(
                    [
                        pymupdf.Point(25, 6),
                        pymupdf.Point(33, 22),
                        pymupdf.Point(17, 22),
                        pymupdf.Point(25, 6),
                    ],
                    width=0.8,
                )
                page.draw_line(pymupdf.Point(20, 10), pymupdf.Point(30, 20), width=0.8)
                page.draw_line(pymupdf.Point(30, 10), pymupdf.Point(20, 20), width=0.8)
            elif index == 1:
                page.draw_polyline(
                    [
                        pymupdf.Point(25, 5),
                        pymupdf.Point(34, 12),
                        pymupdf.Point(31, 23),
                        pymupdf.Point(19, 23),
                        pymupdf.Point(16, 12),
                        pymupdf.Point(25, 5),
                    ],
                    width=0.8,
                )
            elif index == 2:
                for size in (10, 5):
                    page.draw_polyline(
                        [
                            pymupdf.Point(25, 15 - size),
                            pymupdf.Point(25 + size, 15),
                            pymupdf.Point(25, 15 + size),
                            pymupdf.Point(25 - size, 15),
                            pymupdf.Point(25, 15 - size),
                        ],
                        width=0.8,
                    )
            elif index == 3:
                page.draw_circle(middle, 9, width=0.8)
                page.draw_line(pymupdf.Point(17, 11), pymupdf.Point(33, 11), width=0.8)
                page.draw_line(pymupdf.Point(17, 19), pymupdf.Point(33, 19), width=0.8)
            else:
                page.draw_rect(pymupdf.Rect(15, 7, 35, 23), width=0.8)
                page.draw_bezier(
                    pymupdf.Point(17, 15),
                    pymupdf.Point(22, 8),
                    pymupdf.Point(28, 22),
                    pymupdf.Point(33, 15),
                    width=0.8,
                )
            shapes.append(
                (
                    {
                        "kind": "囮 合成の図形",
                        "name": None,
                        "code": "",
                        "group": None,
                        "table_index": None,
                        "row_index": index,
                        "rect": None,
                    },
                    page.get_pixmap(matrix=pymupdf.Matrix(CROP_ZOOM, CROP_ZOOM)),
                )
            )
    return shapes


def _crop(page: pymupdf.Page, rect: tuple[float, float, float, float]) -> pymupdf.Pixmap:
    """升目を切り出す。

    ``find_tables`` が返す矩形は**回転を掛けたあと**の座標なので、
    ``clip`` にそのまま渡す(K-20 で 2 度間違えたところ)。
    """
    return page.get_pixmap(
        clip=pymupdf.Rect(*rect), matrix=pymupdf.Matrix(CROP_ZOOM, CROP_ZOOM)
    )


def build_sheets(
    tiles: list[tuple[str, pymupdf.Pixmap]], out_dir: Path, sheet_zoom: float = SHEET_ZOOM
) -> list[Path]:
    """12 件ずつ並べた紙にする。**升目ごとに枠と通し番号**を入れる。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sheet_index in range(0, len(tiles), PER_SHEET):
        batch = tiles[sheet_index : sheet_index + PER_SHEET]
        with pymupdf.open() as doc:
            page = doc.new_page(width=TILE_W * 4, height=TILE_H * 3)
            for position, (label, pixmap) in enumerate(batch):
                column, row = position % 4, position // 4
                left, top = column * TILE_W, row * TILE_H
                box = pymupdf.Rect(
                    left + (TILE_W - BOX_W) / 2,
                    top + 6,
                    left + (TILE_W + BOX_W) / 2,
                    top + 6 + BOX_H,
                )
                page.draw_rect(box, width=0.6, color=(0.6, 0.6, 0.6))
                scale = min(BOX_W / pixmap.width, BOX_H / pixmap.height, 1.0)
                width, height = pixmap.width * scale, pixmap.height * scale
                middle = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
                page.insert_image(
                    pymupdf.Rect(
                        middle[0] - width / 2,
                        middle[1] - height / 2,
                        middle[0] + width / 2,
                        middle[1] + height / 2,
                    ),
                    pixmap=pixmap,
                )
                page.insert_text(
                    pymupdf.Point(box.x0 + 4, box.y1 + 22),
                    label,
                    fontsize=18,
                    fontname="helv",
                )
            path = out_dir / f"sheet_{sheet_index // PER_SHEET + 1:02d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(sheet_zoom, sheet_zoom)).save(path)
            written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--page", type=int, default=22, help="「名称 / 記号」の表があるページ")
    parser.add_argument("--sheets", required=True, type=Path, help="紙を置く作業用フォルダ")
    parser.add_argument(
        "--answers", required=True, type=Path, help="正解の対応表。**共有フォルダに置くこと**"
    )
    parser.add_argument("--seed", type=int, default=SHUFFLE_SEED)
    args = parser.parse_args(argv)

    rows = name_rows(args.pdf, args.page)
    decoys = blank_cells(args.pdf, args.page, BLANK_DECOYS)
    entries: list[dict[str, Any]] = rows + decoys

    with pymupdf.open(args.pdf) as doc:
        page = doc[args.page - 1]
        pixmaps = [_crop(page, entry["rect"]) for entry in entries]
    for entry, pixmap in drawn_decoys(DRAWN_DECOYS):
        entries.append(entry)
        pixmaps.append(pixmap)

    order = list(range(len(entries)))
    random.Random(args.seed).shuffle(order)
    labelled = [(f"S-{position + 1:03d}", order[position]) for position in range(len(order))]

    sheets = build_sheets([(label, pixmaps[index]) for label, index in labelled], args.sheets)

    answers = [
        {
            "id": label,
            "kind": entries[index]["kind"],
            "name": entries[index]["name"],
            "code": entries[index]["code"],
            "group": entries[index]["group"],
            "table_index": entries[index]["table_index"],
            "row_index": entries[index]["row_index"],
        }
        for label, index in labelled
    ]
    args.answers.parent.mkdir(parents=True, exist_ok=True)
    args.answers.write_text(
        json.dumps({"seed": args.seed, "answers": answers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counted: dict[str, int] = {}
    for entry in entries:
        counted[entry["kind"]] = counted.get(entry["kind"], 0) + 1
    print(f"切り出した升目: {len(entries)} 件")
    for kind in sorted(counted):
        print(f"  {kind}: {counted[kind]} 件")
    print(f"紙: {len(sheets)} 枚 -> {args.sheets}")
    print(f"正解の対応表 -> {args.answers}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
