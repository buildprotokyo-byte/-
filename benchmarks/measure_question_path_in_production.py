"""61周目: **問いの経路は、本番経路から呼ばれているか。**

基準は `docs/d_question_path_in_production_criteria.md`(測る前にコミット済み)。

PR #87 の本文に **「いまの本番経路では全部 C になる」** と書いた。
**それはコードを読んだだけの見立てなので、呼び出しを数えて測る。**

**数え方**: `sys.setprofile` で、`killer_question/` の中の関数に入った回数を数える。
import のしかた(`from ... import 名前`)に左右されないので、
名前で patch するより漏れにくい。

対照つき。**対照が通らなければ結論を出さない。**

使い方::

    .venv/bin/python benchmarks/measure_question_path_in_production.py [実図面PDFのパス]

**図面の中身は出力しない。出すのは件数だけ。**
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KQ_DIR = str(ROOT / "killer_question") + "/"


class CallCounter:
    """`killer_question/` に入った呼び出しを数える。"""

    def __init__(self) -> None:
        self.by_function: dict[str, int] = {}

    def __enter__(self) -> "CallCounter":
        sys.setprofile(self._hook)
        return self

    def __exit__(self, *exc: object) -> None:
        sys.setprofile(None)

    def _hook(self, frame, event, arg) -> None:  # noqa: ANN001
        if event != "call":
            return
        filename = frame.f_code.co_filename
        if not filename.startswith(KQ_DIR):
            return
        name = f"{Path(filename).name}:{frame.f_code.co_name}"
        self.by_function[name] = self.by_function.get(name, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.by_function.values())


def _count_declared_independent_sources() -> tuple[int, list[str]]:
    """`independent_sources` に 0 以外が渡った変数を数える。

    `add_variable` を包んで数える。**製品コードは変えない。**
    """
    from arbitration.consistency_solver import ConsistencySolver

    declared: list[str] = []
    original = ConsistencySolver.add_variable

    def wrapper(self, name, lower, upper, **kwargs):  # noqa: ANN001, ANN202
        if kwargs.get("independent_sources", 0):
            declared.append(name)
        return original(self, name, lower, upper, **kwargs)

    ConsistencySolver.add_variable = wrapper  # type: ignore[assignment]
    return declared, original  # type: ignore[return-value]


def make_synthetic_pdf(path: Path) -> None:
    """壁が1本と寸法の文字だけの、ごく小さい図面。**合成データのみ(取り決め④)。**"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.draw_line(fitz.Point(100, 100), fitz.Point(500, 100))
    page.draw_line(fitz.Point(100, 100), fitz.Point(100, 400))
    page.insert_text(fitz.Point(120, 90), "4000", fontsize=10)
    page.insert_text(fitz.Point(600, 60), "S=1/50", fontsize=10)
    doc.save(path)
    doc.close()


def run_production(pdf_path: Path, case_id: str) -> tuple[CallCounter, list[str]]:
    from intake.drawing_intake import IntakeConfig, read_drawing

    declared, original_add_variable = _count_declared_independent_sources()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config = IntakeConfig(
                case_id=case_id,
                pdf_path=pdf_path,
                answers_path=Path(tmp) / "answers.json",
            )
            with CallCounter() as counter:
                read_drawing(config)
    finally:
        from arbitration.consistency_solver import ConsistencySolver

        ConsistencySolver.add_variable = original_add_variable  # type: ignore[assignment]
    return counter, declared


def run_control() -> CallCounter:
    """対照: 問いの経路を現に使っているベンチマークを、同じ数え方で走らせる。"""
    from benchmarks.run_killer_question_eval import star_scenario
    from killer_question.engine import KillerQuestionEngine

    solver, ground_truth = star_scenario()
    with CallCounter() as counter:
        engine = KillerQuestionEngine(solver)
        engine.run(lambda q: ground_truth[q.variable])
    return counter


def main() -> None:
    real_pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else None

    control = run_control()
    control_passed = control.total >= 1

    payload: dict[str, object] = {
        "C1_対照_問いの経路の呼び出し回数": control.total,
        "C1_通過": control_passed,
    }

    if not control_passed:
        payload["判定"] = "対照が通らないので結論を出さない"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    with tempfile.TemporaryDirectory() as tmp:
        synthetic = Path(tmp) / "synthetic.pdf"
        make_synthetic_pdf(synthetic)
        counter, declared = run_production(synthetic, "synthetic")
    payload["合成_M1M2_問いの経路の呼び出し回数"] = counter.total
    payload["合成_M1M2_内訳"] = counter.by_function
    payload["合成_M3_independent_sourcesを申告した変数の件数"] = len(declared)

    if real_pdf is not None:
        if not real_pdf.exists():
            payload["実図面"] = "パスが見つからない"
        else:
            counter_real, declared_real = run_production(real_pdf, "real")
            payload["実図面_M1M2_問いの経路の呼び出し回数"] = counter_real.total
            payload["実図面_M1M2_内訳"] = counter_real.by_function
            payload["実図面_M3_申告した変数の件数"] = len(declared_real)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
