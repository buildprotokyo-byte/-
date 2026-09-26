"""K-29: 答えが引いた数字が、**そのページに本当に印字されているか**を数える。

なぜこれを数えるのか
--------------------
おーちゃんの K-29 の指示:

> **数字が合っていても、たまたま当たったのか、図面の書き方を理解して選んだのかで、
> 意味がまったく違います。**

正解(見積)に室ごとの床面積が無いことが分かった(`docs/k29_area_expert_reading_criteria.md`
追記 2)ので、**答えの当たり外れでは測れない。**代わりに、**図面自身から取れる正解**で測る
(追記 3 の T1・T2)。

| 記号 | 何を確かめるか |
|---|---|
| **T1 印字の実在** | 引いた数字が、**そのページに本当に印字されている**か(語の完全一致) |
| **T2 式の整合** | 引いた数字だけで、答えの面積が作れるか |

**どちらも図面と答えだけで決まる。**人の正解も見積も要らない。

この道具がしないこと
--------------------
- **正しさを判定しない。**印字されている数字を引いていても、その数字が
  その場所の寸法とはかぎらない。**T1 は「作り話でないこと」しか言わない。**
- **実案件の数字を画面に出さない。**出すのは件数と割合だけ。

実行::

    .venv/bin/python -m benchmarks.measure_k29_cited_numbers --pdf <図面> \\
        --answer 前提なし=<answers.json> --answer 専門家=<answers.json>
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import pymupdf

#: 式が合っていると認める相対誤差。**面積の許容差(±5%)より厳しくする。**
#: ここで見ているのは「引いた数字から答えが作れるか」という算数であって、
#: 読み取りの当たり外れではない。
FORMULA_TOLERANCE = 0.01

#: 引いた数字 2 つの掛け算として答えを作れるか試すときの、最大の組み合わせ数。
_MAX_PAIRS = 400

COLUMNS = (
    "答えの数",
    "数字を引いた答え",
    "引いた数字の総数",
    "T1 印字されていた",
    "T1 印字されていない",
    "T1 全部が印字だった答え",
    "T2 式が作れた答え",
    "根拠が空の答え",
)


def normalize(text: str) -> str:
    """全角・半角と空白のゆれだけを均す。**桁区切りのコンマも落とす。**"""
    body = unicodedata.normalize("NFKC", str(text or ""))
    return "".join(body.split()).replace(",", "")


def page_numbers(pdf_path: str | Path, page_index: int) -> set[str]:
    """そのページに**印字されている**語を、正規化して返す。"""
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            return set()
        page = doc.load_page(page_index)
        words = {normalize(word[4]) for word in page.get_text("words")}
    return {word for word in words if word}


def _value_of(text: str) -> float | None:
    """`3,640` や `≒2,430` のような文字列から数を取り出す。取れなければ None。"""
    match = re.search(r"\d+(?:\.\d+)?", normalize(text))
    return float(match.group()) if match else None


def _printed(cited: str, words: set[str]) -> bool:
    """引いた文字列が、そのページの語として実在するか。

    **語の完全一致**で見る。部分一致を許すと、`1` がどこかにあるだけで
    `1,925` が「印字されていた」ことになってしまう。
    ただし `≒2,430` のように記号が付く書き方は実図面にあるので、
    **数の部分だけを取り出した形**でも照らす。
    """
    key = normalize(cited)
    if key in words:
        return True
    value = _value_of(cited)
    if value is None:
        return False
    stripped = f"{value:g}"
    return stripped in {re.sub(r"[^0-9.]", "", word) for word in words}


def _formula_holds(area_sqm: float | None, values: list[float]) -> bool:
    """引いた数字のどれか 2 つの積で、答えの面積(m^2)が作れるか。

    数字は mm とみなす(建築図面の寸法はほぼ mm。単位が違えば作れない)。
    足し引きで組んだ答えは、ここでは作れない側に数える。**甘く見ない。**
    """
    if area_sqm is None or area_sqm <= 0 or len(values) < 2:
        return False
    pairs = 0
    for index, first in enumerate(values):
        for second in values[index + 1 :]:
            pairs += 1
            if pairs > _MAX_PAIRS:
                return False
            made = first * second / 1_000_000.0
            if made > 0 and abs(made - area_sqm) / area_sqm <= FORMULA_TOLERANCE:
                return True
    return False


def _items(payload: Any) -> list[dict[str, Any]]:
    """答えのファイルを、1 件ずつの並びに直す。

    室ごとに聞いた古い形(`{"R-01": {...}}`)と、場所を自分で選ばせた形
    (`{"items": [...]}`)の**両方**を受ける。
    """
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [value for value in payload.values() if isinstance(value, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def count(items: Iterable[dict[str, Any]], pdf_path: str | Path) -> dict[str, int]:
    counts = {column: 0 for column in COLUMNS}
    cache: dict[int, set[str]] = {}
    for item in items:
        counts["答えの数"] += 1
        cited = item.get("used_numbers") or []
        if not cited:
            # **面積を出していない答え(読めない・質疑)は、根拠が空でも当たり前。**
            # 数えるのは「数字を出したのに根拠が無い」ものだけである。
            if item.get("area_sqm") is not None and not str(item.get("why") or "").strip():
                counts["根拠が空の答え"] += 1
            continue
        counts["数字を引いた答え"] += 1
        values: list[float] = []
        all_printed = True
        for entry in cited:
            counts["引いた数字の総数"] += 1
            text = str(entry.get("text") or "")
            page = entry.get("page")
            try:
                index = int(page) - 1
            except (TypeError, ValueError):
                index = -1
            if index not in cache:
                cache[index] = page_numbers(pdf_path, index) if index >= 0 else set()
            if _printed(text, cache[index]):
                counts["T1 印字されていた"] += 1
            else:
                counts["T1 印字されていない"] += 1
                all_printed = False
            value = _value_of(text)
            if value is not None:
                values.append(value)
        if all_printed:
            counts["T1 全部が印字だった答え"] += 1
        area = item.get("area_sqm")
        if _formula_holds(float(area) if area is not None else None, values):
            counts["T2 式が作れた答え"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answer", action="append", default=[], metavar="名前=パス")
    args = parser.parse_args(argv)

    table: dict[str, dict[str, int]] = {}
    for spec in args.answer:
        name, _, path = spec.partition("=")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        table[name] = count(_items(payload), args.pdf)

    print("| 条件 | " + " | ".join(COLUMNS) + " |")
    print("|---" * (len(COLUMNS) + 1) + "|")
    for name, counts in table.items():
        print(f"| {name} | " + " | ".join(str(counts[c]) for c in COLUMNS) + " |")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
