"""周33 の測定: **位相の語は、数量を出した物のそばにあるか。**

基準は `docs/loop_round33_phase_word_proximity_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

前の周(周32)との違いは**技術ひとつ**である。周32 はページに語が在るかを数えた。
ここでは同じ文字を**位置**で見る。出典(紙に印字された文字)は変えていない。

**周32 の反省を入れてある。** 周32 の囮(材料と性能の語)はこの図面にほとんど無く、
勝ち目が薄かった。ここでは**紙に実際に印字されている位相以外の語**から引く。
インクの在る所から引くので、囮に勝ち目がある。

**紙から取り出した文字は 1 文字も出さない。** 出すのは件数・割合・ページ番号・距離だけ。

実行::

    .venv/bin/python -m benchmarks.measure_phase_word_proximity \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r33.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fitz

from benchmarks.measure_phase_printed import PHASE_WORDS
from estimating.from_intake import quantities_from_intake

#: 紙の 1 ポイントは何ミリか。**実寸には直さない**(縮尺がページごとに違うため)。
MM_PER_POINT = 25.4 / 72.0

#: 根拠の中で座標が入っている欄。`rect_pt` は 4 つ組なので**点として使わない**。
COORD_KEYS = ("center_pt", "polygon_pt", "positions_pt")

#: 近さの 3 段(紙の上のミリ)。
DISTANCES_MM: tuple[float, ...] = (10.0, 25.0, 50.0)

LINE1_MIN_SHARE = 0.10
LINE1_MARGIN = 0.20
DECOY_DRAWS = 10

Point = tuple[float, float]


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _as_points(value: Any) -> list[Point]:
    """`[x, y]` も `[[x, y], ...]` も点の並びに直す。**4 つ組は点ではない。**"""
    if not isinstance(value, (list, tuple)) or not value:
        return []
    if len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        return [(float(value[0]), float(value[1]))]
    out: list[Point] = []
    for item in value:
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and all(isinstance(v, (int, float)) for v in item)
        ):
            out.append((float(item[0]), float(item[1])))
    return out


def points_of(item: Any) -> list[Point]:
    """数量 1 件の根拠に入っている紙の上の点。**無ければ空。**"""
    found: list[Point] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in COORD_KEYS:
                    found.extend(_as_points(child))
                else:
                    walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(getattr(item, "provenance", {}) or {})
    return found


def page_of(item: Any) -> int | None:
    provenance = getattr(item, "provenance", {}) or {}
    number = provenance.get("page_number")
    return number if isinstance(number, int) else None


def phase_boxes(page: Any) -> list[Point]:
    """位相の語が刷られている場所の中心。**語そのものは返さない。**"""
    centres: list[Point] = []
    for word in PHASE_WORDS:
        for rect in page.search_for(word):
            centres.append(((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2))
    return centres


def other_boxes(page: Any) -> list[Point]:
    """位相の語を含まない、印字された語の中心。**囮の引き出し。**"""
    centres: list[Point] = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        token = normalise(text)
        if not token.strip():
            continue
        if any(word in token for word in PHASE_WORDS):
            continue
        centres.append(((x0 + x1) / 2, (y0 + y1) / 2))
    return centres


def nearest_mm(points: Sequence[Point], targets: Sequence[Point]) -> float | None:
    """物の点から、いちばん近い語までの距離(紙の上のミリ)。"""
    if not points or not targets:
        return None
    best = math.inf
    for px, py in points:
        for tx, ty in targets:
            best = min(best, math.hypot(px - tx, py - ty))
    return best * MM_PER_POINT


def share_within(
    distances: Sequence[float | None], limit_mm: float, total: int
) -> float:
    """限界の中に入った割合。**分母は座標を持つ数量の総数で固定する。**

    届かなかったもの(`None`)を分母から外すと、届かないページが多いほど
    割合が上がってしまう。
    """
    if total <= 0:
        return 0.0
    return sum(1 for d in distances if d is not None and d <= limit_mm) / total


def hits_within(
    distances: Sequence[float | None], limit_mm: float
) -> set[int]:
    """限界の中に入った数量の番号。**線3(中身の入れ替わり)を見るため。**"""
    return {
        index
        for index, value in enumerate(distances)
        if value is not None and value <= limit_mm
    }


def distances_for(
    quantities: Sequence[Any], boxes_by_page: Mapping[int, Sequence[Point]]
) -> list[float | None]:
    return [
        nearest_mm(points_of(item), boxes_by_page.get(page_of(item) or -1, ()))
        for item in quantities
    ]


def decoy_shares(
    quantities: Sequence[Any],
    others_by_page: Mapping[int, Sequence[Point]],
    how_many_by_page: Mapping[int, int],
    seed: int,
) -> dict[float, float]:
    """囮: 位相以外の印字語から同じ個数を引く。10 回の中央値。"""
    rng = random.Random(seed)
    per_draw: list[list[float | None]] = []
    for _ in range(DECOY_DRAWS):
        drawn: dict[int, list[Point]] = {}
        for page, wanted in how_many_by_page.items():
            pool = list(others_by_page.get(page, ()))
            if not pool or wanted <= 0:
                drawn[page] = []
            elif wanted >= len(pool):
                drawn[page] = pool
            else:
                drawn[page] = rng.sample(pool, wanted)
        per_draw.append(distances_for(quantities, drawn))
    total = len(quantities)
    return {
        limit: statistics.median(
            share_within(distances, limit, total) for distances in per_draw
        )
        for limit in DISTANCES_MM
    }


def measure(pdf: Path, answers: Path, seed: int) -> tuple[dict, dict]:
    from intake.drawing_intake import IntakeConfig, read_drawing

    phase_by_page: dict[int, list[Point]] = {}
    others_by_page: dict[int, list[Point]] = {}
    with fitz.open(pdf) as doc:
        for index, page in enumerate(doc, start=1):
            phase_by_page[index] = phase_boxes(page)
            others_by_page[index] = other_boxes(page)

    intake = read_drawing(
        IntakeConfig(case_id="round33", pdf_path=pdf, answers_path=answers)
    )
    everything = list(quantities_from_intake(intake))
    located = [item for item in everything if points_of(item) and page_of(item)]

    real = distances_for(located, phase_by_page)
    total = len(located)
    wanted = {page: len(points) for page, points in phase_by_page.items()}
    decoys = decoy_shares(located, others_by_page, wanted, seed)

    real_shares = {limit: share_within(real, limit, total) for limit in DISTANCES_MM}
    narrow, wide = DISTANCES_MM[0], DISTANCES_MM[-1]
    near_hits = hits_within(real, narrow)
    far_hits = hits_within(real, wide)

    line1 = (
        real_shares[narrow] >= LINE1_MIN_SHARE
        and (real_shares[narrow] - decoys[narrow]) >= LINE1_MARGIN
    )

    result = {
        "入口の数量": len(everything),
        "座標を持つ数量": total,
        "位相の語が刷られている箇所": sum(len(v) for v in phase_by_page.values()),
        "位相以外の印字語": sum(len(v) for v in others_by_page.values()),
        "線1_いちばん狭い10mmで近いか": {
            "本物": round(real_shares[narrow], 4),
            "囮": round(decoys[narrow], 4),
            "差": round(real_shares[narrow] - decoys[narrow], 4),
            "合格": (
                f"本物 {LINE1_MIN_SHARE:.0%} 以上、かつ囮との差 {LINE1_MARGIN:.0%} 以上"
            ),
            "通過": line1,
        },
        "線2_囮_3段とも": {
            f"{limit:g}mm": {
                "本物": round(real_shares[limit], 4),
                "囮": round(decoys[limit], 4),
                "差": round(real_shares[limit] - decoys[limit], 4),
            }
            for limit in DISTANCES_MM
        },
        "線3_つまみを振ると中身が入れ替わるか": {
            "断り": "合格・不通過の線ではない。何が起きているかを見る欄",
            f"{narrow:g}mmで当たった件数": len(near_hits),
            f"{wide:g}mmで当たった件数": len(far_hits),
            f"{narrow:g}mmの集合が{wide:g}mmに含まれる件数": len(near_hits & far_hits),
            f"{wide:g}mmで新しく入った件数": len(far_hits - near_hits),
        },
        "線4_本番の判定に触っていないか": {
            "読むだけの測定": True,
            "自動確定した件数": sum(
                1 for item in everything if getattr(item, "is_confirmed", False)
            ),
            "合格": "判定を 1 か所も変えていないこと",
        },
    }
    detail = {
        "断り": "**共有フォルダだけに置く。リポジトリには入れない。**",
        "ページごとの位相の語の箇所数": {
            str(page): len(points) for page, points in sorted(phase_by_page.items())
        },
        "ページごとの位相以外の印字語の数": {
            str(page): len(points) for page, points in sorted(others_by_page.items())
        },
        "座標を持つ数量のページ内訳": {
            str(page): sum(1 for item in located if page_of(item) == page)
            for page in sorted(phase_by_page)
        },
    }
    return result, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detail-out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()

    result, detail = measure(args.pdf, args.answers, args.seed)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.detail_out is not None:
        args.detail_out.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
