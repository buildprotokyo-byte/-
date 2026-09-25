"""周21(空間): **寸法線と結び付いた寸法から、芯々の大きさを組めるか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`pdf_dimensions` にも `find_room_outlines` にも手を入れていない(K-29)。

**着手前にコードを読んで分かったこと(取り決め①)**

**この問いには 2026-09-23 に出した答えが既にある。**
`axes/image_axis/wall_network.py` の冒頭に
「**この案件の図面からは室の面積は出ない**」というキラークエスチョンの
結論があり、中身は `docs/a2_wall_network_report.md`。
そこでは**寸法の数字 49 個(20 ページ)・印字された面積 2 件・
通り芯の符号 0 件**と数えられている。

**それでも測る理由**

同じ報告書の「弱いところ」に、**測っていない予想**が 1 つ残っている。

> 寸法の数字は「3〜5 桁の整数」で数えた。**寸法線と結びつけてはいない。**
> 結びつければもう少し拾えるかもしれないが、**1 室あたり 1 個という桁は変わらない。**

**これは書き残した予想であって、測った値ではない。**
そして**寸法線と結び付ける実装は既にある**(`pdf_dimensions.py`)。
**書き残した予想も、測るまで根拠にしない。**

**あとから足した欄が 1 つある**

``参考_落とした理由`` と ``参考_表とみなされた割合`` は、
**結果を見てから足した診断の欄**である。
線1〜線3 の判定には使っていない。足した理由は、線1 が **0 件**だったので、
**「寸法線が無い」のか「数字が寸法として読めない」のか**を分けないと、
0 の意味が言えないため。**先に決めた線は動かしていない。**

基準は `docs/loop_round21_dimension_chains_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_dimensions import read_dimensions  # noqa: E402
from axes.image_axis.pdf_tables import find_tables  # noqa: E402
from benchmarks.measure_height_destination import ceiling_notes  # noqa: E402

#: 線1 の合格。仕上表の室は 10 個で、面積には 1 室あたり 2 辺が要る。
#: **20 件以上なら「桁が変わった」= 書き残された予想が外れた。**
LINE1_MIN = 20

#: 連なりとみなす端点の近さ(紙の上のポイント)。**実寸ではない。**
JOIN_PT = 5.0

#: 通り芯の符号の書き方。**X/Y を付けない書き方(A・1 だけ)は拾えない。**
GRID_MARK = re.compile(r"^[XYxyＸＹ][-－ー]?\d{1,2}$")


def chains(
    readings: list[tuple[str, tuple[float, float], tuple[float, float]]]
) -> dict[str, int]:
    """向きごとに、端点が繋がっている寸法の並びが何本あるかを数える。

    **2 本以上つながって初めて「連なり」**(1 本は連なりではない)。
    """
    out: dict[str, int] = {}
    for orientation in {item[0] for item in readings}:
        here = [item for item in readings if item[0] == orientation]
        parent = list(range(len(here)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for i in range(len(here)):
            for j in range(i + 1, len(here)):
                ends_i = (here[i][1], here[i][2])
                ends_j = (here[j][1], here[j][2])
                touching = any(
                    ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 <= JOIN_PT
                    for a in ends_i
                    for b in ends_j
                )
                if touching:
                    parent[find(i)] = find(j)
        groups: dict[int, int] = {}
        for index in range(len(here)):
            root = find(index)
            groups[root] = groups.get(root, 0) + 1
        out[orientation] = sum(1 for size in groups.values() if size >= 2)
    return out


def scatter_ends(
    readings: list[tuple[str, tuple[float, float], tuple[float, float]]],
    width: float,
    height: float,
    seed: int,
) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    """囮: **寸法の長さと向きはそのままに、置き場所だけを動かす。**

    **本数も長さも変えない。**変えるのは位置だけで、そこがこの囮の言いたいところ。
    """
    rng = random.Random(seed)
    out = []
    for orientation, start, end in readings:
        dx, dy = end[0] - start[0], end[1] - start[1]
        x = rng.uniform(0.0, max(width - abs(dx), 1.0))
        y = rng.uniform(0.0, max(height - abs(dy), 1.0))
        out.append((orientation, (x, y), (x + dx, y + dy)))
    return out


def grid_marks(page: pymupdf.Page) -> int:
    """通り芯の符号の数。**X/Y の書き方しか拾えない。**"""
    found = 0
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if GRID_MARK.match(span["text"].strip()):
                    found += 1
    return found


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
    """1 ページ分。**寸法の値そのものは返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
            return {"ページ": page_index + 1, "平面図とみなす": False}
        marks = grid_marks(page)
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()

    got = read_dimensions(pdf_path, page_index)
    readings = [
        (reading.orientation, reading.start_pt, reading.end_pt)
        for reading in got.readings
    ]
    decoy = scatter_ends(readings, width, height, seed + page_index)

    # **結果を見てから足した診断の欄。**線の判定には使っていない。
    # 落とした理由のほとんどが「表の升目の中」だったので、
    # **表とみなされた範囲が紙をどれだけ覆っているか**を数える。
    covered = 0.0
    for region in find_tables(pdf_path, page_index):
        x0, y0, x1, y1 = region.rect_pt
        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    share = covered / (width * height) if width and height else 0.0

    reasons: dict[str, int] = {}
    for item in got.skipped:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        "線1_寸法線と結び付いた寸法": len(got.readings),
        "参考_落とした理由": reasons,
        "参考_表とみなされた割合": round(share, 3),
        "落とした数字": len(got.skipped),
        "線2_連なり": chains(readings),
        "線2_囮": chains(decoy),
        "線3_通り芯の符号": marks,
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    readings = sum(row["線1_寸法線と結び付いた寸法"] for row in plan)
    marks = sum(row["線3_通り芯の符号"] for row in plan)

    def chain_total(key: str, orientation: str) -> int:
        return sum(row[key].get(orientation, 0) for row in plan)

    vertical = chain_total("線2_連なり", "縦")
    horizontal = chain_total("線2_連なり", "横")
    decoy_vertical = chain_total("線2_囮", "縦")
    decoy_horizontal = chain_total("線2_囮", "横")
    real_total = vertical + horizontal
    decoy_total = decoy_vertical + decoy_horizontal

    return {
        "ページ": plan,
        "線1_寸法線と結び付けると桁は変わるか": {
            "本物": readings,
            "記録(2026-09-23、20ページで3〜5桁の整数を数えた値)": 49,
            "合格": LINE1_MIN,
            "通過(予想が外れた)": readings >= LINE1_MIN,
            "囮": "置いていない(記録の数え直し)",
        },
        "線2_芯々に要る寸法の連なりは在るか": {
            "縦": vertical,
            "横": horizontal,
            "囮_縦": decoy_vertical,
            "囮_横": decoy_horizontal,
            "通過": vertical >= 1
            and horizontal >= 1
            and real_total - decoy_total >= 1,
        },
        "参考_表とみなされた割合": [row["参考_表とみなされた割合"] for row in plan],
        "参考_落とした理由": {
            reason: sum(row["参考_落とした理由"].get(reason, 0) for row in plan)
            for reason in sorted(
                {key for row in plan for key in row["参考_落とした理由"]}
            )
        },
        "線3_通り芯の符号は本当に0件か": {
            "本物": marks,
            "記録": 0,
            "記録を再現した": marks == 0,
            "限界": "X/Y の書き方しか拾えない。0 件でも「通り芯が無い」とは書かない",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()
    print(json.dumps(measure(args.pdf, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
