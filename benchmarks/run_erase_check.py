"""K-51 消して確かめるを、PDF 1 冊と読み取り結果(1 つ以上)に掛ける。

使い方:
    python3 -m benchmarks.run_erase_check PDF 出力.json 読み1.json [読み2.json ...] [--pages 8] [--caps 0.01,0.05,0.2]

読み取り結果は {"ページ":[{"ページ":N,"要素":[{"種類","位置":[x0,y0,x1,y1]}]}]}(幅 2000 画素の座標)。
読みごとの結果と、全部の読みを合わせた結果(どれか 1 つの読みで印が付けば拾えた)を出す。
実図面・読み取り結果はリポジトリに入れない(パスで渡す)。
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pymupdf

from benchmarks import erase_check as ec


def _add(total: dict, s: dict) -> None:
    for k in ("図形の総数", "数える図形", "拾えた", "落ちた"):
        total[k] = total.get(k, 0) + s[k]
    for k, v in s["除外"].items():
        total.setdefault("除外", {})[k] = total.get("除外", {}).get(k, 0) + v
    for k, (a, b) in s["種類ごと(数える/落ちた)"].items():
        cur = total.setdefault("種類ごと(数える/落ちた)", {}).setdefault(k, [0, 0])
        cur[0] += a
        cur[1] += b
    for k, v in s["落ちた図形の大きさ(画素、長い辺)"].items():
        total.setdefault("落ちた図形の大きさ(画素、長い辺)", {})[k] = total.get("落ちた図形の大きさ(画素、長い辺)", {}).get(k, 0) + v


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("reads", nargs="+")
    ap.add_argument("--pages", default="")
    ap.add_argument("--caps", default="0.01,0.05,0.2")
    ap.add_argument("--decoy", action="store_true",
                    help="囮: 各読みの要素の位置を、同じ大きさのままページ内のでたらめな場所に置き直したものも測る")
    a = ap.parse_args(argv)
    doc = pymupdf.open(a.pdf)
    pages = [int(x) for x in a.pages.split(",")] if a.pages else list(range(1, len(doc) + 1))
    reads = {Path(r).stem: {p["ページ"]: p.get("要素", []) for p in json.load(open(r))["ページ"]} for r in a.reads}
    if a.decoy:
        rng = random.Random(51)
        for n in list(reads):
            fake = {}
            for pno, els in reads[n].items():
                page = doc[pno - 1]
                s = ec.WIDTH_PX / page.rect.width
                w, h = page.rect.width * s, page.rect.height * s
                moved = []
                for e in els:
                    pos = e.get("位置")
                    if not pos or len(pos) != 4:
                        continue
                    bw, bh = abs(pos[2] - pos[0]), abs(pos[3] - pos[1])
                    x, y = rng.uniform(0, max(w - bw, 0)), rng.uniform(0, max(h - bh, 0))
                    moved.append({"種類": e.get("種類"), "位置": [x, y, x + bw, y + bh]})
                fake[pno] = moved
            reads["囮_" + n] = fake
    real = [n for n in reads if not n.startswith("囮_")]
    names = list(reads) + (["合わせて"] if len(real) > 1 else [])
    out = {"設定": ec.SETTINGS, "読み": list(reads), "上限ごと": {}}
    for cap in [float(c) for c in a.caps.split(",")]:
        res = {n: {"ページごと": {}, "全体": {}} for n in names}
        for pno in pages:
            page = doc[pno - 1]
            s = ec.WIDTH_PX / page.rect.width
            w, h = page.rect.width * s, page.rect.height * s
            base = ec.extract_primitives(page, pno)
            ec.mark_exclusions(base, w, h)
            union = [False] * len(base)
            for n in reads:
                for p in base:
                    p.marked = False
                ec.mark_read(base, reads[n].get(pno, []), w * h, cap)
                if n in real:
                    union = [u or p.marked for u, p in zip(union, base)]
                summ = ec.summarize(base)
                res[n]["ページごと"][pno] = summ
                _add(res[n]["全体"], summ)
            if "合わせて" in res:
                for p, u in zip(base, union):
                    p.marked = u
                summ = ec.summarize(base)
                res["合わせて"]["ページごと"][pno] = summ
                _add(res["合わせて"]["全体"], summ)
        for n in names:
            t = res[n]["全体"]
            t["落ちた率"] = round(t["落ちた"] / t["数える図形"], 4) if t.get("数える図形") else None
        out["上限ごと"][str(cap)] = res
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    for cap, res in out["上限ごと"].items():
        print(cap, {n: (r["全体"]["落ちた"], r["全体"]["落ちた率"]) for n, r in res.items()})


if __name__ == "__main__":
    main()
