"""A-2: 室の輪郭と面積の測定。**合成のベクター PDF の上でのみ測る。**

採否の基準は測る前に `docs/a2_room_outline_criteria.md` に置いてある。
結果を見てから基準を変えない。

**負の対照(入れ替え対照を含む)を必ず一緒に回す。**
実図面 PDF はこの作業環境に無いので、実図面での誤り率はここでは測れない。
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale  # noqa: E402

SCALE = DrawingScale(denominator=50.0, source_text="1/50")
PT_PER_MM = (1 / 50) / 25.4 * 72
ORIGIN = (120.0, 120.0)


def mm(value: float) -> float:
    return value * PT_PER_MM


def line(page, x1, y1, x2, y2, width=0.5):
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x1, y1), pymupdf.Point(x2, y2))
    shape.finish(color=(0, 0, 0), width=width)
    shape.commit()


@dataclass(frozen=True)
class RoomSpec:
    """正解の室。位置と大きさは実寸ミリメートル(内法)。"""

    name: str
    x: float
    y: float
    w: float
    h: float

    @property
    def area_sqm(self) -> float:
        return self.w * self.h / 1_000_000.0


#: 3LDK 風の間取り。**壁は共有する 1 本として持つ**(室ごとに 4 辺を描くと、
#: 隣の室の壁が相手の開口をふさいでしまい、実図面と違うものを測ることになる)。
ALL_ROOMS = (
    RoomSpec("リビングダイニング", 0, 0, 5400, 4200),
    RoomSpec("洋室1", 5400, 0, 3600, 4200),
    RoomSpec("廊下", 0, 4200, 9000, 800),
    RoomSpec("洋室2", 0, 5000, 3300, 3000),
    RoomSpec("洋室3", 3300, 5000, 3300, 3000),
    RoomSpec("浴室", 6600, 5000, 2400, 1600),
    RoomSpec("トイレ", 6600, 6600, 2400, 1400),
)

#: 壁 1 本 = ``(x1, y1, x2, y2, 開口の中心, 開口の幅)``。実寸ミリメートル。
#: 開口の幅が 0 なら切れ目なし。
WALLS = (
    (0, 0, 9000, 0, 0.0, 0.0),            # 外周 上
    (0, 8000, 9000, 8000, 0.0, 0.0),      # 外周 下
    (0, 0, 0, 8000, 0.0, 0.0),            # 外周 左
    (9000, 0, 9000, 8000, 0.0, 0.0),      # 外周 右
    (5400, 0, 5400, 4200, 0.0, 0.0),      # LD と洋室1
    (0, 4200, 9000, 4200, 2700.0, 900.0), # 廊下の上(LD の出入口)
    (0, 4200, 9000, 4200, 7200.0, 800.0), # 廊下の上(洋室1 の出入口)
    (0, 5000, 9000, 5000, 1650.0, 800.0), # 廊下の下(洋室2 の出入口)
    (0, 5000, 9000, 5000, 4950.0, 800.0), # 廊下の下(洋室3 の出入口)
    (0, 5000, 9000, 5000, 7800.0, 700.0), # 廊下の下(浴室の出入口)
    (3300, 5000, 3300, 8000, 0.0, 0.0),   # 洋室2 と洋室3
    (6600, 5000, 6600, 8000, 7300.0, 700.0),  # 洋室3 と水回り(トイレの出入口)
    (6600, 6600, 9000, 6600, 0.0, 0.0),   # 浴室とトイレ
)


def _draw_wall(page, wall, wide_openings: bool = False) -> None:
    """壁 1 本を描く。開口があれば切れ目にする。

    同じ直線上の壁が 2 本以上あるとき(廊下の上下)、それぞれが自分の開口だけを
    開けて全長を描く。重なった線は実装側が切って 1 本にまとめる。
    ここで全長を描くのは実図面と同じで、**壁は室ごとではなく通りごとに描かれる**。
    """
    x1, y1, x2, y2, center, width = wall
    if wide_openings and width > 0:
        width = 3000.0
    ox, oy = ORIGIN
    ax, ay = ox + mm(x1), oy + mm(y1)
    bx, by = ox + mm(x2), oy + mm(y2)
    if width <= 0:
        line(page, ax, ay, bx, by)
        return
    gap = mm(width)
    if abs(bx - ax) >= abs(by - ay):
        cx = ox + mm(center)
        line(page, ax, ay, cx - gap / 2, ay)
        line(page, cx + gap / 2, by, bx, by)
    else:
        cy = oy + mm(center)
        line(page, ax, ay, ax, cy - gap / 2)
        line(page, bx, cy + gap / 2, bx, by)


def build_plan(path: Path, condition: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(950, 810), "縮尺 1/50")
    ox, oy = ORIGIN

    for wall in WALLS:
        _draw_wall(page, wall, wide_openings=(condition == "wide_opening"))

    for room in ALL_ROOMS:
        page.insert_text(
            pymupdf.Point(ox + mm(room.x) + 8, oy + mm(room.y) + 16), room.name, fontname="japan"
        )

    if condition == "double_wall":
        # 通りごとに、壁のもう 1 本の線を 120mm ずらして描く。
        # 壁の中身が細長い面として生まれる。
        for x1, y1, x2, y2, center, width in WALLS:
            for offset in (-120.0, 120.0):
                if y1 == y2:
                    shifted = (x1, y1 + offset, x2, y2 + offset, center, width)
                else:
                    shifted = (x1 + offset, y1, x2 + offset, y2, center, width)
                _draw_wall(page, shifted)

    if condition == "fixtures":
        # 壁に接する造作(カウンター・浴槽・便器)。**室が分割される危険がある。**
        for room, w_mm, h_mm in (
            (ALL_ROOMS[0], 2400.0, 600.0),
            (ALL_ROOMS[5], 2400.0, 700.0),
            (ALL_ROOMS[6], 500.0, 700.0),
        ):
            x0 = ox + mm(room.x)
            y0 = oy + mm(room.y)
            x1, y1 = x0 + mm(w_mm), y0 + mm(h_mm)
            for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
                line(page, a, b, c, d, width=0.3)

    # 寸法線(室の外側)。輪郭に混ざってはいけない。
    for index in range(6):
        y = oy - mm(400.0) - index * 4.0
        line(page, ox, y, ox + mm(9000.0), y, width=0.2)

    doc.save(path)
    doc.close()
    return path


def evaluate(path: Path, wall_mm: float = 0.0) -> dict:
    """``wall_mm`` を渡すと、**内法(壁の内側)の正解**でも採点する。

    壁を 2 本線で描いた図面では、線で囲まれた最小の領域は内法になる。
    通り芯の寸法を正解にしたままだと、**内法と芯々の差がそのまま
    「誤差」として出る。** これは実装の誤りではないので、分けて出す。
    """
    got = find_room_outlines(path, 0, SCALE)
    truth = {room.name: room.area_sqm for room in ALL_ROOMS}
    inner_truth = {
        room.name: max(room.w - 2 * wall_mm, 1.0) * max(room.h - 2 * wall_mm, 1.0) / 1_000_000.0
        for room in ALL_ROOMS
    }
    by_name = {}
    for room in got:
        if room.name is not None and room.name not in by_name:
            by_name[room.name] = room

    errors = []
    matched = []
    for name, expected in truth.items():
        room = by_name.get(name)
        if room is None:
            continue
        matched.append(name)
        errors.append(abs(room.area_sqm - expected) / expected)

    # 入れ替え対照: 室名を 1 つずらして突き合わせる。
    names = list(truth)
    shuffled_errors = []
    for index, name in enumerate(names):
        room = by_name.get(name)
        if room is None:
            continue
        other = truth[names[(index + 1) % len(names)]]
        shuffled_errors.append(abs(room.area_sqm - other) / other)

    inner_errors = []
    for name, expected in inner_truth.items():
        room = by_name.get(name)
        if room is not None:
            inner_errors.append(abs(room.area_sqm - expected) / expected)

    invented = [
        (r.name, round(r.area_sqm, 2)) for r in got if r.name is not None and r.name not in truth
    ]
    return {
        "室の再現": f"{len(matched)}/{len(truth)}",
        "出なかった室": [n for n in truth if n not in matched],
        "面積の相対誤差_中央値": round(statistics.median(errors), 5) if errors else None,
        "面積の相対誤差_最大": round(max(errors), 5) if errors else None,
        "入れ替え対照_相対誤差_中央値": round(statistics.median(shuffled_errors), 5)
        if shuffled_errors
        else None,
        "内法を正解にしたときの相対誤差_中央値": round(statistics.median(inner_errors), 5)
        if (wall_mm and inner_errors)
        else None,
        "内法を正解にしたときの相対誤差_最大": round(max(inner_errors), 5)
        if (wall_mm and inner_errors)
        else None,
        "名前が付いた室": len(by_name),
        "作り話の室(正解に無い名前)": invented,
        "名前が付かなかった輪郭": sum(1 for r in got if r.name is None),
        "仮に閉じた辺を持つ室": sum(1 for r in got if r.virtual_edges > 0),
        "出た輪郭の総数": len(got),
    }


def main() -> None:
    tmp = Path(__file__).resolve().parent.parent / ".bench_tmp"
    tmp.mkdir(exist_ok=True)
    report: dict = {"負の対照": {}, "本測定": {}}

    # --- 負の対照(先に回す) ---
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(950, 810), "縮尺 1/50")
    for index in range(30):
        line(page, 100 + index * 30, 200, 100 + index * 30, 400)
    scattered = tmp / "neg_scattered.pdf"
    doc.save(scattered)
    doc.close()
    report["負の対照"]["閉じた領域が無い図面"] = len(find_room_outlines(scattered, 0, SCALE))

    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(950, 810), "縮尺 1/50")
    for row in range(6):
        line(page, 200, 200 + row * mm(900.0), 200 + 4 * mm(1500.0), 200 + row * mm(900.0))
    for col in range(5):
        line(page, 200 + col * mm(1500.0), 200, 200 + col * mm(1500.0), 200 + 5 * mm(900.0))
    # **升目を埋める。** 空欄だらけの格子は実図面では表ではなく平面図の壁である。
    for row in range(5):
        for col in range(4):
            page.insert_text(
                pymupdf.Point(210 + col * mm(1500.0), 215 + row * mm(900.0)),
                f"W{row}{col}",
                fontname="japan",
            )
    table = tmp / "neg_table.pdf"
    doc.save(table)
    doc.close()
    report["負の対照"]["罫線の表だけの図面"] = len(find_room_outlines(table, 0, SCALE))

    # --- 本測定 ---
    for condition in ("clean", "double_wall", "fixtures", "wide_opening"):
        path = build_plan(tmp / f"plan_{condition}.pdf", condition)
        report["本測定"][condition] = evaluate(
            path, wall_mm=120.0 if condition == "double_wall" else 0.0
        )

    report["正解の室"] = {r.name: round(r.area_sqm, 3) for r in ALL_ROOMS}
    report["測れていないこと"] = [
        "実図面での誤り率(実図面 PDF がこの作業環境に無い)",
        "面積が内法か壁芯か(この実装では決めない。area_basis は常に「不明」)",
        "壁・天井の面積(輪郭と階高が要る。階高は別の入力)",
        "見積の行が埋まるかどうか",
    ]
    out = Path(__file__).resolve().parent.parent / "docs" / "a2_room_outline_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
