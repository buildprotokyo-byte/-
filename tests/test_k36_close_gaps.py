"""K-36(2026-09-25)で塞いだ 3 つの穴。**合成データと差し替えだけを使う。**

守りたいこと

1. **「その他」(判断できない)と正直に宣言したページで、読み取りを止めない。**
   周36 の実測で、34 ページを「その他」と宣言すると入口の数量が
   2,887 → 166 件(94% 減)になった。宣言しなければ関門は無いので、
   **正直に答えるほど損をする作り**だった。
   「その他」は宣言しなかったのと同じに扱う。**ほかの種類の関門は変えない**
   (展開図・建具表と宣言したページの扱いは PR #100 の判断待ち)。
2. **巾木の数量の名前が、作る側と探す側で一致している。**
   周28 の実測で、`app.py` は `周長` を探し、`from_room_dimensions` は
   `室の周長` を作っていたので、人が寸法を入れても巾木の行は永久に空だった。
3. **規則ファイルが無くても、入口の数量を捨てない。**
   周31 で、規則ファイルが無いと入口の数量は丸ごと落ち、`gaps` に 1 行
   残るだけだった。**捨てずに「規則なし」の印を付けて残す。**
   行にはしない(規則が無いので当てはめない)。判定には触らない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app
from intake import drawing_intake
from intake.start_kit import PageDeclaration


# ---------------------------------------------------------------------------
# 1. 「その他」は関門にならない
# ---------------------------------------------------------------------------


class _Called(Exception):
    pass


def _raise_called(*_args, **_kwargs):
    raise _Called


def _room_outline(kind: str | None, monkeypatch) -> bool:
    monkeypatch.setattr(drawing_intake, "find_room_outlines", _raise_called)
    declaration = PageDeclaration(page_number=1, kind=kind) if kind else None
    try:
        drawing_intake._room_outline_findings(
            Path("unused.pdf"), 0, 1, None, declaration, []
        )
    except _Called:
        return True
    return False


def _repeated_symbols(kind: str | None, monkeypatch) -> bool:
    monkeypatch.setattr(drawing_intake, "find_repeated_symbols", _raise_called)
    declaration = PageDeclaration(page_number=1, kind=kind) if kind else None
    try:
        drawing_intake._collect_symbols(
            Path("unused.pdf"),
            0,
            page_number=1,
            scale=None,
            declaration=declaration,
            notes=[],
            symbol_clusters=[],
            legend_symbols=[],
        )
    except _Called:
        return True
    return False


def test_a_page_declared_as_unknown_is_still_searched_for_room_outlines(monkeypatch) -> None:
    assert _room_outline("その他", monkeypatch) is True


def test_a_page_declared_as_unknown_is_still_searched_for_repeated_symbols(monkeypatch) -> None:
    assert _repeated_symbols("その他", monkeypatch) is True


@pytest.mark.parametrize("kind", [None, "平面図"])
def test_undeclared_and_plan_pages_are_searched_as_before(kind, monkeypatch) -> None:
    assert _room_outline(kind, monkeypatch) is True
    assert _repeated_symbols(kind, monkeypatch) is True


def test_the_other_declared_kinds_keep_their_gates(monkeypatch) -> None:
    """**この変更は「その他」だけ。** ほかの種類の関門は PR #100 の判断待ち。"""
    assert _room_outline("展開図", monkeypatch) is False
    assert _repeated_symbols("建具表", monkeypatch) is False


# ---------------------------------------------------------------------------
# 2. 巾木の名前
# ---------------------------------------------------------------------------


def test_every_kind_the_finish_lines_look_for_is_a_kind_the_room_input_makes() -> None:
    from estimating.from_room_dimensions import quantities_from_room_dimensions
    from intake.room_dimensions import RoomDimension

    made = {
        q.target.partition("::")[0]
        for q in quantities_from_room_dimensions(
            (
                RoomDimension(
                    room_name="洋室1",
                    length_mm=3600,
                    width_mm=2700,
                    ceiling_height_mm=2400,
                    entered_by="テスト",
                ),
            )
        ).quantities
    }
    wanted = {kind for kind, _unit, _note in app.FINISH_PART_QUANTITY.values()}
    assert wanted <= made, f"探しているのに作られない名前: {sorted(wanted - made)}"


# ---------------------------------------------------------------------------
# 3. 規則が無くても数量を捨てない
# ---------------------------------------------------------------------------


def test_without_rules_the_intake_quantities_are_kept_with_a_no_rule_mark(
    tmp_path: Path,
) -> None:
    import pymupdf

    from tests.test_drawing_intake import _vector_plan_page

    path = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)  # 縮尺・印字の面積・開き戸の円弧が入口の数量になる
    doc.save(path)
    doc.close()

    result = app.run(
        path,
        case_id="K36-TEST",
        answers_path=tmp_path / "a.json",
        build_ledger_stage=False,
    )

    kept = result.kept_quantities
    assert len(kept) == result.extras["入口の数量"]
    assert kept, "合成の図面でも入口の数量が 1 件は出るはず"
    assert {row["印"] for row in kept} == {app.MARK_NO_RULES}
    for row in kept:
        assert row["対象"]
        assert row["単位"]
    # 行にはしない。自動確定も 0 件のまま。
    assert not any(line.path == app.PATH_INTAKE for line in result.lines)
    assert result.auto_confirmed_total == 0
    assert result.as_dict()["行にしなかった数量"]["件数"] == len(kept)
