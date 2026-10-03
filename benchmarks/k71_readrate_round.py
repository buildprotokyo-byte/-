"""K-71 作業 3: 周ごとの前 → 後と囮(`docs/k71_readrate_criteria.md` の線)。

前 = 機械の罫線を足さない台帳(`machine_grid=False`)、後 = 足した台帳。囮は 3 つ:

- 囮 1(K-67): 要素をでたらめな位置に置き直した版(種 67)
- 囮 2(K-67): ページを覆う大きい箱 1 つ(読んでいない領域を大きい箱で囲んだ版)
- 囮 3(強い囮): 罫線の表の四角そのものを種類「表」の箱で囲み、中身は読まない

**新しく AI を呼ばない。出すのは件数と割合だけ。**

    PYTHONPATH=. python -m benchmarks.k71_readrate_round --pdf P011_匿名化v4.pdf --run .../full_R1 --out 周1_R1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.measure_readthrough import big_boxes, load_reading, scatter
from draft.readthrough import pass_fail, readthrough


def table_boxes(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """囮 3: 罫線の表の四角を、種類「表」の箱 1 つずつで囲んだだけの版(中身は読まない)。"""
    import pymupdf

    from benchmarks import erase_check as ec
    from draft.readthrough import _table_rects

    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for number in reading:
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            rects, _ = _table_rects(page)
            out[number] = {"要素": [{"種類": "表", "内容": "表", "位置": [v * scale for v in r]} for r in rects]}
    return out


def rates(result: Mapping[str, Any]) -> dict[str, Any]:
    verdict = pass_fail(result)
    out: dict[str, Any] = {name: row["読了率"] for name, row in verdict["種類ごと"].items()}
    out["点・小さい図形(数、参考)"] = verdict["参考(合否に入れない)"]["点・小さい図形(数)"]
    out["合否"] = verdict["合否"]
    out["機械が足した罫線"] = result.get("機械が読んだ罫線(表)", 0)
    return out


def measure(pdf: Path, reading: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    pages = sorted(reading)
    out: dict[str, Any] = {}
    for label, grid in (("前", False), ("後", True)):
        out[label] = rates(readthrough(pdf, reading, pages, with_unread=False, machine_grid=grid))
    decoys = {"囮1 でたらめに置き直した版": scatter(reading, pdf),
              "囮2 大きい箱で囲んだ版": big_boxes(reading, pdf),
              "囮3 表の四角を表の箱で囲んだだけの版": table_boxes(reading, pdf)}
    for name, fake in decoys.items():
        out[name] = {label: rates(readthrough(pdf, fake, pages, with_unread=False, machine_grid=grid))
                     for label, grid in (("前", False), ("後", True))}
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-71 作業 3 周ごとの前後と囮")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    a = parser.parse_args(argv)
    result = {"回": a.run.name, **measure(a.pdf, load_reading(a.run))}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
