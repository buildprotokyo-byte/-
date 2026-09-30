"""K-53 周 3: 繋いだ単位での落ちと、線の落ちの種類分けを、読みの組ごとに測る。

使い方:
    python3 -m benchmarks.run_erase_check_k53 PDF 出力.json --page 8 --set 名前=読み1.json,読み2.json,読み3.json [--set ...]

上限は 1%(固定)。組ごとに、3 回それぞれ・その囮・3 回合わせて を出す。
実図面・読み取り結果はリポジトリに入れない(パスで渡す)。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pymupdf

from benchmarks import erase_check as ec
from benchmarks.run_erase_check import make_decoys


def _measure(base, marks, kinds):
    for p, m in zip(base, marks):
        p.marked = m
    parts = ec.summarize(base)
    chained = ec.summarize_chained(base)
    lines = [p for p in base if p.id in kinds]
    by_all = Counter(kinds[p.id] for p in lines)
    by_miss = Counter(kinds[p.id] for p in lines if not p.marked)
    return {
        "部品の単位": {k: parts[k] for k in ("数える図形", "拾えた", "落ちた", "落ちた率", "種類ごと(数える/落ちた)")},
        "繋いだ単位": chained,
        "線の種類(数える/落ちた)": {t: [by_all.get(t, 0), by_miss.get(t, 0)] for t in ec.LINE_TYPES},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--page", type=int, default=8)
    ap.add_argument("--set", action="append", required=True)
    a = ap.parse_args(argv)
    doc = pymupdf.open(a.pdf)
    page = doc[a.page - 1]
    s = ec.WIDTH_PX / page.rect.width
    w, h = page.rect.width * s, page.rect.height * s
    base = ec.extract_primitives(page, a.page)
    ec.mark_exclusions(base, w, h)
    kinds = ec.classify_lines(base)
    out = {"設定": ec.SETTINGS, "繋ぐ設定": ec.CHAIN_SETTINGS, "ページ": a.page, "組": {}}
    for spec in a.set:
        name, paths = spec.split("=", 1)
        reads = {f"R{i + 1}": {p["ページ"]: p.get("要素", []) for p in json.load(open(f))["ページ"]}
                 for i, f in enumerate(paths.split(","))}
        reads.update(make_decoys(reads, doc))
        res = {}
        union = [False] * len(base)
        for n, r in reads.items():
            for p in base:
                p.marked = False
            ec.mark_read(base, r.get(a.page, []), w * h)
            marks = [p.marked for p in base]
            if not n.startswith("囮_"):
                union = [u or m for u, m in zip(union, marks)]
            res[n] = _measure(base, marks, kinds)
        res["合わせて"] = _measure(base, union, kinds)
        out["組"][name] = res
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    for name, res in out["組"].items():
        for n, r in res.items():
            pc = r["部品の単位"]["種類ごと(数える/落ちた)"].get("点・小さい図形", [0, 0])
            cc = r["繋いだ単位"]["種類ごと(数える/落ちた)"].get("点・小さい図形", [0, 0])
            print(name, n, "部品", r["部品の単位"]["落ちた"], "点", pc, "繋ぐ", r["繋いだ単位"]["落ちた"], "点", cc,
                  "繋ぐと線に", r["繋いだ単位"]["繋ぐと8画素以上の線の一部になる点・小さい図形の部品"],
                  "うち落ち", r["繋いだ単位"]["そのうち部品の単位で落ちていたもの"])


if __name__ == "__main__":
    main()
