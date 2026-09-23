"""A-2 続き: 壁芯(芯々)の面積の測定。**合成のベクター PDF の上でのみ測る。**

採否の基準は測る前に `docs/a2_center_line_area_criteria.md` に置いてある。
結果を見てから基準を変えない。
前提の決定は `docs/decision_area_basis.md`。

**負の対照(入れ替え対照を含む)を必ず一緒に回す。**
とくに**壁が 1 本線の図面で壁芯が出ないこと**を先に確かめる。
出るなら、厚みの無いところから厚みを作っていることになる。

実図面 PDF はこの作業環境に無いので、実図面での誤り率はここでは測れない。
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale  # noqa: E402
from benchmarks.run_room_outline_eval import ALL_ROOMS, WALLS, build_plan  # noqa: E402

SCALE = DrawingScale(denominator=50.0, source_text="1/50")
PT_PER_MM = (1 / 50) / 25.4 * 72
ORIGIN = (120.0, 120.0)
WALL_MM = 150.0


def mm(value: float) -> float:
    return value * PT_PER_MM


def _line(page, x1, y1, x2, y2) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x1, y1), pymupdf.Point(x2, y2))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _draw_wall_face(page, wall, offset_mm: float, extend_mm: float = 0.0) -> None:
    """壁の片面を 1 本引く。開口があれば切れ目にする。

    **通り芯から ``offset_mm`` だけずらした線**を引く。
    通り芯そのものは引かない(実図面と同じで、描いてあるのは壁の面である)。

    ``extend_mm`` は線の両端を伸ばす長さ。**実図面では壁の面の線は
    交わる相手の壁まで伸ばしてある**(伸ばさないと隅に隙間が開く)。
    """
    x1, y1, x2, y2, center, width = wall
    ox, oy = ORIGIN
    if y1 == y2:
        y1 = y2 = y1 + offset_mm
        low, high = min(x1, x2), max(x1, x2)
        x1, x2 = low - extend_mm, high + extend_mm
    else:
        x1 = x2 = x1 + offset_mm
        low, high = min(y1, y2), max(y1, y2)
        y1, y2 = low - extend_mm, high + extend_mm
    ax, ay = ox + mm(x1), oy + mm(y1)
    bx, by = ox + mm(x2), oy + mm(y2)
    if width <= 0:
        _line(page, ax, ay, bx, by)
        return
    gap = mm(width)
    if abs(bx - ax) >= abs(by - ay):
        cx = ox + mm(center)
        _line(page, ax, ay, cx - gap / 2, ay)
        _line(page, cx + gap / 2, by, bx, by)
    else:
        cy = oy + mm(center)
        _line(page, ax, ay, ax, cy - gap / 2)
        _line(page, bx, cy + gap / 2, bx, by)


def build_two_line_plan(path: Path, wall_mm: float = WALL_MM) -> Path:
    """壁を 2 本線で描いた間取り。**通り芯は描かない。**

    通り芯の座標は `WALLS` のまま。だから**正解の壁芯の面積は
    `ALL_ROOMS` の寸法そのもの**で、内法はそれより壁の厚みぶん小さい。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(950, 810), "縮尺 1/50")
    for wall in WALLS:
        for offset in (-wall_mm / 2.0, wall_mm / 2.0):
            _draw_wall_face(page, wall, offset, extend_mm=wall_mm / 2.0)
    ox, oy = ORIGIN
    for room in ALL_ROOMS:
        page.insert_text(
            pymupdf.Point(ox + mm(room.x) + mm(wall_mm) + 8, oy + mm(room.y) + mm(wall_mm) + 16),
            room.name,
            fontname="japan",
        )
    doc.save(path)
    doc.close()
    return path


def _measure(pdf: Path, basis: str) -> dict[str, dict]:
    out = {}
    for room in find_room_outlines(pdf, 0, SCALE, area_basis=basis):
        if room.name is None:
            continue
        out[room.name] = {
            "面積": round(room.area_sqm, 4),
            "数え方": room.area_basis,
            "理由": room.area_basis_note,
        }
    return out


