"""名前が付いた一致から**抜き取り検査の見本**を選び、図に起こす(K-20 M13)。

なぜ要るのか
------------
「捏造 0 件」は作りで保証できる(名前を作らないので、対照表に無い名前は出ようがない)。
**「間違い 0 件」は保証できない。**対照表の語が図面の別のものに当たっているかどうかは、
**目で見るまで分からない。**実際、この道具で選んだ 12 件を見たところ、
**11 件は正しく、1 件は間違いだった**(壁の展開図にあった 3 文字で、記号の絵が無く、
家具を置く場所を書いたものだった)。

**選び方をこの道具に固定してあるのが要点である。**
人が「当たりの多そうなページ」を選ぶと、抜き取り検査にならない。

選び方
------
1. 名前が付いた一致を **(ページ番号, y, x) の順**に並べる。
2. **先頭から等間隔に** `--samples` 件を取る。
3. その周りを切り出した画を `--out` の下に置く。**目で見るのは人がやる。**

**標準出力には数と位置しか出さない。**図面の語は出さない(報告にそのまま貼れるように)。
**切り出した画は図面そのものなので、`--out` には共有フォルダの外を指定すること。**

実行::

    python -m benchmarks.sample_named_matches --pdf <図面.pdf> \
        --table <対照表.json> --legend-pages 6 22 --samples 12 --out /tmp/見本
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf

from axes.image_axis.legend_lookup import LegendTable, match_marks, normalize
from benchmarks.page_geometry import in_drawing

#: 表題欄を外す線引きは **`benchmarks/page_geometry` に集めてある。**
#: K-26 2 番。以前はこのファイルにも ``TITLE_BLOCK_X = 75.0``(**回転前の x**)を
#: 写していたが、**回転していないページでは表題欄ではなく図面の左端の帯を捨てていた。**

#: 切り出す余白(pt)。記号 1 個が 10pt ほどなので、そのまわりが見える大きさ。
MARGIN_X, MARGIN_Y = 40.0, 26.0

#: 切り出しの拡大率。小さい文字を目で見分けるために大きめに取る。
ZOOM = 6.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--legend-pages", type=int, nargs="*", default=())
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument(
        "--out", type=Path, required=True, help="**共有フォルダの外を指定すること**"
    )
    parser.add_argument(
        "--only-pages",
        type=int,
        nargs="*",
        default=(),
        help="このページだけを母数にする(**群を全部見るとき**に使う)",
    )
    args = parser.parse_args()

    table = LegendTable.load(args.table)
    doc = pymupdf.open(args.pdf)
    legend = set(args.legend_pages)
    only = set(args.only_pages)

    rows = []
    for number in range(1, len(doc) + 1):
        if number in legend or (only and number not in only):
            continue
        page = doc[number - 1]
        words = [
            w
            for w in page.get_text("words")
            if in_drawing(page, w[:4]) and normalize(w[4])
        ]
        if not words:
            continue
        for word, match in zip(words, match_marks([w[4] for w in words], table)):
            if match.matched:
                rows.append((number, round(word[1], 2), round(word[0], 2), word, match.kind))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))

    step = max(1, len(rows) // args.samples) if rows else 1
    picks = rows[::step][: args.samples]

    args.out.mkdir(parents=True, exist_ok=True)
    listing = []
    for index, (number, _, _, word, kind) in enumerate(picks):
        page = doc[number - 1]
        clip = pymupdf.Rect(
            word[0] - MARGIN_X, word[1] - MARGIN_Y, word[2] + MARGIN_X, word[3] + MARGIN_Y
        )
        name = f"{index:02d}_p{number}.png"
        page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(ZOOM, ZOOM)).save(args.out / name)
        listing.append({"見本": index, "ページ": number, "種別": kind, "画": name})

    print(
        json.dumps(
            {
                "名前が付いた件数": len(rows),
                "何件おきに取ったか": step,
                "見本": listing,
                "注意": "正しいかどうかは画を目で見て決める。この道具は数えない。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
