"""45周目の測定(トライアルC): 「表の読み取りは縮尺に依存しない」は本当か。

基準は `docs/c_schedule_scale_independence_criteria.md`(測る前にコミット済み)。

コード自身が `intake/drawing_intake.py:931-932` でそう書いている。
**取り決め①は記録を信じるなだが、コードのコメントも記録である。**

**回し方はこのファイルの中だけ。実装には手を入れない。**

**出すのはページ数と件数だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_schedule_scale_independence --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fitz

from axes.image_axis.pdf_vector_symbols import extract_scale
from axes.image_axis.schedule_tables import read_door_schedules, read_finish_schedules


def read_tables(pdf: Path, pages: list[int]) -> dict:
    """そのページ集合から表を読む。**升目の文字は出さない。**"""
    doors, finishes, door_rows, finish_rows = 0, 0, 0, 0
    door_pages, finish_pages = [], []
    for i in pages:
        d = read_door_schedules(pdf, i)
        f = read_finish_schedules(pdf, i)
        if d:
            door_pages.append(i + 1)
        if f:
            finish_pages.append(i + 1)
        doors += len(d)
        finishes += len(f)
        door_rows += sum(len(s.rows) for s in d)
        finish_rows += sum(len(s.rows) for s in f)
    return {"建具表の数": doors, "仕上表の数": finishes,
            "建具表の行": door_rows, "仕上表の行": finish_rows,
            "建具表のページ": door_pages, "仕上表のページ": finish_pages}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    every = list(range(page_count))
    print(f"図面: {page_count} ページ")

    # V1: 縮尺をまったく読まずに表だけ読む。
    v1 = read_tables(args.pdf, every)

    # V0: いまのまま。縮尺を読んでから、全ページの表を読む。
    scales = {i: extract_scale(args.pdf, i) for i in every}
    v0 = read_tables(args.pdf, every)

    # V2: 縮尺が読めたページだけ表を読む(依存させてしまった場合)。
    with_scale = [i for i in every if scales[i] is not None]
    v2 = read_tables(args.pdf, with_scale)

    print(f"\n縮尺が読めたページ: {len(with_scale)} / {page_count}")

    print("\n=== W1〜W3 ===")
    keys = ("建具表の数", "仕上表の数", "建具表の行", "仕上表の行")
    print(f"{'':<12}{'V0 いまのまま':>14}{'V1 表を先に':>14}{'V2 縮尺に依存':>16}")
    for k in keys:
        print(f"{k:<12}{v0[k]:>14}{v1[k]:>14}{v2[k]:>16}")
    print(f"{'仕上表のページ':<12}{str(v0['仕上表のページ']):>14}"
          f"{str(v1['仕上表のページ']):>14}{str(v2['仕上表のページ']):>16}")

    print("\n=== W4 V0 と V1 の違い ===")
    diffs = [k for k in ("建具表の数", "仕上表の数", "建具表の行", "仕上表の行",
                         "建具表のページ", "仕上表のページ")
             if v0[k] != v1[k]]
    print(f"   違った項目: {diffs if diffs else 'なし'}")
    print(f"   W6 ページ番号の並びは同じか: "
          f"{'はい' if v0['仕上表のページ'] == v1['仕上表のページ'] else 'いいえ'}")

    print("\n=== W5 依存させると失う行 ===")
    lost_d = v0["建具表の行"] - v2["建具表の行"]
    lost_f = v0["仕上表の行"] - v2["仕上表の行"]
    print(f"   建具表の行: {lost_d} / 仕上表の行: {lost_f}")

    print("\n=== 対照 ===")
    c1 = (v0["建具表の数"] == 0 and v0["仕上表の数"] == 2
          and v0["仕上表のページ"] == [3, 4] and v0["仕上表の行"] == 59)
    print(f"対照1 V0 が35・36周目を再現したか"
          f"(建具表0 / 仕上表2 / ページ[3,4] / 行59): {'はい' if c1 else 'いいえ'}")
    print(f"対照2 縮尺が読めたページ数が44周目と同じ 20 か: "
          f"{'はい' if len(with_scale) == 20 else f'いいえ({len(with_scale)})'}")
    reps = [read_tables(args.pdf, every)["仕上表の行"] for _ in range(3)]
    print(f"対照3 反復 3 回(仕上表の行): {reps}")

    print("\n=== 結論の形(基準に先に書いたもの) ===")
    if not c1:
        print("   対照1 が再現しなかった → 結論を出さない。")
    elif not diffs:
        print("   W4 = 違いなし → **コメントは正しい。この順番は意味を持っていない。**"
              " 安全に入れ替えられる並びが1本あると確定。"
              "**ただし入れ替える理由が無いので入れ替えない。記録として残す。**")
    else:
        print(f"   W4 = 違いあり({diffs}) → **コメントのほうが間違っている。**"
              " 何がどう違うかを名指しして、コメントを直す変更をPRに出す。")


if __name__ == "__main__":
    main()
