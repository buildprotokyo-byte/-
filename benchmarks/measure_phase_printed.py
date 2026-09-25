"""周32 の測定: **現況・計画・解体は、紙に印字されているか。**

基準は `docs/loop_round32_is_phase_printed_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

前の周(周31)との違いは**出典ひとつ**である。周31 は入口が出した数量を数えた。
ここでは同じ問いを**紙に印字された文字**に当てる。技術(語の照合と囮)は
周15・周16 と同じ。

**紙から取り出した文字は 1 文字も出さない。** 出すのは件数・割合・ページ番号だけ。
匿名化 v2 には塗りつぶされて見えない文字データが残っているため、
取り出した文字列を出力に混ぜない。探す語のほうは、こちらが先に用意した
一般の建築語なので、このファイルに書いてある。

実行::

    .venv/bin/python -m benchmarks.measure_phase_printed \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r32.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fitz

from estimating.from_intake import quantities_from_intake

#: 位相(現況か計画か解体か)を名指しする語。**基準 3 節のとおり。**
PHASE_WORDS: tuple[str, ...] = (
    "既存", "現況", "現状", "残置", "存置", "新設",
    "新規", "計画", "増設", "撤去", "解体", "改修",
)

#: 囮。建築の語だが、現況か計画かを決めない。**本物と同じ 12 語。**
DECOY_WORDS: tuple[str, ...] = (
    "防火", "耐火", "不燃", "準耐火", "難燃", "遮音",
    "断熱", "耐水", "防水", "耐震", "防露", "吸音",
)

LINE1_MIN_PAGES = 3
LINE3_MIN_SHARE = 0.10
LINE3_MARGIN = 0.20
DECOY_DRAWS = 10


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def page_texts(pdf: Path) -> list[str]:
    """1 ページ 1 文字列。**返すのは呼び出し元の中だけで使う。出力しない。**"""
    with fitz.open(pdf) as doc:
        return [normalise(page.get_text()) for page in doc]


def pages_with_any(words: Sequence[str], texts: Sequence[str]) -> set[int]:
    """語のどれかが出るページ番号(1 始まり)の集合。"""
    return {
        number
        for number, text in enumerate(texts, start=1)
        if any(word and word in text for word in words)
    }


def pages_per_word(words: Sequence[str], texts: Sequence[str]) -> dict[str, list[int]]:
    """語ごとのページ番号。**共有フォルダ用。リポジトリの報告には載せない。**"""
    return {
        word: sorted(pages_with_any((word,), texts))
        for word in words
    }


def pages_of_quantity(item: Any) -> set[int]:
    """数量 1 件の根拠に出てくるページ番号。

    根拠の形は読みの種類ごとに違う(面積は ``occurrences`` の中、開き戸は直下)
    ので、`intake/drawing_intake.py` の `_pages_in_provenance` と同じように
    たどる。**あちらを呼ばない**のは、この層が `intake/` に依存しないため。
    """
    pages: set[int] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            number = value.get("page_number")
            if isinstance(number, int):
                pages.add(number)
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(getattr(item, "provenance", {}) or {})
    return pages


def share_on_pages(quantities: Sequence[Any], pages: set[int]) -> float:
    """そのページ群に載っている数量の割合。

    **ページ番号がたどれない数量は分母に入れる。** 分母から外すと、
    たどれないものが多いほど割合が上がってしまう。
    """
    if not quantities:
        return 0.0
    hit = sum(1 for item in quantities if pages_of_quantity(item) & pages)
    return hit / len(quantities)


def scatter_pages(
    total_pages: int, how_many: int, quantities: Sequence[Any], seed: int
) -> float:
    """囮B: 同じページ数を無作為に引いたときの割合(中央値)。

    引く数がページ数以上なら全ページになる。**そのときは本物と必ず並ぶので、
    線3 は通らない。**(周31 で踏んだ「選ぶ数が母集団より大きい」の逆で、
    ここでは囮が勝つ側に倒れるので安全である。)
    """
    if total_pages <= 0:
        return 0.0
    everything = list(range(1, total_pages + 1))
    if how_many >= total_pages:
        return share_on_pages(quantities, set(everything))
    rng = random.Random(seed)
    draws = [
        share_on_pages(quantities, set(rng.sample(everything, how_many)))
        for _ in range(DECOY_DRAWS)
    ]
    return statistics.median(draws)


def measure(pdf: Path, answers: Path, seed: int) -> tuple[dict, dict]:
    """(報告に載せる結果, 共有フォルダ用の内訳) を返す。"""
    from intake.drawing_intake import IntakeConfig, read_drawing

    texts = page_texts(pdf)
    total_pages = len(texts)
    readable = sum(1 for text in texts if text.strip())

    real_pages = pages_with_any(PHASE_WORDS, texts)
    decoy_pages = pages_with_any(DECOY_WORDS, texts)

    intake = read_drawing(
        IntakeConfig(case_id="round32", pdf_path=pdf, answers_path=answers)
    )
    quantities = list(quantities_from_intake(intake))
    traceable = sum(1 for item in quantities if pages_of_quantity(item))

    real_share = share_on_pages(quantities, real_pages)
    decoy_a = share_on_pages(quantities, decoy_pages)
    decoy_b = scatter_pages(total_pages, len(real_pages), quantities, seed)

    line1 = len(real_pages) >= LINE1_MIN_PAGES and len(real_pages) > len(decoy_pages)
    line3_meaningful = len(real_pages) < total_pages
    line3 = (
        line3_meaningful
        and real_share >= LINE3_MIN_SHARE
        and (real_share - decoy_a) >= LINE3_MARGIN
        and (real_share - decoy_b) >= LINE3_MARGIN
    )

    result = {
        "ページ数": total_pages,
        "文字が取り出せたページ数": readable,
        "入口の数量": len(quantities),
        "ページ番号がたどれた数量": traceable,
        "線1_位相の語が在るページ数": {
            "本物": len(real_pages),
            "囮": len(decoy_pages),
            "合格": f"{LINE1_MIN_PAGES} ページ以上、かつ囮より多い",
            "通過": line1,
        },
        "線3_その語のページに数量が載っているか": {
            "本物の割合": round(real_share, 4),
            "囮Aの割合(語の囮)": round(decoy_a, 4),
            "囮Bの割合(無作為に同数のページ、中央値)": round(decoy_b, 4),
            "合格": (
                f"本物 {LINE3_MIN_SHARE:.0%} 以上、"
                f"囮A・囮B の両方と差 {LINE3_MARGIN:.0%} 以上"
            ),
            "測る意味があるか": line3_meaningful,
            "通過": line3,
        },
        "線4_本番の判定に触っていないか": {
            "読むだけの測定": True,
            "自動確定した件数": sum(
                1 for item in quantities if getattr(item, "is_confirmed", False)
            ),
            "合格": "判定を 1 か所も変えていないこと",
        },
    }
    detail = {
        "断り": "語ごとの内訳。**共有フォルダだけに置く。リポジトリには入れない。**",
        "本物の語ごとのページ": pages_per_word(PHASE_WORDS, texts),
        "囮の語ごとのページ": pages_per_word(DECOY_WORDS, texts),
        "位相の語が在るページ": sorted(real_pages),
        "囮の語が在るページ": sorted(decoy_pages),
    }
    return result, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detail-out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()

    result, detail = measure(args.pdf, args.answers, args.seed)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.detail_out is not None:
        args.detail_out.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
