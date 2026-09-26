"""`app.py`(K-27 の一本通す入口)の通しテスト。**合成データだけを使う。**

守りたいこと

1. 図面の PDF を渡すと、7 つの段が止まらずに最後まで動き、見積の行の候補が出る。
2. **自動確定は 0 件のまま**(入口の判定と当てはめの確定行の両方を数える)。
3. **数字を作らない。** 人の入力が無いときは、仕上表の行の数量は空で、
   「人の入力待ち」と印が付く。入力があれば、その室の数量が入る。
4. **段を飛ばさない。** 色の意味が決まらない記号や「既存のまま」の記号は行にしない。
5. 凡例のページと表題欄の語は数えない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

import app
from tests.test_pdf_tables import draw_table

FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上"),
    ("洋室1", "床", "既存", "フローリング"),
    ("", "壁", "交換", "ビニルクロス"),
    ("", "天井", "既存", "既存"),
)

RED = (1.0, 0.0, 0.0)
BLACK = (0.0, 0.0, 0.0)


def _build_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    # 1 ページ目: 仕上表と平面の記号
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(850, 700), "縮尺 1/50", fontname="japan", fontsize=11)
    draw_table(
        page,
        origin=(80.0, 120.0),
        col_widths=(120.0, 90.0, 90.0, 180.0),
        row_height=24.0,
        rows=FINISH_ROWS,
        caption="内装仕上表",
    )
    for x, colour in ((600, RED), (700, RED), (800, BLACK)):
        page.insert_text(pymupdf.Point(x, 300), "CX", fontsize=9, color=colour)
    # 表題欄(下端 12%)の語は数えない
    page.insert_text(pymupdf.Point(600, 820), "CX", fontsize=9, color=RED)
    # 2 ページ目: 凡例(対照表の出どころなので数えない)
    legend = doc.new_page(width=1190, height=842)
    legend.insert_text(pymupdf.Point(100, 100), "CX", fontsize=9, color=RED)
    doc.save(path)
    doc.close()
    return path


def _legend_table(path: Path) -> Path:
    payload = {
        "binding": "案件の凡例",
        "work_marks": [],
        "symbols": [
            {"code": "CX", "name": "コンセント", "group": "スイッチ・コンセント", "source_page": 2}
        ],
        "line_colors": [
            {"color": list(RED), "label": "赤", "meaning": "交換・新設を表す", "source_page": 2},
            {"color": list(BLACK), "label": "黒", "meaning": "既存のまま", "source_page": 2},
        ],
        "line_styles": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _human(path: Path) -> Path:
    payload = {
        "rooms": [
            {
                "room_name": "洋室1",
                "length_mm": 3600,
                "width_mm": 2700,
                "ceiling_height_mm": 2400,
                "entered_by": "テスト",
            }
        ]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture()
def pdf(tmp_path: Path) -> Path:
    return _build_pdf(tmp_path / "plan.pdf")


def _run(pdf: Path, tmp_path: Path, **kwargs) -> app.OnePassResult:
    return app.run(
        pdf,
        case_id="K27-TEST",
        answers_path=tmp_path / "answers.json",
        legend_table=_legend_table(tmp_path / "legend.json"),
        **kwargs,
    )


def _by_name(result: app.OnePassResult) -> dict[str, app.EstimateLine]:
    return {line.work_item: line for line in result.lines}


def test_all_stages_run_and_nothing_is_auto_confirmed(pdf: Path, tmp_path: Path) -> None:
    result = _run(pdf, tmp_path)

    assert result.auto_confirmed_total == 0
    assert set(result.stages) >= {app.PATH_FINISH, app.PATH_LEGEND, app.PATH_INTAKE, app.PATH_HUMAN}
    assert result.ledger["動かした"] is True
    assert result.lines, "行が 1 行も出ていない"


def test_finish_lines_wait_for_human_input_instead_of_inventing_a_number(
    pdf: Path, tmp_path: Path
) -> None:
    lines = _by_name(_run(pdf, tmp_path))

    assert set(lines) >= {"床 フローリング 改修", "壁 撤去", "壁 ビニルクロス 新設"}
    for name in ("床 フローリング 改修", "壁 撤去", "壁 ビニルクロス 新設"):
        assert lines[name].quantity is None
        assert lines[name].waits_for_human is True
        assert lines[name].place == "洋室1"
    # 「既存のまま」の天井は行にしない
    assert not any(name.startswith("天井") for name in lines)


def test_human_room_dimensions_fill_the_finish_lines(pdf: Path, tmp_path: Path) -> None:
    lines = _by_name(_run(pdf, tmp_path, human_input=_human(tmp_path / "human.json")))

    floor = lines["床 フローリング 改修"]
    assert floor.quantity == pytest.approx(3.6 * 2.7, abs=0.01)
    assert floor.waits_for_human is False
    wall = lines["壁 ビニルクロス 新設"]
    assert wall.quantity == pytest.approx(2 * (3.6 + 2.7) * 2.4, abs=0.05)


def test_legend_symbols_are_counted_by_colour_meaning(pdf: Path, tmp_path: Path) -> None:
    result = _run(pdf, tmp_path)
    lines = _by_name(result)

    # 赤の 2 つだけ。黒(既存のまま)・表題欄・凡例のページのものは数えない。
    assert lines["コンセント 交換・新設"].quantity == 2
    assert not any("既存のまま" in name for name in lines)
    legend = result.stages[app.PATH_LEGEND]
    assert legend[app.STAGE_READ] == 3
    assert legend[app.STAGE_UNDERSTAND] == 3
    assert legend[app.STAGE_ASSEMBLE] == 1


def test_without_a_legend_table_the_stage_says_it_did_not_run(pdf: Path, tmp_path: Path) -> None:
    result = app.run(pdf, case_id="K27-TEST", answers_path=tmp_path / "a.json")

    assert result.stages[app.PATH_LEGEND][app.STAGE_RECOGNIZE] == 0
    assert "動かしていない" in result.extras["凡例"]
    assert any("規則ファイルが無い" in gap for gap in result.gaps)


def test_the_cli_writes_the_answer_rows(pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    code = app.main(
        [
            str(pdf),
            "--case-id",
            "K27-TEST",
            "--out",
            str(out),
            "--legend-table",
            str(_legend_table(tmp_path / "legend.json")),
            "--no-ledger",
        ]
    )

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["自動確定"]["合計"] == 0
    rows = payload["工事項目"]
    assert rows and all({"番号", "工事項目", "場所", "数量", "単位", "根拠"} <= set(r) for r in rows)
    assert payload["繋げなかった部品"]
