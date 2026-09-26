"""K-26 1番: 候補台帳が「小さすぎる(点の汚れ)」として捨てたものの中身を調べる。

台帳(`axes/image_axis/candidate_ledger.py`)は、ベクターの図形 1 つ(PyMuPDF の
``get_drawings()`` の 1 件 = 1 本のパス)の外接矩形の長いほうの辺が
``small_min_side``(0〜1000 の単位で 1.0)より小さいと、数だけ数えて捨てる。
P011 匿名化v2 では 96,816 件あり、**中身を見ていなかった。**

ここでは台帳と同じ座標の直し方(回転を直して 0〜1000)で、捨てた 1 件ずつを
集め直し、次を数える。**出すのは件数とページ番号と大きさだけ。図面の文字は出さない。**

1. 形の種類(大きさ 0 の点 / 線 1 本 / 曲線を含む / 塗り …)
2. 置かれている場所(文字の語の箱の中 / 表題欄 / 凡例の記号の升目の中)
3. 密集しているか(近くに同じような点が何個あるか。ハッチングや点線の目安)
4. 区切りの値を半分・2 倍にしたときの件数(台帳を実際に作り直して数える)
5. 凡例の「記号」の升目(22 ページ、群ごと)の中に、捨てた点が何件あるか
6. 捨てた点どうしを端点でつなぐと、区切り以上の形になるか(円や曲線を細かい
   直線に割って描いている図面では、1 本ずつは点でも、つなぐと記号の輪になる)
7. 凡例の図形だけの 19 行のうち、台帳の候補が升目に 1 つも無い行に、
   つなぐとできる形があるか

``--crops`` を渡すと、区切りの前後 10 件ずつを画像に切り出す(目で見るため)。
**画像は図面そのものなので、リポジトリに置かない。**共有フォルダか作業用フォルダへ。

使い方::

    .venv/bin/python benchmarks/measure_ledger_specks.py --pdf <P011 匿名化v2> \\
        --out <結果の JSON> [--crops <画像を置くフォルダ>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from axes.image_axis.candidate_ledger import (  # noqa: E402
    DROP_SPECK,
    NORM,
    LedgerSettings,
    _page_words,
    build_ledger,
)

#: 凡例の「名称 / 記号」の表があるページ(1 始まり)。K-21 と同じ。
LEGEND_PAGE = 22

#: 密集の目安: この半径(0〜1000 の単位)の中に、ほかの捨てた点が何個あるか。
CROWD_RADIUS = 5.0
CROWD_MIN = 10


def shape_kind(drawing: dict) -> str:
    items = drawing["items"]
    kinds = {item[0] for item in items}
    rect = pymupdf.Rect(drawing["rect"])
    if rect.width == 0 and rect.height == 0:
        return "大きさ0の点"
    if "c" in kinds:
        return "曲線を含む"
    if kinds <= {"l"} and len(items) == 1:
        return "線1本"
    if kinds <= {"l"}:
        return "線だけ(2本以上)"
    if kinds & {"re", "qu"}:
        return "四角"
    return "その他"


def page_specks(page: pymupdf.Page, settings: LedgerSettings) -> list[dict]:
    """台帳と同じ判定で「小さすぎる」になる図形を、位置つきで返す。"""
    matrix = page.rotation_matrix
    sx = NORM / page.rect.width
    sy = NORM / page.rect.height
    out = []
    for index, drawing in enumerate(page.get_drawings()):
        rect = pymupdf.Rect(drawing["rect"]) * matrix
        rect.normalize()
        x0, y0, x1, y1 = rect.x0 * sx, rect.y0 * sy, rect.x1 * sx, rect.y1 * sy
        side = max(x1 - x0, y1 - y0)
        if side >= settings.small_min_side * 2.5:
            continue
        out.append(
            {
                "index": index,
                "box": (x0, y0, x1, y1),
                "side": side,
                "kind": shape_kind(drawing),
                "filled": drawing.get("fill") is not None,
                "stroked": drawing.get("color") is not None,
                "width_pt": drawing.get("width"),
                "pt_rect": tuple(pymupdf.Rect(drawing["rect"])),
            }
        )
    return out


def _inside(box, cell, slack=0.2) -> bool:
    return (
        box[0] >= cell[0] - slack
        and box[1] >= cell[1] - slack
        and box[2] <= cell[2] + slack
        and box[3] <= cell[3] + slack
    )


def _crowd(specks: list[dict]) -> list[int]:
    """各点の近く(CROWD_RADIUS)にある、ほかの点の数。格子に分けて数える。"""
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    centers = []
    for i, s in enumerate(specks):
        cx = (s["box"][0] + s["box"][2]) / 2
        cy = (s["box"][1] + s["box"][3]) / 2
        centers.append((cx, cy))
        grid[(int(cx // CROWD_RADIUS), int(cy // CROWD_RADIUS))].append(i)
    counts = []
    for i, (cx, cy) in enumerate(centers):
        gx, gy = int(cx // CROWD_RADIUS), int(cy // CROWD_RADIUS)
        n = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j != i:
                        ox, oy = centers[j]
                        if (ox - cx) ** 2 + (oy - cy) ** 2 <= CROWD_RADIUS**2:
                            n += 1
        counts.append(n)
    return counts


def _endpoints(drawing: dict) -> list[pymupdf.Point]:
    points = []
    for item in drawing["items"]:
        if item[0] in ("l", "c"):
            points += [item[1], item[-1]]
    return points


def joined_shapes(page: pymupdf.Page, settings: LedgerSettings) -> tuple[Counter, list[tuple]]:
    """捨てた点を、端点が同じ座標(0.01pt に丸める)どうしでつなぐ。

    返すのは (点の数を、つないだ結果で分けた内訳, 区切り以上になった形の 0〜1000 の箱)。
    """
    matrix = page.rotation_matrix
    sx = NORM / page.rect.width
    sy = NORM / page.rect.height
    drawings = page.get_drawings()

    def norm_box(rect) -> tuple[float, float, float, float]:
        r = pymupdf.Rect(rect) * matrix
        r.normalize()
        return (r.x0 * sx, r.y0 * sy, r.x1 * sx, r.y1 * sy)

    def side(box) -> float:
        return max(box[2] - box[0], box[3] - box[1])

    specks = [i for i, d in enumerate(drawings) if side(norm_box(d["rect"])) < settings.small_min_side]
    parent = {i: i for i in specks}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends: dict[tuple[float, float], list[int]] = defaultdict(list)
    for i in specks:
        for p in _endpoints(drawings[i]):
            ends[(round(p.x, 2), round(p.y, 2))].append(i)
    for members in ends.values():
        for j in members[1:]:
            parent[find(j)] = find(members[0])
    groups: dict[int, list[int]] = defaultdict(list)
    for i in specks:
        groups[find(i)].append(i)

    counts: Counter = Counter()
    shapes = []
    for members in groups.values():
        rect = pymupdf.Rect(drawings[members[0]]["rect"])
        for i in members[1:]:
            rect |= pymupdf.Rect(drawings[i]["rect"])
        box = norm_box(rect)
        if len(members) == 1:
            r = drawings[members[0]]["rect"]
            label = "ひとつだけ(大きさ0の点)" if r.width == 0 and r.height == 0 else "ひとつだけ(短い線など)"
        elif side(box) >= settings.small_min_side:
            label = "つなぐと区切り以上の形になる"
            shapes.append(box)
        else:
            label = "つないでも区切り未満"
        counts[label] += len(members)
    return counts, shapes


def _word_index(page: pymupdf.Page) -> dict[tuple[int, int], list[tuple]]:
    """語の箱を 10 単位の格子に入れておく(点ごとに全語を見ると遅いため)。"""
    index: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for w in _page_words(page):
        box = (w.x0, w.y0, w.x1, w.y1)
        for gx in range(int(w.x0 // 10), int(w.x1 // 10) + 1):
            for gy in range(int(w.y0 // 10), int(w.y1 // 10) + 1):
                index[(gx, gy)].append(box)
    return index


def _in_word(box, index) -> bool:
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    return any(_inside(box, w, 0.0) for w in index.get((int(cx // 10), int(cy // 10)), ()))


def legend_cells(pdf: str) -> list[dict]:
    """22 ページの「記号」の升目(0〜1000)と群。答え(名前)は持たない。"""
    from benchmarks.build_legend_symbol_images import name_rows

    with pymupdf.open(pdf) as doc:
        page = doc[LEGEND_PAGE - 1]
        width, height = page.rect.width, page.rect.height
    cells = []
    for number, row in enumerate(name_rows(pdf, LEGEND_PAGE)):
        x0, y0, x1, y1 = row["rect"]
        cells.append(
            {
                "row": number,
                "group": row["kind"],
                "box": (x0 / width * NORM, y0 / height * NORM, x1 / width * NORM, y1 / height * NORM),
                "pt": (x0, y0, x1, y1),
            }
        )
    return cells


def sweep(pdf: str) -> dict:
    """区切りを半分・そのまま・2 倍にして台帳を作り直し、件数を数える。"""
    out = {}
    for factor in (0.5, 1.0, 2.0):
        settings = replace(LedgerSettings(), small_min_side=LedgerSettings().small_min_side * factor)
        ledger = build_ledger(pdf, settings=settings, drawing_list=None)
        out[str(factor)] = {
            "small_min_side": settings.small_min_side,
            "specks": sum(p.dropped.get(DROP_SPECK, 0) for p in ledger.pages),
            "small_kept": sum(p.counts["small"].kept for p in ledger.pages),
            "small_cap_hit_pages": sum(1 for p in ledger.pages if p.counts["small"].cap_hit),
        }
    return out


def crop(doc: pymupdf.Document, page_number: int, speck: dict, path: Path) -> None:
    """その点のまわり(±6pt)を 8 倍で切り出し、点の外接矩形を赤で囲む。"""
    page = doc[page_number - 1]
    r = pymupdf.Rect(speck["pt_rect"])
    clip = pymupdf.Rect(r.x0 - 6, r.y0 - 6, r.x1 + 6, r.y1 + 6)
    shape = page.new_shape()
    mark = pymupdf.Rect(r.x0 - 0.4, r.y0 - 0.4, r.x1 + 0.4, r.y1 + 0.4)
    shape.draw_rect(mark)
    shape.finish(color=(1, 0, 0), width=0.15)
    shape.commit()
    page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(8, 8)).save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--crops")
    args = parser.parse_args()

    settings = LedgerSettings()
    cutoff = settings.small_min_side
    cells = legend_cells(args.pdf)

    everything: list[tuple[int, dict]] = []
    by_kind = Counter()
    by_place = Counter()
    by_page = Counter()
    crowd_hist = Counter()
    legend = defaultdict(Counter)
    joined = Counter()
    joined_shape_sizes = Counter()
    legend_joined: dict = {}
    with pymupdf.open(args.pdf) as doc:
        for page in doc:
            number = page.number + 1
            near = page_specks(page, settings)
            specks = [s for s in near if s["side"] < cutoff]
            everything += [(number, s) for s in near]
            if not specks:
                continue
            words = _word_index(page)
            crowd = _crowd(specks)
            for s, n in zip(specks, crowd):
                by_page[number] += 1
                by_kind[s["kind"]] += 1
                box = s["box"]
                if _in_word(box, words):
                    place = "文字の語の箱の中"
                elif box[1] >= settings.title_bottom_from:
                    place = "表題欄の帯(下端)"
                else:
                    place = "図面の中"
                by_place[place] += 1
                crowd_hist["近くに10個以上" if n >= CROWD_MIN else "近くに10個未満"] += 1
            counts, shapes = joined_shapes(page, settings)
            joined.update(counts)
            for box in shapes:
                side = max(box[2] - box[0], box[3] - box[1])
                joined_shape_sizes[
                    "小輪郭の大きさ(20以下)" if side <= settings.small_max_side else "20より大きい"
                ] += 1
            if number == LEGEND_PAGE:
                ledger_page = build_ledger(args.pdf, pages=[page.number], drawing_list=None).pages[0]
                kept_boxes = [
                    (c.x0, c.y0, c.x1, c.y1)
                    for c in (*ledger_page.small, *ledger_page.lines, *ledger_page.regions)
                ]
                empty = Counter()
                for cell in cells:
                    if not cell["group"].startswith("群B"):
                        continue
                    has_kept = any(_inside(b, cell["box"]) for b in kept_boxes)
                    has_joined = any(_inside(b, cell["box"]) for b in shapes)
                    empty["行"] += 1
                    empty["台帳の候補が升目に無い行"] += not has_kept
                    empty["そのうち、つなぐと形ができる行"] += (not has_kept) and has_joined
                legend_joined = dict(empty)
                for cell in cells:
                    inside = [s for s in specks if _inside(s["box"], cell["box"])]
                    kept = [
                        s
                        for s in near
                        if s["side"] >= cutoff and _inside(s["box"], cell["box"])
                    ]
                    g = legend[cell["group"]]
                    g["升目"] += 1
                    g["捨てた点がある升目"] += bool(inside)
                    g["捨てた点(合計)"] += len(inside)
                    g["区切りのすぐ上(1.0〜2.5)で残った図形がある升目"] += bool(kept)
                    g["捨てた点だけで、残った小さな図形が無い升目"] += bool(inside) and not kept

        # 区切りの前後 10 件ずつ(大きさの順。同じ大きさはページ・位置で並べる)
        ordered = sorted(everything, key=lambda e: (e[1]["side"], e[0], e[1]["box"][1], e[1]["box"][0]))
        below = [e for e in ordered if e[1]["side"] < cutoff][-10:]
        above = [e for e in ordered if e[1]["side"] >= cutoff][:10]
        edges = {
            "捨てた側(区切りのすぐ下)": [
                {"page": p, "side": round(s["side"], 3), "kind": s["kind"]} for p, s in below
            ],
            "残した側(区切りのすぐ上)": [
                {"page": p, "side": round(s["side"], 3), "kind": s["kind"]} for p, s in above
            ],
        }
        if args.crops:
            out_dir = Path(args.crops)
            out_dir.mkdir(parents=True, exist_ok=True)
            for label, group in (("below", below), ("above", above)):
                for i, (p, s) in enumerate(group):
                    with pymupdf.open(args.pdf) as fresh:
                        crop(fresh, p, s, out_dir / f"{label}_{i:02d}_p{p}.png")

    sizes = Counter()
    for _p, s in everything:
        if s["side"] < cutoff:
            sizes[f"{int(s['side'] * 10) / 10:.1f}"] += 1

    report = {
        "cutoff": cutoff,
        "specks": sum(by_page.values()),
        "by_page": dict(sorted(by_page.items())),
        "by_kind": dict(by_kind),
        "by_place": dict(by_place),
        "crowd": dict(crowd_hist),
        "size_histogram_0.1": dict(sorted(sizes.items())),
        "edges": edges,
        "legend_page22": {k: dict(v) for k, v in legend.items()},
        "joined": dict(joined),
        "joined_shapes": dict(joined_shape_sizes),
        "legend_group_b_empty_cells": legend_joined,
        "sweep": sweep(args.pdf),
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("specks", "by_kind", "by_place", "crowd", "legend_page22", "joined", "joined_shapes", "legend_group_b_empty_cells", "sweep")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
