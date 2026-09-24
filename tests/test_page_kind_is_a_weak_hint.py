"""人が宣言したページの種類は**弱い手がかり**であって、読み方を縛らない。

原則4(`docs/principles/start_kit.md`)の条件3「図面の読み方を縛らない」と、
原則5「人が与えた情報も、図面の中身と突き合わせて矛盾を探す」を固定する。

直す前の動き(`intake/drawing_intake.py`)
----------------------------------------
人が「このページは建具表です」と宣言すると、そのページでは
`find_door_arcs()` を**呼ぶことすらしなかった。** 宣言が誤っていても
検出する手段が無く、そのページの開き戸は**黙って 0 件**になった。
人の 1 回の入力が、図面の読み方そのものを縛っていた。

直したあとの動き(このファイルが固定するもの)
--------------------------------------------
1. **宣言に関わらず、円弧の探索は常に行う。**
2. 宣言と読み取りが食い違ったら(「建具表」と宣言されたページで円弧が出た)、
   **片方を黙って捨てず**、食い違いとして記録し人の判断へ回す。
3. 食い違った読みは**止めずに出す**(原則5)。ただし宣言と衝突している分だけ
   寄りかかっている前提が増えるので、由来を ``assumed`` にして
   見積の行では「仮説に基づく」になり、**自動確定には上がらない。**
4. 宣言と読み取りが**合っている**ページでは、何も変わらない。
5. 宣言が無いページでも、何も変わらない。

削除したテストと、その心配事の引き継ぎ先
----------------------------------------
`tests/test_intake_start_kit.py` にあった
`test_door_arcs_are_not_hunted_on_a_page_declared_as_a_table` は、
**2026-09-22 に削除した**(おーちゃんの判断、案A)。
「建具表と宣言されたら開き戸は 0 件」を正しい振る舞いとして固定していたが、
**その振る舞い自体が原則4の条件3に反していた**ので、書き換えではなく取り消した。

**ただし、元のテストが心配していたことは消えていない。**
docstring にあった「建具表と宣言されたページで円弧を探すと、罫線や記号を
拾いうる」は本当に起きる。この修正の立場は、それを**0 件にすることではなく、
自動確定させず人に回すこと**で扱う、である。受け持ちは次の 4 件。

===================================================  ======================
元のテストの心配事                                    受け持つテスト
===================================================  ======================
拾った件数が事実として下流へ行かないこと              `test_a_reading_that_disagrees_becomes_a_hypothesis`
自動で確定してしまわないこと                          `test_nothing_is_auto_confirmed_from_a_disagreeing_reading`
人が気づけること                                      `test_the_disagreement_is_recorded_for_a_human`
表かもしれないという材料が残ること                    `test_a_table_read_on_the_same_page_is_recorded_as_supporting_the_declaration`
===================================================  ======================

元のテストの 2 つ目の `assert`(ページの記録に「建具表」の語が残る)は、
`test_the_note_says_the_reading_was_not_stopped` が引き継いでいる。

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
from tests.test_drawing_intake_schedules import _draw_one_arc, _schedule_page


def _pdf(tmp_path: Path, build) -> Path:
    path = tmp_path / "synthetic_page_kind.pdf"
    doc = pymupdf.open()
    build(doc)
    doc.save(path)
    doc.close()
    return path


def _config(
    path: Path, tmp_path: Path, start_kit: StartKit | None = None
) -> IntakeConfig:
    return IntakeConfig(
        pdf_path=path,
        case_id="TEST-PAGE-KIND",
        answers_path=tmp_path / "answers.json",
        start_kit=start_kit or StartKit(),
    )


@pytest.fixture()
def schedule_with_an_arc(tmp_path: Path) -> Path:
    """本物の建具表の紙に、開き戸の円弧が 1 つ描かれているページ。

    実図面でも起きる形である(建具表の紙に姿図が載る)。宣言が正しくても
    円弧は出るので、**宣言が誤っている場合と正しい場合の両方**をここで扱う。
    """
    return _pdf(
        tmp_path,
        lambda doc: (_schedule_page(doc), _draw_one_arc(doc.load_page(0))),
    )


def _door_arc_findings(result):
    return [item for item in result.findings if item.target.startswith("開き戸")]


# ---------------------------------------------------------------------------
# 1. 宣言は読み取りを止めない
# ---------------------------------------------------------------------------


def test_door_arcs_are_still_read_on_a_page_declared_as_a_table(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """**原則4の条件3。** 人の宣言で読み取りの範囲を狭めない。"""
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    findings = _door_arc_findings(result)
    assert findings, "宣言を理由に円弧の探索をやめてはいけない"
    assert findings[0].value_range == (1.0, 1.0)


def test_the_arc_keeps_its_evidence_even_when_the_declaration_disagrees(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """座標も元の縮尺も残る。**証拠を落とさない。**"""
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    provenance = _door_arc_findings(result)[0].provenance
    assert provenance["page_number"] == 1
    assert provenance["arcs"], "円弧ごとの座標が残っていない"
    assert provenance["declared_page_kind"] == "建具表"
    assert provenance["declaration_conflict"] is True


# ---------------------------------------------------------------------------
# 2. 食い違いは黙って捨てず、人の判断へ回す
# ---------------------------------------------------------------------------


def test_the_disagreement_is_recorded_for_a_human(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    disagreements = [
        item
        for item in result.pending_decisions
        if item.kind == PAGE_KIND_DISAGREEMENT
    ]
    assert len(disagreements) == 1
    assert disagreements[0].page_number == 1
    assert "建具表" in disagreements[0].detail
    assert dict(disagreements[0].observed)["開き戸の円弧"] == 1.0


def test_a_table_read_on_the_same_page_is_recorded_as_supporting_the_declaration(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """**人の宣言のほうが正しい場合もある。** 材料を両方残す。

    このページでは建具表が実際に表として読めている。円弧が表の罫線や
    姿図の記号かもしれない、という判断材料になるので、食い違いの記録に残す。
    """
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    disagreement = next(
        item
        for item in result.pending_decisions
        if item.kind == PAGE_KIND_DISAGREEMENT
    )
    assert dict(disagreement.observed)["読めた建具表"] == 1.0


def test_the_note_says_the_reading_was_not_stopped(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    notes = result.pages[0].notes
    assert any("建具表" in note for note in notes)
    assert not any("開き戸は探さない" in note for note in notes), (
        "宣言を理由に探索をやめたと書いてはいけない"
    )


# ---------------------------------------------------------------------------
# 3. 止めずに出すが、自動確定には上げない(原則5)
# ---------------------------------------------------------------------------


def test_a_reading_that_disagrees_becomes_a_hypothesis(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """宣言と衝突した読みは、見積の行で「仮説に基づく」になる。"""
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    assert _door_arc_findings(result)[0].derivation == "assumed"

    items = [
        item
        for item in quantities_from_intake(result)
        if item.target.startswith("開き戸")
    ]
    assert items, "数量そのものは出す。止めない"
    assert items[0].basis == BASIS_HYPOTHETICAL


def test_nothing_is_auto_confirmed_from_a_disagreeing_reading(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="建具表"),)
            ),
        )
    )

    assert not [
        decision
        for decision in result.decisions
        if decision.target.startswith("開き戸") and decision.confirmed
    ]


# ---------------------------------------------------------------------------
# 4. 合っているとき・宣言が無いときは何も変わらない
# ---------------------------------------------------------------------------


def test_a_declaration_that_agrees_with_the_drawing_changes_nothing(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """「平面図」と宣言されたページの円弧は、今までどおり読んだ値のまま。"""
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="平面図"),)
            ),
        )
    )

    finding = _door_arc_findings(result)[0]
    assert finding.derivation == "read"
    assert finding.provenance["declaration_conflict"] is False
    assert not [
        item
        for item in result.pending_decisions
        if item.kind == PAGE_KIND_DISAGREEMENT
    ]

    items = [
        item
        for item in quantities_from_intake(result)
        if item.target.startswith("開き戸")
    ]
    assert items[0].basis == BASIS_INFERRED


def test_no_declaration_behaves_as_before(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(schedule_with_an_arc, tmp_path))

    finding = _door_arc_findings(result)[0]
    assert finding.derivation == "read"
    assert finding.provenance["declared_page_kind"] is None
    assert not result.pending_decisions


def test_a_declaration_with_no_arcs_raises_no_disagreement(tmp_path: Path) -> None:
    """**円弧が 0 件なら食い違いは無い。** 捨てられた読みが無いため。

    ここで食い違いを立てると、正しい宣言のたびに人へ質問が飛ぶ。
    """
    path = _pdf(tmp_path, lambda doc: _schedule_page(doc))
    result = read_drawing(
        _config(
            path,
            tmp_path,
            StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="仕上表"),)
            ),
        )
    )

    assert not _door_arc_findings(result)
    assert not [
        item
        for item in result.pending_decisions
        if item.kind == PAGE_KIND_DISAGREEMENT
    ]


# ---------------------------------------------------------------------------
# 5. 現況/計画の宣言は、これまでどおり対象名に残る
# ---------------------------------------------------------------------------


def test_the_phase_declaration_still_reaches_the_reading(
    schedule_with_an_arc: Path, tmp_path: Path
) -> None:
    """**この修正は種類(kind)だけを扱う。** 現況/計画(phase)の宣言は届き続ける。

    **確かめ方を 2026-09-23 に変えた。** 元は
    `assert ... .target == "開き戸::現況::ページ1"` で、phase が対象名に
    入っていることを固定していた。phase は意味の4欄(`Meaning.phase`)へ移したので、
    **届くことを確かめる先をそちらに変える。狙いは変えない**
    (`docs/principles/scope_of_work_diff.md` 3-2、3-3)。
    """
    result = read_drawing(
        _config(
            schedule_with_an_arc,
            tmp_path,
            StartKit(
                page_declarations=(
                    PageDeclaration(page_number=1, kind="建具表", phase="現況"),
                )
            ),
        )
    )

    finding = _door_arc_findings(result)[0]
    assert finding.target == "開き戸::ページ1"
    assert finding.meaning is not None
    assert finding.meaning.phase == "現況"
