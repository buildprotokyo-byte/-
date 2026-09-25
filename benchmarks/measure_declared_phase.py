"""周36 の測定: **人が位相を宣言したら、実図面で何が変わるか。**

基準は `docs/loop_round36_declared_phase_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

**この周が測るのは配管であって、正しさではない。**
渡す位相の値は**こちらが機械的に作ったもの**(奇数ページ=現況、偶数ページ=計画)で、
**意味は無い。** 周32 で、ページに 1 つ位相を宣言する形はこの図面と粒度が合っていないと
分かっている。**配管が通っても、この図面では正しい値は入らない。**

**いちばん大事なのは裏返しの危険**(線3)。人が 1 回入れた値だけで自動確定が起きないか。

**`app.py` と `intake/` は 1 行も変えない。** 渡すのは実行時の引数だけ(K-29)。
**図面の中身は 1 文字も出さない。** 出すのは件数だけ。

実行::

    .venv/bin/python -m benchmarks.measure_declared_phase \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r36.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import fitz

from axes.reading.meaning import PHASE_UNKNOWN
from estimating.from_intake import quantities_from_intake
from estimating.pipeline import build_estimate_draft_from_quantities
from estimating.rules import load_rules
from intake.start_kit import PageDeclaration, StartKit

#: 渡す位相。**ページ番号で機械的に振る。意味は無い。**
#: 意味のある振り方を作ると、こちらが図面を読んで決めた値になってしまう。
ODD_PAGE_PHASE = "現況"
EVEN_PAGE_PHASE = "計画"

#: 宣言するページの種類。**位相だけを見たいので、種類は当たり障りのないものにする。**
DECLARED_KIND = "その他"

LINE1_MIN_SHARE = 0.90


def declarations_for(page_count: int) -> tuple[PageDeclaration, ...]:
    """全ページぶんの宣言。**位相は機械的。意味は無い。**"""
    return tuple(
        PageDeclaration(
            page_number=number,
            kind=DECLARED_KIND,
            phase=ODD_PAGE_PHASE if number % 2 else EVEN_PAGE_PHASE,
        )
        for number in range(1, page_count + 1)
    )


def page_count_of(pdf: Path) -> int:
    with fitz.open(pdf) as doc:
        return doc.page_count


def phase_counts(quantities: Sequence[Any]) -> Counter:
    """位相の内訳。`None` は「意味そのものが付いていない」。"""
    return Counter(
        "意味なし" if item.phase is None else str(item.phase) for item in quantities
    )


def decided(quantities: Sequence[Any]) -> int:
    """位相が `不明` 以外に決まっている件数。"""
    return sum(
        1
        for item in quantities
        if item.phase is not None and item.phase != PHASE_UNKNOWN
    )


def confirmed(quantities: Sequence[Any]) -> int:
    return sum(1 for item in quantities if getattr(item, "is_confirmed", False))


def phase_ruleset(kinds: Sequence[str], path: Path) -> Path:
    """現況を条件にした**合成の**規則。**実案件には使わない。**

    種類ごとに 1 本ずつ機械的に作る。**結果を見て選んだ種類ではない。**
    """
    payload = {
        "format_version": 3,
        "ruleset_id": "round36-synthetic",
        "description": (
            "周36 の測定のためだけの合成の規則。実在の会社の積算ルールでも、"
            "実案件の見積明細から作ったものでもない。実案件には使わない。"
        ),
        "rules": [
            {
                "rule_id": f"phase-probe-{index}",
                "kind": kind,
                "unit_dimension": "count",
                "description": "現況を条件にした合成の規則。配管を見るためだけのもの。",
                "phase": ["現況"],
                "line_items": [
                    {
                        "code": f"RD36-{index:03d}",
                        "work_item": "合成の行",
                        "major_category": "合成の区分",
                        "unit": "箇所",
                    }
                ],
            }
            for index, kind in enumerate(sorted(set(kinds)))
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run(pdf: Path, answers: Path, start_kit: StartKit | None) -> list[Any]:
    from intake.drawing_intake import IntakeConfig, read_drawing

    intake = read_drawing(
        IntakeConfig(
            case_id="round36",
            pdf_path=pdf,
            answers_path=answers,
            start_kit=start_kit,
        )
    )
    return list(quantities_from_intake(intake))


def hits_and_settled(quantities: Sequence[Any], rules: Path) -> tuple[int, int]:
    """(規則が当たった件数, 当てはめで確定した行)。"""
    ruleset = load_rules(rules)
    hits = sum(
        1
        for item in quantities
        for rule in ruleset.rules
        if rule.match(item).matched
    )
    draft = build_estimate_draft_from_quantities(quantities, ruleset)
    return hits, len(draft.settled_lines)


def measure(pdf: Path, answers: Path) -> dict:
    before = run(pdf, answers, None)
    kit = StartKit(page_declarations=declarations_for(page_count_of(pdf)))
    after = run(pdf, answers, kit)

    with tempfile.TemporaryDirectory() as tmp:
        rules = phase_ruleset(
            [item.kind for item in after], Path(tmp) / "rules.json"
        )
        hits_before, settled_before = hits_and_settled(before, rules)
        hits_after, settled_after = hits_and_settled(after, rules)

    decided_after = decided(after)
    share = decided_after / len(after) if after else 0.0

    return {
        "断り": "渡した位相はページ番号で機械的に振ったもの。**値に意味は無い。**",
        "線1_配管は通るか": {
            "渡す前に位相が決まっている数量": decided(before),
            "渡した後": decided_after,
            "割合": round(share, 4),
            "合格": f"渡す前 0 件、渡した後 {LINE1_MIN_SHARE:.0%} 以上",
            "通過": decided(before) == 0 and share >= LINE1_MIN_SHARE,
        },
        "線2_対照_数量そのものは変わらないか": {
            "渡す前の数量": len(before),
            "渡した後の数量": len(after),
            "合格": "同じ件数",
            "通過": len(before) == len(after),
        },
        "線3_裏返しの危険": {
            "渡す前に自動確定した数量": confirmed(before),
            "渡した後": confirmed(after),
            "合格": "どちらも 0 件",
            "通過": confirmed(before) == 0 and confirmed(after) == 0,
            "意味": "0 でなければその場で止めて報告する",
        },
        "線4_規則は当たるようになるか": {
            "渡す前に当たった件数": hits_before,
            "渡した後": hits_after,
            "当てはめで確定した行(前)": settled_before,
            "当てはめで確定した行(後)": settled_after,
            "合格": "前 0 件・後 1 件以上、かつ確定した行は前後とも 0 件",
            "通過": (
                hits_before == 0
                and hits_after >= 1
                and settled_before == 0
                and settled_after == 0
            ),
        },
        "参考_位相の内訳": {
            "渡す前": dict(sorted(phase_counts(before).items())),
            "渡した後": dict(sorted(phase_counts(after).items())),
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
