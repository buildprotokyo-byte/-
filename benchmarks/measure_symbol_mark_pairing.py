"""記号 1 個について、**色**と**改装種別の印**が何を言っているかを並べる(K-20 M11)。

**結論を先に書く: この道具は対照 C12 に落ちた。出た表を根拠に使っていない。**
印の位置だけを同じページの中で入れ替えても表がほとんど変わらないので、
**この表は近さを見ていない**(「どの印が隣にあるか」ではなく「そのページにどの印が
あるか」を映しているだけ)。**残してあるのは測り直せるようにするためである。**

凡例は工事の区分を 2 通りで書いている。**色**(赤=交換・新設 / 青=既存移設・脱着 /
黒=既存のまま)と、**改装種別の印**(`交` `脱` `移` の文字)である。
いままでは別々に数えていて、**同じ記号 1 個について 2 つが何を言っているかを
突き合わせていなかった。**

**この道具は組み合わせの表を出すだけで、意味を繋がない。**
`交` が赤に当たるはず、といった対応表は**作らない。**凡例はその対応を書いていないし、
改装種別の表は `撤去` を赤で刷っているのに色の表の赤には撤去が無い
(**凡例自身が食い違っている**)。こちらで辻褄を合わせると、
**図面が言っていないことを言わせる**ことになる。

**対照 C12**: 印の位置だけを同じページの中で入れ替えて同じ表を作る。
**表の形がはっきり崩れないなら、この道具は近さを見ていない。**

**数だけを出す。**図面の中身は出さない。

実行::

    python -m benchmarks.measure_symbol_mark_pairing --pdf <図面.pdf> \
        --table <対照表.json> --pages 23 24
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path

import pymupdf

from axes.image_axis.legend_lookup import (
    distinguishable,
    match_line_colors,
    LegendTable,
    normalize,
)
from benchmarks.page_geometry import in_drawing

#: 表題欄を外す線引きは **`benchmarks/page_geometry` に集めてある。**
#: K-26 2 番。以前はこのファイルにも ``TITLE_BLOCK_X = 75.0``(**回転前の x**)を
#: 写していたが、**回転していないページでは表題欄ではなく図面の左端の帯を捨てていた。**

#: 改装種別の印がその記号のものとみなせる最大の隔たり(pt)。
#: **実測で校正した値ではない。**記号 1 個が 10pt ほどなので、その 3 倍を
#: 「すぐ隣」として置いた。離れた印を拾うより、印が無いと出るほうが安全な向き。
NEAR_PT = 30.0

NO_MARK = "印が無い"
NO_COLOUR = "凡例に無い色"


def _centre(rect: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def _spans(page: pymupdf.Page) -> list[tuple[str, tuple[float, ...], tuple[float, ...]]]:
    """文字・色・位置。色は記号がどう刷られているかを見るために要る。"""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if not in_drawing(page, span["bbox"]) or not normalize(span["text"]):
                    continue
                packed = int(span["color"])
                colour = (
                    round(((packed >> 16) & 0xFF) / 255.0, 4),
                    round(((packed >> 8) & 0xFF) / 255.0, 4),
                    round((packed & 0xFF) / 255.0, 4),
                )
                out.append((span["text"], colour, tuple(span["bbox"])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="*", required=True)
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=None,
        help="**対照 C12**: 印の位置だけを同じページの中で入れ替える",
    )
    args = parser.parse_args()

    table = LegendTable.load(args.table)
    raw = json.loads(args.table.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = {}
    for row in raw["symbols"]:
        names.setdefault(normalize(str(row["code"])), set()).add(str(row["name"]))
    unique = {code for code, value in names.items() if len(value) == 1}
    marks = {normalize(str(row["code"])) for row in raw["work_marks"]}

    doc = pymupdf.open(args.pdf)
    pairs: Counter = Counter()
    rng = random.Random(args.shuffle_seed)
    for number in args.pages:
        spans = _spans(doc[number - 1])
        symbols = [
            s
            for s in spans
            if normalize(s[0]) in unique and distinguishable(s[0])
        ]
        mark_spans = [s for s in spans if normalize(s[0]) in marks]
        if args.shuffle_seed is not None and len(mark_spans) > 1:
            boxes = [s[2] for s in mark_spans]
            rng.shuffle(boxes)
            mark_spans = [(s[0], s[1], box) for s, box in zip(mark_spans, boxes)]

        colours = match_line_colors([s[1] for s in symbols], table)
        for symbol, colour in zip(symbols, colours):
            cx, cy = _centre(symbol[2])
            best, best_d = NO_MARK, NEAR_PT
            for mark in mark_spans:
                mx, my = _centre(mark[2])
                distance = math.hypot(mx - cx, my - cy)
                if distance <= best_d:
                    best, best_d = normalize(mark[0]), distance
            meaning = colour.meaning if colour.matched else NO_COLOUR
            pairs[(meaning, best)] += 1

    report = {
        "対象のページ": args.pages,
        "対照 C12(印の位置を入れ替えた)": args.shuffle_seed is not None,
        "記号の総数": sum(pairs.values()),
        "色の意味 × いちばん近い改装種別の印": {
            f"{meaning} / {mark}": count for (meaning, mark), count in sorted(pairs.items())
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
