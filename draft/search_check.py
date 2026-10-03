"""K-67 4 節 段階 1: **「どこに書いてあるか」検索の正答率。**文字の層から自動で 100 問作る。

問いの作り方(測る前に決めた):

1. ページの文字の層から語を取る。**1 ページにしか出ない語**だけを使う(答えが 1 つに決まるため)。
2. 2 文字以上。数字だけの語は使わない(どのページにもあるため)。
3. 種を固定して 100 問選ぶ(同じ種なら毎回同じ 100 問。回をまたいで比べられる)。

正答の決め方: **台帳(読みの要素)の、その語を含む要素が、そのページにあるか。**
AI に「同じか」を判定させない。文字の突き合わせだけ(K-66 の部品は行の判定用で、ここは別)。

**これは「読めた」の検査であって「理解した」の検査ではない。**
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

SEED = 67
COUNT = 100
MIN_LENGTH = 2
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _usable(word: str) -> bool:
    word = word.strip()
    return len(word) >= MIN_LENGTH and bool(_HAS_LETTER.search(word))


def build_questions(pdf: Path, pages: Sequence[int], *, count: int = COUNT, seed: int = SEED) -> list[dict[str, Any]]:
    """1 ページにしか出ない語を 100 問ぶん選ぶ。"""
    import pymupdf

    where: dict[str, set[int]] = {}
    boxes: dict[tuple[str, int], list[float]] = {}
    with pymupdf.open(pdf) as doc:
        for number in pages:
            if number - 1 >= doc.page_count:
                continue
            page = doc.load_page(number - 1)
            for w in page.get_text("words"):
                word = w[4].strip()
                if not _usable(word):
                    continue
                where.setdefault(word, set()).add(number)
                boxes.setdefault((word, number), [round(v, 1) for v in w[:4]])
    unique = sorted(word for word, numbers in where.items() if len(numbers) == 1)
    rng = random.Random(seed)
    rng.shuffle(unique)
    chosen = unique[:count]
    return [
        {"番号": index, "問い": f"この語はどのページにありますか: {word}",
         "語": word, "答えのページ": next(iter(where[word]))}
        for index, word in enumerate(sorted(chosen), 1)
    ]


def score(questions: Sequence[Mapping[str, Any]], reading: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """台帳がその語をそのページで持っているか。**持っていなければ不正答。**"""
    by_page: dict[int, str] = {}
    for number, entry in reading.items():
        texts = [str(e.get("内容") or "") for e in (entry.get("要素") or [])]
        texts.append(str(entry.get("描かれているもの") or ""))
        by_page[int(number)] = " ".join(texts)

    right: list[dict[str, Any]] = []
    wrong: list[dict[str, Any]] = []
    for question in questions:
        page = question["答えのページ"]
        word = question["語"]
        if word in by_page.get(page, ""):
            right.append({"番号": question["番号"], "ページ": page})
        else:
            # どのページなら当たったか(**別のページで当たっていたら「場所を間違えた」**)
            elsewhere = sorted(n for n, text in by_page.items() if word in text)
            wrong.append({
                "番号": question["番号"], "答えのページ": page,
                "台帳で見つかったページ": elsewhere,
                "外した型": "台帳に無い" if not elsewhere else "別のページに入れた",
                "語の長さ": len(word),
                "数字を含む": any(c.isdigit() for c in word),
            })
    total = len(questions)
    kinds: dict[str, int] = {}
    for row in wrong:
        kinds[row["外した型"]] = kinds.get(row["外した型"], 0) + 1
    return {
        "問いの数": total,
        "正答": len(right),
        "正答率": round(len(right) / total, 4) if total else None,
        "外した型": kinds,
        "外した問いの語の長さの平均": round(sum(r["語の長さ"] for r in wrong) / len(wrong), 2) if wrong else None,
        "外した問いのうち数字を含む": sum(1 for r in wrong if r["数字を含む"]),
        "外した問い": wrong[:40],
    }
