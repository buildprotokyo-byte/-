"""周39 の測定: **記号 2,488 件に、名前は付くのか。**

基準は `docs/loop_round39_can_symbols_be_named_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

**入口が出す 2,887 件のうち 2,488 件(86%)が記号で、いま名前が付いていない。**
名前を付ける部品(`name_clusters`)はあるが、**材料の `legend_symbols` は
ページを `凡例` と宣言したときだけ作られる**(`drawing_intake.py:2019`)。
**いままでの周は全部、材料 0 件の条件で測っていた。**

**付いた名前が正しいかは測らない。付くか付かないかだけ。**

**`app.py` と `intake/` は 1 行も変えない**(K-29)。渡すのは実行時の引数だけ。
**図面の中身は 1 文字も出さない。** 出すのは件数だけ。

実行::

    .venv/bin/python -m benchmarks.measure_symbol_naming \
        --pdf <匿名化v2.pdf> --answers <回答.json> \
        --legend-table <対照表.json> --out r39.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Sequence

import fitz

from intake.start_kit import PageDeclaration, StartKit

SEED = 20260925

#: 囮に使う種類。本物と同じ宣言をする。
LEGEND_KIND = "凡例"

#: 線3 の合格。**数量の総件数の減りがこれ未満。**
LINE3_MAX_LOST_SHARE = 0.10

#: 線1 の合格。**囮のこの倍以上。**
LINE1_DECOY_FACTOR = 2


def legend_pages_from(table_path: Path) -> tuple[int, ...]:
    """人が作った対照表が名乗っているページ。**記憶からではなくここから取る。**"""
    payload = json.loads(table_path.read_text(encoding="utf-8"))
    pages = {
        int(row["source_page"])
        for key in ("work_marks", "symbols", "line_colors", "line_styles")
        for row in payload.get(key, [])
        if isinstance(row, dict) and "source_page" in row
    }
    return tuple(sorted(pages))


def decoy_pages(page_count: int, real: Sequence[int], how_many: int) -> tuple[int, ...]:
    """**凡例ではない**ページから同じ数だけ引く。"""
    rng = random.Random(SEED)
    candidates = [n for n in range(1, page_count + 1) if n not in set(real)]
    return tuple(sorted(rng.sample(candidates, how_many)))


def page_count_of(pdf: Path) -> int:
    with fitz.open(pdf) as doc:
        return doc.page_count


def declarations_for(pages: Sequence[int]) -> tuple[PageDeclaration, ...]:
    """**そのページだけ**を凡例と宣言する。他のページは宣言しない。"""
    return tuple(
        PageDeclaration(page_number=number, kind=LEGEND_KIND) for number in pages
    )


def run(pdf: Path, answers: Path, pages: Sequence[int] | None) -> list[Any]:
    from estimating.from_intake import quantities_from_intake
    from intake.drawing_intake import IntakeConfig, read_drawing

    kit = None if pages is None else StartKit(page_declarations=declarations_for(pages))
    intake = read_drawing(
        IntakeConfig(
            case_id="round39", pdf_path=pdf, answers_path=answers, start_kit=kit
        )
    )
    return list(quantities_from_intake(intake))


def named(quantities: Sequence[Any]) -> int:
    """名前が付いた記号の件数。**`provenance` を読む。数え直さない。**"""
    return sum(
        1
        for item in quantities
        if (getattr(item, "provenance", None) or {}).get("symbol_name") is not None
    )


def symbols(quantities: Sequence[Any]) -> int:
    return sum(1 for item in quantities if item.kind == "記号")


def confirmed(quantities: Sequence[Any]) -> int:
    return sum(1 for item in quantities if getattr(item, "is_confirmed", False))


def summarise(quantities: Sequence[Any]) -> dict:
    return {
        "数量": len(quantities),
        "記号の数量": symbols(quantities),
        "名前が付いた数量": named(quantities),
        "自動確定した数量": confirmed(quantities),
    }


def measure(pdf: Path, answers: Path, table: Path) -> dict:
    real = legend_pages_from(table)
    decoy = decoy_pages(page_count_of(pdf), real, len(real))

    before = summarise(run(pdf, answers, None))
    real_result = summarise(run(pdf, answers, real))
    decoy_result = summarise(run(pdf, answers, decoy))

    lost = before["数量"] - real_result["数量"]
    return {
        "断り": (
            "付いた名前が正しいかは測っていない。**付くか付かないかだけ。**"
        ),
        "凡例のページ(人が作った対照表から)": list(real),
        "囮のページ(凡例ではないページから種 20260925 で引いた)": list(decoy),
        "宣言しない": before,
        "本物": real_result,
        "囮": decoy_result,
        "線1_名前は付くか": {
            "合格": f"前 0 件、本物が 1 件以上で囮の {LINE1_DECOY_FACTOR} 倍以上",
            "前": before["名前が付いた数量"],
            "本物": real_result["名前が付いた数量"],
            "囮": decoy_result["名前が付いた数量"],
            "通過": (
                before["名前が付いた数量"] == 0
                and real_result["名前が付いた数量"] >= 1
                and real_result["名前が付いた数量"]
                >= LINE1_DECOY_FACTOR * decoy_result["名前が付いた数量"]
            ),
        },
        "線3_対照_2ページぶんの関門の代金": {
            "合格": f"減りが {int(before['数量'] * LINE3_MAX_LOST_SHARE)} 件未満",
            "減った件数": lost,
            "通過": lost < before["数量"] * LINE3_MAX_LOST_SHARE,
        },
        "線4_裏返しの危険": {
            "合格": "どの条件でも自動確定した数量が 0 件",
            "最大": max(
                before["自動確定した数量"],
                real_result["自動確定した数量"],
                decoy_result["自動確定した数量"],
            ),
            "通過": all(
                r["自動確定した数量"] == 0
                for r in (before, real_result, decoy_result)
            ),
            "意味": "0 でなければその場で止めて報告する",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--legend-table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    result = measure(args.pdf, args.answers, args.legend_table)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
