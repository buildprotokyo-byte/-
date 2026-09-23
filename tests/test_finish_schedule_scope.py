"""`estimating/finish_schedule_scope.py` の回帰テスト。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**
実図面と実見積は、匿名化済みでもテストに入れない。

この部品がやるのは 1 つだけ。**内装仕上表の 1 行を、おーちゃんが決めた
読み方の表(K-05)に従って区分に割り当てること。** 守りたいのは 6 つ。

1. 読み方の表のとおりに区分が付くこと(6 通り)。
2. **下地欄が空欄の継続行を、自動で判定しないこと。** 問いとして出す。
   アクセントクロス(同じ壁の別の面。面積が分かれる)と
   出隅コーナー材(巾木に付属する部材。長さの単位が違う)で意味が違う。
3. **数量を 1 件も作らないこと。** 面積と長さは人の入力から来る。
4. 根拠に**ページ番号と行番号**が必ず入ること。
5. **下地の列が無い表では、工事の有無を名乗らないこと**(全行が問い)。
6. 読み取り側が「部位も仕上も空」として落とした行も、**継続行として
   拾い直すこと。** 落としたままだと問いが 10 件消える。
7. **「撤去して新設」の行は、要素 2 つ(撤去・新設)になること。**
   おーちゃんの回答(札、2026-09-23 15:11)。どちらの要素も根拠には
   **同じ仕上表のページと行番号**が入る。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axes.image_axis.schedule_tables import read_finish_schedules
from estimating.finish_schedule_scope import (
    READING_FINISH_ONLY,
    READING_FROM_BASE,
    READING_NO_WORK,
    READING_QUESTION,
    READING_REPLACE,
    assign_finish_schedule_scope,
)
from estimating.scope_diff import (
    WORK_ALTERED,
    WORK_AS_IS,
    WORK_NEW,
    WORK_REMOVAL,
    WORK_UNDECIDED,
)
from tests.test_pdf_tables import single_table_pdf

COL_WIDTHS = (90.0, 70.0, 90.0, 110.0, 130.0)

#: 読み方の表の 6 通りを 1 行ずつ。**列は 室名/部位/下地/仕上/メーカー。**
SIX_READINGS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上", "メーカー"),
    ("洋室1", "床", "既存", "既存", ""),
    ("洋室1", "壁", "既存", "ビニルクロス", "架空社"),
    ("洋室1", "天井", "交換", "ビニルクロス", "架空社"),
    ("洋室1", "巾木", "ー", "", ""),
    ("洋室1", "廻縁", "既存", "", ""),
    ("洋室1", "腰壁", "合板12mm", "タイル", "架空社"),
)


def _schedule(path: Path, rows: tuple[tuple[str | None, ...], ...]):
    col_widths = tuple(COL_WIDTHS[: len(rows[0])])
    return read_finish_schedules(single_table_pdf(path, rows, col_widths=col_widths), 0)[
        0
    ]


# ---------------------------------------------------------------------------
# 1. 読み方の表のとおりに区分が付く
# ---------------------------------------------------------------------------


def test_the_six_readings_of_the_table_are_assigned_as_written(tmp_path: Path) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    assert [a.reading for a in result.assignments] == [
        READING_NO_WORK,        # 既存 / 既存
        READING_FINISH_ONLY,    # 既存 / 材料名
        READING_REPLACE,        # 交換 / 何でも
        READING_NO_WORK,        # ー  (該当なし)
        READING_NO_WORK,        # 既存 / 空欄
        READING_FROM_BASE,      # 材料名 / 何でも
    ]


def test_the_not_applicable_row_keeps_its_reason(tmp_path: Path) -> None:
    """「ー」も「既存」も工事なしだが、**理由は別物なので残す。**"""
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    not_applicable = result.assignments[3]
    assert not_applicable.reading == READING_NO_WORK
    assert "該当なし" in not_applicable.reason
    assert not_applicable.reason != result.assignments[0].reason


def test_the_readings_map_onto_the_five_work_kinds(tmp_path: Path) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    assert [[i.work_kind for i in a.items] for a in result.assignments] == [
        [WORK_AS_IS],
        [WORK_ALTERED],
        [WORK_REMOVAL, WORK_NEW],  # 撤去して新設 は 2 つに分かれる
        [WORK_AS_IS],
        [WORK_AS_IS],
        [WORK_ALTERED],
    ]


def test_a_replacement_becomes_two_elements_a_removal_and_an_install(
    tmp_path: Path,
) -> None:
    """**「撤去して新設」の行は見積では 2 行になる。**

    おーちゃんの回答(札、2026-09-23 15:11)。1 件に畳むと片方が見えなくなる
    ので、**要素のほうを 2 つ作る。** 読み(`reading`)は 1 行分のまま
    「撤去して新設」で残し、どちらが元の行かを追えるようにする。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    replacement = result.assignments[2]
    assert replacement.reading == READING_REPLACE
    assert [item.work_kind for item in replacement.items] == [WORK_REMOVAL, WORK_NEW]
    # 区分が分かれたので、**片方を注記に逃がす必要はもう無い。**
    assert all(item.alternatives == () for item in replacement.items)


