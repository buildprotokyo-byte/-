"""K-55: 位置つきの読みと名前づけから、記号の数え上げの答案と囮を作る。

使い方:
    python3 -m benchmarks.k55_symbol_count 読み.json 名前づけ.json 出力の置き場 [--decoys 20]

出力: ``counts.json``(本番の ``--symbol-counts`` に渡す答案)と ``decoy_01.json`` …(囮: 数え上げの行の
数を、同じ単位の行どうしで入れ替えたもの。行の名前・場所はそのまま)。
実図面・読み取り結果はリポジトリに入れない(パスで渡す)。
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from intake.positioned_symbol_count import count_symbols


def decoy(reading: dict, seed: int) -> dict:
    rng = random.Random(seed)
    rows = [dict(r) for r in reading["行"]]
    by_unit: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_unit.setdefault(r["単位"], []).append(i)
    for idx in by_unit.values():
        qs = [rows[i]["数量"] for i in idx]
        rng.shuffle(qs)
        for i, q in zip(idx, qs):
            rows[i]["数量"] = q
            rows[i]["式"] = "囮: 数を同じ単位の行どうしで入れ替えた"
    return {**reading, "行": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("reading")
    ap.add_argument("naming")
    ap.add_argument("out_dir")
    ap.add_argument("--decoys", type=int, default=20)
    a = ap.parse_args(argv)
    res = count_symbols(json.load(open(a.reading)), json.load(open(a.naming)))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reading = res.as_reading()
    (out / "counts.json").write_text(json.dumps(reading, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in range(1, a.decoys + 1):
        (out / f"decoy_{k:02d}.json").write_text(
            json.dumps(decoy(reading, 5500 + k), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    print(json.dumps(res.summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
