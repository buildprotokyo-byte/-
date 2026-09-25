"""室の輪郭と繰り返す記号も、人が宣言したページの種類で**止めない**。

PR #37(`tests/test_page_kind_is_a_weak_hint.py`)は開き戸の円弧について
「人が宣言したページの種類は弱い手がかりにとどめる」(原則4の条件3)に
直した。ところが同じ束の中の**室の輪郭**と**繰り返す記号**は、
`intake/drawing_intake.py` の中でまだ宣言を探索の範囲として使っていた
(2026-09-23 に見つかった。K-04 の 5)。

直す前の動き
------------
- 人が「平面図」以外(例:「仕上表」)と宣言したページでは
  `find_room_outlines()` を**呼びもしなかった**。
- 人が「平面図」「設備図」以外と宣言したページでは
  `find_repeated_symbols()` を**呼びもしなかった**。
- 宣言が誤っていても気づく手段が無く、そのページの室と記号は**黙って 0 件**。
- 宣言が**無い**ページは、直す前から全部探していた(ここは変わらない)。

直したあとの動き(このファイルが固定するもの)
--------------------------------------------
#37 の開き戸と同じ形にそろえる。

1. 宣言に関わらず、室の輪郭と繰り返す記号は常に探す。
2. 宣言と読み取りが食い違ったら(「仕上表」と宣言されたページで室の輪郭が
   出た)、読みは捨てずに出し、由来を ``assumed`` にする。
   見積の行では「仮説に基づく」になり、**自動確定には上がらない。**
3. 食い違いは `PendingDecision`(``page_kind_disagreement``)で人へ回す。
   **1 ページにつき 1 件にまとめる。** 開き戸・室・記号で別々に質問を
   立てると、同じ宣言について人へ 3 回聞くことになる。
4. 宣言と合っているページ・宣言が無いページでは何も変わらない。
5. 出なかったとき(0 件)は食い違いを立てない。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**
顧客の図面はリポジトリに置かない。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from estimating.basis import BASIS_HYPOTHETICAL, BASIS_INFERRED
from estimating.from_intake import quantities_from_intake
from intake.drawing_intake import (
    PAGE_KIND_DISAGREEMENT,
    IntakeConfig,
    read_drawing,
)
from intake.start_kit import PageDeclaration, StartKit
from tests.test_drawing_intake_schedules import _draw_one_arc
from tests.test_pdf_repeated_symbols import (
    PT_PER_MM_AT_50,
    _draw_outlet,
    _draw_switch,
)
from tests.test_pdf_room_outlines import _line, _mm


def _draw_plan(page: pymupdf.Page) -> None:
    """1/50 の平面図らしいページ。9000×6000mm の 1 室と、記号 2 種(3 個・2 個)。"""
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    x0, y0 = 150.0, 150.0
    x1, y1 = x0 + _mm(9000.0), y0 + _mm(6000.0)
    for segment in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        _line(page, *segment)
    page.insert_text(pymupdf.Point(x0 + 40, y0 + 40), "洋室", fontname="japan")
    size_pt = 200.0 * PT_PER_MM_AT_50
    for x in (250.0, 350.0, 450.0):
        _draw_outlet(page, x, 300.0, 0.0, size_pt)
    for x in (250.0, 350.0):
        _draw_switch(page, x, 400.0, 0.0, size_pt)


@pytest.fixture()
def plan(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_plan.pdf"
    doc = pymupdf.open()
    _draw_plan(doc.new_page(width=1190, height=842))
    doc.save(path)
    doc.close()
    return path


def _read(path: Path, tmp_path: Path, kind: str | None):
    start_kit = (
        StartKit(page_declarations=(PageDeclaration(page_number=1, kind=kind),))
        if kind is not None
        else StartKit()
    )
    return read_drawing(
        IntakeConfig(
            pdf_path=path,
            case_id="TEST-PAGE-KIND-GATE",
            answers_path=tmp_path / "answers.json",
            start_kit=start_kit,
        )
    )


def _rooms(result):
    return [item for item in result.findings if item.target.startswith("室::")]


def _symbols(result):
    return [item for item in result.findings if item.target.startswith("記号::")]


def _disagreements(result):
    return [
        item for item in result.pending_decisions if item.kind == PAGE_KIND_DISAGREEMENT
    ]


# ---------------------------------------------------------------------------
# 1. 宣言は読み取りを止めない
# ---------------------------------------------------------------------------


def test_room_outlines_are_still_read_on_a_page_declared_as_a_finish_table(
    plan: Path, tmp_path: Path
) -> None:
    """**原則4の条件3。** 「仕上表」と宣言されても室の輪郭は探す。"""
    rooms = _rooms(_read(plan, tmp_path, "仕上表"))
    assert rooms, "宣言を理由に室の輪郭の探索をやめてはいけない"
    assert rooms[0].target == "室::洋室::ページ1"


def test_repeated_symbols_are_still_read_on_a_page_declared_as_a_finish_table(
    plan: Path, tmp_path: Path
) -> None:
    symbols = _symbols(_read(plan, tmp_path, "仕上表"))
    assert sorted(item.value_range[0] for item in symbols) == [2.0, 3.0], (
        "宣言を理由に繰り返す記号の探索をやめてはいけない"
    )


def test_repeated_symbols_are_still_read_on_a_page_declared_as_a_legend(
    plan: Path, tmp_path: Path
) -> None:
    """「凡例」と宣言されたページでも、繰り返す記号は探す。

    **名前を凡例からしか取らない決まり(`LEGEND_PAGE_KINDS`)はそのまま。**
    変えたのは「凡例と宣言したら記号を数えない」ことだけ。
    """
    symbols = _symbols(_read(plan, tmp_path, "凡例"))
    assert sorted(item.value_range[0] for item in symbols) == [2.0, 3.0]


def test_the_notes_do_not_say_the_search_was_skipped(
    plan: Path, tmp_path: Path
) -> None:
    notes = _read(plan, tmp_path, "仕上表").pages[0].notes
    assert not any("室の輪郭は探さない" in note for note in notes)
    assert not any("繰り返す記号は探さない" in note for note in notes)
    assert any("仕上表" in note for note in notes), "宣言と食い違ったことをページの記録に残す"


# ---------------------------------------------------------------------------
# 2. 食い違った読みは「仮説に基づく」にし、自動確定させない
# ---------------------------------------------------------------------------


def test_a_disagreeing_room_outline_becomes_a_hypothesis(
    plan: Path, tmp_path: Path
) -> None:
    result = _read(plan, tmp_path, "仕上表")
    room = _rooms(result)[0]
    assert room.derivation == "assumed"
    assert room.provenance["declared_page_kind"] == "仕上表"
    assert room.provenance["declaration_conflict"] is True

    items = [item for item in quantities_from_intake(result) if item.target.startswith("室::")]
    assert items, "数量そのものは出す。止めない"
    assert all(item.basis == BASIS_HYPOTHETICAL for item in items)


def test_disagreeing_repeated_symbols_become_hypotheses(
    plan: Path, tmp_path: Path
) -> None:
    result = _read(plan, tmp_path, "仕上表")
    for symbol in _symbols(result):
        assert symbol.derivation == "assumed"
        assert symbol.provenance["declared_page_kind"] == "仕上表"
        assert symbol.provenance["declaration_conflict"] is True

    items = [item for item in quantities_from_intake(result) if item.target.startswith("記号::")]
    assert items
    assert all(item.basis == BASIS_HYPOTHETICAL for item in items)


def test_nothing_is_auto_confirmed_from_a_disagreeing_room_or_symbol(
    plan: Path, tmp_path: Path
) -> None:
    result = _read(plan, tmp_path, "仕上表")
    assert not [
        decision
        for decision in result.decisions
        if decision.target.startswith(("室::", "記号::"))
        and (decision.confirmed or decision.tier == 1)
    ]


# ---------------------------------------------------------------------------
# 3. 食い違いは人の判断へ回す。1 ページ 1 件にまとめる
# ---------------------------------------------------------------------------


def test_the_disagreement_is_recorded_once_for_the_page(
    plan: Path, tmp_path: Path
) -> None:
    disagreements = _disagreements(_read(plan, tmp_path, "仕上表"))
    assert len(disagreements) == 1, "同じページの同じ宣言について、質問は 1 件"
    observed = dict(disagreements[0].observed)
    assert disagreements[0].page_number == 1
    assert "仕上表" in disagreements[0].detail
    assert observed["室の輪郭"] == 1.0
    assert observed["繰り返す記号の群"] == 2.0


def test_door_arc_room_and_symbol_disagreements_share_one_question(
    tmp_path: Path,
) -> None:
    """#37 の開き戸の食い違いと同じページなら、同じ 1 件に足す。"""
    path = tmp_path / "plan_with_arc.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    _draw_plan(page)
    _draw_one_arc(page)
    doc.save(path)
    doc.close()

    disagreements = _disagreements(_read(path, tmp_path, "建具表"))
    assert len(disagreements) == 1
    observed = dict(disagreements[0].observed)
    assert observed["開き戸の円弧"] == 1.0
    assert observed["室の輪郭"] >= 1.0
    assert observed["繰り返す記号の群"] == 2.0


