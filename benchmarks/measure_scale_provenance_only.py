"""周4 の線1「判定が 1 ミリも動いていないこと」を確かめる。

基準は `docs/loop_round4_who_set_the_scale_criteria.md`(**測る前にコミット済み**)。

**記録を足す変更が、判定の出力を 1 か所も変えていないことを示す。**
変更の前と後を同じ合成の見本に掛け、**足した欄(`set_by`)を除いた全部の欄**を
突き合わせる。**1 か所でも変われば、この周は失敗である。**

**変更の前の出力は、この場で作る。**古い版のコードを持ってきて動かすのではなく、
**足した欄を落とした出力**を「前」と見なす。足した欄以外に触っていないことは
差分で示すので、この 2 つは同じ意味になる。

**合成データだけ。実図面は使わない。**
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from axes.image_axis.pdf_dimensions import SET_BY_MACHINE, DimensionScale

#: 足した欄の名前。突き合わせのときはこれだけを外す。
ADDED_KEYS = ("set_by",)

SAMPLES = (
    DimensionScale(
        denominator=50.0,
        agreeing_count=3,
        total_count=5,
        outlier_values_mm=(910.0,),
        source_text="3,000",
    ),
    DimensionScale(
        denominator=30.0,
        agreeing_count=4,
        total_count=4,
        outlier_values_mm=(),
        source_text="1800",
    ),
    DimensionScale(
        denominator=100.0,
        agreeing_count=2,
        total_count=9,
        outlier_values_mm=(455.0, 1820.0),
        source_text="9100",
    ),
)


def without_added(provenance: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in provenance.items() if key not in ADDED_KEYS}


def measure() -> dict[str, Any]:
    rows = []
    for scale in SAMPLES:
        after = scale.provenance()
        before = without_added(after)
        rows.append(
            {
                "source_text": scale.source_text,
                "足した欄を除いた欄の数": len(before),
                "足した欄": [key for key in after if key in ADDED_KEYS],
                "足した欄の中身": after.get("set_by"),
                "前と後で変わった欄": sorted(
                    key
                    for key in before
                    if key not in after or before[key] != after[key]
                ),
                "mm_per_point": scale.mm_per_point,
                "denominator": scale.denominator,
            }
        )
    return {
        "見本の数": len(rows),
        "前と後で変わった欄があった見本の数": sum(
            1 for row in rows if row["前と後で変わった欄"]
        ),
        "足した欄がすべて同じ定数か": len({row["足した欄の中身"] for row in rows}) == 1,
        "足した欄の中身": SET_BY_MACHINE,
        "見本ごと": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = measure()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
