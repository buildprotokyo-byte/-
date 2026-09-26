"""K-45(おーちゃん 2026-09-26)の決め直しを守るテスト。**合成のデータだけを使う。**

守りたいこと

1. 仕上表の「下地が既存・仕上の欄が空欄」は、**推奨なしの問い**になる。
   本番の道(`app.py`)で行にならず、黙って消えずに問いの中身として出る。
2. 撤去の網の面ごとに「床組 新設」「天井組 新設」「天井 石膏ボード 張」を
   **要確認**として出す。面積は撤去の網と同じ。**自動確定は 0 件のまま。**
3. 「特記がなければ既存のまま」(行政基準・候補)を使ったものには印が付く。
   **案件の凡例と食い違えば、案件の凡例が優先する。**
4. 波及で出たものは「要確認」に留める(文書の決まりとして残っていること)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app
from knowledge.table import (
    BINDING_PROJECT_LEGEND,
    BINDING_PUBLIC_STANDARD,
    KnowledgeError,
    load_knowledge,
    parse_knowledge,
    resolve_conflict,
    usage_mark,
)
from tests.test_k42_notes_hatch import BLUE_RECT, MM_PER_PT_1_50, _full_pdf, _run
from tests.test_pdf_tables import single_table_pdf

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 1. 仕上の欄が空欄 → 推奨なしの問い
# ---------------------------------------------------------------------------

BLANK_FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上"),
    ("洋室1", "床", "既存", "フローリング"),
    ("洋室1", "廻縁", "既存", ""),
)


def test_app_turns_a_blank_finish_into_a_question_not_a_line(tmp_path: Path) -> None:
    pdf = single_table_pdf(
        tmp_path / "finish.pdf", BLANK_FINISH_ROWS, col_widths=(120.0, 90.0, 90.0, 180.0)
    )
    lines, _counts, extra = app._finish_lines(pdf, [1], {})

    assert not any(line.work_item.startswith("廻縁") for line in lines)
    assert extra["仕上表の問い"] == 1
    assert extra["仕上表の読み"].get("工事なし", 0) == 0
    (question,) = extra["仕上表の問いの中身"]
    assert question["問い"] == (
        "洋室1・廻縁の仕上の記載が見当たりません。この部位の工事はどうなりますか。"
    )
    assert question["推奨"] is None


# ---------------------------------------------------------------------------
# 2. 撤去の網から、新設 3 行を要確認として出す
# ---------------------------------------------------------------------------


def test_hatch_regions_also_give_three_needs_check_rows(tmp_path: Path) -> None:
    # K-46: 線引きは AI の判定。ここでは網の 5 行を AI が「工事の行」とした形で渡す
    # (判定が無いと全部の行が要確認になり、撤去の行が候補のままかを確かめられない)。
    from estimating.line_judge import AIJudgmentFileJudge

    judge = AIJudgmentFileJudge(
        [{"工事": work, "判定": "工事の行", "理由": "合成"}
         for work in ("床組 撤去", "天井組 撤去", *app.HATCH_INFERRED_NEW_WORKS)]
    )
    result = _run(_full_pdf(tmp_path / "full.pdf"), tmp_path, line_judge=judge)

    assert result.auto_confirmed_total == 0
    rows = [line for line in result.lines if line.path == app.PATH_DEMOLITION_HATCH]
    removal = [line for line in rows if line.work_item.endswith("撤去")]
    inferred = [line for line in rows if line.work_item in app.HATCH_INFERRED_NEW_WORKS]

    assert [line.work_item for line in inferred] == [
        "床組 新設", "天井組 新設", "天井 石膏ボード 張"
    ]
    x0, y0, x1, y1 = BLUE_RECT
    expected = (x1 - x0) * (y1 - y0) * (MM_PER_PT_1_50 / 1000.0) ** 2
    for line in inferred:
        assert line.quantity == pytest.approx(expected, rel=0.02)
        assert line.quantity == removal[0].quantity
        assert line.place == removal[0].place
        # AI が工事の行と判定しても、推し量った行は要確認のまま(線引きは確かさを上げない)
        assert line.certainty == app.CERTAINTY_NEEDS_CHECK
        answer = line.as_answer_row(1)
        assert answer["確かさ"] == "要確認"
        assert "撤去の範囲を新設の範囲と推し量った。確定しない" in answer["根拠"][0]["根拠"]
        assert "撤去の網の面積" in answer["備考"]
    # 撤去の行は元のまま「候補」
    for line in removal:
        assert line.certainty == app.CERTAINTY_CANDIDATE


def test_hatch_needs_check_rows_confirm_nothing_in_the_output(tmp_path: Path) -> None:
    payload = _run(_full_pdf(tmp_path / "full.pdf"), tmp_path).as_dict()

    assert payload["自動確定"]["合計"] == 0
    certainties = {row["工事項目"]: row["確かさ"] for row in payload["工事項目"]}
    for work in app.HATCH_INFERRED_NEW_WORKS:
        assert certainties[work] == "要確認"
    assert "確定" not in set(certainties.values())


def test_hatch_needs_check_rows_have_no_area_without_a_scale(tmp_path: Path) -> None:
    result = _run(_full_pdf(tmp_path / "bare.pdf", legend=False, scale=False), tmp_path)

    inferred = [line for line in result.lines if line.work_item in app.HATCH_INFERRED_NEW_WORKS]
    assert len(inferred) == 3
    assert all(line.quantity is None for line in inferred)  # 推測で埋めない
    assert result.auto_confirmed_total == 0


# ---------------------------------------------------------------------------
# 3. 「特記がなければ既存のまま」と、案件の凡例の優先
# ---------------------------------------------------------------------------

CANDIDATES = ROOT / "benchmarks" / "fixtures" / "knowledge_candidates_encoded.json"


def test_the_public_standard_rule_is_in_the_table_as_a_candidate() -> None:
    table = load_knowledge(CANDIDATES)
    entry = next(e for e in table.entries if e.source.clause == "6.1.3(5)")

    assert entry.source.binding == BINDING_PUBLIC_STANDARD == "行政基準"
    assert entry.is_candidate
    assert "既存のまま" in entry.statement
    assert "特記" in entry.overridden_by
    assert BINDING_PROJECT_LEGEND in entry.overridden_by
    assert "案件の凡例が優先" in entry.note
    assert "特記なき場合は新設とする" in entry.note


def test_anything_made_with_the_rule_carries_a_mark() -> None:
    table = load_knowledge(CANDIDATES)
    entry = next(e for e in table.entries if e.source.clause == "6.1.3(5)")

    mark = usage_mark(entry)
    assert entry.entry_id in mark
    assert "行政基準" in mark
    assert "候補" in mark
    assert "6.1.3(5)" in mark


def _entry(entry_id: str, binding: str, statement: str) -> dict:
    return {
        "entry_id": entry_id,
        "kind": "波及",
        "statement": statement,
        "source": {
            "document": "架空の資料",
            "clause": "1.1",
            "url": "",
            "read_directly": True,
            "publisher": "架空の発行元",
            "edition": "架空版",
            "checked_on": None,
            "binding": binding,
        },
        "adoption_status": "候補",
        "confidence": "一次資料",
        "scope": "住宅改修",
        "applies_to": {"parts": ["天井", "壁"]},
        "needs_human_check": True,
        "detail": {"trigger": "天井を撤去する", "affected": [], "extent": {"text": "架空"}, "symmetric": False},
    }


def _table(*entries: dict):
    return parse_knowledge(
        {"format_version": 2, "table_id": "k45-test", "synthetic": True, "entries": list(entries)}
    )


def test_the_project_legend_wins_over_the_public_standard_on_conflict() -> None:
    table = _table(
        _entry("STD", "行政基準", "天井を撤去しても、取り合う壁面は特記がなければ既存のままとする。"),
        _entry("LEG", "案件の凡例", "特記なき場合は新設とする。"),
    )
    standard, legend = table.entries

    for first, second in ((standard, legend), (legend, standard)):
        conflict = resolve_conflict(first, second)
        assert conflict.winner.entry_id == "LEG"
        assert conflict.loser.entry_id == "STD"
        assert "案件の凡例が優先" in conflict.reason
        assert conflict.as_dict()["loser_binding"] == "行政基準"  # 負けた側も残す


def test_without_a_legend_the_stronger_binding_wins_and_ties_are_refused() -> None:
    table = _table(
        _entry("LAW", "法令", "架空の法令。"),
        _entry("STD", "行政基準", "架空の基準。"),
        _entry("STD2", "行政基準", "架空の基準 その 2。"),
    )
    law, standard, standard2 = table.entries

    assert resolve_conflict(standard, law).winner.entry_id == "LAW"
    with pytest.raises(KnowledgeError):
        resolve_conflict(standard, standard2)


# ---------------------------------------------------------------------------
# 4. 波及は要確認に留める(文書の決まり)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["docs/principles/start_kit.md", "docs/prompts/03_最適解AI_まとめる.md"],
)
def test_the_spread_rule_now_says_needs_check_only(path: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")

    assert "K-45" in text
    assert "~~" in text  # 元の文は消さずに打ち消し線で残す
    for line in text.splitlines():
        if "想定して見積もる" in line and "K-45" not in line:
            assert line.strip().lstrip("- ").startswith("~~"), line
