"""K-67 6 節: **読了率の物差し自体を測る。**頑健性・囮・スキープ版・未読の所在。

基準は `docs/k67_readthrough_criteria.md`(測る前にコミット済み)。線:

- 本物の読了率が囮 2 つより高い
- 大きい箱で囲んだ版が高い読了率にならない(面積の上限 1% が効く)
- スキャン版(文字の層なし)で「測れない」と出る
- 未読の所在が指せる割合 100%

**新しく AI を呼ばない。**K-61 が保存した P011 v4 の読みをそのまま使う。
**出すのは件数と割合だけ。**図面の中身・室名・行の名前は出さない。

実行::

    PYTHONPATH=. python -m benchmarks.measure_readthrough \
        --pdf /mnt/project-files/anonymized/P011_匿名化v4.pdf \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R1 --out /tmp/k67_robust.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Mapping

from draft.readthrough import AREA_CAPS, DEFAULT_CAP, readthrough

SEED = 67


def load_reading(run: Path) -> dict[int, dict[str, Any]]:
    draft = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in (draft["読む"].get("読み") or {}).items()}


def scatter(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """**囮 1**: 要素の位置を、大きさのままページ内のでたらめな場所に置き直す(K-51 の囮)。"""
    import pymupdf

    from benchmarks import erase_check as ec

    rng = random.Random(SEED)
    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for number, entry in reading.items():
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            w, h = page.rect.width * scale, page.rect.height * scale
            moved = []
            for element in entry.get("要素", []):
                pos = element.get("位置")
                if not pos or len(pos) != 4:
                    continue
                bw, bh = abs(pos[2] - pos[0]), abs(pos[3] - pos[1])
                x, y = rng.uniform(0, max(w - bw, 0)), rng.uniform(0, max(h - bh, 0))
                moved.append({"種類": element.get("種類"), "位置": [x, y, x + bw, y + bh]})
            out[number] = {"要素": moved}
    return out


def big_boxes(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """**囮 2**: 読んでいない領域を、ページを覆う大きい箱 1 つで囲んだ版。

    面積の上限(1%)が効いていれば、この箱には印が付かないので読了率はほぼ 0 になる。
    **効いていなければ、何も読まずに 100% が出てしまう。**
    """
    import pymupdf

    from benchmarks import erase_check as ec

    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for number in reading:
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            w, h = page.rect.width * scale, page.rect.height * scale
            out[number] = {"要素": [{"種類": "大きい箱", "位置": [0.0, 0.0, w, h]}]}
    return out


def scan_version(pdf: Path, pages: list[int], work: Path) -> Path:
    """文字の層が無い版(各ページを画像 1 枚にする)。**測れないと出ることを確かめるため。**"""
    import pymupdf

    work.mkdir(parents=True, exist_ok=True)
    target = work / "スキャン版.pdf"
    with pymupdf.open(pdf) as src:
        out = pymupdf.open()
        for number in pages:
            page = src.load_page(number - 1)
            pix = page.get_pixmap(dpi=96)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=pix)
        out.save(target)
        out.close()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-67 読了率の物差しを測る")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--scan-pages", type=int, default=3, help="スキャン版を作るページ数(費用のため少なく)")
    parser.add_argument("--work", type=Path, default=Path("/tmp/k67_work"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    reading = load_reading(args.run)
    pages = sorted(reading)

    result: dict[str, Any] = {"回": args.run.name, "ページ数": len(pages)}

    # (a) 頑健性: 面積の上限を変える
    caps: dict[str, Any] = {}
    for cap in AREA_CAPS:
        r = readthrough(args.pdf, reading, pages, cap=cap, with_unread=False)
        caps[f"上限 {cap:.0%}"] = {
            "読了率": r["読了率"], "数える図形": r["数える図形"], "信号の分布": r["信号の分布"],
        }
    result["頑健性(面積の上限)"] = caps

    base = readthrough(args.pdf, reading, pages, cap=DEFAULT_CAP)
    result["本物"] = {
        "読了率": base["読了率"], "種類ごと": base["種類ごと(重なりなし)"],
        "墨の量": base["墨の量で見た読了率"], "別の切り口": base["別の切り口(重なる)"],
        "信号の分布": base["信号の分布"], "赤の割合": base["赤の割合"],
        "案件全体の警告": base["案件全体の警告"],
        "未読の数": base["未読の数"], "未読の所在が指せた": base["未読の所在が指せた"],
        "未読の所在が指せた割合": base["未読の所在が指せた割合"],
        "測れないページ": base["測れないページ"],
    }

    # (a) 囮 2 つ
    decoys: dict[str, Any] = {}
    for name, builder in (("囮1 でたらめに置き直した版", scatter), ("囮2 大きい箱で囲んだ版", big_boxes)):
        fake = builder(reading, args.pdf)
        r = readthrough(args.pdf, fake, pages, cap=DEFAULT_CAP, with_unread=False)
        decoys[name] = {"読了率": r["読了率"], "信号の分布": r["信号の分布"]}
    result["囮"] = decoys
    result["線: 本物が囮より高い"] = all(
        (base["読了率"] or 0) > (d["読了率"] or 0) for d in decoys.values()
    )
    result["線: 大きい箱が高い読了率にならない"] = (decoys["囮2 大きい箱で囲んだ版"]["読了率"] or 0) < 0.10

    # (a) スキャン版
    scan_pages = pages[: args.scan_pages]
    scan_pdf = scan_version(args.pdf, scan_pages, args.work)
    scan_reading = {n: reading[n] for n in scan_pages}
    renumbered = {i + 1: scan_reading[n] for i, n in enumerate(scan_pages)}
    r = readthrough(scan_pdf, renumbered, list(renumbered), cap=DEFAULT_CAP, with_unread=False)
    result["スキャン版"] = {
        "元のページ": scan_pages,
        "測れないページ": r["測れないページ"],
        "測れたページ": r["測れたページ"],
        "読了率": r["読了率"],
        "手段": [p["手段"] for p in r["ページごと"]],
        "測れない理由": [p.get("測れない理由") for p in r["ページごと"]],
    }
    result["線: スキャン版で測れないと出る"] = r["測れないページ"] == len(scan_pages)
    result["線: 未読の所在 100%"] = base["未読の所在が指せた割合"] == 1.0

    print(json.dumps({k: v for k, v in result.items() if k != "本物"}, ensure_ascii=False, indent=1))
    print("\n本物:", json.dumps(result["本物"], ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
