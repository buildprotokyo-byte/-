"""K-30 読む順番: 読み手の答えを**図面自身**と突き合わせる。

基準は `docs/k30_reading_order_criteria.md`(**測る前にコミット済み**)。
構えは `docs/reading_stance.md`(0 節が一番上)。

**この道具がすること**

| 線 | 何を確かめるか |
|---|---|
| **甲 縮尺** | 読み手の縮尺が、**表題部に印字された縮尺**と ±5% で合うか |
| **乙 印字の実在** | 根拠に挙げた文字列が、**そのページに本当に印字されている**か |
| **丙 読めないページ** | 図形が 1 つも無いページで、読めないと答えられたか |

**採点しないもの**(基準に先に書いた):
`designer_intent`・`inference`・`knowledge_used`・`drawing_kind`。
**図面自身から正解が取れないので、数えられるふりをしない。**

**この道具がしないこと**

- **本番の経路を一切通らない。**
- **図面の文字を 1 文字も出さない。**出すのは件数と割合だけ。

実行::

    .venv/bin/python -m benchmarks.measure_k30_reading_order \\
        --pdf <図面> --answer <answers.json>
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from axes.image_axis.candidate_ledger import (  # noqa: E402
    LedgerSettings,
    _page_words,
    read_title_block,
)

#: 縮尺が合っていると認める幅。**既存の許容差。緩めない。**
SCALE_TOLERANCE = 0.05

#: 続きの語を繋いで照らすときの、繋ぐ語数の上限。
#: **これは基準に無い緩めた数え方なので、別の欄に出す。**
_MAX_RUN = 8


def normalize(text: str) -> str:
    """全角・半角と空白のゆれだけを均す。**桁区切りのコンマも落とす。**"""
    body = unicodedata.normalize("NFKC", str(text or ""))
    return "".join(body.split()).replace(",", "")


def page_words(page: pymupdf.Page) -> tuple[set[str], list[str]]:
    """そのページに印字されている語と、読み順に並べた語を返す。"""
    raw = page.get_text("words")
    ordered = [normalize(word[4]) for word in sorted(raw, key=lambda w: (round(w[1], 1), w[0]))]
    ordered = [word for word in ordered if word]
    return set(ordered), ordered


def printed_exact(text: str, words: set[str]) -> bool:
    """**語の完全一致**(基準に書いた数え方。K-29 の T1 と同じ)。"""
    key = normalize(text)
    return bool(key) and key in words


def printed_run(text: str, ordered: list[str]) -> bool:
    """続きの語を繋いだ形でも照らす。**基準に無い緩めた数え方。**

    日本語の見出しは語に割れて取れることがあるので、
    **緩めるとどれだけ増えるか**を別に出すために用意している。
    **こちらの数字を採否の線には使わない。**
    """
    key = normalize(text)
    if not key:
        return False
    for start in range(len(ordered)):
        joined = ""
        for word in ordered[start : start + _MAX_RUN]:
            joined += word
            if joined == key:
                return True
            if len(joined) > len(key):
                break
    return False


def _evidence(item: dict[str, Any]) -> list[str]:
    """基準で採点すると決めた 3 つの欄の文字列を集める。"""
    out: list[str] = []
    out.extend(str(text) for text in item.get("kind_evidence") or [])
    out.extend(str(text) for text in item.get("scale_evidence") or [])
    for found in item.get("expression_evidence") or []:
        if isinstance(found, dict):
            out.extend(str(text) for text in found.get("printed") or [])
    return [text for text in out if str(text).strip()]


def measure(pdf_path: str | Path, answers: dict[str, Any]) -> dict:
    items = answers.get("items") if isinstance(answers, dict) else answers
    rows: list[dict] = []
    with pymupdf.open(pdf_path) as doc:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            index = int(item.get("page", 0)) - 1
            if not 0 <= index < doc.page_count:
                continue
            page = doc.load_page(index)
            words, ordered = page_words(page)
            printed = read_title_block(_page_words(page), LedgerSettings())
            said = item.get("scale_denominator")
            row: dict = {
                "page": index + 1,
                "縮尺を答えた": said is not None,
                "表題部に縮尺が印字されている": printed.scale_denominator is not None,
                "縮尺が合った": None,
                "根拠の数": 0,
                "印字されていた(完全一致)": 0,
                "印字されていた(語を繋いだ形)": 0,
                "読めないと答えた": bool(str(item.get("unreadable_reason") or "").strip()),
                "図形がある": bool(page.get_drawings()),
            }
            if said is not None and printed.scale_denominator:
                gap = abs(float(said) - printed.scale_denominator) / printed.scale_denominator
                row["縮尺が合った"] = gap <= SCALE_TOLERANCE
            for text in _evidence(item):
                row["根拠の数"] += 1
                if printed_exact(text, words):
                    row["印字されていた(完全一致)"] += 1
                    row["印字されていた(語を繋いだ形)"] += 1
                elif printed_run(text, ordered):
                    row["印字されていた(語を繋いだ形)"] += 1
            rows.append(row)
    return {"pages": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    checkable = [row for row in rows if row["縮尺が合った"] is not None]
    matched = [row for row in checkable if row["縮尺が合った"]]
    total = sum(row["根拠の数"] for row in rows)
    exact = sum(row["印字されていた(完全一致)"] for row in rows)
    run = sum(row["印字されていた(語を繋いだ形)"] for row in rows)
    blank = [row for row in rows if not row["図形がある"]]
    return {
        "答えたページ": len(rows),
        "縮尺を答えたページ": sum(1 for row in rows if row["縮尺を答えた"]),
        "縮尺を突き合わせられたページ": len(checkable),
        "縮尺が合ったページ": len(matched),
        "線甲(突き合わせられたページのうちの割合)": (
            round(len(matched) / len(checkable), 4) if checkable else None
        ),
        "根拠の数": total,
        "印字されていた(完全一致)": exact,
        "線乙(完全一致の割合)": round(exact / total, 4) if total else None,
        "印字されていた(語を繋いだ形)": run,
        "参考(語を繋いだ形の割合。**基準に無い緩めた数え方**)": (
            round(run / total, 4) if total else None
        ),
        "図形が無いページ": len(blank),
        "線丙(図形が無いページで読めないと答え、縮尺を埋めなかった)": (
            all(row["読めないと答えた"] and not row["縮尺を答えた"] for row in blank)
            if blank
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answer", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    answers = json.loads(args.answer.read_text(encoding="utf-8"))
    result = measure(args.pdf, answers)
    for row in result["pages"]:
        print(
            f"p{row['page']:2d} 縮尺={row['縮尺を答えた']} 合った={row['縮尺が合った']} "
            f"根拠={row['根拠の数']:3d} 完全一致={row['印字されていた(完全一致)']:3d} "
            f"繋いだ形={row['印字されていた(語を繋いだ形)']:3d} "
            f"読めない={row['読めないと答えた']} 図形={row['図形がある']}"
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
