"""周10「落ちた字を、決まり文句の語彙表で直す」。

基準は `docs/loop_round10_heading_repair_criteria.md`(**測る前にコミット済み**。
語彙表の指紋もそこに書いてある)。

**読み方は変えない。**周6・周8・周9 が読んだ結果に、**直しを 1 段足すだけ**である。

**引き当ては当てずっぽうを本物らしく見せる。**だから**囮にも同じ直しを掛け、
囮の 3 文字以上が増えていないかを必ず数える**(基準の線3)。

**語彙表も正解も実案件の紙から拾ったものなのでリポジトリに置かない。**引数で渡す。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from benchmarks.score_maker_drawing_reading import NEAR_MIN_DICE, dice, fold

#: 見出しとして直す先頭の部分。**数字・記号・英字は含めない**(値に触らないため)。
HEADING_HEAD = re.compile(r"^[^\W\da-zA-Z_]+")

#: 直す先頭の部分の最短の長さ。1 文字では何にでも近くなる。
MIN_HEAD_LENGTH = 2


def repair(text: str, vocabulary: list[str]) -> tuple[str, str | None]:
    """文字列の**先頭の見出しの部分だけ**を語彙表で直す。

    戻りは (直した文字列, 当てた語 または None)。**数値・単位・型番には触らない。**
    """
    match = HEADING_HEAD.match(text)
    if not match:
        return text, None
    head = match.group(0)
    if len(head) < MIN_HEAD_LENGTH:
        return text, None
    best = None
    best_score = 0.0
    for word in vocabulary:
        score = dice(fold(head), fold(word))
        if score > best_score:
            best, best_score = word, score
    if best is None or best_score < NEAR_MIN_DICE or best == head:
        return text, None
    return best + text[len(head):], best


def _count_long(rows: list[list[Any]]) -> int:
    return sum(1 for row in rows if len(str(row[-2])) >= 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--texts", required=True, help="読めた文字(共有フォルダ)")
    parser.add_argument("--vocabulary", required=True, help="語彙表(共有フォルダ)")
    parser.add_argument("--out", required=True, type=Path, help="直した結果の書き出し先")
    parser.add_argument("--pairs", type=Path, help="置き換わった前後の組の書き出し先")
    args = parser.parse_args(argv)

    vocabulary = json.loads(Path(args.vocabulary).read_text(encoding="utf-8"))["見出し"]
    pages = json.loads(Path(args.texts).read_text(encoding="utf-8"))

    pairs: list[dict[str, Any]] = []
    summary = {"本物_直した件数": 0, "囮_直した件数": 0}
    before = {"本物_3文字以上": 0, "囮_3文字以上": 0}
    after = {"本物_3文字以上": 0, "囮_3文字以上": 0}

    for page in pages:
        for key, label in (("_本物", "本物"), ("_囮", "囮"), ("_囮_位置", "囮")):
            rows = page.get(key)
            if not rows:
                continue
            before[f"{label}_3文字以上"] += _count_long(rows)
            for row in rows:
                fixed, word = repair(str(row[-2]), vocabulary)
                if word is not None:
                    pairs.append(
                        {
                            "ページ": page["ページ番号"],
                            "どちら": label,
                            "前": row[-2],
                            "後": fixed,
                            "当てた語": word,
                        }
                    )
                    summary[f"{label}_直した件数"] += 1
                    row[-2] = fixed
            after[f"{label}_3文字以上"] += _count_long(rows)

    args.out.write_text(
        json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.pairs:
        args.pairs.write_text(
            json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(
        json.dumps(
            {
                **summary,
                "直す前": before,
                "直した後": after,
                "囮の3文字以上が増えた数": after["囮_3文字以上"] - before["囮_3文字以上"],
                "本物の3文字以上が増えた数": after["本物_3文字以上"] - before["本物_3文字以上"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
