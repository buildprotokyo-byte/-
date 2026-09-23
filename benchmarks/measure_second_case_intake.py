"""62周目: **2件目の図面を、いまの読み取りに通すと何が出るか。**

基準は `docs/d_second_case_intake_criteria.md`(測る前にコミット済み)。

**対照つき**: 同じ測り方を P011v2 に当てて、開き戸の円弧が 90 件再現するか。
**再現しなければ結論を出さない。**

使い方::

    .venv/bin/python benchmarks/measure_second_case_intake.py \\
        --second <2件目の図面PDF> --control <P011v2のPDF>

**図面の中身は出さない。出すのは件数と手法の名前だけ**(取り決め④)。
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

from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402


def measure(pdf_path: Path, case_id: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        config = IntakeConfig(
            case_id=case_id,
            pdf_path=pdf_path,
            answers_path=Path(tmp) / "answers.json",
        )
        result = read_drawing(config)

    by_method = Counter(f.method_id for f in result.findings)
    by_tier = Counter(d.tier for d in result.decisions)
    return {
        "ページ数": len(result.pages),
        "M1_手法ごとの件数": dict(sorted(by_method.items())),
        "M1_読めた数量の合計": len(result.findings),
        "M2_階層ごとの件数": {str(k): v for k, v in sorted(by_tier.items())},
        "M3_試せなかった手法があるページ数": len(result.pages_with_unattempted_methods),
        "M4_建具表の行数": len(result.door_schedule_rows),
        "M4_仕上表の行数": len(result.finish_schedule_rows),
        "人の判断待ちの件数": len(result.pending_decisions),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    args = parser.parse_args()

    payload: dict[str, object] = {}

    control = measure(args.control, "control")
    payload["C1_対照_P011v2"] = control
    arcs = control["M1_手法ごとの件数"].get("pdf_vector_door_arc", 0)  # type: ignore[union-attr]
    payload["C1_開き戸の円弧"] = arcs
    payload["C1_通過"] = arcs == 90

    if arcs != 90:
        payload["判定"] = f"対照が通らない(円弧 {arcs} 件、期待 90 件)ので結論を出さない"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    payload["2件目"] = measure(args.second, "second")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
