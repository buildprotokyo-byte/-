"""66周目: **見積の行 1 つを見て「何が決め手だったか」が分かるか。**

基準は `docs/d_decisive_reason_criteria.md`(測る前にコミット済み)。

**合成データだけを使う(取り決め④)。実図面での測定は PC 側(Codex-A)。**

対照は 4 つ。**どれかが通らなければ結論を出さない。**

- **C0**: それぞれの入力で行が 1 行以上出ること(0 行で割合を出さない)
- **C1**: 証拠の欠けた決め手が作れないこと
- **C2**: 決め手を作る関数を壊すと M1 が 0 に落ちること(**壊し試験**)
- **C3**: 既存の指標(行数・確定件数・基づきの内訳)が変更の前後で動かないこと

使い方::

    .venv/bin/python benchmarks/measure_decisive_reason.py

**出すのは件数と割合だけ。**
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 足す前のコード。**基準をコミットした回**(66周目の基準)。
BEFORE_REV = "225bd26"

EXAMPLE_RULES = ROOT / "estimating" / "examples" / "synthetic_rules.json"


# ---------------------------------------------------------------------------
# 固定の入力(**合成データのみ**)
# ---------------------------------------------------------------------------


def _write_synthetic_pdf(path: Path) -> None:
    """`tests/test_drawing_intake.py` と同じ合成平面図(+ スキャンのページ)。"""
    import pymupdf

    from tests.test_drawing_intake import _scanned_page, _vector_plan_page

    doc = pymupdf.open()
    _vector_plan_page(doc)
    _scanned_page(doc)
    doc.save(path)
    doc.close()


def run_production_path(tmp: Path) -> tuple[list, list, dict]:
    """**入力あ**: 入口 → 数量 → 見積の行(同梱の見本規則)。"""
    from estimating.from_intake import not_obtained_from_intake, quantities_from_intake
    from estimating.mapping import map_quantities
    from estimating.rules import load_rules
    from intake.drawing_intake import IntakeConfig, read_drawing

    pdf = tmp / "synthetic_plan.pdf"
    _write_synthetic_pdf(pdf)
    result = read_drawing(
        IntakeConfig(
            case_id="DEC-BENCH", pdf_path=pdf, answers_path=tmp / "answers.json"
        )
    )
    mapping = map_quantities(quantities_from_intake(result), load_rules(EXAMPLE_RULES))
    lines = [line for m in mapping.mappings for line in m.lines]
    not_obtained = list(not_obtained_from_intake(result)) + list(mapping.not_obtained())
    existing = {
        "行数": len(lines),
        "確定した行数": len(mapping.settled_lines()),
        "基づきの内訳": mapping.basis_counts_text(),
    }
    return lines, not_obtained, existing


def run_fixed_quantities() -> tuple[list, list, dict]:
    """**入力い**: 64周目と同じ固定の合成数量。"""
    from estimating.mapping import map_quantities
    from estimating.quantities import QuantityItem
    from estimating.rules import load_rules

    quantities = [
        QuantityItem(
            target="開き戸::1階",
            value_range=(3.0, 3.0),
            unit="箇所",
            method_id="pdf_vector_door_arc",
            axis_id="image",
            tier=3,
            action="requires_review",
        ),
        QuantityItem(
            target="施工対象床面積::全体",
            value_range=(55.0, 55.0),
            unit="㎡",
            method_id="pdf_text_area",
            axis_id="text",
            tier=3,
            action="requires_review",
        ),
    ]
    mapping = map_quantities(quantities, load_rules(EXAMPLE_RULES))
    lines = [line for m in mapping.mappings for line in m.lines]
    existing = {
        "行数": len(lines),
        "確定した行数": len(mapping.settled_lines()),
        "基づきの内訳": mapping.basis_counts_text(),
    }
    return lines, list(mapping.not_obtained()), existing


# ---------------------------------------------------------------------------
# 測る
# ---------------------------------------------------------------------------


def measure(lines, not_obtained) -> dict:  # noqa: ANN001
    from estimating.decisive import (
        counts_by_reason,
        lines_without_reason,
        not_obtained_counts,
    )

    without = lines_without_reason(lines)
    return {
        "M1_決め手の付いた行の割合": (
            round((len(lines) - len(without)) / len(lines), 4) if lines else None
        ),
        "M1_行数": len(lines),
        "M1_決め手が無い行": [line.source_target for line in without],
        "M2_決め手の種類別の件数": counts_by_reason(lines),
        "M3_取れなかった理由別の件数": not_obtained_counts(not_obtained),
    }


def control_1_refusals() -> dict:
    """**C1**: 証拠の欠けた決め手が作れないこと。"""
    from estimating.decisive import (
        NOT_OBTAINED_OTHER,
        REASON_HUMAN_ANSWER,
        REASON_KNOWLEDGE_RULE,
        REASON_PATHS_AGREED,
        REASON_SUMMARY_SEARCH,
        DecisiveError,
        DecisiveReason,
        NotObtained,
    )

    attempts = {
        "ルールIDの無い知識のルール": lambda: DecisiveReason(kind=REASON_KNOWLEDGE_RULE),
        "経路が1本だけの一致": lambda: DecisiveReason(
            kind=REASON_PATHS_AGREED, agreeing_paths=("a",), paths_independent=True
        ),
        "独立性を書かない一致": lambda: DecisiveReason(
            kind=REASON_PATHS_AGREED, agreeing_paths=("a", "b")
        ),
        "問いの分からない人の回答": lambda: DecisiveReason(kind=REASON_HUMAN_ANSWER),
        "項目の無い要約資料": lambda: DecisiveReason(kind=REASON_SUMMARY_SEARCH),
        "知らない種類": lambda: DecisiveReason(kind="なんとなく"),
        "説明の無いその他": lambda: NotObtained(reason=NOT_OBTAINED_OTHER, target="x"),
    }
    refused: dict[str, bool] = {}
    for name, make in attempts.items():
        try:
            make()
        except DecisiveError:
            refused[name] = True
        else:
            refused[name] = False
    return refused


def control_2_break_test() -> dict:
    """**C2**: 決め手を作る関数を壊したら M1 が 0 に落ちること。

    作業用の複製を作って壊す。**`__pycache__` を消し、バイトコードを
    書かせない**(壊し方でバイト数が変わらないと古い `.pyc` が残る)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "broken"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(work), "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        try:
            target = work / "estimating" / "decisive.py"
            text = target.read_text(encoding="utf-8")
            marker = "    reasons: list[DecisiveReason] = []"
            assert marker in text, "壊す場所が見つからない"
            text = text.replace(
                marker, marker + "\n    return ()  # **壊し試験**", 1
            )
            target.write_text(text, encoding="utf-8")
            # このベンチマーク自身はまだコミットしていないことがあるので、
            # **いまの版を複製に置く**(複製の中だけで完結させるため)。
            shutil.copy2(Path(__file__), work / "benchmarks" / Path(__file__).name)
            for cache in work.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)

            # **壊した複製の中だけで走らせる。** 元の木は `sys.path` に入れない。
            script = (
                "import json, sys, tempfile\n"
                f"sys.path.insert(0, {str(work)!r})\n"
                "from pathlib import Path\n"
                "from benchmarks.measure_decisive_reason import "
                "measure, run_production_path\n"
                "with tempfile.TemporaryDirectory() as t:\n"
                "    lines, no, _ = run_production_path(Path(t))\n"
                "    print(json.dumps(measure(lines, no), ensure_ascii=False))\n"
            )
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [str(ROOT / ".venv" / "bin" / "python"), "-c", script],
                cwd=work,
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                return {"走った": False, "詳細": proc.stderr[-400:]}
            broken = json.loads(proc.stdout.strip().splitlines()[-1])
            return {
                "走った": True,
                "壊した後のM1": broken["M1_決め手の付いた行の割合"],
                "壊した後の種類別": broken["M2_決め手の種類別の件数"],
            }
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(work)],
                cwd=ROOT,
                capture_output=True,
            )


