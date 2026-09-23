"""14周目の測定: 人が数えた記号の個数を受け取る口が、見積の行まで届くかを測る。

**抽出と採点を分ける。**

- `run_path(entries)` … **ゴールデンを読まない。** 入力から数量と見積の行を作る。
- `reach(quantities, golden_path)` … そのあとで正解と突き合わせる。

**出すのは件数と割合だけ。** 実案件の品目名・記号名・個数そのものは印字しない。

**この測定は「人が正しく数えられるか」を測っていない。** 個数はゴールデンから
取って人の代わりに入れる。測るのは**経路が数量まで届くか**だけである。

実行::

    .venv/bin/python -m benchmarks.measure_symbol_counts --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path

from estimating.from_symbol_counts import quantities_from_symbol_counts
from estimating.mapping import map_quantities
from estimating.rules import load_rules
from intake.symbol_counts import COUNTABLE_UNITS, SymbolCount

#: 合成の規則。**実案件の見積明細からは作っていない。**
SYNTHETIC_RULES = Path("estimating/examples/synthetic_symbol_rules.json")

#: **測る前に決めた語彙**(`docs/a1_symbol_count_intake_criteria.md` 6 節)。
#: 基準の文書 0 章が挙げた 4 語。**増やさない。**
VOCABULARY: tuple[str, ...] = ("コンセント", "スイッチ", "配線", "TEL")

#: 対照3(入れ替え)に使う、記号とは関係のない語。
SWAPPED_VOCABULARY: tuple[str, ...] = ("床", "天井", "建具", "仮設")

#: 数えたことにするページ(1 始まり)。**どの語も同じページにする。**
COUNTED_PAGE = 22


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").upper()


def symbol_rows(golden_path: Path) -> list[dict]:
    """ゴールデンの `symbol_count` 行。**採点のためだけに読む。**"""
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    return [
        item
        for item in payload["expected_items"]
        if item.get("expected_source_type") == "symbol_count"
    ]


def stand_in_operator(
    rows: list[dict], vocabulary: tuple[str, ...]
) -> list[SymbolCount]:
    """**人の代わり。** 語彙ごとに、当たる行の個数を足して 1 件ずつ入れる。

    ここだけが正解を入力側へ持ち込む。**人が正しく数えられたと仮定している。**
    数え落とし・二重数えはこの測定では起きない。
    """
    entries: list[SymbolCount] = []
    for word in vocabulary:
        total = 0
        found = False
        for row in rows:
            if not _matches(word, row):
                continue
            quantity = row.get("quantity")
            if isinstance(quantity, (int, float)) and float(quantity).is_integer():
                total += int(quantity)
                found = True
        if not found:
            # 当たる行が無ければ「数えたが 0 だった」ではなく、入れない。
            continue
        entries.append(
            SymbolCount(word, total, page_number=COUNTED_PAGE, counted_with="目視")
        )
    return entries


def _matches(word: str, row: dict) -> bool:
    needle = _norm(word)
    for key in ("canonical_work_item", "work_item", "middle_category"):
        if needle in _norm(str(row.get(key) or "")):
            return True
    return False


def run_path(entries: list[SymbolCount]) -> dict[str, object]:
    """**ゴールデンを読まない。** 入力から数量と見積の行を作る。"""
    result = quantities_from_symbol_counts(entries)
    ruleset = load_rules(SYNTHETIC_RULES)
    mapping = map_quantities(result.quantities, ruleset)
    lines = [
        line
        for item in mapping.mappings
        for outcome in item.outcomes
        for line in outcome.lines
    ]
    return {
        "入れた件数": len(entries),
        "作られた数量": len(result.quantities),
        "作らなかった理由": len(result.gaps),
        "当たった見積の行": len(lines),
        "確定した数量": sum(1 for line in lines if line.is_confirmed_quantity),
        "数量の対象名": [q.target for q in result.quantities],
    }


def reach(entries: list[SymbolCount], rows: list[dict]) -> int:
    """入れた語彙で、ゴールデンの行のうち何行に数量が付くか。"""
    reached = 0
    for row in rows:
        if any(_matches(entry.symbol_name, row) for entry in entries):
            reached += 1
    return reached


def uncountable_units(rows: list[dict]) -> dict[str, int]:
    """個数では答えられない単位の行数。**単位の名前だけを出す。**"""
    out: dict[str, int] = {}
    for row in rows:
        unit = str(row.get("unit") or "")
        if unit not in COUNTABLE_UNITS:
            out[unit] = out.get(unit, 0) + 1
    return out


def random_vocabulary(rng: random.Random, size: int) -> tuple[str, ...]:
    letters = "あいうえおかきくけこさしすせそ"
    return tuple(
        "".join(rng.choice(letters) for _ in range(5)) for _ in range(size)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    print(f"ゴールデンの symbol_count 行: {len(rows)}")

    entries = stand_in_operator(rows, VOCABULARY)
    main_run = run_path(entries)
    print("\n=== 本測定 ===")
    print(f"M1 見積の行に当たった数量: {main_run['当たった見積の行']} / 入れた語彙 {len(VOCABULARY)}")
    print(f"M2 ゴールデンの行に届いた: {reach(entries, rows)} / {len(rows)}")
    print(f"M3 自動確定: {main_run['確定した数量']}")
    unit_counts = uncountable_units(rows)
    print(f"M4 個数で答えられない単位の行: {sum(unit_counts.values())} / {len(rows)} {unit_counts}")
    print(f"   作られた数量 {main_run['作られた数量']} / 作らなかった理由 {main_run['作らなかった理由']}")

    print("\n=== 対照 ===")
    empty = run_path([])
    print(f"対照1 空の入力: 数量 {empty['作られた数量']} / 行 {empty['当たった見積の行']}")

    rng = random.Random(args.seed)
    random_reach = []
    for _ in range(10):
        words = random_vocabulary(rng, len(VOCABULARY))
        fake = [SymbolCount(w, 3, page_number=COUNTED_PAGE) for w in words]
        random_reach.append(reach(fake, rows))
    print(f"対照2 でたらめな記号名 10 回: 届いた行 {random_reach}")

    swapped = [SymbolCount(w, 3, page_number=COUNTED_PAGE) for w in SWAPPED_VOCABULARY]
    swapped_run = run_path(swapped)
    print(
        f"対照3 入れ替え({len(SWAPPED_VOCABULARY)}語): "
        f"届いた行 {reach(swapped, rows)} / 見積の行 {swapped_run['当たった見積の行']}"
    )

    repeats = [run_path(stand_in_operator(rows, VOCABULARY)) for _ in range(3)]
    print(
        "対照4 反復 3 回: "
        f"{[(r['作られた数量'], r['当たった見積の行']) for r in repeats]}"
    )


if __name__ == "__main__":
    main()
