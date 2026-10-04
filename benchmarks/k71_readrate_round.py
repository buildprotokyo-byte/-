"""K-71 作業 3: 周ごとの前 → 後と囮(`docs/k71_readrate_criteria.md` の線)。

周 1: 前 = 機械の罫線を足さない台帳(`machine_grid=False`)、後 = 足した台帳。
周 2: 前 = 周 1 の後(矩形・四角形の長さ 0、`rect_perimeter=False`)、後 = 矩形・四角形を周長で数える。囮は 3 つ:

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
    out["線(長さ)の全部"] = ((result.get("墨の量で見た読了率") or {}).get("線(長さ)") or {}).get("全部")
    return out


#: 周ごとの 前 / 後 の設定(readthrough の引数)。
ROUNDS = {
    1: ({"machine_grid": False, "rect_perimeter": False}, {"machine_grid": True, "rect_perimeter": False}),
    2: ({"machine_grid": True, "rect_perimeter": False}, {"machine_grid": True, "rect_perimeter": True}),
}


def rect_perimeter_total(pdf: Path, pages: Sequence[int]) -> float:
    """周 2 の分母の検算: 種類「線」の矩形・四角形(除外なし・測れるページ)の周長の和。"""
    import pymupdf

    from benchmarks import erase_check as ec
    from draft.readthrough import line_ink_length, why_cannot_measure

    total = 0.0
    with pymupdf.open(pdf) as doc:
        for number in pages:
            page = doc.load_page(number - 1)
            prims = ec.extract_primitives(page, number)
            scale = ec.WIDTH_PX / page.rect.width
            ec.mark_exclusions(prims, page.rect.width * scale, page.rect.height * scale)
            live = [p for p in prims if not p.excluded]
            if why_cannot_measure(page, len(live), len(page.get_text("words"))):
                continue
            total += sum(line_ink_length(p) for p in live if p.category == "線" and p.kind in ("矩形", "四角形"))
    return total


def measure(pdf: Path, reading: Mapping[int, Mapping[str, Any]], round_no: int = 1) -> dict[str, Any]:
    pages = sorted(reading)
    settings = dict(zip(("前", "後"), ROUNDS[round_no]))
    out: dict[str, Any] = {"周": round_no}
    for label, kw in settings.items():
        out[label] = rates(readthrough(pdf, reading, pages, with_unread=False, **kw))
    decoys = {"囮1 でたらめに置き直した版": scatter(reading, pdf),
              "囮2 大きい箱で囲んだ版": big_boxes(reading, pdf),
              "囮3 表の四角を表の箱で囲んだだけの版": table_boxes(reading, pdf)}
    for name, fake in decoys.items():
        out[name] = {label: rates(readthrough(pdf, fake, pages, with_unread=False, **kw))
                     for label, kw in settings.items()}
    if round_no == 2:
        added = (out["後"]["線(長さ)の全部"] or 0.0) - (out["前"]["線(長さ)の全部"] or 0.0)
        expected = rect_perimeter_total(pdf, pages)
        out["分母の検算"] = {"増えた長さ": round(added, 1), "矩形・四角形の周長の和": round(expected, 1),
                         "差 1 画素以内": abs(added - expected) <= 1.0}
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-71 作業 3 周ごとの前後と囮")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--round", type=int, default=1, choices=sorted(ROUNDS))
    a = parser.parse_args(argv)
    result = {"回": a.run.name, **measure(a.pdf, load_reading(a.run), a.round)}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