def control_3_existing_metrics(after: dict) -> dict:
    """**C3**: 既存の指標が、足す前のコードと 1 件も変わらないこと。"""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "before"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(work), BEFORE_REV],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        try:
            script = (
                "import json, sys, tempfile\n"
                f"sys.path.insert(0, {str(work)!r})\n"
                "from pathlib import Path\n"
                "from estimating.from_intake import quantities_from_intake\n"
                "from estimating.mapping import map_quantities\n"
                "from estimating.rules import load_rules\n"
                "from intake.drawing_intake import IntakeConfig, read_drawing\n"
                "from tests.test_drawing_intake import _scanned_page, _vector_plan_page\n"
                "import pymupdf\n"
                "with tempfile.TemporaryDirectory() as t:\n"
                "    p = Path(t)/'s.pdf'\n"
                "    d = pymupdf.open(); _vector_plan_page(d); _scanned_page(d)\n"
                "    d.save(p); d.close()\n"
                "    r = read_drawing(IntakeConfig(case_id='DEC-BENCH', pdf_path=p,"
                " answers_path=Path(t)/'a.json'))\n"
                "    m = map_quantities(quantities_from_intake(r),"
                f" load_rules({str(EXAMPLE_RULES)!r}))\n"
                "    lines = [l for x in m.mappings for l in x.lines]\n"
                "    print(json.dumps({'行数': len(lines),"
                " '確定した行数': len(m.settled_lines()),"
                " '基づきの内訳': m.basis_counts_text()}, ensure_ascii=False))\n"
            )
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [str(ROOT / ".venv" / "bin" / "python"), "-c", script],
                cwd=work,
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                return {"走った": False, "詳細": proc.stderr[-400:]}
            before = json.loads(proc.stdout.strip().splitlines()[-1])
            return {
                "走った": True,
                "足す前": before,
                "足した後": after,
                "同じ": before == after,
            }
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(work)],
                cwd=ROOT,
                capture_output=True,
            )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        prod_lines, prod_not_obtained, prod_existing = run_production_path(Path(tmp))
    fixed_lines, fixed_not_obtained, fixed_existing = run_fixed_quantities()

    payload: dict[str, object] = {
        "round": 66,
        "criteria_file": "docs/d_decisive_reason_criteria.md",
        "benchmark": "benchmarks/measure_decisive_reason.py",
        "synthetic_only": True,
        "入力あ_本番経路": measure(prod_lines, prod_not_obtained),
        "入力い_固定の合成数量": measure(fixed_lines, fixed_not_obtained),
        "C0_行が出たか": {
            "入力あ": len(prod_lines) >= 1,
            "入力い": len(fixed_lines) >= 1,
        },
        "C1_でたらめを断るか": control_1_refusals(),
        "C2_壊し試験": control_2_break_test(),
        "C3_既存の指標": control_3_existing_metrics(prod_existing),
        "既存の指標_入力い": fixed_existing,
    }

    c0 = all(payload["C0_行が出たか"].values())  # type: ignore[union-attr]
    c1 = all(payload["C1_でたらめを断るか"].values())  # type: ignore[union-attr]
    c2 = payload["C2_壊し試験"].get("走った") and (  # type: ignore[union-attr]
        payload["C2_壊し試験"].get("壊した後のM1") == 0.0  # type: ignore[union-attr]
    )
    c3 = payload["C3_既存の指標"].get("同じ") is True  # type: ignore[union-attr]
    payload["対照の結果"] = {"C0": c0, "C1": c1, "C2": bool(c2), "C3": c3}

    m1_prod = payload["入力あ_本番経路"]["M1_決め手の付いた行の割合"]  # type: ignore[index]
    m1_fixed = payload["入力い_固定の合成数量"]["M1_決め手の付いた行の割合"]  # type: ignore[index]

    if not (c0 and c1 and c2 and c3):
        payload["判定"] = "対照が通らないので結論を出さない"
    elif m1_prod == 1.0 and m1_fixed == 1.0:
        payload["判定"] = "採用(全件テストと CI が通ればマージ)"
    else:
        payload["判定"] = "決め手の付かない行がある。名指しして報告し、マージは判断を仰ぐ"

    out = ROOT / "docs" / "d_decisive_reason_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
