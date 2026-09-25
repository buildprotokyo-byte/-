"""周22(空間): **寸法の連なりで紙を格子に割ると、室が 1 つずつ入るか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`pdf_dimensions` にも `find_room_outlines` にも手を入れていない(K-29)。

**なぜこの周があるか(取り決め①で分かったこと)**

周21 は「平面図の 9 割が表とみなされ、寸法が 0 件になっている」を
**次の周で直すべき欠陥**として報告した。**それは既に直っていた。**
PR #139(`claude/k29-area-expert-reading`、2026-09-24)が
`_looks_like_a_drawing_frame()` を足して、図面枠を寸法の除外から外している。
**周21 は、その直しが入っていない枝の上で測っていた。**

**だから周22 は、周21 を #139 の上で測り直す周である。**
線1〜線3 は `measure_dimension_chains.py` をそのまま使う(**1 文字も変えない**)。
**この道具が受け持つのは線4 だけ**で、それがこの周で唯一の新しい問いである。

**線4 の問い**

芯々で室の面積を出すには、**縦と横の寸法が同じ室を挟んでいる**必要がある。
寸法が何件読めても、**どの寸法がどの室のものか**が決まらなければ面積にならない。
周21 は「測っていない」と書いて次に渡した。ここで測る。

基準は `docs/loop_round22_dimension_grid_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_dimensions import read_dimensions  # noqa: E402
from benchmarks.measure_dimension_chains import JOIN_PT  # noqa: E402
from benchmarks.measure_height_destination import ceiling_notes  # noqa: E402
from benchmarks.measure_room_face_link import (  # noqa: E402
    name_positions,
    schedule_rooms,
)

#: 線4 の合格。仕上表の室は 10 個。**5 ページ合計で 3 個以上**。
LINE4_MIN = 3

Reading = tuple[str, tuple[float, float], tuple[float, float]]


def chain_members(readings: list[Reading]) -> list[Reading]:
    """**連なり(端点が繋がった 2 本以上の組)に属する寸法だけ**を返す。

    1 本きりの寸法は格子に使わない。**連なっていない寸法の端点は、
    区切りの位置として意味を持たない。**
    """
    out: list[Reading] = []
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
        sizes: dict[int, int] = {}
        for index in range(len(here)):
            sizes[find(index)] = sizes.get(find(index), 0) + 1
        out.extend(here[index] for index in range(len(here)) if sizes[find(index)] >= 2)
    return out


def _merge(values: list[float], tolerance: float = JOIN_PT) -> list[float]:
    """近すぎる位置を 1 本にまとめる。**まとめないと升目が虫食いになる。**"""
    merged: list[float] = []
    for value in sorted(values):
        if not merged or value - merged[-1] > tolerance:
            merged.append(value)
    return merged


def grid_lines(readings: list[Reading]) -> tuple[list[float], list[float]]:
    """連なりの区切りを、**寸法線と直角な方向へ伸ばした格子線**にする。

    - ``横`` の寸法(横に走る線)の端点は **x の位置**を決める → **縦の格子線**
    - ``縦`` の寸法(縦に走る線)の端点は **y の位置**を決める → **横の格子線**

    ``斜め`` の寸法は使わない。**斜めの寸法から直角な格子線は決まらない。**
    """
    xs: list[float] = []
    ys: list[float] = []
    for orientation, start, end in readings:
        if orientation == "横":
            xs.extend([start[0], end[0]])
        elif orientation == "縦":
            ys.extend([start[1], end[1]])
    return _merge(xs), _merge(ys)


def _cell_of(
    point: tuple[float, float], xs: list[float], ys: list[float]
) -> tuple[int, int] | None:
    """その点がどの升目に入るか。**格子の外なら None。**"""
    column = None
    for index in range(len(xs) - 1):
        if xs[index] <= point[0] <= xs[index + 1]:
            column = index
            break
    row = None
    for index in range(len(ys) - 1):
        if ys[index] <= point[1] <= ys[index + 1]:
            row = index
            break
    if column is None or row is None:
        return None
    return (column, row)


def cells_with_one_room(
    readings: list[Reading], names: list[tuple[str, tuple[float, float]]]
) -> dict[str, int]:
    """連なりから格子を組み、**室名がちょうど 1 個入った升目**を数える。"""
    xs, ys = grid_lines(readings)
    return count_in_grid(xs, ys, names)


def scatter_grid(
    xs: list[float], ys: list[float], width: float, height: float, seed: int
) -> tuple[list[float], list[float]]:
    """囮′: **格子線の本数はそのままに、位置だけでたらめに置き直す。**

    **基準に最初に書いた囮(寸法そのものを散らす `scatter_ends`)は
    当たりようのない囮だった。**紙の上に散らすと端点が 5pt 以内で繋がらず、
    連なりが 0 本になり、**格子線が 1 本もできない。**升目 0 個の囮は
    どんな図面でも必ず 0 を返す(周10・周16 と同じ失敗)。
    **測る前に差し替え、理由を基準の追記1 に書いてコミットしてある。**

    こちらの囮は、問い(**寸法の区切りは室の境目に来ているか**)を
    そのまま裏返している。**升目の数はほぼ同じで、変わるのは位置だけ。**
    """
    rng = random.Random(seed)
    return (
        _merge([rng.uniform(0.0, width) for _ in xs]),
        _merge([rng.uniform(0.0, height) for _ in ys]),
    )


def count_in_grid(
    xs: list[float], ys: list[float], names: list[tuple[str, tuple[float, float]]]
) -> dict[str, int]:
    """**室名がちょうど 1 個入った升目**の数を数える。

    升目の数そのものは成果にしない(格子を細かくすれば増える)。
    **見るのは「ちょうど 1 個」だけ**で、**囮と並べて見る。**
    """
    if len(xs) < 2 or len(ys) < 2:
        return {"升目": 0, "室名が1個": 0, "格子の外の室名": len(names)}
    counts: dict[tuple[int, int], int] = {}
    outside = 0
    for _, point in names:
        cell = _cell_of(point, xs, ys)
        if cell is None:
            outside += 1
            continue
        counts[cell] = counts.get(cell, 0) + 1
    return {
        "升目": (len(xs) - 1) * (len(ys) - 1),
        "室名が1個": sum(1 for value in counts.values() if value == 1),
        "格子の外の室名": outside,
    }


def _coverage(
    xs: list[float], ys: list[float], width: float, height: float
) -> dict[str, float]:
    """格子が紙のどれだけを覆っているか。**あとから足した診断の欄。**"""
    if len(xs) < 2 or len(ys) < 2 or width <= 0 or height <= 0:
        return {"横": 0.0, "縦": 0.0, "面積": 0.0}
    across = (xs[-1] - xs[0]) / width
    down = (ys[-1] - ys[0]) / height
    return {
        "横": round(across, 3),
        "縦": round(down, 3),
        "面積": round(across * down, 3),
    }


def measure_page(pdf_path: Path, page_index: int, seed: int, rooms: list[str]) -> dict:
    """1 ページ分。**室名も寸法の値も返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
            return {"ページ": page_index + 1, "平面図とみなす": False}
        here = name_positions(page, rooms)
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()

    got = read_dimensions(pdf_path, page_index)
    readings: list[Reading] = [
        (reading.orientation, reading.start_pt, reading.end_pt)
        for reading in got.readings
    ]
    members = chain_members(readings)
    xs, ys = grid_lines(members)
    decoy_xs, decoy_ys = scatter_grid(xs, ys, width, height, seed + page_index)

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        "寸法": len(readings),
        "連なりに属する寸法": len(members),
        "格子線": [len(xs), len(ys)],
        "印字された室名": len(here),
        "線4_本物": count_in_grid(xs, ys, here),
        "線4_囮": count_in_grid(decoy_xs, decoy_ys, here),
        # **結果を見てから足した診断の欄。**線4 の判定には使っていない。
        # 足した理由は、線4 が不通過だったときに
        # 「格子が室の境目に来ていない」のか「格子が紙をほとんど割っていない」のかを
        # 分けないと、数字の意味が言えないため。**先に決めた線は動かしていない。**
        "参考_格子が覆う紙の割合": _coverage(xs, ys, width, height),
    }


