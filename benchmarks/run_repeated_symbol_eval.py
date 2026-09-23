"""A-1: 繰り返す記号の読み取りの測定。**合成のベクター PDF の上でのみ測る。**

採否の基準は測る前に `docs/a1_repeated_symbol_criteria.md` に置いてある。
結果を見てから基準を変えない。

**負の対照を必ず一緒に回す。** 記号を 1 つも置かない図面・1 個だけ置いた図面・
寸法線の矢印だけの図面で同じ数字が出ないことを確かめてから、本測定の数字を読む。

実図面 PDF はこの作業環境に無いので、**実図面での誤り率はここでは測れない。**
"""

from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_repeated_symbols import (  # noqa: E402
    find_repeated_symbols,
    name_clusters,
    read_legend_symbols,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale  # noqa: E402

SCALE = DrawingScale(denominator=50.0, source_text="1/50")
PT_PER_MM = (1 / 50) / 25.4 * 72


def _rot(x, y, angle_deg):
    rad = math.radians(angle_deg)
    return (
        lambda dx, dy: pymupdf.Point(
            x + dx * math.cos(rad) - dy * math.sin(rad),
            y + dx * math.sin(rad) + dy * math.cos(rad),
        )
    )


def _arc_bezier(shape, place, radius, start_deg, end_deg, steps):
    step = (end_deg - start_deg) / steps
    for segment in range(steps):
        a0 = math.radians(start_deg + segment * step)
        a1 = math.radians(start_deg + (segment + 1) * step)
        handle = 4.0 / 3.0 * math.tan((a1 - a0) / 4.0) * radius
        p0 = (radius * math.cos(a0), radius * math.sin(a0))
        p3 = (radius * math.cos(a1), radius * math.sin(a1))
        p1 = (p0[0] - handle * math.sin(a0), p0[1] + handle * math.cos(a0))
        p2 = (p3[0] + handle * math.sin(a1), p3[1] - handle * math.cos(a1))
        shape.draw_bezier(place(*p0), place(*p1), place(*p2), place(*p3))


def draw_outlet(page, x, y, angle, size):
    """コンセント: 半円 + 引出線。"""
    place = _rot(x, y, angle)
    shape = page.new_shape()
    _arc_bezier(shape, place, size / 2.0, 0.0, 180.0, 2)
    shape.draw_line(place(-size / 2.0, 0.0), place(-size * 1.5, 0.0))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def draw_switch(page, x, y, angle, size):
    """スイッチ: 丸 + 斜めの線。"""
    place = _rot(x, y, angle)
    shape = page.new_shape()
    _arc_bezier(shape, place, size / 3.0, 0.0, 360.0, 4)
    shape.draw_line(place(size / 3.0, 0.0), place(size * 1.3, size * 0.8))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def draw_ceiling_rose(page, x, y, angle, size):
    """引掛シーリング: 丸 + 十字。"""
    place = _rot(x, y, angle)
    shape = page.new_shape()
    _arc_bezier(shape, place, size / 2.2, 0.0, 360.0, 4)
    shape.draw_line(place(-size / 2.2, 0.0), place(size / 2.2, 0.0))
    shape.draw_line(place(0.0, -size / 2.2), place(0.0, size / 2.2))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def draw_tel(page, x, y, angle, size):
    """TEL 引出口: 三角。"""
    place = _rot(x, y, angle)
    shape = page.new_shape()
    shape.draw_line(place(-size / 2, size / 3), place(size / 2, size / 3))
    shape.draw_line(place(size / 2, size / 3), place(0.0, -size / 2))
    shape.draw_line(place(0.0, -size / 2), place(-size / 2, size / 3))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


SYMBOLS = {
    "埋込コンセント": draw_outlet,
    "片切スイッチ": draw_switch,
    "引掛シーリング": draw_ceiling_rose,
    "TEL引出口": draw_tel,
}


def draw_clutter(page, seed: int, arrows: int = 20, hatch: int = 40) -> None:
    """記号でないのに繰り返すもの: 寸法線の矢印とハッチング。**誤検出の源。**"""
    rng = random.Random(seed)
    size = 200.0 * PT_PER_MM
    for _ in range(arrows):
        x, y = rng.uniform(80, 1100), rng.uniform(80, 780)
        shape = page.new_shape()
        # 寸法線の矢羽根(実寸 60mm 程度。記号よりずっと小さい)
        a = 60.0 * PT_PER_MM
        shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + a, y - a / 3))
        shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + a, y + a / 3))
        shape.finish(color=(0, 0, 0), width=0.2)
        shape.commit()
    for index in range(hatch):
        shape = page.new_shape()
        x = 300 + index * 4.0
        shape.draw_line(pymupdf.Point(x, 600), pymupdf.Point(x + 20, 620))
        shape.finish(color=(0, 0, 0), width=0.2)
        shape.commit()
    # 壁(長い線)と外枠
    for y in (150.0, 450.0, 700.0):
        shape = page.new_shape()
        shape.draw_line(pymupdf.Point(60, y), pymupdf.Point(1120, y))
        shape.finish(color=(0, 0, 0), width=1.2)
        shape.commit()
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, 1170, 822))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    _ = size


