"""63周目: **人が「平面図」と宣言するかどうかで、出てくる数はどれだけ変わるか。**

基準は `docs/d_declaration_effect_criteria.md`(測る前にコミット済み)。

**対照は同じ経路の中で立てる**(62 周目に別の経路の数字と比べて止まったため)。

使い方::

    .venv/bin/python benchmarks/measure_declaration_effect.py \\
        --control <P011v2> --second <2件目の図面>

**図面の中身は出さない。件数と手法の名前だけ。**
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402
from intake.start_kit import PageDeclaration, StartKit  # noqa: E402

#: 62 周目に出た数字。**対照はこれと一致しなければならない。**
CONTROL_EXPECTED = {
    "pdf_vector_door_arc": 5,
    "pdf_text_area": 2,
    "仕上表の行数": 59,
    "試せなかった手法があるページ数": 14,
}


def page_count(pdf_path: Path) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def measure(pdf_path: Path, case_id: str, *, declare_all_plans: bool) -> dict[str, object]:
    start_kit = None
    if declare_all_plans:
        start_kit = StartKit(
            page_declarations=tuple(
                PageDeclaration(page_number=n, kind="平面図")
                for n in range(1, page_count(pdf_path) + 1)
            )
        )
    with tempfile.TemporaryDirectory() as tmp:
        config = IntakeConfig(
            case_id=case_id,
            pdf_path=pdf_path,
            answers_path=Path(tmp) / "answers.json",
            start_kit=start_kit,
        )
        result = read_drawing(config)

    return {
        "M1_手法ごとの件数": dict(sorted(Counter(f.method_id for f in result.findings).items())),
        "M1_合計": len(result.findings),
        "M2_階層ごとの件数": {str(k): v for k, v in sorted(Counter(d.tier for d in result.decisions).items())},
        "M3_試せなかった手法があるページ数": len(result.pages_with_unattempted_methods),
        "M4_建具表の行数": len(result.door_schedule_rows),
        "M4_仕上表の行数": len(result.finish_schedule_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    args = parser.parse_args()

    payload: dict[str, object] = {}

    control_a = measure(args.control, "control", declare_all_plans=False)
    payload["P011v2_A_前提なし"] = control_a

    methods = control_a["M1_手法ごとの件数"]  # type: ignore[assignment]
    passed = (
        methods.get("pdf_vector_door_arc", 0) == CONTROL_EXPECTED["pdf_vector_door_arc"]
        and methods.get("pdf_text_area", 0) == CONTROL_EXPECTED["pdf_text_area"]
        and control_a["M4_仕上表の行数"] == CONTROL_EXPECTED["仕上表の行数"]
        and control_a["M3_試せなかった手法があるページ数"]
        == CONTROL_EXPECTED["試せなかった手法があるページ数"]
    )
    payload["C1_通過"] = passed
    payload["C1_期待した数"] = CONTROL_EXPECTED

    if not passed:
        payload["判定"] = "対照が通らないので結論を出さない"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    payload["P011v2_B_全ページ平面図"] = measure(args.control, "control_b", declare_all_plans=True)
    payload["2件目_A_前提なし"] = measure(args.second, "second", declare_all_plans=False)
    payload["2件目_B_全ページ平面図"] = measure(args.second, "second_b", declare_all_plans=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
