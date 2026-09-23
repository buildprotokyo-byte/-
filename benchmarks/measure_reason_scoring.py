"""67周目: **出した行を正解に突き合わせ、当たり外れを理由別に数えられるか。**

基準は `docs/d_reason_scoring_criteria.md`(測る前にコミット済み)。

**合成の正解と合成の図面だけを使う(取り決め④)。実案件は PC 側(Codex-A)。**

対照は 3 つ。**どれかが通らなければ結論を出さない。**

- **C1**: 工事内容が 1 文字違う正解・単位が違う正解を当たりにしないこと
- **C2**: 突き合わせの鍵を壊すと M1 が 0 に落ちること(**壊し試験**)
- **C3**: 理由別の合計 + 決め手の無い行 = 行の総数(**二重に数えていないか**)

使い方::

    .venv/bin/python benchmarks/measure_reason_scoring.py

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

EXAMPLE_RULES = ROOT / "estimating" / "examples" / "synthetic_rules.json"


def synthetic_golden() -> list:
    """**合成の正解。** 実案件の見積明細ではない。

    同梱の見本規則(`estimating/examples/synthetic_rules.json`)が作る行に
    合わせて書いてある。**当ててほしい項目と、当たらないはずの項目を両方入れる。**
    """
    from estimating.scoring import GoldenItem

    rules = json.loads(EXAMPLE_RULES.read_text(encoding="utf-8"))
    out = [
        GoldenItem(
            work_item=line["work_item"],
            unit=line["unit"],
            code=line.get("code"),
            major_category=line.get("major_category"),
        )
        for rule in rules["rules"]
        for line in rule["line_items"]
    ]
    # **出せないはずの項目。** これが当たったら突き合わせが緩んでいる。
    out.append(GoldenItem(work_item="この図面からは出ない項目", unit="式"))
    return out


def produced_lines(tmp: Path) -> list:
    """本番経路(入口 → 数量 → 見積の行)が出した行。**正解は見ていない。**"""
    import pymupdf

    from estimating.from_intake import quantities_from_intake
    from estimating.mapping import map_quantities
    from estimating.rules import load_rules
    from intake.drawing_intake import IntakeConfig, read_drawing
    from tests.test_drawing_intake import _scanned_page, _vector_plan_page

    pdf = tmp / "synthetic_plan.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    _scanned_page(doc)
    doc.save(pdf)
    doc.close()

    result = read_drawing(
        IntakeConfig(case_id="SCORE", pdf_path=pdf, answers_path=tmp / "answers.json")
    )
    mapping = map_quantities(quantities_from_intake(result), load_rules(EXAMPLE_RULES))
    return [line for m in mapping.mappings for line in m.lines]


def control_1_no_loosening() -> dict:
    """**C1**: 1 文字違い・単位違いを当たりにしないこと。"""
    from estimating.decisive import REASON_OBSERVED, DecisiveReason
    from estimating.mapping import MappedLine
    from estimating.scoring import GoldenItem, score_lines

    def line(work_item: str, unit: str) -> MappedLine:
        return MappedLine(
            work_item=work_item,
            unit=unit,
            value_range=(1.0, 1.0),
            canonical_range=(1, 1),
            decisive=(DecisiveReason(kind=REASON_OBSERVED),),
        )

    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)
    return {
        "1文字違いを当たりにしない": not score_lines(
            [line("建具取付け", "箇所")], golden
        ).hit_lines,
        "単位違いを当たりにしない": not score_lines(
            [line("建具取付", "㎡")], golden
        ).hit_lines,
        # **表記のゆれ(㎡ と m²)は吸収するが、別の語にはしない。**
        "単位の表記のゆれは当たりにする": bool(
            score_lines(
                [line("床仕上", "m²")], (GoldenItem(work_item="床仕上", unit="㎡"),)
            ).hit_lines
        ),
    }


def control_2_break_test() -> dict:
    """**C2**: 突き合わせの鍵を壊すと当たりが 0 に落ちること。"""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "broken"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(work), "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        try:
            target = work / "estimating" / "scoring.py"
            text = target.read_text(encoding="utf-8")
            marker = '        return ("code", _norm(self.code))'
            assert marker in text, "壊す場所が見つからない"
            text = text.replace(
                marker, '        return ("code", "**壊し試験**")', 1
            ).replace(
                '        return ("name", _norm(self.work_item), _norm(self.unit))',
                '        return ("name", "**壊し試験**")',
                1,
            )
            target.write_text(text, encoding="utf-8")
            shutil.copy2(Path(__file__), work / "benchmarks" / Path(__file__).name)
            for cache in work.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)

            script = (
                "import json, sys, tempfile\n"
                f"sys.path.insert(0, {str(work)!r})\n"
                "from pathlib import Path\n"
                "from benchmarks.measure_reason_scoring import "
                "produced_lines, synthetic_golden\n"
                "from estimating.scoring import score_lines\n"
                "with tempfile.TemporaryDirectory() as t:\n"
                "    lines = produced_lines(Path(t))\n"
                "    r = score_lines(lines, synthetic_golden())\n"
                "    print(json.dumps(r.as_dict(), ensure_ascii=False))\n"
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
                "壊した後の当たった行": broken["当たった行"],
                "壊した後の言い当てた割合": broken["言い当てた割合"],
            }
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(work)],
                cwd=ROOT,
                capture_output=True,
            )


def main() -> int:
    from estimating.scoring import score_lines

    with tempfile.TemporaryDirectory() as tmp:
        lines = produced_lines(Path(tmp))
    result = score_lines(lines, synthetic_golden())

    counted = sum(score.total for score in result.by_reason())
    c3_total = len(result.hit_lines) + len(result.extra_lines)
    c3 = counted + len(result.lines_without_reason()) == c3_total

    payload: dict[str, object] = {
        "round": 67,
        "criteria_file": "docs/d_reason_scoring_criteria.md",
        "benchmark": "benchmarks/measure_reason_scoring.py",
        "synthetic_only": True,
        "M1M2M3": result.as_dict(),
        "C1_緩めていないか": control_1_no_loosening(),
        "C2_壊し試験": control_2_break_test(),
        "C3_二重に数えていないか": {
            "理由別の合計": counted,
            "決め手の無い行": len(result.lines_without_reason()),
            "行の総数": c3_total,
            "合う": c3,
        },
    }

    c1 = all(payload["C1_緩めていないか"].values())  # type: ignore[union-attr]
    c2 = payload["C2_壊し試験"].get("走った") and (  # type: ignore[union-attr]
        payload["C2_壊し試験"].get("壊した後の当たった行") == 0  # type: ignore[union-attr]
    )
    payload["対照の結果"] = {"C1": c1, "C2": bool(c2), "C3": c3}
    payload["判定"] = (
        "採用(全件テストと CI が通ればマージ)"
        if (c1 and c2 and c3)
        else "対照が通らないので結論を出さない"
    )

    out = ROOT / "docs" / "d_reason_scoring_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
