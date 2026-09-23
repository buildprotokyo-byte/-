"""44周目の測定(トライアルC): 「縮尺が読めなければ開き戸を探さない」の値段。

基準は `docs/c_scale_gate_cost_criteria.md`(測る前にコミット済み)。

35周目に自分で「測れていないこと」として書いた穴を測る。
**縮尺が読めないページでは、開き戸を探してすらいない。**

**仮の縮尺を当てるのはこのファイルの中だけ。実装には手を入れない。**
**これは「その縮尺が正しい」という主張ではない。** どの縮尺を当てても0件なら
取りこぼしが無いと言えるだけの、片側しか言えない測り方である。

**出すのはページ数と件数だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_scale_gate_cost --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fitz

from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale, find_door_arcs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)

    read, unread = {}, []
    for i in range(page_count):
        scale = extract_scale(args.pdf, i)
        if scale is None:
            unread.append(i)
        else:
            read[i] = scale
    print(f"図面: {page_count} ページ")
    print(f"R1 縮尺が読めたページ {len(read)} / 読めなかったページ {len(unread)}")

    # R2: 実際に出てきた縮尺の種類。**こちらで作った値は使わない。**
    kinds = sorted({s.denominator for s in read.values()})
    print(f"R2 実際に出てきた縮尺の種類: {len(kinds)} 通り(分母 {kinds})")

    print("\n=== 対照1 縮尺が読めたページを、本来の縮尺で ===")
    own = {i: len(find_door_arcs(args.pdf, i, s)) for i, s in read.items()}
    print(f"   円弧の合計: {sum(own.values())} / 円弧が出たページ数: "
          f"{sum(1 for v in own.values() if v)}")

    print("\n=== R3 縮尺が読めなかったページに、実在する縮尺を仮に当てる ===")
    per_page: dict[int, dict[float, int]] = {}
    for i in unread:
        per_page[i] = {}
        for d in kinds:
            arcs = find_door_arcs(args.pdf, i, DrawingScale(denominator=d,
                                                            source_text="仮(測定用)"))
            per_page[i][d] = len(arcs)
    header = "ページ " + " ".join(f"1/{d:g}".rjust(7) for d in kinds)
    print(f"   {header}")
    for i in unread:
        row = " ".join(str(per_page[i][d]).rjust(7) for d in kinds)
        print(f"   {i + 1:>5} {row}")

    r4 = [i for i in unread if not any(per_page[i].values())]
    r5 = [i for i in unread if any(per_page[i].values())]
    print(f"\nR4 どの縮尺を当てても 0 件だったページ: {len(r4)} / {len(unread)}")
    print(f"R5 1 つでも円弧が出たページ: {len(r5)} / {len(unread)}")
    if r5:
        lo = min(min(per_page[i].values()) for i in r5)
        hi = max(max(per_page[i].values()) for i in r5)
        tot = {d: sum(per_page[i][d] for i in r5) for d in kinds}
        print(f"R6 そのページでの円弧の数の振れ: 最小 {lo} / 最大 {hi}")
        print(f"   縮尺ごとの合計: {tot}")

    print("\n=== 対照2・R7 縮尺が読めたページに、別の縮尺を当てる ===")
    swings = []
    for i, s in read.items():
        counts = {d: len(find_door_arcs(args.pdf, i, DrawingScale(denominator=d,
                                                                  source_text="仮(測定用)")))
                  for d in kinds}
        if len(set(counts.values())) > 1:
            swings.append(i)
    print(f"   別の縮尺を当てると数が変わったページ: {len(swings)} / {len(read)}")
    print(f"   縮尺は円弧の判定に効いているか: {'はい' if swings else 'いいえ'}")

    print("\n=== 対照3 反復 3 回(R5 のページ数) ===")
    reps = []
    for _ in range(3):
        n = 0
        for i in unread:
            if any(len(find_door_arcs(args.pdf, i, DrawingScale(denominator=d,
                                                                source_text="仮")))
                   for d in kinds):
                n += 1
        reps.append(n)
    print(f"   {reps}")

    print("\n=== 結論の形(基準に先に書いたもの) ===")
    if not swings:
        print("   対照2 が通らなかった(縮尺を変えても数が変わらない)"
              " → 縮尺は円弧の判定に効いていない。結論を出さない。")
    elif len(r4) == len(unread):
        print("   R4 = 全部 → **順番の決まりは何も取りこぼしていない。**"
              " この並びは入れ替える価値なし。次の並びへ。")
    else:
        print(f"   R5 = {len(r5)} ≧ 1 → **探してすらいないページに候補がある。**"
              " ただし振れが大きければ「拾える」とは書かない。"
              "**書くのは上限だけ。**")


if __name__ == "__main__":
    main()
