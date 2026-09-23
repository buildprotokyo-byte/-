"""6周目: 実図面の壁が 1 本線で描かれているか 2 本線で描かれているかを数える。

採否の基準は測る前に `docs/a2_real_drawing_wall_criteria.md` に置いてある。
結果を見てから基準を変えない。

**図面のパスは引数で渡す。** 実図面・見積明細はリポジトリに置かない決まりなので、
既定値を持たせない。**中身(室名・寸法・数量)は出力しない。件数だけを出す。**

使い方:
    python benchmarks/measure_real_drawing_walls.py <PDFのパス> [出力先のJSON]
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    _segments,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale  # noqa: E402

#: 「長い直線」とみなす下限(ポイント)。1/50 なら実寸 700mm、1/100 なら 1400mm。
LONG_PT = 40.0

#: 壁の厚みとしてありうる 2 本線の間隔(ポイント)。
#: 1/50 なら実寸 35〜530mm、1/100 なら 70〜1060mm。**どちらの縮尺でも壁を含む幅。**
MIN_GAP_PT = 1.0
MAX_GAP_PT = 15.0

#: 平行とみなす角度の差(度)。
ANGLE_TOLERANCE_DEG = 1.0

#: 相棒とみなすのに要る重なり(短いほうの長さに対する割合)。
MIN_OVERLAP = 0.5


def _describe(segment: tuple[float, float, float, float]) -> tuple[float, float, float] | None:
    """線分を (角度[0,pi), 直線の位置, 長さ) に直す。短すぎる線分は None。"""
    x1, y1, x2, y2 = segment
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < LONG_PT:
        return None
    angle = math.atan2(dy, dx) % math.pi
    # 原点から直線までの符号つき距離(法線 (-sin, cos) に射影)
    offset = -x1 * math.sin(angle) + y1 * math.cos(angle)
    return angle, offset, length


def _span(segment: tuple[float, float, float, float], angle: float) -> tuple[float, float]:
    """線分を、その直線に沿った区間に直す。"""
    x1, y1, x2, y2 = segment
    ux, uy = math.cos(angle), math.sin(angle)
    a = x1 * ux + y1 * uy
    b = x2 * ux + y2 * uy
    return (min(a, b), max(a, b))


def count_paired_lines(segments: list[tuple[float, float, float, float]]) -> dict:
    """長い直線のうち、平行で重なる相棒が近くにあるものを数える。

    **室の輪郭が取れなくても、壁の描き方だけを見るための指標。**
    2 本線で描かれた壁はここに出る。1 本線の壁は出ない。
    """
    described: list[tuple[float, float, float, tuple[float, float]]] = []
    for segment in segments:
        info = _describe(segment)
        if info is None:
            continue
        angle, offset, length = info
        described.append((angle, offset, length, _span(segment, angle)))

    if not described:
        return {"長い直線": 0, "相棒のある直線": 0, "間隔の分布": {}}

    tolerance = math.radians(ANGLE_TOLERANCE_DEG)
    buckets: dict[int, list[int]] = defaultdict(list)
    for index, (angle, _, _, _) in enumerate(described):
        key = int(angle / tolerance)
        for neighbour in (key - 1, key, key + 1):
            buckets[neighbour].append(index)

    paired: set[int] = set()
    gaps: Counter = Counter()
    seen: set[tuple[int, int]] = set()
    for key, members in buckets.items():
        if key != int(described[members[0]][0] / tolerance):
            pass  # 隣の升目も見るので、ここでは絞らない
        members = sorted(set(members), key=lambda i: described[i][1])
        for position, first in enumerate(members):
            a_angle, a_offset, a_length, a_span = described[first]
            for second in members[position + 1:]:
                b_angle, b_offset, b_length, b_span = described[second]
                gap = abs(b_offset - a_offset)
                if gap > MAX_GAP_PT:
                    break  # 位置順に並んでいるので、これ以上は遠い
                if gap < MIN_GAP_PT:
                    continue
                if abs(a_angle - b_angle) > tolerance:
                    continue
                overlap = min(a_span[1], b_span[1]) - max(a_span[0], b_span[0])
                if overlap < MIN_OVERLAP * min(a_length, b_length):
                    continue
                pair = (first, second) if first < second else (second, first)
                if pair in seen:
                    continue
                seen.add(pair)
                paired.add(first)
                paired.add(second)
                gaps[round(gap, 1)] += 1

    return {
        "長い直線": len(described),
        "相棒のある直線": len(paired),
        "相棒のある割合": round(len(paired) / len(described), 4),
        "組の数": len(seen),
        "間隔の分布(多い順に10件)": dict(gaps.most_common(10)),
    }


_REASON = re.compile(r"(\d+)/(\d+) の辺で壁の厚みが図面から読めない")


def measure_rooms(pdf_path: Path, page_index: int, denominator: float) -> dict:
    """そのページで室の輪郭が取れるか、芯々が出せるか、出せない理由は何か。

    **室名も面積も出さない。件数だけを出す。**
    """
    scale = DrawingScale(denominator=denominator, source_text=f"1/{denominator:g}")
    rooms = find_room_outlines(pdf_path, page_index, scale, area_basis="壁芯")
    unknown_edges: Counter = Counter()
    all_edges_unknown = 0
    for room in rooms:
        if room.area_basis == "壁芯":
            continue
        match = _REASON.search(room.area_basis_note)
        if match is None:
            unknown_edges["理由が別"] += 1
            continue
        bad, total = int(match.group(1)), int(match.group(2))
        unknown_edges[f"{bad}/{total}"] += 1
        if bad == total:
            all_edges_unknown += 1
    settled = sum(1 for room in rooms if room.area_basis == "壁芯")
    return {
        "輪郭が取れた面": len(rooms),
        "芯々を出せた面": settled,
        "芯々の割合": round(settled / len(rooms), 4) if rooms else None,
        "全部の辺で厚みが読めない面": all_edges_unknown,
        "読めない辺の内訳(多い順に6件)": dict(unknown_edges.most_common(6)),
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    report: dict = {"ページごと": {}}
    with pymupdf.open(pdf_path) as doc:
        report["ページ数"] = doc.page_count
        for index in range(doc.page_count):
            page = doc.load_page(index)
            drawings = len(page.get_drawings())
            segments = _segments(page)
            result = count_paired_lines(segments)
            report["ページごと"][f"p{index + 1}"] = {
                "図形": drawings,
                "線分": len(segments),
                **result,
            }

    # --- 室の輪郭と芯々。縮尺は 2 つ試す(P011 の縮尺は図面から確かめていない)。
    report["室と芯々"] = {}
    for denominator in (50.0, 100.0):
        per_page = {}
        for index in range(report["ページ数"]):
            try:
                per_page[f"p{index + 1}"] = measure_rooms(pdf_path, index, denominator)
            except Exception as error:  # noqa: BLE001
                per_page[f"p{index + 1}"] = {"失敗": f"{type(error).__name__}"}
        total = sum(d.get("輪郭が取れた面", 0) for d in per_page.values())
        settled = sum(d.get("芯々を出せた面", 0) for d in per_page.values())
        report["室と芯々"][f"1/{denominator:g}"] = {
            "ページごと": per_page,
            "輪郭が取れた面の合計": total,
            "芯々を出せた面の合計": settled,
            "芯々の割合": round(settled / total, 4) if total else None,
            "全部の辺で厚みが読めない面の合計": sum(
                d.get("全部の辺で厚みが読めない面", 0) for d in per_page.values()
            ),
        }

    # --- 負の対照 2: 同じページを 2 回読む
    twice = [measure_rooms(pdf_path, 7, 100.0) for _ in range(2)]
    report["負の対照"] = {
        "2_同じページを2回読む": {
            "1回目": twice[0]["輪郭が取れた面"],
            "2回目": twice[1]["輪郭が取れた面"],
            "合格": twice[0] == twice[1],
        }
    }

    heavy = {
        name: data for name, data in report["ページごと"].items() if data["長い直線"] >= 50
    }
    report["まとめ"] = {
        "長い直線が50本以上あるページ": len(heavy),
        "そのうち相棒のある割合が0.5以上": sum(
            1 for data in heavy.values() if data["相棒のある割合"] >= 0.5
        ),
        "そのうち相棒のある割合が0.2未満": sum(
            1 for data in heavy.values() if data["相棒のある割合"] < 0.2
        ),
        "割合の一覧": {name: data["相棒のある割合"] for name, data in sorted(heavy.items())},
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
