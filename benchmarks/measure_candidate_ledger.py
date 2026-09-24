"""K-23: 候補台帳を実図面で測る。

基準は `docs/k23_candidate_ledger_criteria.md`(測る前にコミット済み)。

使い方::

    .venv/bin/python benchmarks/measure_candidate_ledger.py \\
        --v2 <P011 匿名化v2 の PDF> --v1 <P011 v1(スキャン)の PDF> --out <結果の JSON>

**出すのは件数・真偽・ページ番号・指紋だけ。図面の文字は 1 文字も出さない。**
表題欄から読んだ名称・図番は「取れたか」の真偽にしてから書き出す。

3 回の処理は**別々のプロセス**で走らせる(同じプロセスの中だけで揃っていても、
プロセスをまたいで揺れる非決定性は見えないため)。キャッシュは使わない。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from axes.image_axis.candidate_ledger import (  # noqa: E402
    LedgerCache,
    LedgerSettings,
    build_ledger,
)


def summarize(ledger) -> list[dict]:
    """ページごとの件数と真偽だけにする。文字は落とす。"""
    out = []
    for page in ledger.pages:
        title = page.title
        out.append(
            {
                "page": page.page_index + 1,
                "source": page.source,
                "rotation": page.rotation,
                "has_text": page.has_text,
                "counts": {
                    kind: {
                        "found": c.found,
                        "kept": c.kept,
                        "limit": c.limit,
                        "cap_hit": c.cap_hit,
                    }
                    for kind, c in page.counts.items()
                },
                "dropped_specks": page.dropped_specks,
                "duplicate_lines": page.duplicate_lines,
                "gate": page.gate.status,
                "reasons": list(page.gate.reasons),
                "error": page.error is not None,
                "title": {
                    "read": bool(title and title.read),
                    "name": bool(title and title.drawing_name),
                    "number": bool(title and title.drawing_number),
                    "scale_denominator": title.scale_denominator if title else None,
                    "scale_stated_none": bool(title and title.scale_stated_none),
                    "revision_date": bool(title and title.revision_date),
                },
                "digest": page.content_digest(),
                "from_cache": page.from_cache,
            }
        )
    return out


def one_run(pdf: str) -> None:
    """子プロセスとして 1 回処理し、要約を標準出力に JSON で出す。"""
    started = time.perf_counter()
    ledger = build_ledger(pdf)
    print(json.dumps({"seconds": time.perf_counter() - started, "pages": summarize(ledger)}))


def run_in_subprocess(pdf: str) -> dict:
    result = subprocess.run(
        [sys.executable, __file__, "--one-run", pdf],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def stability(pdf: str, label: str, runs: int = 3) -> dict:
    results = [run_in_subprocess(pdf) for _ in range(runs)]
    pages = results[0]["pages"]
    mismatched = []
    for index in range(len(pages)):
        views = [
            json.dumps({k: r["pages"][index][k] for k in ("counts", "gate", "reasons", "title", "digest")}, sort_keys=True)
            for r in results
        ]
        if len(set(views)) != 1:
            mismatched.append(pages[index]["page"])
    return {
        "file": label,
        "runs": runs,
        "seconds": [round(r["seconds"], 1) for r in results],
        "pages": pages,
        "mismatched_pages": mismatched,
    }


def aggregate(pages: list[dict]) -> dict:
    gate = Counter(p["gate"] for p in pages)
    reasons = Counter(r for p in pages for r in p["reasons"])
    caps = {
        kind: [p["page"] for p in pages if p["counts"][kind]["cap_hit"]]
        for kind in ("lines", "regions", "small")
    }
    totals = {
        kind: {
            "found": sum(p["counts"][kind]["found"] for p in pages),
            "kept": sum(p["counts"][kind]["kept"] for p in pages),
        }
        for kind in ("lines", "regions", "small")
    }
    title = {
        key: sum(1 for p in pages if p["title"][key])
        for key in ("read", "name", "number", "revision_date", "scale_stated_none")
    }
    title["scale"] = sum(1 for p in pages if p["title"]["scale_denominator"] is not None)
    distinct = {
        kind: len({p["counts"][kind]["found"] for p in pages}) for kind in ("lines", "regions", "small")
    }
    return {
        "gate": dict(gate),
        "reasons": dict(reasons),
        "cap_hit_pages": caps,
        "totals": totals,
        "title_found_pages": title,
        "distinct_values_across_pages": distinct,
        "sources": dict(Counter(p["source"] for p in pages)),
    }


def cache_check(pdf: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cache = LedgerCache(tmp)
        first = build_ledger(pdf, cache=cache)
        second = build_ledger(pdf, cache=cache)
        changed = build_ledger(pdf, cache=cache, settings=replace(LedgerSettings(), canny_low=40))
    fresh = build_ledger(pdf)
    return {
        "first_run_hits": sum(p.from_cache for p in first.pages),
        "second_run_hits": sum(p.from_cache for p in second.pages),
        "pages": len(second.pages),
        "second_equals_fresh": [p.content_digest() for p in second.pages]
        == [p.content_digest() for p in fresh.pages],
        "changed_setting_hits": sum(p.from_cache for p in changed.pages),
        "changed_setting_keys_differ": all(
            a.cache_key != b.cache_key for a, b in zip(first.pages, changed.pages)
        ),
    }


def old_path(pdf: str, runs: int = 3) -> list[dict]:
    """いまの経路(繰り返す図形の群)をページごとに。縮尺が読めなければ動かない。"""
    from axes.image_axis.pdf_repeated_symbols import find_repeated_symbols
    from axes.image_axis.pdf_vector_symbols import extract_scale

    import pymupdf

    with pymupdf.open(pdf) as doc:
        count = doc.page_count
    out = []
    for index in range(count):
        scale = extract_scale(pdf, index)
        if scale is None:
            out.append({"page": index + 1, "scale_read": False, "groups": [0] * runs})
            continue
        groups = [len(find_repeated_symbols(pdf, index, scale)) for _ in range(runs)]
        out.append({"page": index + 1, "scale_read": True, "groups": groups})
    return out


def drawing_list_check(pdf: str, list_page: int) -> list[dict]:
    """表題欄から読んだ名称・図番が、図面リストのページの文字の中にあるか(真偽だけ)。

    **正しさの測定ではない。** 図面リストも同じ PDF の中の文字なので、
    両方が同じように間違っていれば一致する。表記のゆれ(「・」の有無など)でも外れる。
    """
    import re
    import unicodedata

    import pymupdf

    def norm(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))

    def loose(text: str) -> str:
        return re.sub(r"[・、,()（）\-ー‐－]", "", norm(text))

    with pymupdf.open(pdf) as doc:
        listing = doc.load_page(list_page - 1).get_text("text")
    strict_all, loose_all = norm(listing), loose(listing)
    out = []
    for page in build_ledger(pdf).pages:
        title = page.title
        if title is None or not (title.drawing_name or title.drawing_number):
            continue
        out.append(
            {
                "page": page.page_index + 1,
                "name": bool(title.drawing_name),
                "name_in_list": bool(title.drawing_name) and norm(title.drawing_name) in strict_all,
                "name_in_list_loose": bool(title.drawing_name)
                and loose(title.drawing_name) in loose_all,
                "number": bool(title.drawing_number),
                "number_in_list": bool(title.drawing_number)
                and norm(title.drawing_number) in strict_all,
                "number_in_list_loose": bool(title.drawing_number)
                and loose(title.drawing_number) in loose_all,
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-run")
    parser.add_argument("--v2")
    parser.add_argument("--v1")
    parser.add_argument("--out")
    parser.add_argument("--list-page", type=int, help="v2 の図面リストのページ(1 始まり)")
    args = parser.parse_args()
    if args.one_run:
        one_run(args.one_run)
        return

    report: dict = {}
    for label, pdf in (("P011 匿名化v2", args.v2), ("P011 v1(スキャン)", args.v1)):
        if not pdf:
            continue
        stab = stability(pdf, label)
        stab["aggregate"] = aggregate(stab["pages"])
        stab["cache"] = cache_check(pdf)
        report[label] = stab
        print(label, "食い違ったページ", stab["mismatched_pages"], stab["aggregate"]["gate"], file=sys.stderr)
    if args.v2:
        report["old_path_v2"] = old_path(args.v2)
        if args.list_page:
            report["drawing_list_check_v2"] = drawing_list_check(args.v2, args.list_page)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