def test_equipment_page_symbols_agree_but_rooms_are_flagged(
    plan: Path, tmp_path: Path
) -> None:
    """「設備図」は記号が出てよいページ。室の輪郭は見込んでいないので回す。"""
    result = _read(plan, tmp_path, "設備図")
    assert all(item.derivation == "read" for item in _symbols(result))
    assert all(item.provenance["declaration_conflict"] is False for item in _symbols(result))
    assert [item.derivation for item in _rooms(result)] == ["assumed"]
    observed = dict(_disagreements(result)[0].observed)
    assert "繰り返す記号の群" not in observed
    assert observed["室の輪郭"] == 1.0


# ---------------------------------------------------------------------------
# 4. 合っているとき・宣言が無いとき・何も出ないときは変わらない
# ---------------------------------------------------------------------------


def test_a_plan_declared_as_a_plan_changes_nothing(plan: Path, tmp_path: Path) -> None:
    result = _read(plan, tmp_path, "平面図")
    assert _rooms(result) and _symbols(result)
    for item in _rooms(result) + _symbols(result):
        assert item.derivation == "read"
        assert item.provenance["declaration_conflict"] is False
    assert not _disagreements(result)
    items = [
        item
        for item in quantities_from_intake(result)
        if item.target.startswith(("室::", "記号::"))
    ]
    assert items and all(item.basis == BASIS_INFERRED for item in items)


def test_no_declaration_behaves_as_before(plan: Path, tmp_path: Path) -> None:
    result = _read(plan, tmp_path, None)
    assert _rooms(result) and _symbols(result)
    for item in _rooms(result) + _symbols(result):
        assert item.derivation == "read"
        assert item.provenance["declared_page_kind"] is None
    assert not result.pending_decisions


def test_nothing_found_raises_no_disagreement(tmp_path: Path) -> None:
    """**0 件なら食い違いは無い。** 捨てられた読みが無いため。"""
    path = tmp_path / "text_only.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    page.insert_text(pymupdf.Point(100, 100), "特記仕様 1. 一般事項", fontname="japan")
    doc.save(path)
    doc.close()

    result = _read(path, tmp_path, "その他")
    assert not _rooms(result)
    assert not _symbols(result)
    assert not _disagreements(result)
