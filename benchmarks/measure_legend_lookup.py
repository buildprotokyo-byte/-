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
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from benchmarks import page_geometry

from axes.image_axis.legend_lookup import (
    KIND_EQUIPMENT,
    KIND_WORK,
    LegendTable,
    distinguishable,
    mark_colour_agreement,
    match_line_colors,
    match_line_styles,
    match_marks,
    normalize,
    needs_knowledge,
    summarize,
)

#: 表題欄を外す線引きは **`benchmarks/page_geometry` に集めてある**(K-26 2 番)。
#: 同じ規則が 8 つのファイルに写し取られていて、5 つが回転前の座標のまま残っていた。
TITLE_BLOCK_BOTTOM = page_geometry.TITLE_BLOCK_BOTTOM
title_block_top = page_geometry.title_block_top
_shown = page_geometry.shown


def _words(page: pymupdf.Page) -> list[str]:
    cut = title_block_top(page)
    return [
        w[4]
        for w in page.get_text("words")
        if _shown(page, w[:4]).y0 < cut and normalize(w[4])
    ]


def _coloured_words(page: pymupdf.Page) -> list[tuple[str, tuple[float, ...]]]:
    """文字とその色。**記号が凡例と同じ色で刷られているか**を見るために使う。"""
    cut = title_block_top(page)
    out: list[tuple[str, tuple[float, ...]]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if _shown(page, span["bbox"]).y0 >= cut or not normalize(span["text"]):
                    continue
                packed = int(span["color"])
                out.append(
                    (
                        span["text"],
                        (
                            round(((packed >> 16) & 0xFF) / 255.0, 4),
                            round(((packed >> 8) & 0xFF) / 255.0, 4),
                            round((packed & 0xFF) / 255.0, 4),
                        ),
                    )
                )
    return out


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


#: 図面の語は空白で切って拾うので、**中に空白のある記号は 1 語と一致しようがない。**
_HAS_SPACE = re.compile(r"\s")

#: 括弧の中が空欄の記号(図面では数や機種名が入る)も、そのままでは一致しない。
_EMPTY_BRACKET = re.compile(r"[(（][\s　]*[)）]")


def _table_side_counts(
    table: LegendTable, texts: list[str], loose: bool, named_keys: set[tuple[str, str]]
) -> dict[str, Any]:
    """**対照表の側から**、行ごとに図面で当たったかを数える(M9)。

    数えるだけで、ここで行を削ったりはしない。0 件の行は
    **記号の書き方だけで**理由を分ける(図面を見て推し量らない)。
    """
    seen = Counter(normalize(t) for t in texts)
    code_names: dict[str, set[str]] = {}
    for row in table.symbols:
        code_names.setdefault(normalize(str(row["code"])), set()).add(str(row["name"]))
    for row in table.work_marks:
        code_names.setdefault(normalize(str(row["code"])), set()).add(str(row["meaning"]))

    out: dict[str, dict[str, int]] = {}
    for kind, rows, match_kind in (
        ("設備の記号", table.symbols, KIND_EQUIPMENT),
        ("工事の区分", table.work_marks, KIND_WORK),
    ):
        hit = 0
        named_rows = 0
        reasons: Counter = Counter()
        silent: Counter = Counter()
        for row in rows:
            raw = str(row["code"])
            key = normalize(raw)
            if (match_kind, key) in named_keys:
                named_rows += 1
            if seen.get(key):
                hit += 1
                if (match_kind, key) not in named_keys:
                    if kind == "設備の記号" and not loose and not distinguishable(raw):
                        silent["仮の判断で落ちた形"] += 1
                    elif len(code_names.get(key, ())) > 1:
                        silent["1つに決まらない"] += 1
                    else:
                        silent["そのほか"] += 1
                continue
            if _HAS_SPACE.search(raw) or _EMPTY_BRACKET.search(raw):
                reasons["完全一致しようがない形"] += 1
            elif kind == "設備の記号" and not loose and not distinguishable(raw):
                reasons["仮の判断で落ちた形"] += 1
            elif len(code_names.get(key, ())) > 1:
                reasons["1つに決まらない"] += 1
            else:
                reasons["図面に出なかった"] += 1
        out[kind] = {
            "対照表の行": len(rows),
            "記号の文字が図面に出た行": hit,
            "**その行から名前が付いた行**": named_rows,
            "文字は出たが名前が付かなかった行": hit - named_rows,
            "名前が付かなかった理由": dict(silent),
            "文字が1回も出なかった行": len(rows) - hit,
            "出なかった理由": dict(reasons),
        }
    return out


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

    coloured: list[tuple[str, tuple[float, ...]]] = []
    coloured_pages: list[tuple[int, str, tuple[float, ...]]] = []
    for number in range(1, len(doc) + 1):
        if number in legend:
            continue
        for text, colour in _coloured_words(doc[number - 1]):
            coloured.append((text, colour))
            coloured_pages.append((number, text, colour))
    colour_agreement = mark_colour_agreement(
        [
            (text, colour)
            for text, colour in coloured
            if any(
                normalize(str(row["code"])) == normalize(text)
                for row in table.work_marks
            )
        ],
        table,
    )

    # **設備の記号の色から、その 1 個の工事の区分が読めるか。**
    # 凡例は「配線・シンボル色」として、記号にも同じ色の決まりを書いている。
    scope = set(args.color_scope_pages)
    symbol_codes = {normalize(str(row["code"])) for row in table.symbols}
    symbol_colours: list[tuple[int, tuple[float, ...]]] = [
        (number, colour)
        for number, text, colour in coloured_pages
        if normalize(text) in symbol_codes
        and (args.loose or distinguishable(text))
    ]
    symbol_meaning = Counter()
    symbol_meaning_in_scope = Counter()
    for (number, colour), match in zip(
        symbol_colours, match_line_colors([c for _, c in symbol_colours], table)
    ):
        key = match.meaning if match.matched else "凡例に無い色"
        symbol_meaning[key] += 1
        if number in scope:
            symbol_meaning_in_scope[key] += 1

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
            # K-22 判断 1。**名前の出どころを分けて見せる。**ここが
            # {"対照表": n} だけなら、知識の道はまだ 1 件も名前を出していない。
            "名前の出どころ": counts.by_source,
            # K-22 判断 3。**決めてはいけない**(名前が 2 つ)件数。
            "質疑へ回す件数": counts.questions,
        },
        # K-22 判断 1・2。**知識の道へ回してよい箇所**の数。
        # **これは「知識が答えられる数」ではない。**回してよい入口の数である。
        "知識の道へ回す箇所": {
            "件数": sum(1 for m in matches if needs_knowledge(m)),
            "理由別": dict(
                Counter(m.reason for m in matches if needs_knowledge(m))
            ),
            "語の種類": len({m.text for m in matches if needs_knowledge(m)}),
        },
        "対照表の行が図面で当たったか": _table_side_counts(
            table,
            texts,
            args.loose,
            {(m.kind, normalize(m.text)) for m in named},
        ),
        "工事の区分が凡例と同じ色で刷られているか": colour_agreement,
        "設備の記号の色から工事の区分が読めた件数": {
            "全ページ": dict(symbol_meaning),
            "凡例が「使用する」と書いたページだけ": dict(symbol_meaning_in_scope),
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
