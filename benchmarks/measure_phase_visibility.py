"""**現況と計画を、対象の名前ではなく意味の欄で分けたとき、何が変わるか。**

基準は `docs/d_phase_visibility_criteria.md`(測る前にコミット済み)。

対象の変更は `docs/principles/scope_of_work_diff.md` 9 節の 2 番と 3 番。
**直す前と直した後で同じこのスクリプトを走らせて、数字を並べる。**

図面は**この中で組み立てる合成のベクター PDF** である。実図面は使わない
(取り決め④)。

使い方::

    .venv/bin/python benchmarks/measure_phase_visibility.py --out before.json
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

import pymupdf  # noqa: E402

from estimating.from_intake import quantities_from_intake  # noqa: E402
from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402
from intake.start_kit import PageDeclaration, StartKit  # noqa: E402
from tests.test_drawing_intake import _vector_plan_page  # noqa: E402
from tests.test_pdf_tables import draw_table  # noqa: E402

#: 1 枚目と 2 枚目の建具表に書く数量。**違う数にしてあるのが要点。**
#: いまの実装はこの 2 つを `(2.0, 5.0)` という 1 つのレンジに混ぜる。
EXISTING_QUANTITY = "2"
PLANNED_QUANTITY = "5"


def _door_rows(quantity: str) -> tuple[tuple[str | None, ...], ...]:
    return (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", quantity),
    )


def _schedule_page(doc: pymupdf.Document, quantity: str) -> None:
    page = doc.new_page(width=1190, height=842)
    page.insert_text(
        pymupdf.Point(850, 800), "縮尺 1/50", fontname="japan", fontsize=11
    )
    draw_table(
        page,
        origin=(80.0, 120.0),
        col_widths=(110.0, 90.0, 80.0, 80.0, 70.0),
        row_height=24.0,
        rows=_door_rows(quantity),
        caption="建具表",
    )


def build_pdf(path: Path) -> Path:
    """4 枚。平面図 2 枚(現況・計画)と、建具表 2 枚(現況・計画)。"""
    doc = pymupdf.open()
    _vector_plan_page(doc)
    _vector_plan_page(doc)
    _schedule_page(doc, EXISTING_QUANTITY)
    _schedule_page(doc, PLANNED_QUANTITY)
    doc.save(path)
    doc.close()
    return path


#: B 組で渡す宣言。**A 組では何も渡さない。**
DECLARATIONS = (
    PageDeclaration(page_number=1, kind="平面図", phase="現況"),
    PageDeclaration(page_number=2, kind="平面図", phase="計画"),
    PageDeclaration(page_number=3, kind="建具表", phase="現況"),
    PageDeclaration(page_number=4, kind="建具表", phase="計画"),
)


def _phase_of(item: object) -> str | None:
    """読み(または数量)に付いた意味の `phase`。**無ければ None。**

    直す前のコードでは `meaning` が無い・`None` なので、ここは必ず None を返す。
    """
    meaning = getattr(item, "meaning", None)
    if meaning is None:
        return None
    return getattr(meaning, "phase", None)


def measure(pdf_path: Path, *, declare: bool) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        config = IntakeConfig(
            case_id="MEASURE-PHASE",
            pdf_path=pdf_path,
            answers_path=Path(tmp) / "answers.json",
            start_kit=StartKit(page_declarations=DECLARATIONS) if declare else None,
        )
        result = read_drawing(config)
        quantities = quantities_from_intake(result)

    findings = tuple(result.findings)
    return {
        "M1_対象名と値": {
            item.target: list(item.value_range) for item in sorted(
                findings, key=lambda f: f.target
            )
        },
        "M2_意味が付いた読み": sum(
            1 for item in findings if getattr(item, "meaning", None) is not None
        ),
        "M2_読みの総数": len(findings),
        "M3_意味のphaseの内訳": dict(
            sorted(Counter(str(_phase_of(item)) for item in findings).items())
        ),
        "M4_意味が付いた数量": sum(
            1 for item in quantities if getattr(item, "meaning", None) is not None
        ),
        "M4_数量の総数": len(quantities),
        "M5_階層ごとの件数": dict(
            sorted(
                Counter(str(d.tier) for d in result.decisions).items()
            )
        ),
        "M6_建具表の数量": {
            item.target: list(item.value_range)
            for item in sorted(findings, key=lambda f: f.target)
            if item.target.startswith("建具数量")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = build_pdf(Path(tmp) / "phase_visibility.pdf")
        report = {
            "A_宣言なし": measure(pdf_path, declare=False),
            "B_宣言あり": measure(pdf_path, declare=True),
        }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
