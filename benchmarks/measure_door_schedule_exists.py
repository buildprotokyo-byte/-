"""38周目の測定(トライアルB): 建具表はこの図面にあるのか、無いのか。

基準は `docs/b_door_schedule_exists_criteria.md`(測る前にコミット済み)。

37周目で基準が自分自身と食い違った((あ)建具表が無い / (い)見出しが合っていない)。
直し方が正反対なので、この1点だけを分ける。

**こちらの語を図面に当てて、当たったページ数だけを数える向き。**
図面の文字をこちらに持ってこないので、中身は1文字も出ない。

実行::

    .venv/bin/python -m benchmarks.measure_door_schedule_exists --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.schedule_tables import (
    DOOR_COLUMN_SYNONYMS,
    FINISH_COLUMN_SYNONYMS,
    HEADER_SEARCH_ROWS,
    _role_of,
)

#: 対照2。図面に出るはずのない語。ここで 0 にならなければ数え方が壊れている。
IMPOSSIBLE_WORD = "冷蔵庫置場専用階段"


def pages_with_word(pdf: Path, page_count: int, word: str) -> list[int]:
    """その語が文字として現れるページ番号(1 始まり)。**文字は出さない。**"""
    hits = []
    with fitz.open(pdf) as doc:
        for i in range(page_count):
            if word in doc[i].get_text():
                hits.append(i + 1)
    return hits


def header_cells(table, synonyms) -> tuple[int, int]:
    """(見出し行の升目の総数, どの役割にも当たらなかった升目の数)。"""
    total = 0
    unmatched = 0
    for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
        for cell in table.rows[row_index]:
            if not cell.text.strip():
                continue
            total += 1
            if _role_of(cell.text, synonyms) is None:
                unmatched += 1
    return total, unmatched


def measure(pdf: Path, page_count: int, word: str, synonyms, label: str):
    print(f"\n=== {label}(語: {len(word)} 文字)===")
    pages = pages_with_word(pdf, page_count, word)
    print(f"G1 その語が現れるページ数: {len(pages)} / {page_count}")
    if not pages:
        print("G2 そのうち罫線の表があるページ: 0")
        print("G3 そのページにある表の数: 0")
        print("G4 見出し行の升目: 0 / 当たらなかった升目: 0")
        return len(pages), 0

    tables = []
    pages_with_tables = 0
    for page in pages:
        found = list(find_tables(pdf, page - 1))
        if found:
            pages_with_tables += 1
        tables += found
    print(f"G2 そのうち罫線の表があるページ: {pages_with_tables}")
    print(f"G3 そのページにある表の数: {len(tables)}")

    total = unmatched = 0
    for table in tables:
        t, u = header_cells(table, synonyms)
        total += t
        unmatched += u
    ratio = f"{unmatched / total:.3f}" if total else "-"
    print(f"G4 見出し行の升目 {total} / どの役割にも当たらなかった升目 {unmatched}"
          f" = {ratio}")
    return len(pages), pages_with_tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    print(f"図面: {page_count} ページ")

    g1, g2 = measure(args.pdf, page_count, "建具", DOOR_COLUMN_SYNONYMS, "建具")
    measure(args.pdf, page_count, "仕上", FINISH_COLUMN_SYNONYMS, "G5・対照1 仕上")

    print("\n=== 対照 ===")
    impossible = pages_with_word(args.pdf, page_count, IMPOSSIBLE_WORD)
    print(f"対照2 出るはずのない語: {len(impossible)} ページ"
          f" / 0 になったか: {'はい' if not impossible else 'いいえ'}")
    repeats = [len(pages_with_word(args.pdf, page_count, "建具")) for _ in range(3)]
    print(f"対照3 反復 3 回(G1): {repeats}")

    print("\n=== 分かれ道(基準に先に書いたもの) ===")
    if impossible:
        print("   対照2 が 0 にならなかった → 数え方が壊れている。結論を出さない。")
    elif g1 >= 1 and g2 >= 1:
        print("   G1 ≧ 1 かつ G2 ≧ 1 → (い)。建具表はこの図面にある。"
              "落ちどころは見出しの当て方。")
    elif g1 >= 1:
        print("   G1 ≧ 1 かつ G2 = 0 → 建具の語はあるが罫線の表が無いページ。")
    else:
        print("   G1 = 0 → (あ)に寄る。別の手がかりで探す周を作る。")


if __name__ == "__main__":
    main()
