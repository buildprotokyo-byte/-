"""周37 の測定: **位相が全部正しく付いたとして、見積の行は変わるか。**

基準は `docs/loop_round37_phase_ceiling_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

**手段ではなく目的の側を見る周である。** 周32〜周36 は「位相をどう付けるか」を
5 周やった。ここでは**手段を全部成功させたことにして**、
**撤去の行と新設の行が分かれるか**を見る。

**入れる位相はこちらが作ったものである。正しい位相はこの図面からは分からない**
(周32・周35)。**だから「位相が正しく付く」とは書かない。**

**`app.py` と `intake/` と `estimating/` は 1 行も変えない**(K-29)。
出てきた数量を、この道具の中で組み直すだけである。
**図面の中身は 1 文字も出さない。** 出すのは件数だけ。

実行::

    .venv/bin/python -m benchmarks.measure_phase_ceiling \
        --pdf <匿名化v2.pdf> --answers <回答.json> --out r37.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from axes.reading.meaning import (
    PHASE_EXISTING,
    PHASE_PLANNED,
    PURPOSE_UNESTABLISHED,
    Meaning,
)
from estimating.pipeline import build_estimate_draft_from_quantities
from estimating.rules import load_rules

#: 反実仮想で入れる位相。**3 通りとも「正しい位相」ではない。**
#: 見たいのは「位相が何であれ、行の側が動くか」である。
ASSIGNMENTS = ("全部現況", "全部計画", "ページ番号で交互")

#: いまある見本の規則。**23 本のうち 1 本も `phase` を条件に持っていない**(追記1)。
EXISTING_RULES = (
    Path("estimating/examples/synthetic_rules.json"),
    Path("estimating/examples/synthetic_symbol_rules.json"),
)

#: 撤去の行かどうかを見分ける語。**行の文言だけを見る。**
DEMOLITION_WORDS = ("撤去", "解体", "取り壊")


def page_of(item: Any) -> int:
    """数量が載っているページ。分からなければ 0。"""
    provenance = getattr(item, "provenance", None) or {}
    number = provenance.get("page_number")
    return int(number) if isinstance(number, int) else 0


def phase_for(item: Any, assignment: str) -> str:
    if assignment == "全部現況":
        return PHASE_EXISTING
    if assignment == "全部計画":
        return PHASE_PLANNED
    return PHASE_EXISTING if page_of(item) % 2 else PHASE_PLANNED


def with_phase(quantities: Sequence[Any], assignment: str) -> list[Any]:
    """**器ごと位相を入れた数量**を作る。元の数量は変えない。

    器(`Meaning`)が無い数量には器を作る。**`what` と `where` は
    その数量が既に持っているものから作る**(読めていない欄を埋めない)。
    """
    out: list[Any] = []
    for item in quantities:
        phase = phase_for(item, assignment)
        current = getattr(item, "meaning", None)
        if current is not None:
            meaning = dataclasses.replace(current, phase=phase)
        else:
            page = page_of(item)
            meaning = Meaning(
                what=item.kind,
                where=f"ページ{page}" if page else "ページ不明",
                phase=phase,
                purpose_link=PURPOSE_UNESTABLISHED,
            )
        out.append(dataclasses.replace(item, meaning=meaning))
    return out


def phase_ruleset(kinds: Sequence[str], path: Path) -> Path:
    """位相を条件にした**合成の**規則。**実案件には使わない。**"""
    payload = {
        "format_version": 3,
        "ruleset_id": "round37-synthetic",
        "description": (
            "周37 の測定のためだけの合成の規則。実在の会社の積算ルールでも、"
            "実案件の見積明細から作ったものでもない。実案件には使わない。"
        ),
        "rules": [
            {
                "rule_id": f"phase-{label}-{index}",
                "kind": kind,
                "unit_dimension": "count",
                "description": "位相を条件にした合成の規則。行が動くかを見るためだけのもの。",
                "phase": [phase],
                "line_items": [
                    {
                        "code": f"RD37-{label}-{index:03d}",
                        "work_item": work_item,
                        "major_category": "合成の区分",
                        "unit": "箇所",
                    }
                ],
            }
            for index, kind in enumerate(sorted(set(kinds)))
            for label, phase, work_item in (
                ("E", PHASE_EXISTING, "合成の撤去の行"),
                ("P", PHASE_PLANNED, "合成の新設の行"),
            )
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def line_fingerprints(draft: Any) -> tuple[str, ...]:
    """行の並び。**囮(位相の中身を入れ替える)の突き合わせに使う。**"""
    out: list[str] = []
    for mapping in draft.mapping.mappings:
        for line in mapping.lines:
            out.append(
                f"{line.rule_id}|{line.code}|{line.work_item}|{line.unit}"
                f"|{line.value_range[0]}|{line.value_range[1]}"
            )
    return tuple(sorted(out))


def demolition_lines(fingerprints: Sequence[str]) -> int:
    return sum(1 for f in fingerprints if any(w in f for w in DEMOLITION_WORDS))


def confirmed(quantities: Sequence[Any]) -> int:
    return sum(1 for item in quantities if getattr(item, "is_confirmed", False))


def outcome(quantities: Sequence[Any], rules: Sequence[Path]) -> dict:
    """1 つの条件の結果。**行の数と、行の並びの指紋。**"""
    fingerprints: list[str] = []
    settled = 0
    for path in rules:
        draft = build_estimate_draft_from_quantities(quantities, load_rules(path))
        fingerprints.extend(line_fingerprints(draft))
        settled += len(draft.settled_lines)
    unique = tuple(sorted(set(fingerprints)))
    return {
        "行の数": len(unique),
        "撤去の行": demolition_lines(unique),
        "当てはめで確定した行": settled,
        "自動確定した数量": confirmed(quantities),
        "_指紋": unique,
    }


def measure(pdf: Path, answers: Path) -> dict:
    from estimating.from_intake import quantities_from_intake
    from intake.drawing_intake import IntakeConfig, read_drawing

    intake = read_drawing(
        IntakeConfig(case_id="round37", pdf_path=pdf, answers_path=answers)
    )
    base = list(quantities_from_intake(intake))

    with tempfile.TemporaryDirectory() as tmp:
        synthetic = phase_ruleset([item.kind for item in base], Path(tmp) / "r.json")
        rulesets = {
            "いまある見本の規則": EXISTING_RULES,
            "位相を条件にした規則": (synthetic,),
        }

        result: dict[str, Any] = {
            "断り": (
                "入れた位相はこちらが作ったもの。**正しい位相ではない。**"
                "見たいのは「位相が何であれ、行の側が動くか」。"
            ),
            "入口の数量": len(base),
            "器を持っていた数量": sum(
                1 for item in base if getattr(item, "meaning", None) is not None
            ),
            "規則ごと": {},
        }

        for rules_name, paths in rulesets.items():
            before = outcome(base, paths)
            afters = {
                name: outcome(with_phase(base, name), paths) for name in ASSIGNMENTS
            }
            fingerprints = {name: afters[name].pop("_指紋") for name in ASSIGNMENTS}
            before.pop("_指紋")

            differing = sum(
                1
                for name in ASSIGNMENTS
                if fingerprints[name] != fingerprints[ASSIGNMENTS[0]]
            )
            grew = [name for name in ASSIGNMENTS if afters[name]["行の数"] > before["行の数"]]
            demolition = [name for name in ASSIGNMENTS if afters[name]["撤去の行"] >= 1]

            result["規則ごと"][rules_name] = {
                "入れる前": before,
                "入れた後": afters,
                "線1_行が増えたか": {
                    "合格": "1 行でも増える",
                    "増えた入れ方": grew,
                    "通過": bool(grew),
                },
                "線2_撤去の行": {
                    "合格": "1 行以上",
                    "出た入れ方": demolition,
                    "通過": bool(demolition),
                },
                "線4_囮_位相の中身を入れ替えたら行は変わるか": {
                    "合格": "3 通りが全部同じではない(どれか 1 つでも行の並びが違う)",
                    "1 つ目と違った数": differing,
                    "通過": differing >= 1,
                    "意味": (
                        "3 通りとも同じなら、位相の中身は行に何も効いていない"
                    ),
                },
            }

        settled_all = [
            result["規則ごと"][name]["入れる前"]["当てはめで確定した行"] for name in rulesets
        ] + [
            result["規則ごと"][name]["入れた後"][a]["当てはめで確定した行"]
            for name in rulesets
            for a in ASSIGNMENTS
        ]
        confirmed_all = [
            result["規則ごと"][name]["入れる前"]["自動確定した数量"] for name in rulesets
        ] + [
            result["規則ごと"][name]["入れた後"][a]["自動確定した数量"]
            for name in rulesets
            for a in ASSIGNMENTS
        ]
        result["線3_裏返しの危険"] = {
            "合格": "自動確定した数量も、当てはめで確定した行も、全部 0 件",
            "自動確定した数量の最大": max(confirmed_all),
            "当てはめで確定した行の最大": max(settled_all),
            "通過": max(confirmed_all) == 0 and max(settled_all) == 0,
            "意味": "0 でなければその場で止めて報告する",
        }
        result["参考_種類の数"] = len({item.kind for item in base})
        result["参考_ページの内訳"] = len(Counter(page_of(item) for item in base))
    return result


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
