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
                "dropped": page.dropped,
                "title_conflicts": [c.kind for c in page.title_conflicts],
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


def one_run(pdf: str, legacy_caps: bool = False) -> None:
    """子プロセスとして 1 回処理し、要約を標準出力に JSON で出す。

    ``legacy_caps`` は K-23 の上限(線 1,800・閉領域 300・小輪郭 600)で処理する。
    上限を上げたときに時間と大きさがどう変わるかを比べるため。
    """
    settings = LEGACY_CAPS if legacy_caps else LedgerSettings()
    started = time.perf_counter()
    ledger = build_ledger(pdf, settings=settings)
    seconds = time.perf_counter() - started
    size = len(
        json.dumps([p.to_dict() for p in ledger.pages], ensure_ascii=False).encode("utf-8")
    )
    print(json.dumps({"seconds": seconds, "json_bytes": size, "pages": summarize(ledger)}))


LEGACY_CAPS = replace(LedgerSettings(), max_lines=1800, max_regions=300, max_small=600)


def run_in_subprocess(pdf: str, legacy_caps: bool = False) -> dict:
    result = subprocess.run(
        [sys.executable, __file__, "--one-run", pdf] + (["--legacy-caps"] if legacy_caps else []),
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
    legacy = run_in_subprocess(pdf, legacy_caps=True)
    return {
        "file": label,
        "runs": runs,
        "seconds": [round(r["seconds"], 1) for r in results],
        "json_bytes": [r["json_bytes"] for r in results],
        "legacy_caps": {
            "seconds": round(legacy["seconds"], 1),
            "json_bytes": legacy["json_bytes"],
            "cap_hit_pages": sum(
                1 for p in legacy["pages"] if any(c["cap_hit"] for c in p["counts"].values())
            ),
        },
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
    dropped = Counter()
    for p in pages:
        dropped.update(p["dropped"])
    conflicts = Counter(k for p in pages for k in p["title_conflicts"])
    return {
        "dropped": dict(dropped),
        "title_conflicts": dict(conflicts),
        "title_conflict_pages": {
            k: [p["page"] for p in pages if k in p["title_conflicts"]] for k in conflicts
        },
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-run")
    parser.add_argument("--legacy-caps", action="store_true")
    parser.add_argument("--v2")
    parser.add_argument("--v1")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.one_run:
        one_run(args.one_run, legacy_caps=args.legacy_caps)
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
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
