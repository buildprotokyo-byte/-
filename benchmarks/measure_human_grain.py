"""周35 の測定: **人に聞くなら何件で、その単位は位相を 1 つに決められる粗さか。**

基準は `docs/loop_round35_human_grain_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

前の周(周34)との違いは**出典ひとつ**である。周32〜34 は 3 周とも同じ PDF の中を
見ていた。ここでは**人の入力**を出典にする(設計④の「出典 3 つ」の 3 本目)。

**手間と粗さの両方を数える。** 単位が細かいほど人の手間は増え、粗いほど間違える。
**「全体で 1 つ」を囮として必ず入れる**——質問 1 件で全部を覆うので手間の線では
必ず勝つ。**粗さの線で落ちなければ、粗さの線が効いていない。**

**紙から取り出した文字・対象名は 1 文字も出さない。** 出すのは件数と割合だけ。

実行::

    .venv/bin/python -m benchmarks.measure_human_grain \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r35.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

import fitz

from benchmarks.measure_phase_printed import normalise
from benchmarks.measure_phase_word_proximity import (
    MM_PER_POINT,
    Point,
    page_of,
    points_of,
)

#: 位相の語の**組**。周32 と同じ語を 3 つに束ねる。
PHASE_GROUPS: dict[str, tuple[str, ...]] = {
    "現況の側": ("既存", "現況", "現状", "残置", "存置"),
    "計画の側": ("新設", "新規", "計画", "増設"),
    "解体の側": ("撤去", "解体", "改修"),
}

#: 粗さを見る距離(紙の上のミリ)。周33・周34 のいちばん広い段と同じ。
NEAR_MM = 50.0

LINE1_MAX_QUESTIONS = 100
LINE1_MIN_COVER = 0.90
LINE2_MAX_MIXED = 0.20

#: 囮の粒度の名前。**手間では必ず勝ち、粗さでは必ず落ちるはず。**
DECOY_GRAIN = "全体で1つ(囮)"


def group_positions(pdf: Path) -> dict[int, dict[str, list[Point]]]:
    """ページごと・組ごとに、語が刷られている場所の中心。**語は返さない。**"""
    out: dict[int, dict[str, list[Point]]] = {}
    with fitz.open(pdf) as doc:
        for index, page in enumerate(doc, start=1):
            per_group: dict[str, list[Point]] = {}
            for name, words in PHASE_GROUPS.items():
                centres: list[Point] = []
                for word in words:
                    for rect in page.search_for(word):
                        centres.append(
                            ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
                        )
                per_group[name] = centres
            out[index] = per_group
    return out


def groups_near(
    item: Any, positions: Mapping[int, Mapping[str, Sequence[Point]]]
) -> set[str]:
    """その数量の点から `NEAR_MM` 以内に出る位相の組。"""
    page = page_of(item)
    if page is None:
        return set()
    per_group = positions.get(page, {})
    limit_pt = NEAR_MM / MM_PER_POINT
    found: set[str] = set()
    points = points_of(item)
    for name, centres in per_group.items():
        for px, py in points:
            if any(
                (px - cx) ** 2 + (py - cy) ** 2 <= limit_pt * limit_pt
                for cx, cy in centres
            ):
                found.add(name)
                break
    return found


def units_of(
    quantities: Sequence[Any], key: Callable[[Any], Any]
) -> dict[Any, list[Any]]:
    """粒度ごとに数量をまとめる。**鍵の中身は外へ出さない。**"""
    out: dict[Any, list[Any]] = defaultdict(list)
    for item in quantities:
        out[key(item)].append(item)
    return dict(out)


def cover_of(units: Mapping[Any, Sequence[Any]], total: int) -> float:
    """その粒度で覆える数量の割合。**鍵が付かない数量は覆えない。**"""
    if total <= 0:
        return 0.0
    return sum(len(items) for key, items in units.items() if key is not None) / total


def mixed_share(
    units: Mapping[Any, Sequence[Any]],
    positions: Mapping[int, Mapping[str, Sequence[Point]]],
) -> tuple[float, int, int]:
    """位相が 1 つに決まらない単位の割合と、その内訳。

    返すのは (割合, 2 組以上の単位, 語が 1 つも出ない単位)。
    **語が出ない単位は「決まらない」には数えない**(決めようがないのであって、
    混ざっているのではない)。**分母は語が 1 組以上出た単位。**
    """
    mixed = 0
    silent = 0
    decided = 0
    for items in units.values():
        found: set[str] = set()
        for item in items:
            found |= groups_near(item, positions)
        if not found:
            silent += 1
        elif len(found) >= 2:
            mixed += 1
        else:
            decided += 1
    speaking = mixed + decided
    return (mixed / speaking if speaking else 0.0), mixed, silent


def measure(pdf: Path, answers: Path) -> tuple[dict, dict]:
    from intake.drawing_intake import IntakeConfig, read_drawing
    from estimating.from_intake import quantities_from_intake

    positions = group_positions(pdf)
    intake = read_drawing(
        IntakeConfig(case_id="round35", pdf_path=pdf, answers_path=answers)
    )
    everything = list(quantities_from_intake(intake))
    located = [item for item in everything if points_of(item) and page_of(item)]
    total = len(located)

    grains: dict[str, Callable[[Any], Any]] = {
        DECOY_GRAIN: lambda _item: "案件",
        "ページ": page_of,
        "対象の種類": lambda item: item.kind,
        "対象名": lambda item: item.target,
    }

    rows: dict[str, dict] = {}
    for name, key in grains.items():
        units = units_of(located, key)
        questions = len([k for k in units if k is not None])
        cover = cover_of(units, total)
        share, mixed, silent = mixed_share(units, positions)
        rows[name] = {
            "質問の数": questions,
            "覆える割合": round(cover, 4),
            "位相が1つに決まらない単位の割合": round(share, 4),
            "2組以上が出た単位": mixed,
            "語が1つも出ない単位": silent,
            "線1(手間)": questions <= LINE1_MAX_QUESTIONS and cover >= LINE1_MIN_COVER,
            "線2(粗さ)": share <= LINE2_MAX_MIXED,
        }

    real = {name: row for name, row in rows.items() if name != DECOY_GRAIN}
    line1 = any(row["線1(手間)"] for row in real.values())
    line2 = any(row["線2(粗さ)"] for row in real.values())
    line3 = any(row["線1(手間)"] and row["線2(粗さ)"] for row in real.values())
    decoy = rows[DECOY_GRAIN]

    result = {
        "入口の数量": len(everything),
        "座標を持つ数量": total,
        "粒度ごと": rows,
        "線1_手間": {
            "合格": f"質問 {LINE1_MAX_QUESTIONS} 件以下で {LINE1_MIN_COVER:.0%} 以上を覆う粒度がある",
            "通過": line1,
        },
        "線2_粗さ": {
            "合格": f"決まらない単位の割合が {LINE2_MAX_MIXED:.0%} 以下の粒度がある",
            "通過": line2,
        },
        "線3_両方を満たす粒度があるか": {
            "合格": "1 つ以上ある",
            "通過": line3,
        },
        "囮の確認": {
            "断り": "全体で1つ。手間では必ず勝ち、粗さでは必ず落ちるはず",
            "手間で勝った": decoy["線1(手間)"],
            "粗さで落ちた": not decoy["線2(粗さ)"],
            "落ちなければ粗さの線が効いていない": True,
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
        "ページごとの組ごとの箇所数": {
            str(page): {name: len(points) for name, points in per_group.items()}
            for page, per_group in sorted(positions.items())
        },
    }
    return result, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detail-out", type=Path, default=None)
    args = parser.parse_args()

    result, detail = measure(args.pdf, args.answers)
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
