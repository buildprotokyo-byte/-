"""周27(目的から数える): **一本通した出力を、単位ごとに棚卸しする。**

**この道具は数えるだけ。**`app.py` にも `intake/` にも手を入れていない(K-29)。
**走らせるのは `app.py` のほうで、ここは出た JSON を数えるだけである。**

周26 で室の大きさが頭打ちになり、札(決めてもらう問い)の推しの案として
**「面積が要る行を先に洗う」**を挙げた。**その案を進めるための道具である。**

**この順番は K-24 の原則に沿う**——手段から考えず、まず目的から推論する。
「室の面積をどう出すか」は手段で、**「見積のどの行が面積を要るのか」が
目的の側の問い**である。

基準は `docs/loop_round27_what_area_blocks_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 線1 の合格。数量が入っている行が、出た行の 3 割以上。
LINE1_SHARE = 0.3


def census(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """行を単位ごとに数える。**数量の値そのものは返さない。件数だけ。**"""
    units = Counter(str(row.get("単位")) for row in rows)
    with_quantity = Counter(
        str(row.get("単位")) for row in rows if row.get("数量") is not None
    )
    waiting = [row for row in rows if row.get("人の入力待ち")]
    places = Counter(str(row.get("場所")) for row in waiting)
    return {
        "行": len(rows),
        "単位ごとの行": dict(sorted(units.items())),
        "数量が入っている行": sum(
            1 for row in rows if row.get("数量") is not None
        ),
        "単位ごとの数量が入っている行": dict(sorted(with_quantity.items())),
        "人の入力待ちの行": len(waiting),
        "人の入力待ちが指す場所": len(places),
        "場所あたりの行数": dict(sorted(Counter(places.values()).items())),
        "道ごとの行": dict(sorted(Counter(str(row.get("道")) for row in rows).items())),
    }


def judge(counted: Mapping[str, Any]) -> dict[str, Any]:
    """線1・線2 の判定。**合格の線は基準のまま。**"""
    rows = counted["行"]
    with_quantity = counted["数量が入っている行"]
    square = counted["単位ごとの数量が入っている行"].get("㎡", 0)
    return {
        "線1_面積が無くても数量が出ている行": {
            "本物": with_quantity,
            "出た行": rows,
            "合格": f"{LINE1_SHARE:.0%} 以上",
            "通過": with_quantity >= LINE1_SHARE * rows if rows else False,
            "囮": "置いていない(出力の棚卸しであって、位置や当たりを当てる測定ではない)",
        },
        "線2_㎡の行に数量は入っているか": {
            "本物": square,
            "先に書いた予想": "0 行",
            "予想どおり": square == 0,
        },
    }


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """線3(対照)。**こちらで数え直した 2 つを並べる。**

    **向こうの報告書に書いてある数字を写さない。**
    """
    keys = (
        "行",
        "単位ごとの行",
        "数量が入っている行",
        "単位ごとの数量が入っている行",
        "道ごとの行",
    )
    same = all(before[key] == after[key] for key in keys)
    return {
        "前": {key: before[key] for key in keys},
        "後": {key: after[key] for key in keys},
        "行の側は変わったか": not same,
    }


def load(path: str | Path) -> list[Mapping[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return list(json.load(handle)["工事項目"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="app.py が書いた JSON")
    parser.add_argument("--before", type=Path, default=None, help="比べる古い JSON")
    args = parser.parse_args()
    counted = census(load(args.output))
    result: dict[str, Any] = {"棚卸し": counted, **judge(counted)}
    if args.before is not None:
        result["線3_対照"] = compare(census(load(args.before)), counted)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
