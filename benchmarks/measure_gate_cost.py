"""周38 の測定: **2 つの関門は、それぞれいくら取るのか。**

基準は `docs/loop_round38_which_gate_costs_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

**これは原則の違反である。** `docs/principles/start_kit.md` 4 節の条件3
「**図面の読み方を縛らない**」、対応表の「**人の入力は読む範囲を狭めるものではない**」。
**おーちゃんは 2026-09-22 に、この原則で開き戸の円弧の 1 か所を直すと決めている**
(案A、`tests/test_page_kind_is_a_weak_hint.py`)。**同じ形が 2 か所残っている。**

**直さない**(K-29「`app.py` と `intake/` には触らない」)。**数えるだけ。**
**図面の中身は 1 文字も出さない。** 出すのは件数だけ。

実行::

    .venv/bin/python -m benchmarks.measure_gate_cost \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r38.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz

from intake.start_kit import (
    LEGEND_PAGE_KINDS,
    PageDeclaration,
    REPEATED_SYMBOL_PAGE_KINDS,
    ROOM_OUTLINE_PAGE_KINDS,
    StartKit,
)

#: 周36 で測った件数。**この周では測り直さない**(同じコード・同じ図面)。
NO_DECLARATION = 2887
DECLARED_OTHER = 166

#: これから測る種類。
KINDS_TO_MEASURE = ("設備図", "凡例")

#: 線1 の合格。**`その他` で失われる件数の半分未満。**
LOST_BY_OTHER = NO_DECLARATION - DECLARED_OTHER
LINE1_MAX_LOST = LOST_BY_OTHER / 2


def gates_for(kind: str) -> dict[str, str]:
    """その種類がどの関門を開くか。**コードの定数から引く。手で書かない。**"""
    return {
        "繰り返す記号": (
            "凡例として読む別の道"
            if kind in LEGEND_PAGE_KINDS
            else ("開く" if kind in REPEATED_SYMBOL_PAGE_KINDS else "閉じる")
        ),
        "室の輪郭": "開く" if kind in ROOM_OUTLINE_PAGE_KINDS else "閉じる",
    }


def page_count_of(pdf: Path) -> int:
    with fitz.open(pdf) as doc:
        return doc.page_count


def declarations_for(page_count: int, kind: str) -> tuple[PageDeclaration, ...]:
    """全ページを同じ種類と宣言する。**位相は渡さない**(周36・周37 の問い)。"""
    return tuple(
        PageDeclaration(page_number=number, kind=kind)
        for number in range(1, page_count + 1)
    )


def run(pdf: Path, answers: Path, kind: str | None) -> list[Any]:
    from estimating.from_intake import quantities_from_intake
    from intake.drawing_intake import IntakeConfig, read_drawing

    kit = (
        None
        if kind is None
        else StartKit(page_declarations=declarations_for(page_count_of(pdf), kind))
    )
    intake = read_drawing(
        IntakeConfig(
            case_id="round38", pdf_path=pdf, answers_path=answers, start_kit=kit
        )
    )
    return list(quantities_from_intake(intake))


def confirmed(quantities: list[Any]) -> int:
    return sum(1 for item in quantities if getattr(item, "is_confirmed", False))


def measure(pdf: Path, answers: Path) -> dict:
    measured: dict[str, dict] = {}
    for kind in KINDS_TO_MEASURE:
        quantities = run(pdf, answers, kind)
        measured[kind] = {
            "関門": gates_for(kind),
            "数量": len(quantities),
            "失われた件数": NO_DECLARATION - len(quantities),
            "自動確定した数量": confirmed(quantities),
        }

    設備図 = measured["設備図"]
    凡例 = measured["凡例"]
    室の輪郭の値段 = 設備図["失われた件数"]
    記号の値段 = LOST_BY_OTHER - 室の輪郭の値段

    return {
        "断り": (
            "これは原則4 の条件3(図面の読み方を縛らない)の違反を数えたもの。"
            "**直していない**(K-29)。"
        ),
        "宣言しないとき": NO_DECLARATION,
        "その他と宣言したとき": DECLARED_OTHER,
        "その他で失われた件数": LOST_BY_OTHER,
        "測った種類": measured,
        "関門の値段": {
            "室の輪郭(測った)": 室の輪郭の値段,
            "繰り返す記号(引き算。足し合わせの確認はできない)": 記号の値段,
        },
        "線1_室の輪郭は記号より安いか": {
            "合格": f"設備図で失われる件数が {LINE1_MAX_LOST:.0f} 件未満",
            "失われた件数": 室の輪郭の値段,
            "通過": 室の輪郭の値段 < LINE1_MAX_LOST,
        },
        "線2_凡例は別の道か": {
            "合格": f"件数が その他 の {DECLARED_OTHER} 件と違う",
            "凡例の数量": 凡例["数量"],
            "通過": 凡例["数量"] != DECLARED_OTHER,
        },
        "線3_裏返しの危険": {
            "合格": "どの宣言でも自動確定した数量が 0 件",
            "自動確定の最大": max(m["自動確定した数量"] for m in measured.values()),
            "通過": all(m["自動確定した数量"] == 0 for m in measured.values()),
            "意味": "0 でなければその場で止めて報告する",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    result = measure(args.pdf, args.answers)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
