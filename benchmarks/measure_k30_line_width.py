"""K-30 ②線の太さ: この図面が本当に太さで描き分けているかを数える。

基準は `docs/k30_line_width_criteria.md`(**測る前にコミット済み**)。
構えは `docs/reading_stance.md`、製図の決まりの原文は `docs/drafting_rules_reference.md`。

**なぜ数えるのか**

JIS A 0150 13.2.3・13.2.5 は細線:太線:極太線 = 1:2:4 と決め、
**切られている壁・柱・建具枠は太く、見えがかりの家具・設備・寸法線は細い**としている。
だが **2026-09-24 12:01 の決まり**により、**決まりが言っていることは、実際の図面で
そうなっているかを数えてから根拠にする。**
①ページの構造で、JIS のとおりに直すと**かえって読めなくなる**場面を踏んだばかりである。

**この道具がしないこと**

- **本番の経路を一切通らない。**`axes/` にも `intake/` にも何も足していない。
- **図面の文字を 1 文字も出さない。**出すのは件数・割合・太さの値だけ。

実行::

    .venv/bin/python -m benchmarks.measure_k30_line_width --pdf <図面> [--json <出力>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.measure_k30_page_structure import (  # noqa: E402
    FRAME_MAX_RATIO,
    FRAME_MIN_RATIO,
    SNAP,
    largest_closed_rect,
    merge,
    segments,
)

#: 山の切れ目。**JIS の 1:2:4 は隣どうしが 2 倍なので、1.5 倍を切れ目にすれば分かれる。**
#: 基準に先に書いた値であり、結果を見てから動かしていない。
PEAK_RATIO = 1.5

#: 太さを同じものとみなす丸め(pt)。PDF の線幅は浮動小数で入っている。
WIDTH_ROUND = 3


def line_widths(page: pymupdf.Page) -> Counter[float]:
    """そのページの線を、太さごとに数える。

    **太さは回転で変わらない**ので、表示の向きに直す必要はない。
    太さが記録されていない線は `0.0` として数える(**既定値で埋めない**)。
    """
    counts: Counter[float] = Counter()
    for drawing in page.get_drawings():
        width = drawing.get("width")
        key = 0.0 if width is None else round(float(width), WIDTH_ROUND)
        strokes = sum(1 for item in drawing["items"] if item[0] in ("l", "re", "qu", "c"))
        if strokes:
            counts[key] += strokes
    return counts


def peaks(counts: Counter[float]) -> list[list[float]]:
    """太さを昇順に並べ、**隣との比が 1.5 倍以上のところで切る。**

    太さが記録されていない線(0.0)は、比が計算できないので山に入れない。
    **数えなかったことを隠さないため、呼び出し側で別に数える。**
    """
    values = sorted(width for width in counts if width > 0)
    if not values:
        return []
    groups: list[list[float]] = [[values[0]]]
    for width in values[1:]:
        if width / groups[-1][-1] >= PEAK_RATIO:
            groups.append([width])
        else:
            groups[-1].append(width)
    return groups


def _prune_leaves(nodes: dict, edges: list[tuple]) -> int:
    """**端点を辿って出発点に戻れる線**の本数を数える。

    端が 1 本しか繋がっていない点を繰り返し落とすと、残るのは
    **輪になっている線と、輪どうしを結ぶ線**だけになる。
    """
    degree: Counter = Counter()
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    alive = [True] * len(edges)
    changed = True
    while changed:
        changed = False
        for index, (a, b) in enumerate(edges):
            if not alive[index]:
                continue
            if degree[a] <= 1 or degree[b] <= 1:
                alive[index] = False
                degree[a] -= 1
                degree[b] -= 1
                changed = True
    return sum(alive)


def closed_ratio(page: pymupdf.Page, wanted: set[float]) -> tuple[int, int]:
    """その太さの線だけを取り出したとき、**輪になっている本数と全体の本数。**"""
    matrix = page.rotation_matrix
    edges: list[tuple] = []
    nodes: dict = {}

    def node(point) -> tuple[int, int]:
        key = (round(point.x / SNAP), round(point.y / SNAP))
        nodes.setdefault(key, key)
        return key

    for drawing in page.get_drawings():
        width = drawing.get("width")
        key = 0.0 if width is None else round(float(width), WIDTH_ROUND)
        if key not in wanted:
            continue
        for item in drawing["items"]:
            if item[0] == "l":
                edges.append((node(item[1] * matrix), node(item[2] * matrix)))
            elif item[0] == "re":
                rect = pymupdf.Rect(item[1] * matrix)
                corners = [
                    node(pymupdf.Point(rect.x0, rect.y0)),
                    node(pymupdf.Point(rect.x1, rect.y0)),
                    node(pymupdf.Point(rect.x1, rect.y1)),
                    node(pymupdf.Point(rect.x0, rect.y1)),
                ]
                edges.extend(
                    (corners[i], corners[(i + 1) % 4]) for i in range(4)
                )
    if not edges:
        return 0, 0
    return _prune_leaves(nodes, edges), len(edges)


def measure(pdf_path: str | Path) -> dict:
    pages: list[dict] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            horizontal, vertical = segments(page)
            frame = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
            framed = False
            if frame is not None:
                x0, y0, x1, y1 = frame
                ratio = (x1 - x0) * (y1 - y0) / (page.rect.width * page.rect.height)
                framed = FRAME_MIN_RATIO <= ratio <= FRAME_MAX_RATIO
            counts = line_widths(page)
            groups = peaks(counts)
            row: dict = {
                "page": index + 1,
                "枠が取れた": framed,
                "線の本数": sum(counts.values()),
                "太さが記録されていない線": counts.get(0.0, 0),
                "太さの種類": len([w for w in counts if w > 0]),
                "山の数": len(groups),
                "山ごとの太さ": [[round(w, 3) for w in group] for group in groups],
                "山ごとの本数": [sum(counts[w] for w in group) for group in groups],
                "いちばん太い山といちばん細い山の比": (
                    round(max(groups[-1]) / min(groups[0]), 2) if len(groups) >= 2 else None
                ),
                "太い山の輪": None,
                "細い山の輪": None,
            }
            if framed and len(groups) >= 2:
                thick_closed, thick_total = closed_ratio(page, set(groups[-1]))
                thin_closed, thin_total = closed_ratio(page, set(groups[0]))
                row["太い山の輪"] = [thick_closed, thick_total]
                row["細い山の輪"] = [thin_closed, thin_total]
            pages.append(row)
    return {"pages": pages, "summary": summarize(pages)}


def summarize(pages: list[dict]) -> dict:
    framed = [page for page in pages if page["枠が取れた"]]
    split = [page for page in framed if page["山の数"] >= 2]
    thick_closed = sum(page["太い山の輪"][0] for page in split if page["太い山の輪"])
    thick_total = sum(page["太い山の輪"][1] for page in split if page["太い山の輪"])
    thin_closed = sum(page["細い山の輪"][0] for page in split if page["細い山の輪"])
    thin_total = sum(page["細い山の輪"][1] for page in split if page["細い山の輪"])
    ratios = [
        page["いちばん太い山といちばん細い山の比"]
        for page in split
        if page["いちばん太い山といちばん細い山の比"] is not None
    ]
    return {
        "ページ数": len(pages),
        "枠が取れたページ": len(framed),
        "山が2つ以上あるページ": len(split),
        "線A(枠が取れたページのうちの割合)": (
            round(len(split) / len(framed), 4) if framed else None
        ),
        "太い山と細い山の比の最小": min(ratios) if ratios else None,
        "太い山と細い山の比の最大": max(ratios) if ratios else None,
        "太い山の線のうち輪になっている割合": (
            round(thick_closed / thick_total, 4) if thick_total else None
        ),
        "細い山の線のうち輪になっている割合": (
            round(thin_closed / thin_total, 4) if thin_total else None
        ),
        "線B(太い山のほうが高いか)": (
            (thick_closed / thick_total) > (thin_closed / thin_total)
            if thick_total and thin_total
            else None
        ),
        "太さが記録されていない線": sum(page["太さが記録されていない線"] for page in pages),
        "線の本数": sum(page["線の本数"] for page in pages),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    result = measure(args.pdf)
    for row in result["pages"]:
        print(
            f"p{row['page']:2d} 枠={row['枠が取れた']} 線={row['線の本数']:6d} "
            f"太さの種類={row['太さの種類']:3d} 山={row['山の数']} "
            f"本数={row['山ごとの本数']} 比={row['いちばん太い山といちばん細い山の比']} "
            f"太い山の輪={row['太い山の輪']} 細い山の輪={row['細い山の輪']}"
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