def make_plan(path: Path, counts: dict[str, int], seed: int = 0, clutter: bool = True) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    if clutter:
        draw_clutter(page, seed)
    rng = random.Random(seed + 1)
    size = 200.0 * PT_PER_MM
    for name, count in counts.items():
        for _ in range(count):
            draw = SYMBOLS[name]
            draw(page, rng.uniform(100, 1080), rng.uniform(100, 760), rng.choice([0, 90, 180, 270, 45]), size)
    doc.save(path)
    doc.close()
    return path


def make_legend(path: Path, names: list[str]) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    size = 200.0 * PT_PER_MM
    y = 150.0
    for name in names:
        page.insert_text(pymupdf.Point(80, y + 5), name, fontname="japan")
        SYMBOLS[name](page, 400.0, y, 0.0, size)
        y += 120.0
    doc.save(path)
    doc.close()
    return path


@dataclass
class Result:
    label: str
    expected: dict[str, int]
    got: dict[str, int]
    unnamed_clusters: int
    total_clusters: int


def evaluate(tmp: Path, counts: dict[str, int], seed: int, clutter: bool, label: str) -> Result:
    plan = make_plan(tmp / f"{label}_plan.pdf", counts, seed=seed, clutter=clutter)
    legend = make_legend(tmp / f"{label}_legend.pdf", list(SYMBOLS))
    clusters = find_repeated_symbols(plan, 0, SCALE)
    named = name_clusters(clusters, read_legend_symbols(legend, 0, SCALE))
    got = {n.name: n.count for n in named if n.name is not None}
    return Result(
        label=label,
        expected=counts,
        got=got,
        unnamed_clusters=sum(1 for n in named if n.name is None),
        total_clusters=len(clusters),
    )


def main() -> None:
    tmp = Path(__file__).resolve().parent.parent / ".bench_tmp"
    tmp.mkdir(exist_ok=True)
    report: dict = {"負の対照": {}, "本測定": {}}

    # --- 負の対照(先に回す) ---
    empty = evaluate(tmp, {}, seed=7, clutter=True, label="neg_記号なし")
    report["負の対照"]["記号を1つも置かない図面"] = {
        "出た群": empty.total_clusters,
        "名前が付いた群": len(empty.got),
        "名前なしの群": empty.unnamed_clusters,
    }
    single = evaluate(tmp, {"埋込コンセント": 1}, seed=8, clutter=False, label="neg_1個だけ")
    report["負の対照"]["同じ形を1個だけ置いた図面"] = {
        "出た群": single.total_clusters,
        "名前が付いた群": len(single.got),
    }
    arrows_only = make_plan(tmp / "neg_arrows.pdf", {}, seed=9, clutter=True)
    report["負の対照"]["寸法線の矢印とハッチングだけ"] = {
        "出た群": len(find_repeated_symbols(arrows_only, 0, SCALE)),
    }

    # --- 本測定(3 回、置く場所と回転を変えて) ---
    truth = {"埋込コンセント": 12, "片切スイッチ": 9, "引掛シーリング": 5, "TEL引出口": 3}
    runs = []
    for seed in (0, 1, 2):
        r = evaluate(tmp, truth, seed=seed, clutter=True, label=f"run{seed}")
        hit = sum(1 for k, v in truth.items() if r.got.get(k) == v)
        runs.append(
            {
                "seed": seed,
                "正解": truth,
                "読めた": r.got,
                "記号の群の再現": f"{len(r.got)}/{len(truth)}",
                "個数の的中": f"{hit}/{len(truth)}",
                "誤検出(名前の付かない群)": r.unnamed_clusters,
                "出た群の総数": r.total_clusters,
            }
        )
    report["本測定"]["各回"] = runs
    report["測れていないこと"] = [
        "実図面での誤り率(実図面 PDF がこの作業環境に無い)",
        "スキャンされたページ(常に0件。0件は「記号が無い」ではない)",
        "見積の行が埋まるかどうか(数量化と対応付けがもう1段ある)",
    ]
    out = Path(__file__).resolve().parent.parent / "docs" / "a1_repeated_symbol_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