def test_both_halves_of_a_replacement_point_at_the_same_row_of_the_schedule(
    tmp_path: Path,
) -> None:
    """**2 行に分けても、根拠は同じ仕上表の同じ行である。**

    おーちゃんの指定どおり、どちらにも同じページ番号と行番号を入れる。
    ここが違うと、見積の 2 行が別々の根拠を持っているように見える。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    removal, install = result.assignments[2].items
    assert len(removal.evidence) == 1 and len(install.evidence) == 1
    assert removal.evidence[0].page_number == install.evidence[0].page_number
    assert removal.evidence[0].row_index == install.evidence[0].row_index
    assert removal.evidence[0].kind == install.evidence[0].kind == "仕上表から読んだ"
    # 部屋と部位も同じ。**分けたのは工事区分だけ。**
    assert (removal.what, removal.where) == (install.what, install.where)


def test_only_the_replacement_reading_becomes_two_elements(tmp_path: Path) -> None:
    """**分けるのは「撤去して新設」だけ。** ほかの読みは 1 行 1 要素のまま。

    「下地からやり替え」も撤去を含みうるが、**どこまで撤去するかは仕上表
    からは決まらない。** おーちゃんの回答が名指ししたのは「撤去して新設」
    だけなので、ここを勝手に広げない。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    for assignment in result.assignments:
        expected = 2 if assignment.reading == READING_REPLACE else 1
        assert len(assignment.items) == expected, assignment.reading


# ---------------------------------------------------------------------------
# 2. 下地欄が空欄の継続行は、自動で判定しない
# ---------------------------------------------------------------------------


CONTINUATION: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上", "メーカー"),
    ("洋室1", "壁", "軸組新設", "ビニルクロス", "架空社"),
    ("", "", "", "ビニルクロス", "架空社 アクセントクロス"),
    ("", "", "", "", "架空社 出隅コーナー材"),
)


def test_a_continuation_row_with_a_blank_base_becomes_a_question(
    tmp_path: Path,
) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", CONTINUATION))

    assert [a.reading for a in result.assignments] == [
        READING_FROM_BASE,
        READING_QUESTION,
        READING_QUESTION,
    ]
    assert len(result.questions) == 2


def test_a_continuation_row_the_reader_dropped_is_still_seen(tmp_path: Path) -> None:
    """**部位も仕上も空の行は読み取り側が落とす。** そこで消さない。

    P011 では出隅コーナー材の行がこれに当たり、落としたままだと
    問いが 10 件消える。
    """
    schedule = _schedule(tmp_path / "a.pdf", CONTINUATION)
    assert len(schedule.rows) == 2 and len(schedule.skipped_rows) == 1

    result = assign_finish_schedule_scope(schedule)
    assert len(result.assignments) == 3


def test_a_question_shows_the_previous_row_and_this_row(tmp_path: Path) -> None:
    """おーちゃんの指定どおり、**前の行と、この行に書かれていること**を並べる。"""
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", CONTINUATION))

    question = result.questions[0]
    assert "同じ工事の材料違い" in question.question
    assert question.previous_room == "洋室1"
    assert question.previous_part == "壁"
    assert question.previous_finish == "ビニルクロス"
    assert any("アクセントクロス" in text for text in question.this_row_texts)


def test_a_question_is_undecided_rather_than_guessed(tmp_path: Path) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", CONTINUATION))

    assert [i.work_kind for i in result.assignments[1].items] == [WORK_UNDECIDED]
    assert result.assignments[1].question is not None


# ---------------------------------------------------------------------------
# 3. 数量を 1 件も作らない
# ---------------------------------------------------------------------------


def test_no_quantity_is_produced(tmp_path: Path) -> None:
    """面積と長さは人の入力から来る(原則 3-1・4)。**ここでは出さない。**"""
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    for assignment in result.assignments:
        for item in assignment.items:
            assert item.value_range is None
            assert item.unit is None


# ---------------------------------------------------------------------------
# 4. 根拠にページ番号と行番号が入る
# ---------------------------------------------------------------------------


