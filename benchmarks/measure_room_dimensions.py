"""9周目の測定: 人が入れた室の寸法から数量と見積の行が出るか。

`docs/a2_human_room_dimensions_criteria.md` の指標をそのまま測る。

**出すのは件数と割合だけ。** 室名・寸法・数量の値は印字しない。
図面は読まない(この周は人の入力だけを扱う)。

実行::

    .venv/bin/python -m benchmarks.measure_room_dimensions
    .venv/bin/python -m benchmarks.measure_room_dimensions --golden <採点用.json>

``--golden`` はゴールデンベンチマークの**項目の種類と工種だけ**を読み、
「図形から出す ㎡」のうち何件がこの経路の射程かを数える。
**正解の数量は読まない。**(`quantity` の欄には触らない。)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from estimating.from_room_dimensions import (
    KIND_FLOOR_AREA,
    KIND_PERIMETER,
    KIND_WALL_AREA,
    quantities_from_room_dimensions,
)
from estimating.mapping import map_quantities
from estimating.rules import load_rules
from intake.room_dimensions import RoomDimension

ROOM_RULES = Path("estimating/examples/synthetic_room_rules.json")

#: 合成の入力。**実案件の室名・寸法は使わない。**
#: 10 室にしてあるのは、8 周目の報告書が「10 個」と書いた規模に合わせるため。
SYNTHETIC_ROOMS: tuple[tuple[str, float, float, float | None], ...] = (
    ("室1", 3640.0, 2730.0, 2400.0),
    ("室2", 4550.0, 3640.0, 2400.0),
    ("室3", 2730.0, 1820.0, 2400.0),
    ("室4", 3640.0, 3640.0, 2500.0),
    ("室5", 1820.0, 1365.0, 2200.0),
    ("室6", 5460.0, 3640.0, 2400.0),
    ("室7", 2730.0, 2730.0, 2400.0),
    ("室8", 3641.0, 2731.0, 2400.0),   # 1cm² きざみで表せない寸法
    ("室9", 4550.0, 2730.0, None),     # 天井高だけ入っていない
    ("室10", 3640.0, 1820.0, 2400.0),
)

#: 「図形から出す ㎡」を、要る入力で分けるための語。
#: **一般的な工事の言葉だけ。** 実案件の見積明細の表記は書かない。
FLOOR_OR_CEILING_WORDS = ("床", "置床", "天井", "フロア", "ﾌﾛｱ", "ﾌﾛｰﾘﾝｸﾞ", "フローリング", "タイル", "ﾀｲﾙ")
WALL_WORDS = ("壁", "クロス", "ｸﾛｽ", "間仕切", "ふかし")


def _rooms(*, drop_heights: bool = False, rename: bool = False):
    rooms = []
    for index, (name, length, width, height) in enumerate(SYNTHETIC_ROOMS):
        rooms.append(
            RoomDimension(
                room_name=f"別名{index}" if rename else name,
                length_mm=length,
                width_mm=width,
                ceiling_height_mm=None if drop_heights else height,
                area_basis="芯々",
                entered_by="測定",
            )
        )
    return rooms


def _measure(rooms) -> dict[str, object]:
    result = quantities_from_room_dimensions(rooms)
    ruleset = load_rules(ROOM_RULES)
    mapped = map_quantities(result.quantities, ruleset)
    counts = {kind: 0 for kind in (KIND_FLOOR_AREA, KIND_PERIMETER, KIND_WALL_AREA)}
    for item in result.quantities:
        if item.kind in counts:
            counts[item.kind] += 1
    return {
        "数量": len(result.quantities),
        "床面積": counts[KIND_FLOOR_AREA],
        "周長": counts[KIND_PERIMETER],
        "内壁面積": counts[KIND_WALL_AREA],
        "見積の行": sum(len(m.lines) for m in mapped.mappings),
        "確定した行": len(mapped.settled_lines()),
        "規則が当たらない": len(mapped.unmapped()),
        "足りない理由": len(result.gaps),
        "値": tuple(item.canonical_range for item in result.quantities),
    }


def _print(label: str, rows: list[dict[str, object]]) -> None:
    first = rows[0]
    same = all(row == first for row in rows)
    shown = {k: v for k, v in first.items() if k != "値"}
    flag = "" if same else "  **回ごとに違う**"
    print(f"{label}: {shown}{flag}")


def _golden_reach(path: Path) -> None:
    """ゴールデンの**項目の種類と工種だけ**を読む。正解の数量は読まない。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["expected_items"]
    geometry = [i for i in items if i.get("expected_source_type") == "geometry_derived"]
    sqm = [i for i in geometry if i.get("unit") == "㎡"]
    floor_like = wall_like = other = 0
    for item in sqm:
        name = str(item.get("canonical_work_item") or item.get("work_item") or "")
        # **床・天井を先に見る。** 「天井クロス貼」は「クロス」で壁に入って
        # しまうが、数量の出どころは床面積である。順番を逆にすると 1 件ずれる。
        if any(word in name for word in FLOOR_OR_CEILING_WORDS):
            floor_like += 1
        elif any(word in name for word in WALL_WORDS):
            wall_like += 1
        else:
            other += 1
    print()
    print("ゴールデンの射程(項目の種類と工種だけを読んだ。正解の数量は読んでいない)")
    print(f"  全項目: {len(items)}")
    print(f"  図形から出す: {len(geometry)}  / うち ㎡: {len(sqm)}")
    print(f"    床・天井の語を含む(床面積で出せる): {floor_like}")
    print(f"    壁の語を含む(周長と天井高が要る):   {wall_like}")
    print(f"    どちらの語も無い:                   {other}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--golden", type=Path, default=None)
    args = parser.parse_args()

    print(f"合成の入力: {len(SYNTHETIC_ROOMS)} 室 / 各条件 {args.runs} 回")
    print()
    conditions = {
        "本条件(縦・横・天井高)": lambda: _rooms(),
        "負1 何も入れない": lambda: [],
        "負3 天井高が無い": lambda: _rooms(drop_heights=True),
        "負5 室名だけ入れ替え": lambda: _rooms(rename=True),
    }
    values: dict[str, tuple] = {}
    for label, build in conditions.items():
        rows = [_measure(build()) for _ in range(args.runs)]
        _print(label, rows)
        values[label] = rows[0]["値"]

    print()
    base = values["本条件(縦・横・天井高)"]
    renamed = values["負5 室名だけ入れ替え"]
    print(
        "負5 入れ替え対照: 値が室名で変わっていないか … "
        + ("変わっていない" if base == renamed else "**変わった(欠陥)**")
    )

    if args.golden is not None:
        _golden_reach(args.golden)


if __name__ == "__main__":
    main()
