"""凡例のページから**対照表**を作る(K-20)。

なぜ「読ませる」のではなく「対照表」なのか
------------------------------------------
11 周目・12 周目は凡例のページから「名前 ↔ 図形」を**作らせた**。1 つの名前に
最大 474 通りの形が付いた(`docs/a1_legend_region_report.md`)。原因は K-12 で
分かっている。**名前を付ける相手が「記号 1 個」ではなく「線や円弧の 1 本 1 本」
だった。**

この道具は名前を作らない。**凡例が自分で書いている対を、そのまま写すだけ**である。
写したものを `axes.image_axis.legend_lookup` が引き当てに使う。

凡例の並び(この案件の図面で実測した形)
--------------------------------------
ページは 270 度回っているので、**文字は 90 度倒れて入っている**。そのため

* **1 つの行は「同じ x の縦並び」**になる(行が x、列が y)。
* 見出し(`名称` `記号` `内容`)は右端の 1 本の列(同じ x)に縦に並ぶ。

対にしてよいのは、**同じ表の中で x が揃っている 1 組だけ**である。別の表の語や、
表の中の注記と対にしないために、次の 4 つで絞る。

1. 表の切れ目を、見出しの列にある `〈…〉`(群の名前)で決める。
2. 名前の列は **y が 1 つの値に揃っている**(左そろえ)。揃っていない語は注記。
3. 記号の列は、`記号` の見出しの直後にある **1 かたまり**だけ。
4. 対は **1 対 1** のものだけ採る。相手が 2 つ以上いるものは捨てる。

**出力は図面から取った文字なので、リポジトリに置かない。**`--out` で共有フォルダの
パスを渡すこと。既定値は無い。

実行::

    python -m benchmarks.build_legend_lookup --pdf <図面.pdf> \
        --color-pages 6 22 --symbol-pages 22 --mark-pages 6 --out <表.json>
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

#: 表題欄はこれより左。事務所名・個人名・登録番号が入るので、**対照表に入れない。**
TITLE_BLOCK_X = 75.0

#: 同じ行とみなす x のずれ(pt)。凡例の文字は 90 度回転して入っているので、
#: **1 行が「同じ x の縦並び」**になる。
SAME_ROW_X = 4.0

#: 折り返した説明文を同じ升目とみなす x の幅(pt)。
EXPLAIN_X = 6.0

#: 同じ表の中とみなす x の間隔(pt)。これより離れたら別の表。
SAME_TABLE_X = 20.0

#: 同じ列とみなす y の間隔(pt)。これより離れたら別の升目。
SAME_COLUMN_Y = 6.0

#: 名前の列の y のずれ(pt)。名前は左そろえなので、ほぼ 1 つの値に揃う。
NAME_ROW_Y = 1.5

#: 見出しの文字は升目の中ほどに置かれるので、記号が見出しより少し上から始まることがある。
HEADER_SLACK_Y = 3.0

#: 記号として長すぎる字数。これを超えたら注記とみなす。
MAX_CODE_LENGTH = 8

#: 注記に出てくる語。**記号にも名前にも使わない。**
_NOTE = re.compile(r"例|記入|表記|確認|※|場合|する")

#: 記号は少なくとも 1 文字、字か数字を含む。
_HAS_LETTER = re.compile(r"[0-9A-Za-zぁ-んァ-ヶ一-龥ｦ-ﾟ]")

_OPEN = "(（"
_CLOSE = ")）"


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def _balanced(text: str) -> bool:
    """括弧が閉じているか。`乾（` のような切れ端を記号にしないため。"""
    return sum(text.count(c) for c in _OPEN) == sum(text.count(c) for c in _CLOSE)


def _usable_code(text: str) -> bool:
    body = _norm(text)
    return (
        1 <= len(body) <= MAX_CODE_LENGTH
        and bool(_HAS_LETTER.search(body))
        and _balanced(body)
        and not _NOTE.search(body)
    )


def _usable_name(text: str) -> bool:
    body = _norm(text)
    return len(body) >= 2 and not _NOTE.search(body)


def _words(page: pymupdf.Page) -> list[tuple[float, float, float, float, str]]:
    return [
        (w[0], w[1], w[2], w[3], w[4])
        for w in page.get_text("words")
        if w[0] >= TITLE_BLOCK_X and _norm(w[4])
    ]


def _spans(page: pymupdf.Page) -> list[dict[str, Any]]:
    """色つきの文字を、色ごと・位置つきで返す。"""
    out: list[dict[str, Any]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if span["bbox"][0] < TITLE_BLOCK_X or not _norm(span["text"]):
                    continue
                out.append(
                    {
                        "text": span["text"],
                        "color": span["color"],
                        "x": span["bbox"][0],
                        "y": span["bbox"][1],
                    }
                )
    return out


def _rgb(packed: int) -> list[float]:
    return [
        round(((packed >> 16) & 0xFF) / 255.0, 4),
        round(((packed >> 8) & 0xFF) / 255.0, 4),
        round((packed & 0xFF) / 255.0, 4),
    ]


def _first_cluster(words, key, gap: float) -> list:
    """`key` の順に並べて、**最初のひとかたまり**だけを返す。"""
    ordered = sorted(words, key=key)
    if not ordered:
        return []
    out = [ordered[0]]
    for word in ordered[1:]:
        if key(word) - key(out[-1]) > gap:
            break
        out.append(word)
    return out


def colour_rules(page: pymupdf.Page, page_number: int) -> list[dict[str, Any]]:
    """**色の意味**を、凡例が自分で名乗っている色から取る。

    「赤色」という語が**実際に赤で印字されている**。その色を鍵にする。
    **こちらで「赤はふつう撤去だろう」と決めない。**
    """
    words = _words(page)
    rules: list[dict[str, Any]] = []
    for span in _spans(page):
        label = _norm(span["text"])
        if len(label) > 3 or not label.endswith("色"):
            continue
        below = [
            w
            for w in words
            if abs(w[0] - span["x"]) <= SAME_ROW_X and w[1] > span["y"] + 1
        ]
        if not below:
            continue
        meaning = min(below, key=lambda w: w[1])[4]
        rules.append(
            {
                "color": _rgb(span["color"]),
                "label": span["text"].strip(),
                "meaning": meaning.strip(),
                "source_page": page_number,
            }
        )
    return rules


def mark_rules(page: pymupdf.Page, page_number: int) -> list[dict[str, Any]]:
    """「記号」と「内容」が見出しの表(改装種別凡例)を写す。

    記号の升目は**見出しから x で続くひとかたまり**に限る。離れたところに別の表
    (色の定義)があるので、そこまで拾うと工事の区分に色が混ざる。
    """
    words = _words(page)
    rules: list[dict[str, Any]] = []
    for head in [w for w in words if _norm(w[4]) == "記号"]:
        row = [
            w
            for w in words
            if abs(w[1] - head[1]) <= SAME_COLUMN_Y and w[0] < head[0] and w is not head
        ]
        # 見出しから x をたどって、**途切れるまで**が同じ表。
        codes = []
        edge = head[0]
        for word in sorted(row, key=lambda w: -w[0]):
            if edge - word[0] > SAME_TABLE_X:
                break
            edge = word[0]
            codes.append(word)
        codes = [c for c in codes if _usable_code(c[4])]
        if not codes:
            continue
        left = min(c[0] for c in codes)
        floor = max(c[3] for c in codes)
        below = [
            w
            for w in words
            if left - SAME_ROW_X <= w[0] <= head[0] and w[1] > floor + 1
        ]
        meanings = _first_cluster(below, key=lambda w: w[1], gap=SAME_COLUMN_Y)
        spans = _spans(page)
        for code in codes:
            cell = [m for m in meanings if abs(m[0] - code[0]) <= EXPLAIN_X]
            if not cell:
                continue
            cell.sort(key=lambda w: -w[0])
            rule = {
                "code": code[4].strip(),
                "meaning": "".join(w[4].strip() for w in cell),
                "source_page": page_number,
            }
            # **凡例がその記号を何色で刷っているか。**図面の同じ記号と突き合わせる相手。
            colours = {
                span["color"]
                for span in spans
                if _norm(span["text"]) == _norm(code[4])
                and abs(span["x"] - code[0]) <= SAME_ROW_X
                and abs(span["y"] - code[1]) <= SAME_COLUMN_Y
            }
            if len(colours) == 1:
                rule["color"] = _rgb(colours.pop())
            rules.append(rule)
    return rules


def symbol_rules(page: pymupdf.Page, page_number: int) -> list[dict[str, Any]]:
    """「名称」と「記号」が見出しの表(電気位置図凡例)を写す。

    **対にしてよいのは、同じ表の中で x が 1 対 1 に揃っている組だけ。**相手が
    2 つ以上いるものは、どちらか選べば捏造になるので捨てる。
    """
    words = _words(page)
    heads = sorted([w for w in words if _norm(w[4]) == "名称"], key=lambda w: w[1])
    if not heads:
        return []
    head_x = min(w[0] for w in heads)
    column = [w for w in words if w[0] >= head_x - SAME_TABLE_X]
    marks = sorted(
        [w for w in column if _norm(w[4]) == "記号"], key=lambda w: w[1]
    )
    starts = sorted(w[1] for w in column if w[4].startswith("〈"))
    others = sorted(
        w[1]
        for w in column
        if not w[4].startswith("〈") and _norm(w[4]) not in ("名称", "記号")
    )
    if len(starts) != len(marks):
        return []

    rules: list[dict[str, Any]] = []
    for index, (top, mark) in enumerate(zip(starts, marks)):
        if index + 1 < len(starts):
            bottom = starts[index + 1]
        else:
            bottom = next((y for y in others if y > mark[1]), float("inf"))
        body = [
            w
            for w in words
            if top - 2 <= w[1] < bottom
            and w[0] < head_x - SAME_ROW_X
            and not w[4].startswith("〈")
        ]
        # 見出しの文字は升目の中で中ほどに置かれるので、**記号が見出しより少し上から
        # 始まることがある。**その分だけ境を上げる。
        split = mark[1] - HEADER_SLACK_Y
        above = [w for w in body if w[1] < split]
        if not above:
            continue
        # 小見出し(`〈…〉`)は表の中にも並ぶ。**行の x で、どの小見出しの下かが決まる。**
        # **同じ y にあるものだけを見る。**帯で拾うと次の表の小見出しまで混ざる。
        groups = sorted(
            (
                w
                for w in words
                if w[4].startswith("〈")
                and abs(w[1] - top) <= SAME_COLUMN_Y
                and w[0] < head_x - SAME_ROW_X
            ),
            key=lambda w: w[0],
        )
        # 名前は左そろえなので、**y が最も多く揃っている値**が名前の列。
        row_y = Counter(round(w[1]) for w in above).most_common(1)[0][0]
        names = [
            w for w in above if abs(w[1] - row_y) <= NAME_ROW_Y and _usable_name(w[4])
        ]
        codes = _first_cluster(
            [w for w in body if w[1] >= split],
            key=lambda w: w[1],
            gap=SAME_COLUMN_Y,
        )
        codes = [c for c in codes if _usable_code(c[4])]
        for name in names:
            hits = [c for c in codes if abs(c[0] - name[0]) <= SAME_ROW_X]
            if len(hits) != 1:
                continue
            back = [n for n in names if abs(n[0] - hits[0][0]) <= SAME_ROW_X]
            if len(back) != 1:
                continue
            group = next((g[4].strip() for g in groups if g[0] >= name[0]), "")
            rules.append(
                {
                    "code": hits[0][4].strip(),
                    "name": name[4].strip(),
                    "group": group.strip("〈〉"),
                    "source_page": page_number,
                }
            )
    return rules


def line_style_rules(page: pymupdf.Page, page_number: int) -> list[dict[str, Any]]:
    """線種(実線・破線・二点鎖線)の見本を凡例から写す。

    おーちゃんの決め(K-20 4 番): **破線の刻みの比率が見本と合うものだけを一致**と
    し、太さと色は参考にとどめる。見本は「線種の名前が書いてあって、その升目に線が
    1 本引いてある」ところから取る。**名前が書かれていない線は見本にしない。**
    """
    words = _words(page)
    named = [w for w in words if _norm(w[4]).endswith(("線",)) and len(_norm(w[4])) <= 6]
    rules: list[dict[str, Any]] = []
    for label in named:
        near = [
            d
            for d in page.get_drawings()
            if abs(d["rect"].x0 - label[0]) <= EXPLAIN_X and d["items"]
        ]
        dashes = {d.get("dashes") for d in near}
        patterns = sorted(d for d in dashes if d and d != "[] 0")
        if not patterns:
            continue
        rules.append(
            {
                "label": label[4].strip(),
                "dash_pattern": patterns[0],
                "source_page": page_number,
            }
        )
    return rules


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--color-pages", type=int, nargs="*", default=())
    parser.add_argument("--mark-pages", type=int, nargs="*", default=())
    parser.add_argument("--symbol-pages", type=int, nargs="*", default=())
    parser.add_argument("--line-pages", type=int, nargs="*", default=())
    parser.add_argument(
        "--out", type=Path, required=True, help="**共有フォルダのパスを渡すこと**"
    )
    args = parser.parse_args()

    doc = pymupdf.open(args.pdf)
    table: dict[str, Any] = {
        "note": "凡例から写した対照表。図面の中身なのでリポジトリに置かない。",
        "binding": "案件の凡例",
        "line_colors": [],
        "line_styles": [],
        "work_marks": [],
        "symbols": [],
    }
    for number in args.color_pages:
        table["line_colors"] += colour_rules(doc[number - 1], number)
    for number in args.mark_pages:
        table["work_marks"] += mark_rules(doc[number - 1], number)
    for number in args.symbol_pages:
        table["symbols"] += symbol_rules(doc[number - 1], number)
    for number in args.line_pages:
        table["line_styles"] += line_style_rules(doc[number - 1], number)

    args.out.write_text(
        json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"線の色 {len(table['line_colors'])}件 / "
        f"線種 {len(table['line_styles'])}件 / "
        f"記号(工事の区分) {len(table['work_marks'])}件 / "
        f"記号(設備) {len(table['symbols'])}件 → {args.out}"
    )


if __name__ == "__main__":
    main()
