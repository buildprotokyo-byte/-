"""凡例の対照表で照合した結果を**数える**(K-20 3 番)。

測るものは `docs/k20_legend_lookup_criteria.md` に先に書いてある。ここは数えるだけで、
**数を良く見せる加工をしない。**名前が付かなかった数(不明)も同じだけ出す。

**数だけを出す。**図面の中身(室名・寸法・事務所名)は出さない。出力をそのまま
リポジトリの報告に貼れるようにするためである。

実行::

    python -m benchmarks.measure_legend_lookup --pdf <図面.pdf> \
        --table <対照表.json> --legend-pages 6 22
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from axes.image_axis.legend_lookup import (
    KIND_EQUIPMENT,
    KIND_WORK,
    LegendTable,
    match_line_colors,
    match_line_styles,
    match_marks,
    normalize,
    summarize,
)

#: 表題欄はこれより左。事務所名・個人名・登録番号が入るので**数にも入れない。**
TITLE_BLOCK_X = 75.0


def _words(page: pymupdf.Page) -> list[str]:
    return [w[4] for w in page.get_text("words") if w[0] >= TITLE_BLOCK_X and normalize(w[4])]


def _fabrication_check(matches, table: LegendTable) -> list[str]:
    """**捏造が 1 件でもあるか。**名前が対照表の同じ行から来ていなければ捏造。"""
    allowed: dict[tuple[str, str], set[str]] = {}
    for row in table.work_marks:
        allowed.setdefault((KIND_WORK, normalize(str(row["code"]))), set()).add(
            str(row["meaning"])
        )
    for row in table.symbols:
        allowed.setdefault((KIND_EQUIPMENT, normalize(str(row["code"]))), set()).add(
            str(row["name"])
        )
    bad: list[str] = []
    for match in matches:
        if not match.matched:
            continue
        key = (match.kind, normalize(match.text))
        value = match.meaning if match.kind == KIND_WORK else match.name
        if value not in allowed.get(key, set()):
            bad.append(f"{match.text} → {value}")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--legend-pages", type=int, nargs="*", default=())
    parser.add_argument(
        "--color-scope-pages",
        type=int,
        nargs="*",
        default=(),
        help="**凡例が色の意味を使うと書いたページ**(この図面では電気位置図)",
    )
    parser.add_argument(
        "--loose",
        action="store_true",
        help="**仮の判断(見分けの付かない形の記号を落とす)を外して数える**",
    )
    args = parser.parse_args()

    table = LegendTable.load(args.table)
    doc = pymupdf.open(args.pdf)
    legend = set(args.legend_pages)

    texts: list[str] = []
    per_page: dict[int, list[str]] = {}
    pages_with_text = 0
    for number in range(1, len(doc) + 1):
        if number in legend:
            continue
        words = _words(doc[number - 1])
        if words:
            pages_with_text += 1
        per_page[number] = words
        texts.extend(words)

    matches = match_marks(texts, table, strict_equipment_codes=not args.loose)
    counts = summarize(matches)
    loose_named = summarize(
        match_marks(texts, table, strict_equipment_codes=False)
    ).named
    named = [m for m in matches if m.matched]
    bad = _fabrication_check(matches, table)

    strokes: list[tuple[float, ...]] = []
    scoped_strokes: list[tuple[float, ...]] = []
    for number in range(1, len(doc) + 1):
        if number in legend:
            continue
        for drawing in doc[number - 1].get_drawings():
            colour = drawing.get("color")
            if colour:
                value = tuple(round(float(c), 4) for c in colour)
                strokes.append(value)
                if number in set(args.color_scope_pages):
                    scoped_strokes.append(value)
    colour_matches = match_line_colors(strokes, table)
    colour_counts = summarize(colour_matches)
    scoped = summarize(match_line_colors(scoped_strokes, table))
    scoped_named = Counter(
        m.name for m in match_line_colors(scoped_strokes, table) if m.matched
    )
    dash_patterns = [
        d.get("dashes")
        for number in range(1, len(doc) + 1)
        if number not in legend
        for d in doc[number - 1].get_drawings()
    ]
    real_dashes = [p for p in dash_patterns if p and p != "[] 0"]
    style_matches = match_line_styles([()] * len(real_dashes), table)

    report: dict[str, Any] = {
        "対照表に入った件数": {
            "工事の区分": len(table.work_marks),
            "設備の記号": len(table.symbols),
            "線の色": len(table.line_colors),
            "線種": len(table.line_styles),
            "記号の合計": table.mark_count,
        },
        "照合の対象": {
            "凡例を除いたページ": len(doc) - len(legend),
            "文字のあったページ": pages_with_text,
            "拾った文字の総数": counts.total,
        },
        "照合の結果": {
            "名前が付いた件数": counts.named,
            "仮の判断を外したときの名前が付いた件数": loose_named,
            "不明の件数": counts.unknown,
            "捏造の件数": len(bad),
            "不明の内訳": counts.by_reason,
            "名前が付いた内訳": dict(Counter(m.name for m in named)),
            "区分別": dict(Counter(m.kind for m in named)),
        },
        "工事の区分がどのページに出たか": {
            name: {
                str(number): sum(
                    1
                    for m in match_marks(words, table)
                    if m.matched and m.kind == KIND_WORK and m.name == name
                )
                for number, words in per_page.items()
                if any(
                    m.matched and m.kind == KIND_WORK and m.name == name
                    for m in match_marks(words, table)
                )
            }
            for name in sorted(
                {m.name for m in matches if m.matched and m.kind == KIND_WORK}
            )
        },
        "ページごとに名前が付いた件数": {
            str(number): summarize(match_marks(words, table)).named
            for number, words in per_page.items()
            if summarize(match_marks(words, table)).named
        },
        "線": {
            "線の総数": len(strokes),
            "刻みのある線": len(real_dashes),
            "線種で名前が付いた件数": sum(1 for m in style_matches if m.matched),
            "色が凡例と一致した件数(全ページ)": colour_counts.named,
            "色が凡例と合わなかった件数(全ページ)": colour_counts.unknown,
            "色の内訳(全ページ)": dict(
                Counter(m.name for m in colour_matches if m.matched)
            ),
            "凡例が効くと書いたページだけ": {
                "ページ": list(args.color_scope_pages),
                "線の総数": scoped.total,
                "一致": scoped.named,
                "不明": scoped.unknown,
                "内訳": dict(scoped_named),
            },
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if bad:
        print("\n捏造:", bad[:10])


if __name__ == "__main__":
    main()
