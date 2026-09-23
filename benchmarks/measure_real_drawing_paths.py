"""35周目の測定(トライアルB): 実図面に、main にある経路を全部当てる。

基準は `docs/b_real_drawing_paths_criteria.md`(測る前にコミット済み)。

**出すのは件数とページ番号だけ。** 室名・寸法・数量・事務所名は 1 文字も印字しない。
匿名化 v2 には塗りつぶされて見えない文字データが残っている。

実行::

    .venv/bin/python -m benchmarks.measure_real_drawing_paths --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
import hashlib
from pathlib import Path

import fitz

from arbitration.item_keys import SCOPE_CONFIRM, SCOPE_TO_HUMAN, normalise_item_key
from axes.image_axis.pdf_vector_symbols import (
    extract_scale,
    find_area_labels,
    find_door_arcs,
)
from axes.image_axis.schedule_tables import read_door_schedules, read_finish_schedules


def fingerprint(pdf: Path) -> str:
    """そのデータ源の指紋。**同じ PDF なら同じ指紋。**"""
    return hashlib.sha256(pdf.read_bytes()).hexdigest()[:12]


def run_paths(pdf: Path, page_count: int) -> dict[str, dict]:
    """経路ごとに「鍵の一覧」と「取れたページ」を返す。**値は出さない。**"""
    out: dict[str, dict] = {}

    keys: list[str] = []
    pages: list[int] = []
    for i in range(page_count):
        rows = [r for s in read_door_schedules(pdf, i) for r in s.rows]
        if rows:
            pages.append(i + 1)
        keys += [f"建具::{r.mark}" for r in rows if getattr(r, "mark", None)]
    out["P1 建具表"] = {"keys": keys, "pages": pages}

    keys, pages = [], []
    for i in range(page_count):
        rows = [r for s in read_finish_schedules(pdf, i) for r in s.rows]
        if rows:
            pages.append(i + 1)
        keys += [f"仕上::{r.part}" for r in rows if r.part]
    out["P2 内装仕上表"] = {"keys": keys, "pages": pages}

    keys, pages = [], []
    scales: dict[int, object] = {}
    for i in range(page_count):
        scale = extract_scale(pdf, i)
        if scale is not None:
            scales[i] = scale
            pages.append(i + 1)
    out["P5 縮尺の印字"] = {"keys": [f"縮尺::{i + 1}" for i in scales], "pages": pages}

    keys, pages = [], []
    for i, scale in scales.items():
        arcs = find_door_arcs(pdf, i, scale)
        if arcs:
            pages.append(i + 1)
        keys += [f"建具::開き戸{n}" for n in range(len(arcs))]
    out["P3 開き戸の円弧"] = {"keys": keys, "pages": pages,
                          "note": "縮尺が読めたページだけ(縮尺が要るため)"}

    keys, pages = [], []
    for i in range(page_count):
        labels = find_area_labels(pdf, i)
        if labels:
            pages.append(i + 1)
        keys += [f"面積::{a.label}" for a in labels]
    out["P4 面積の記載"] = {"keys": keys, "pages": pages}

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    print(f"図面: {page_count} ページ / 指紋 {fingerprint(args.pdf)}")

    paths = run_paths(args.pdf, page_count)

    print("\n=== D1 経路ごとの出力 ===")
    for label in sorted(paths):
        info = paths[label]
        note = f" / {info['note']}" if "note" in info else ""
        print(f"   {label}: {len(info['keys'])} 件 / 取れたページ {info['pages']}{note}")

    broken = [label for label, info in paths.items() if not info["keys"]]
    print(f"\nD2 折れている経路(0 件しか出ない): {len(broken)} 本 {broken}")

    print("\n=== D3 鍵の重なり ===")
    for scope, name in ((SCOPE_CONFIRM, "確定(狭い)"), (SCOPE_TO_HUMAN, "人へ(広い)")):
        buckets: dict[str, set[str]] = collections.defaultdict(set)
        for label, info in paths.items():
            for key in info["keys"]:
                buckets[normalise_item_key(key, scope=scope)].add(label)
        shared = {k: v for k, v in buckets.items() if len(v) >= 2}
        print(f"   {name}: 鍵 {len(buckets)} 個 / **2 本以上の経路に出る鍵 {len(shared)} 個**")

    print("\nD4 独立したデータ源")
    prints = {label: fingerprint(args.pdf) for label in paths if paths[label]["keys"]}
    print(f"   動いている経路 {len(prints)} 本 / 異なる指紋 {len(set(prints.values()))} 個")
    print("   → 指紋が 1 個なら、経路がいくつ一致しても独立した証言は 1 つ")

    only_one = 0
    buckets = collections.defaultdict(set)
    for label, info in paths.items():
        for key in info["keys"]:
            buckets[normalise_item_key(key, scope=SCOPE_CONFIRM)].add(label)
    only_one = sum(1 for v in buckets.values() if len(v) == 1)
    print(f"\nD5 1 本の経路にしか出てこない鍵: {only_one} / {len(buckets)}")

    print("\n=== 対照 ===")
    same = {k: v for k, v in buckets.items()}
    doubled: dict[str, set[str]] = collections.defaultdict(set)
    for label, info in paths.items():
        for key in info["keys"]:
            n = normalise_item_key(key, scope=SCOPE_CONFIRM)
            doubled[n].add(label)
            doubled[n].add(label + "(写し)")
    agreed = sum(1 for v in doubled.values() if len(v) >= 2)
    print(f"対照1 同じ PDF を 2 回読む: 全 {len(doubled)} 鍵のうち一致 {agreed}"
          f" / 独立したデータ源 {len({fingerprint(args.pdf)})}")

    changed = collections.defaultdict(set)
    for label, info in paths.items():
        for key in info["keys"]:
            changed[normalise_item_key(key, scope=SCOPE_CONFIRM)].add(label)
            changed[normalise_item_key(key + "ちがい", scope=SCOPE_CONFIRM)].add(label + "(改)")
    differing = sum(1 for k, v in changed.items() if "(改)" in "".join(v))
    print(f"対照2 わざと違う鍵にした写し: 別の鍵として {differing} 件出る(食い違いに回る)")

    repeats = [len(run_paths(args.pdf, page_count)["P2 内装仕上表"]["keys"]) for _ in range(3)]
    print(f"対照3 反復 3 回(P2 の件数): {repeats}")


if __name__ == "__main__":
    main()
