"""24周目の測定: 9周目の経路が面積の行 40 件のうち何件に届きうるか。

**数だけを測る。質(間違い率)で順位を付けない**(21周目の結論)。

**出すのは件数と割合だけ。** 実案件の品目名・手がかり語・数量は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_area_row_coverage --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import unicodedata
from pathlib import Path

from estimating.from_room_dimensions import quantities_from_room_dimensions
from estimating.mapping import map_quantities
from estimating.rules import load_rules
from intake.room_dimensions import RoomDimension

SYNTHETIC_RULES = Path("estimating/examples/synthetic_room_rules.json")

#: 9 周目の経路が出せる 3 種類の単位。
ANSWERABLE_UNITS: tuple[str, ...] = ("㎡", "m")

#: **3 種類の名前(床面積・内壁面積・室の周長)そのものから取った 4 語。**
#: 正解ファイルの品目名は見ていない。
KIND_WORDS: tuple[str, ...] = ("床", "壁", "天井", "周")

#: 合成の室。**実案件の寸法ではない。**
SYNTHETIC_ROOMS = (
    RoomDimension("合成室1", 3640.0, 2730.0, 2400.0, area_basis="芯々"),
    RoomDimension("合成室2", 4550.0, 3640.0, 2400.0, area_basis="芯々"),
    RoomDimension("合成室3", 1820.0, 1365.0, 2300.0, area_basis="芯々"),
)


def rows_of(golden: Path, kind: str) -> list[dict]:
    payload = json.loads(golden.read_text(encoding="utf-8"))
    return [i for i in payload["expected_items"] if i.get("expected_source_type") == kind]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def hits_word(row: dict, words: tuple[str, ...]) -> bool:
    """**採点側だけ。** 行の手がかり語に、どれかの語が入っているか。"""
    terms = " ".join(str(t) for t in (row.get("trigger_terms") or []))
    return any(w in _norm(terms) for w in words)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    rows = rows_of(args.golden, "geometry_derived")
    print(f"面積の行(geometry_derived): {len(rows)}")

    units = collections.Counter(str(r.get("unit") or "") for r in rows)
    print(f"\nX0 単位の分布: {dict(units)}")

    by_unit = [r for r in rows if str(r.get("unit") or "") in ANSWERABLE_UNITS]
    print(f"X1 単位で答えられる行: {len(by_unit)} / {len(rows)}")

    by_word = [r for r in by_unit if hits_word(r, KIND_WORDS)]
    print(f"X2 語でも当たる行    : {len(by_word)} / {len(rows)}")
    only_word = [r for r in rows if hits_word(r, KIND_WORDS)]
    print(f"   (参考)単位を問わず語で当たる行: {len(only_word)} / {len(rows)}")

    result = quantities_from_room_dimensions(SYNTHETIC_ROOMS)
    ruleset = load_rules(SYNTHETIC_RULES)
    mapping = map_quantities(result.quantities, ruleset)
    lines = [
        line
        for item in mapping.mappings
        for outcome in item.outcomes
        for line in outcome.lines
    ]
    confirmed = sum(1 for line in lines if line.is_confirmed_quantity)
    print(
        f"\nX3 合成の室 {len(SYNTHETIC_ROOMS)} 室 → 数量 {len(result.quantities)} 件 / "
        f"見積の行 {len(lines)} 件 / 作らなかった理由 {len(result.gaps)} 件"
    )
    print(f"X4 自動確定: {confirmed}")

    print("\n=== 対照 ===")
    base = len(only_word) / len(rows)
    print(f"対照1 別の種類の行に 4 語を当てる(面積の行では {len(only_word)}/{len(rows)} = {base:.3f})")
    worst = 0.0
    for kind in ("symbol_count", "explicit_text", "standard_rule"):
        others = rows_of(args.golden, kind)
        hit = sum(1 for r in others if hits_word(r, KIND_WORDS))
        ratio = hit / len(others)
        worst = max(worst, ratio)
        print(f"   {kind}: {hit} / {len(others)} = {ratio:.3f}")
    print(f"   いちばん高い {worst:.3f} < 半分 {base / 2:.3f} か: {'はい' if worst < base / 2 else 'いいえ'}")

    rng = random.Random(args.seed)
    letters = "あいうえおかきくけこさしすせそ"
    fakes = []
    for _ in range(10):
        words = tuple("".join(rng.choice(letters) for _ in range(2)) for _ in range(4))
        fakes.append(sum(1 for r in rows if hits_word(r, words)))
    print(f"対照2 でたらめな 4 語 10 回: {fakes}")

    repeats = [sum(1 for r in rows_of(args.golden, "geometry_derived") if hits_word(r, KIND_WORDS)) for _ in range(3)]
    print(f"対照3 反復 3 回: {repeats}")


if __name__ == "__main__":
    main()