def _errors(got: dict[str, dict], truth: dict[str, float]) -> dict:
    pairs = [(name, data["面積"], truth[name]) for name, data in got.items() if name in truth]
    if not pairs:
        return {"件数": 0}
    rel = [abs(value - expected) / expected for _, value, expected in pairs]
    return {
        "件数": len(pairs),
        "中央値": round(statistics.median(rel), 5),
        "最大": round(max(rel), 5),
        "いちばんずれた室": max(pairs, key=lambda p: abs(p[1] - p[2]) / p[2])[0],
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    tmp = root / ".bench_tmp"
    tmp.mkdir(exist_ok=True)

    truth_center = {r.name: r.area_sqm for r in ALL_ROOMS}
    truth_inner = {
        r.name: (r.w - WALL_MM) * (r.h - WALL_MM) / 1_000_000.0 for r in ALL_ROOMS
    }
    report: dict = {"負の対照": {}, "本測定": {}, "3回の繰り返し": {}}

    two_line = build_two_line_plan(tmp / "two_line.pdf")
    single = build_plan(tmp / "single_line.pdf", "plain")

    # --- 負の対照 ---
    single_center = _measure(single, "壁芯")
    named_center = {n: d for n, d in single_center.items() if d["数え方"] == "壁芯"}
    report["負の対照"]["1_壁が1本線の図面"] = {
        "取れた室": len(single_center),
        "壁芯を名乗った室": len(named_center),
        "理由の例": next(iter(single_center.values()))["理由"] if single_center else "",
        "期待": "壁芯は1件も出ない",
        "合格": len(single_center) > 0 and len(named_center) == 0,
    }

    thick = build_two_line_plan(tmp / "two_line_thick.pdf", wall_mm=WALL_MM * 2)
    thick_center = _measure(thick, "壁芯")
    base_center = _measure(two_line, "壁芯")
    moved = {
        name: round(abs(thick_center[name]["面積"] - data["面積"]) / data["面積"], 5)
        for name, data in base_center.items()
        if name in thick_center
    }
    report["負の対照"]["2_壁の厚みを2倍にする"] = {
        "壁芯が動いた割合": moved,
        "いちばん動いた": max(moved.values()) if moved else None,
        "期待": "通り芯は動いていないので壁芯も動かない(1%以下)",
        "合格": bool(moved) and max(moved.values()) <= 0.01,
    }

    inner_only = _measure(two_line, "内法")
    report["負の対照"]["3_同じ図面を内法で数える"] = {
        "内法を名乗った室": sum(1 for d in inner_only.values() if d["数え方"] == "内法"),
        "内法の誤差": _errors(inner_only, truth_inner),
        "期待": "内法として数えれば内法の正解に合う",
        "合格": _errors(inner_only, truth_inner).get("最大", 1.0) <= 0.01,
    }

    # 入れ替え対照: 各室の壁芯を、面積が違う別の室の正解と突き合わせる。
    names = [r.name for r in ALL_ROOMS]
    swapped = {
        name: truth_center[names[(index + 1) % len(names)]]
        for index, name in enumerate(names)
    }
    swap_err = _errors(base_center, swapped)
    report["負の対照"]["4_入れ替え対照"] = {
        "入れ替えたときの誤差": swap_err,
        "期待": "誤差が大きく出る(中央値 10%以上)",
        "合格": swap_err.get("中央値", 0.0) >= 0.10,
    }

    # --- 本測定 ---
    silent = [
        name for name, data in base_center.items()
        if data["数え方"] == "壁芯" and "厚み" not in data["理由"]
    ]
    report["本測定"] = {
        "輪郭が取れた室": len(base_center),
        "壁芯を出せた室": sum(1 for d in base_center.values() if d["数え方"] == "壁芯"),
        "壁芯の誤差": _errors(
            {n: d for n, d in base_center.items() if d["数え方"] == "壁芯"}, truth_center
        ),
        "黙って出した数": len(silent),
        "室ごと": {
            name: {
                "出した壁芯": data["面積"],
                "正解の壁芯": round(truth_center.get(name, 0.0), 4),
                "数え方": data["数え方"],
            }
            for name, data in sorted(base_center.items())
        },
    }

    # --- 3 回の繰り返し ---
    repeats = []
    for run in range(3):
        pdf = build_two_line_plan(tmp / f"two_line_r{run}.pdf")
        got = _measure(pdf, "壁芯")
        repeats.append({
            "壁芯を出せた室": sum(1 for d in got.values() if d["数え方"] == "壁芯"),
            "誤差": _errors({n: d for n, d in got.items() if d["数え方"] == "壁芯"}, truth_center),
        })
    report["3回の繰り返し"] = {"各回": repeats, "すべて同じか": all(r == repeats[0] for r in repeats)}

    errors = report["本測定"]["壁芯の誤差"]
    report["判定"] = {
        "誤差が中央値・最大ともに1%以下":
            errors.get("件数", 0) > 0
            and errors.get("中央値", 1.0) <= 0.01
            and errors.get("最大", 1.0) <= 0.01,
        "黙って出した数が0": len(silent) == 0,
        "負の対照はすべて合格": all(v["合格"] for v in report["負の対照"].values()),
    }

    out = root / "docs/a2_center_line_area_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
