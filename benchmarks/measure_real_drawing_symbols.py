"""10周目の測定: 繰り返す図形から記号を数える手法を、実図面で測る。

**抽出と採点を分ける。**

- `extract(pdf_path, pages)` … **引数は PDF とページ番号だけ。** 正解を読まない。
- `score(clusters, golden_path, pages)` … そのあとで正解と突き合わせる。

**出すのは件数と割合だけ。** 記号の名前・位置・個数そのものは印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_real_drawing_symbols \
        --pdf <図面.pdf> --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from axes.image_axis.pdf_repeated_symbols import find_repeated_symbols
from axes.image_axis.pdf_vector_symbols import extract_scale

#: 記号を探すページ(0 始まり)。基準の文書で 22・23・24 ページと決めた。
TARGET_PAGES: tuple[int, ...] = (21, 22, 23)

#: 対照1 に使う、図形がほとんど無いページ(0 始まり)。
EMPTY_PAGES: tuple[int, ...] = (24, 25)

#: 対照3 に使う、**記号の行が推奨していない図面ページ**(0 始まり)。
#: 図形は多いが電気の図面ではない。ここで同じくらい当たるなら、
#: 「個数が合った」はページの中身を見ていないことになる。
OTHER_DRAWING_PAGES: tuple[int, ...] = (32, 33)


def pdf_path_of(args) -> Path:
    return args.pdf


def extract(pdf_path: Path, pages: tuple[int, ...]) -> dict[int, list[int]]:
    """**PDF だけを見る。** ページごとに、出た群の個数の並びを返す。"""
    out: dict[int, list[int]] = {}
    for page_index in pages:
        try:
            scale = extract_scale(pdf_path, page_index)
        except Exception:
            scale = None
        if scale is None:
            # 縮尺が読めないページでは実寸が出せないので、この手法は動かない。
            # **空は「記号が無い」ではなく「読めていない」である。**
            out[page_index] = []
            continue
        clusters = find_repeated_symbols(pdf_path, page_index, scale)
        out[page_index] = [cluster.count for cluster in clusters]
    return out


def expected_counts(golden_path: Path, pages: tuple[int, ...]) -> dict[int, list[int]]:
    """ゴールデンから、記号の行の正解の個数を**採点のためだけに**読む。

    ページ番号は 1 始まりで入っているので、0 始まりに直して突き合わせる。
    """
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    out: dict[int, list[int]] = {page: [] for page in pages}
    for item in payload["expected_items"]:
        if item.get("expected_source_type") != "symbol_count":
            continue
        quantity = item.get("quantity")
        if not isinstance(quantity, (int, float)):
            continue
        recommended = {p - 1 for p in (item.get("recommended_pages") or [])}
        for page in pages:
            if page in recommended:
                out[page].append(int(quantity))
    return out


def hit_rate(found: list[int], wanted: list[int]) -> tuple[int, int]:
    """正解の個数と**ちょうど一致する群**があった行の数を数える。

    同じ個数の群を 2 つの行に使い回さない(多重集合として引き当てる)。
    """
    pool = Counter(found)
    hits = 0
    for value in wanted:
        if pool[value] > 0:
            pool[value] -= 1
            hits += 1
    return hits, len(wanted)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    runs = [extract(args.pdf, TARGET_PAGES) for _ in range(args.runs)]
    stable = all(run == runs[0] for run in runs)
    found = runs[0]
    wanted = expected_counts(args.golden, TARGET_PAGES)

    print(f"対照2 同じページを {args.runs} 回: " + ("同じ" if stable else "**違う(欠陥)**"))
    print()
    print("本測定(抽出は PDF だけを見た。ここから先が採点)")
    total_hits = total_rows = total_clusters = 0
    for page in TARGET_PAGES:
        hits, rows = hit_rate(found[page], wanted[page])
        total_hits += hits
        total_rows += rows
        total_clusters += len(found[page])
        ratio = f"{hits / rows:.3f}" if rows else "—"
        print(
            f"  {page + 1} ページ: 出た群 {len(found[page]):4d} / "
            f"正解の行 {rows:2d} / 的中 {hits:2d}  ({ratio})"
        )
    overall = total_hits / total_rows if total_rows else 0.0
    print(
        f"  合計: 出た群 {total_clusters} / 正解の行 {total_rows} / "
        f"**的中 {total_hits} ({overall:.3f})**"
    )
    print(f"  余りの群: {total_clusters - total_hits}")

    print()
    empty = extract(args.pdf, EMPTY_PAGES)
    for page in EMPTY_PAGES:
        mark = "合格" if not empty[page] else "**不合格**"
        print(f"対照1 図形の少ない {page + 1} ページ: 群 {len(empty[page])} 件  {mark}")

    print()
    # **対照3と対照4は、基準の文書に書いたままでは何も測れないと分かった。**
    # ①記号の行は 26 件とも 22〜26 ページを推奨しているので、ページを入れ替えても
    #   突き合わせる正解の並びが同じになる。
    # ②「正解の個数を並べ替える」は、多重集合として引き当てる採点では
    #   **並べ替えても結果が 1 件も変わらない。**
    # どちらも作り直した。**採否の基準(対照よりはっきり高いこと)は変えていない。**
    for other in OTHER_DRAWING_PAGES:
        found_other = extract(pdf_path_of(args), (other,))[other]
        hits, rows = hit_rate(found_other, wanted[TARGET_PAGES[0]])
        ratio = hits / rows if rows else 0.0
        print(
            f"対照3 記号の行が推奨していない {other + 1} ページ: "
            f"群 {len(found_other)} / 的中 {hits}/{rows} ({ratio:.3f})"
        )
        swap_ratio = max(locals().get("swap_ratio", 0.0), ratio)

    rng = random.Random(0)
    reference = wanted[TARGET_PAGES[0]]
    high = max(reference) if reference else 1
    draws = []
    for _ in range(100):
        fake = [rng.randint(1, high) for _ in reference]
        hits, rows = hit_rate(found[TARGET_PAGES[0]], fake)
        draws.append(hits / rows if rows else 0.0)
    mean = sum(draws) / len(draws)
    print(
        f"対照4 でたらめな正解(1〜{high} の乱数を同じ件数)100回: 平均 {mean:.3f}"
    )

    print()
    print("判定のための読み方:")
    print(f"  本測定 {overall:.3f} / 入れ替え {swap_ratio:.3f} / でたらめ {mean:.3f}")
    if overall >= 0.5 and overall >= 2 * max(swap_ratio, mean):
        print("  → 採用の条件を満たす")
    elif overall > max(swap_ratio, mean):
        print("  → 保留(対照より高いが 0.5 に届かない)")
    else:
        print("  → **不採用(対照と同じか低い。たまたま合っているだけ)**")


if __name__ == "__main__":
    main()