def test_every_element_carries_the_page_and_the_row_it_came_from(
    tmp_path: Path,
) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    for assignment in result.assignments:
        for item in assignment.items:
            evidence = item.evidence
            assert len(evidence) == 1
            assert evidence[0].kind == "仕上表から読んだ"
            assert evidence[0].page_number == 1
            assert evidence[0].row_index >= 1


def test_the_rows_are_numbered_as_the_table_has_them(tmp_path: Path) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    assert [a.items[0].evidence[0].row_index for a in result.assignments] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]


# ---------------------------------------------------------------------------
# 5. 下地の列が無い表では、工事の有無を名乗らない
# ---------------------------------------------------------------------------


NO_BASE_COLUMN: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室1", "床", "フローリング"),
    ("洋室1", "壁", "ビニルクロス"),
)


def test_without_a_base_column_every_row_becomes_a_question(tmp_path: Path) -> None:
    """**下地が読めない表で「仕上だけやり替え」と言わない。**

    下地欄が「既存」だから仕上だけ、という読みは、下地欄があって初めて
    成り立つ。列そのものが無い表では、同じ結論に見えても根拠が無い。
    """
    schedule = _schedule(tmp_path / "a.pdf", NO_BASE_COLUMN)
    result = assign_finish_schedule_scope(schedule)

    assert [a.reading for a in result.assignments] == [READING_QUESTION] * 2
    assert all(
        item.work_kind == WORK_UNDECIDED
        for a in result.assignments
        for item in a.items
    )
    assert result.no_base_column is True


# ---------------------------------------------------------------------------
# 6. 部位の引き継ぎ
# ---------------------------------------------------------------------------


CARRIED_PART: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上", "メーカー"),
    ("洋室1", "壁", "軸組新設", "ビニルクロス", "架空社"),
    ("", "", "増張ボード6mm", "タイル", "架空社"),
)


def test_a_blank_part_is_carried_forward_and_the_fact_is_recorded(
    tmp_path: Path,
) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", CARRIED_PART))

    second = result.assignments[1]
    assert second.reading == READING_FROM_BASE
    assert second.part == "壁"
    assert second.part_source == "carried_forward"
    assert any("引き継" in note for note in second.items[0].notes)


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def test_the_counts_add_up_to_the_number_of_rows(tmp_path: Path) -> None:
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    assert sum(result.counts_by_reading().values()) == len(result.assignments)
    assert result.unassigned == ()


def test_the_element_count_is_the_row_count_plus_the_replacements(
    tmp_path: Path,
) -> None:
    """**行の数と要素の数はもう同じではない。** 足し算で数えない。

    「撤去して新設」の行だけが 2 つになるので、要素の数は
    行の数 + 「撤去して新設」の件数である。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    replacements = result.counts_by_reading()[READING_REPLACE]
    assert replacements == 1
    assert result.item_count == len(result.assignments) + replacements
    assert sum(result.counts_by_work_kind().values()) == result.item_count


def test_an_unknown_base_word_is_not_forced_into_a_reading(tmp_path: Path) -> None:
    """**知らない書き方を材料名とみなさない。** みなすと工事が水増しされる。

    「未定」は材料名ではないので「下地からやり替え」にしてはいけない。
    表に無い書き方は問いへ回す。
    """
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "部位", "下地", "仕上", "メーカー"),
        ("洋室1", "床", "未定", "フローリング", ""),
    )
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", rows))

    assert result.assignments[0].reading == READING_QUESTION


def test_the_reading_names_are_the_ones_the_owner_wrote() -> None:
    assert READING_NO_WORK == "工事なし"
    assert READING_FINISH_ONLY == "仕上だけやり替え"
    assert READING_REPLACE == "撤去して新設"
    assert READING_FROM_BASE == "下地からやり替え"
    assert READING_QUESTION == "問い"


def test_a_row_with_no_characters_at_all_is_not_a_question(tmp_path: Path) -> None:
    """**罫線だけ引いてある空の行を問いにしない。**

    実図面の仕上表は、行数の半分近くが文字の無い空の行だった。
    これを継続行として拾うと、人に見せる問いが 16 件から 67 件へ膨らむ。
    """
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "部位", "下地", "仕上", "メーカー"),
        ("洋室1", "壁", "軸組新設", "ビニルクロス", "架空社"),
        ("", "", "", "", ""),
        ("", "", "", "", "架空社 出隅コーナー材"),
    )
    schedule = _schedule(tmp_path / "a.pdf", rows)
    result = assign_finish_schedule_scope(schedule)

    assert len(result.assignments) == 2
    assert [a.reading for a in result.assignments] == [
        READING_FROM_BASE,
        READING_QUESTION,
    ]
