"""記号の文字が「記号の塊の上にあるか」で分けられるかを測る(K-20 M10)。

**結論を先に書く: この測り方は対照 A10 に 2 回とも落ちた。採用していない。**
いま名前が付いている設備の記号 40 件のうち、記号くらいの大きさの線の塊に
触れていたのは **26 件(65%)**で、先に決めた通る条件(9 割以上残る)に届かない。
**残してあるのは、測り直せるようにするためである。**

何を試したのか
--------------
仮の判断(1 文字と数字だけの記号を落とす)は、**文字の形だけを見た線引き**である。
`2` や `E` が寸法なのか記号なのかは、文字を見ても決まらない。
**記号は線で描かれた絵の上に刷られ、寸法や室番号は絵の上に無い。**
だから位置で分けられるはず、という道だった。

なぜ落ちたのか(切り分け済み)
------------------------------
**この図面では記号が配線の線に繋がっている。**繋がりで塊を作ると、
記号 1 個ではなく**配線の網全体が 1 つの塊**になる。1 回目は、
いま名前が付く語 36 件が触れている塊が **36 件とも幅 200pt 以上**だった。
細長い図形(配線)を塊に入れないようにして 5/40 → 26/40 まで上がったが、
**9 割には届かない。**基準に「作り直しは 1 回だけ」と先に書いたので、そこで止めた。

**対照 C11(塊の位置を 50pt ずらすと件数がはっきり減る)は 2 回とも通っている。**
つまり**この測り方は位置を見てはいる**。形の重なり(C6 に落ちた)とは違う落ち方で、
「測りたいものを測っていない」のではなく「この図面では塊が作れない」である。

**仮の判断で落ちている語のうち塊の上にあった件数も出るが、根拠には使わない。**
A10 に落ちた測り方の数字だからである。

実行::

    python -m benchmarks.measure_legend_symbol_position --pdf <図面.pdf> \
        --table <対照表.json> --legend-pages 6 22
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pymupdf

from axes.image_axis.legend_lookup import distinguishable, normalize
from benchmarks.page_geometry import in_drawing

#: 表題欄を外す線引きは **`benchmarks/page_geometry` に集めてある。**
#: K-26 2 番。以前はこのファイルにも ``TITLE_BLOCK_X = 75.0``(**回転前の x**)を
#: 写していたが、**回転していないページでは表題欄ではなく図面の左端の帯を捨てていた。**

#: 繋がりを見るときの粗さ(pt)。これより近い図形どうしを 1 つの塊にする。
CELL = 3.0

#: 記号 1 個くらいの大きさ(pt)。これより小さい塊も大きい塊も記号とみなさない。
MIN_SIZE, MAX_SIZE = 3.0, 25.0

#: これより長い図形は**配線**とみなして塊に入れない。
#: 入れると配線の網ごと 1 つの塊になり、大きさで絞る条件が効かなくなる(1 回目の失敗)。
LONG = 30.0

#: 文字と塊が触れているとみなす余白(pt)。
PAD = 2.0


def symbol_clusters(page: pymupdf.Page, shift: float = 0.0) -> list[pymupdf.Rect]:
    """記号 1 個くらいの大きさの、線の塊を返す。

    `shift` は**対照 C11 のためのずらし**。塊を丸ごとずらして同じ判定をしたとき、
    件数がはっきり減らないなら、その判定は位置を見ていない。
    """
    rects = [
        pymupdf.Rect(drawing["rect"])
        for drawing in page.get_drawings()
        if pymupdf.Rect(drawing["rect"]).width <= LONG
        and pymupdf.Rect(drawing["rect"]).height <= LONG
    ]
    parent = list(range(len(rects)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    grid: dict[tuple[int, int], list[int]] = {}
    for index, rect in enumerate(rects):
        for gx in range(int(rect.x0 // CELL), int(rect.x1 // CELL) + 1):
            for gy in range(int(rect.y0 // CELL), int(rect.y1 // CELL) + 1):
                grid.setdefault((gx, gy), []).append(index)
    for cell in grid.values():
        for other in cell[1:]:
            left, right = find(cell[0]), find(other)
            if left != right:
                parent[left] = right

    groups: dict[int, list[int]] = {}
    for index in range(len(rects)):
        groups.setdefault(find(index), []).append(index)

    out: list[pymupdf.Rect] = []
    for members in groups.values():
        box = pymupdf.Rect(rects[members[0]])
        for member in members[1:]:
            box |= rects[member]
        if MIN_SIZE <= box.width <= MAX_SIZE and MIN_SIZE <= box.height <= MAX_SIZE:
            out.append(pymupdf.Rect(box.x0 + shift, box.y0 + shift, box.x1 + shift, box.y1 + shift))
    return out


def touches(word: tuple[float, float, float, float], boxes: list[pymupdf.Rect]) -> bool:
    rect = pymupdf.Rect(word)
    return any(
        pymupdf.Rect(b.x0 - PAD, b.y0 - PAD, b.x1 + PAD, b.y1 + PAD).intersects(rect)
        for b in boxes
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--legend-pages", type=int, nargs="*", default=())
    parser.add_argument(
        "--shift",
        type=float,
        nargs="*",
        default=(0.0, 50.0),
        help="**対照 C11**: 塊をずらす量(pt)。0 と 50 を両方出す。",
    )
    args = parser.parse_args()

    table = json.loads(args.table.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = {}
    for row in table["symbols"]:
        names.setdefault(normalize(str(row["code"])), set()).add(str(row["name"]))
    unique = {code for code, value in names.items() if len(value) == 1}

    doc = pymupdf.open(args.pdf)
    legend = set(args.legend_pages)
    report: dict[str, dict[str, int]] = {}
    for shift in args.shift:
        total: Counter = Counter()
        for number in range(1, len(doc) + 1):
            if number in legend:
                continue
            page = doc[number - 1]
            words = [
                w
                for w in page.get_text("words")
                if in_drawing(page, w[:4]) and normalize(w[4]) in names
            ]
            if not words:
                continue
            boxes = symbol_clusters(page, shift)
            for word in words:
                code = normalize(word[4])
                if code not in unique:
                    group = "1つに決まらない語"
                elif distinguishable(word[4]):
                    group = "いま名前が付く語"
                else:
                    group = "仮の判断で落ちる語"
                total[group] += 1
                if touches(word[:4], boxes):
                    total[f"{group}(塊の上)"] += 1
        report[f"塊のずらし{shift:.0f}pt"] = dict(sorted(total.items()))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