def measure(pdf_path: Path, seed: int) -> dict:
    rooms = schedule_rooms(pdf_path)
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed, rooms) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    real = sum(row["線4_本物"]["室名が1個"] for row in plan)
    decoy = sum(row["線4_囮"]["室名が1個"] for row in plan)
    names = sum(row["印字された室名"] for row in plan)
    outside = sum(row["線4_本物"]["格子の外の室名"] for row in plan)

    return {
        "仕上表の室": len(rooms),
        "ページ": plan,
        "参考_格子が覆う紙の割合": [row["参考_格子が覆う紙の割合"] for row in plan],
        "参考_印字された室名": {"のべ": names, "格子の外": outside},
        "線4_寸法の格子に室が1つずつ入るか": {
            "本物": real,
            "囮": decoy,
            "合格": LINE4_MIN,
            "通過": real >= LINE4_MIN and real > decoy,
            "但し書き": (
                "升目が室を1つ含むことと、その升目の辺が壁芯であることは別。"
                "「芯々の面積が出た」とは書かない"
            ),
        },
    }


def check_definition() -> dict:
    """**合成の紙で数え方を確かめる**(周12 の教訓。実図面に当てる前に)。

    紙は作らない。**格子の組み方と升目の数え方だけ**を、手で置いた寸法で見る。
    """
    readings: list[Reading] = [
        ("横", (0.0, 0.0), (100.0, 0.0)),
        ("横", (100.0, 0.0), (200.0, 0.0)),
        ("縦", (0.0, 0.0), (0.0, 100.0)),
        ("縦", (0.0, 100.0), (0.0, 200.0)),
        ("横", (500.0, 500.0), (600.0, 500.0)),
    ]
    members = chain_members(readings)
    names = [
        ("室A", (50.0, 50.0)),
        ("室B", (150.0, 50.0)),
        ("室C", (50.0, 150.0)),
        ("室D", (150.0, 150.0)),
        ("室E", (155.0, 155.0)),
    ]
    xs, ys = grid_lines(members)
    decoy_xs, decoy_ys = scatter_grid(xs, ys, 800.0, 800.0, 20260925)
    return {
        "連なりに属する寸法": len(members),
        "1本きりの寸法を外した": len(members) == 4,
        "格子線": [len(xs), len(ys)],
        "升目と室名": count_in_grid(xs, ys, names),
        "囮_格子線": [len(decoy_xs), len(decoy_ys)],
        "囮_升目と室名": count_in_grid(decoy_xs, decoy_ys, names),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="?", help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--check", action="store_true", help="合成の確かめだけ行う")
    args = parser.parse_args()
    if args.check or args.pdf is None:
        print(json.dumps(check_definition(), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(measure(args.pdf, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
