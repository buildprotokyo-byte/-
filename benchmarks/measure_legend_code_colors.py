"""記号の語が**何色で刷られているか**をページの群ごとに数える(K-20 M12)。

何のために要るのか
------------------
いま名前が付く件数をいちばん減らしているのは**仮の判断**
(1 文字と数字だけの記号を落とす)で、これは**文字の形だけを見た線引き**である。
`2` や `E` が寸法なのか記号なのかは、文字を見ても決まらない。

**凡例は「配線・シンボル色」として色の意味を自分で書いている。**
寸法と室番号は図面の既定の墨(黒)で刷られ、記号は工事の区分に応じて赤か青になる。
**だから「1 文字でも赤か青なら記号」と分けられるかもしれない。**
この道具は、その分け方が成り立つかを数える。

**対照 C13**: 凡例が「ここで使う」と書いていないページで同じ数え方をする。
そこでも赤・青が同じくらい出るなら、**色は記号を指していない。**

**この道具の数字から「赤なら交換・新設」とは読めない。**
実物を見たところ、**この図面は撤去の記号も赤で描いていた**
(赤い記号が `撤` と書かれた赤い囲みの中にあった)。
**色で分かるのは「記号かどうか」までで、「撤去か新設か」は分からない。**

**数だけを出す。**図面の中身は出さない。

実行::

    python -m benchmarks.measure_legend_code_colors --pdf <図面.pdf> \
        --table <対照表.json> --legend-pages 6 22 --scope-pages 23 24
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pymupdf

from axes.image_axis.legend_lookup import (
    LegendTable,
    distinguishable,
    match_line_colors,
    normalize,
)
from benchmarks.page_geometry import in_drawing

#: 表題欄を外す線引きは **`benchmarks/page_geometry` に集めてある。**
#: K-26 2 番。以前はこのファイルにも ``TITLE_BLOCK_X = 75.0``(**回転前の x**)を
#: 写していたが、**回転していないページでは表題欄ではなく図面の左端の帯を捨てていた。**


def _coloured(page: pymupdf.Page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if not in_drawing(page, span["bbox"]) or not normalize(span["text"]):
                    continue
                packed = int(span["color"])
                yield span["text"], (
                    round(((packed >> 16) & 0xFF) / 255.0, 4),
                    round(((packed >> 8) & 0xFF) / 255.0, 4),
                    round((packed & 0xFF) / 255.0, 4),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--legend-pages", type=int, nargs="*", default=())
    parser.add_argument(
        "--scope-pages",
        type=int,
        nargs="*",
        required=True,
        help="**凡例が「ここで使う」と書いたページ。**残りが対照 C13 になる。",
    )
    args = parser.parse_args()

    table = LegendTable.load(args.table)
    raw = json.loads(args.table.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = {}
    for row in raw["symbols"]:
        names.setdefault(normalize(str(row["code"])), set()).add(str(row["name"]))
    unique = {code for code, value in names.items() if len(value) == 1}

    doc = pymupdf.open(args.pdf)
    legend = set(args.legend_pages)
    scope = set(args.scope_pages)
    groups = {
        "凡例が使うと書いたページ": scope,
        "**対照 C13** そのほかのページ": set(range(1, len(doc) + 1)) - scope - legend,
    }

    report: dict[str, dict[str, int]] = {}
    for label, pages in groups.items():
        counts: Counter = Counter()
        for number in sorted(pages):
            items = [
                (text, colour)
                for text, colour in _coloured(doc[number - 1])
                if normalize(text) in names
            ]
            if not items:
                continue
            for (text, _), match in zip(
                items, match_line_colors([c for _, c in items], table)
            ):
                code = normalize(text)
                if code not in unique:
                    kind = "1つに決まらない語"
                elif distinguishable(text):
                    kind = "いま名前が付く語"
                else:
                    kind = "仮の判断で落ちる語"
                counts[f"{kind} / {match.name if match.matched else '凡例に無い色'}"] += 1
        report[label] = dict(sorted(counts.items()))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
