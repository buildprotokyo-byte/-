"""周6〜周8 の読み取りを、**同じ物差しで**採点する。

基準は `docs/loop_round6_maker_drawing_criteria.md` ほか各周の基準。
**正解は実図面由来なのでリポジトリに置かない。**`--answers` で共有フォルダから渡す。

数える区分は 3 つ。**判定に使うのは「そのまま一致」だけ**(各周の基準)。

1. **届いた**: その場所から何かしら文字が返ったか(**1 段目、そこに何かがあると分かる**)
2. **そのまま一致**: 空白を除いて正解と同じ文字列になったか(**2 段目、字を正しく読む**)
3. **近い**: 2-gram の Dice が 0.5 以上(K-33 の追記2 と同じ数え方)

**この 3 つを分けるのが要点である。**場所を当てることと、字を正しく読むことは別。
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

#: 「近い」と見なす 2-gram の Dice(K-33 の追記2 と同じ値)。
NEAR_MIN_DICE = 0.5


def normalize(text: str) -> str:
    """空白だけを落とす。**文字の異同はそのまま残す**(判定はそのまま一致でする)。"""
    return re.sub(r"\s+", "", text)


def fold(text: str) -> str:
    """全角と半角の違いだけをそろえる(「近い」を数えるときに使う)。"""
    return unicodedata.normalize("NFKC", normalize(text))


def _bigrams(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(len(text) - 1)} or {text}


def dice(left: str, right: str) -> float:
    one, two = _bigrams(left), _bigrams(right)
    if not one or not two:
        return 0.0
    return 2.0 * len(one & two) / (len(one) + len(two))


def score_place(answer: str, candidates: list[str]) -> dict[str, Any]:
    """1 か所ぶん採る。候補は、その場所から返った文字列を**繋いだもの**も含める。"""
    want = normalize(answer)
    joined = normalize("".join(candidates))
    pool = [normalize(text) for text in candidates] + ([joined] if candidates else [])
    exact = any(text == want for text in pool)
    near = max((dice(fold(text), fold(want)) for text in pool), default=0.0)
    return {
        "届いた": bool(candidates),
        "そのまま一致": exact,
        "近い": near >= NEAR_MIN_DICE,
        "一番近かった値": round(near, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, help="正解(共有フォルダ)")
    parser.add_argument("--texts", required=True, help="読めた文字(共有フォルダ)")
    parser.add_argument("--label", required=True, help="どの周か")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    pages = json.loads(Path(args.texts).read_text(encoding="utf-8"))
    by_page = {page["ページ番号"]: [row[-2] for row in page["_本物"]] for page in pages}

    rows = []
    for place in answers["場所"]:
        texts = by_page.get(place["ページ"], [])
        want = fold(place["正解"])
        near_texts = [
            text for text in texts if dice(fold(text), want) > 0.0
        ]
        rows.append(
            {
                "ページ": place["ページ"],
                "種類": place["種類"],
                **score_place(place["正解"], near_texts),
            }
        )

    result = {
        "どの周": args.label,
        "場所の数": len(rows),
        "届いた": sum(1 for row in rows if row["届いた"]),
        "そのまま一致": sum(1 for row in rows if row["そのまま一致"]),
        "近い": sum(1 for row in rows if row["近い"]),
        "場所ごと": rows,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "場所ごと"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
