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
7. **「撤去して新設」と「下地からやり替え」の行は、要素 2 つ(撤去・新設)に
   なること。** おーちゃんの回答(札、2026-09-23 15:11 と K-08 1番)。
   どちらの要素も根拠には**同じ仕上表のページと行番号**が入る。
   「下地からやり替え」の撤去のほうには、**範囲が仕上表からは決まらない**
   ことを注記で残す。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axes.image_axis.schedule_tables import read_finish_schedules
from estimating.finish_schedule_scope import (
    BASE_EXISTING,
    BASE_NOT_APPLICABLE,
    BASE_OWNER_SUPPLIED,
    BASE_REPLACED,
    BASE_SAME_AS_ABOVE,
    BASE_UNDETERMINED,
    CAUSE_FINISH_BLANK,
    CAUSE_OWNER_SUPPLIED,
    CAUSE_SAME_AS_ABOVE,
    CAUSE_UNKNOWN_BASE_WORD,
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
        READING_QUESTION,       # 既存 / 空欄(K-45 で「工事なし」から問いへ)
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
        [WORK_ALTERED],  # 仕上だけやり替え。**畳まれるのはこの読みだけ**
        [WORK_REMOVAL, WORK_NEW],  # 撤去して新設
        [WORK_AS_IS],
        [WORK_UNDECIDED],  # 既存 / 空欄。K-45 で問いへ(区分を決めない)
        [WORK_REMOVAL, WORK_NEW],  # 下地からやり替え(K-08 1番)
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


def test_a_from_base_row_becomes_two_elements_as_well(tmp_path: Path) -> None:
    """**「下地からやり替え」も撤去と新設の 2 行になる**(K-08 1番)。

    おーちゃんの理屈: 下地からやり替えるなら、既存の下地を撤去する工事は
    必ず起きる。**範囲が決まらないのは数量の話で、工事があるかどうかとは
    別である。** 数量は人の入力から来るので、ここで止める理由が無い。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    from_base = result.assignments[5]
    assert from_base.reading == READING_FROM_BASE
    assert [item.work_kind for item in from_base.items] == [WORK_REMOVAL, WORK_NEW]

    removal, install = from_base.items
    assert removal.evidence[0].page_number == install.evidence[0].page_number
    assert removal.evidence[0].row_index == install.evidence[0].row_index
    assert (removal.what, removal.where) == (install.what, install.where)


def test_the_removal_half_of_a_from_base_row_says_the_extent_is_unknown(
    tmp_path: Path,
) -> None:
    """**撤去の行には「範囲は仕上表からは決まらない」を残す**(K-08 1番)。

    工事があることは言えるが、どこまで撤去するかは表に書いていない。
    新設のほうにはこの断りを付けない(仕上表が材料名を書いている)。
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    removal, install = result.assignments[5].items
    assert any("範囲は仕上表からは決まらない" in note for note in removal.notes)
    assert not any("範囲は仕上表からは決まらない" in note for note in install.notes)


def test_the_readings_that_stay_one_element_stay_one_element(tmp_path: Path) -> None:
    """**分けるのは「撤去して新設」と「下地からやり替え」の 2 つだけ。**

    工事なし・仕上だけやり替え・問いは 1 行 1 要素のままである。
    """
    two = {READING_REPLACE, READING_FROM_BASE}
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    for assignment in result.assignments:
        expected = 2 if assignment.reading in two else 1
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


def test_the_element_count_is_the_row_count_plus_the_split_readings(
    tmp_path: Path,
) -> None:
    """**行の数と要素の数はもう同じではない。** 足し算で数えない。

    2 つに分かれるのは「撤去して新設」と「下地からやり替え」なので、
    要素の数は 行の数 + その 2 つの読みの件数である。
    """
    counts = assign_finish_schedule_scope(
        _schedule(tmp_path / "a.pdf", SIX_READINGS)
    )

    split = counts.counts_by_reading()[READING_REPLACE] + (
        counts.counts_by_reading()[READING_FROM_BASE]
    )
    assert split == 2
    assert counts.item_count == len(counts.assignments) + split
    assert sum(counts.counts_by_work_kind().values()) == counts.item_count


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


# ---------------------------------------------------------------------------
# 7. 下地欄の書き方の一覧(K-10 1番・2番)
# ---------------------------------------------------------------------------


def _one_row(tmp_path: Path, base: str, finish: str = "ビニルクロス"):
    """下地欄だけを差し替えた 2 行の表。1 行目は普通の行(問いの「前の行」)。"""
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "部位", "下地", "仕上", "メーカー"),
        ("洋室1", "壁", "軸組新設", "ビニルクロス", "架空社"),
        ("洋室1", "天井", base, finish, "架空社"),
    )
    return assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", rows))


def test_same_as_above_is_a_question_of_its_own_kind(tmp_path: Path) -> None:
    """**「同上」は「まだ決まっていない」ではない**(K-10 1番)。

    上の行と同じ、という意味である。だからといって**上の行の下地を自動で
    引き継がない。** 継続行と同じで、問いに回す。
    """
    result = _one_row(tmp_path, "同上")

    assert result.assignments[1].reading == READING_QUESTION
    question = result.questions[0]
    assert question.cause == CAUSE_SAME_AS_ABOVE
    assert question.question == "この行の下地は、上の行と同じですか"


def test_the_same_as_above_question_shows_the_previous_base(tmp_path: Path) -> None:
    """おーちゃんの指定どおり、**上の行の室名・部位・下地**を並べて見せる。"""
    question = _one_row(tmp_path, "同上").questions[0]

    assert question.previous_room == "洋室1"
    assert question.previous_part == "壁"
    assert question.previous_base == "軸組新設"


def test_same_as_above_does_not_inherit_the_base_of_the_row_above(
    tmp_path: Path,
) -> None:
    """**引き継いだ結果「下地からやり替え」にしない。** 区分不明で止める。"""
    result = _one_row(tmp_path, "同上")

    assert [i.work_kind for i in result.assignments[1].items] == [WORK_UNDECIDED]
    assert result.assignments[1].base == "同上"


def test_owner_supplied_material_is_a_question_not_a_missing_job(
    tmp_path: Path,
) -> None:
    """**「施主支給」は工事が無いのではない**(K-10 2番)。

    おーちゃんの言葉で、材料の出どころが違うだけである。**材料費が落ちる
    一方、手間は残る。** どちらに寄せるかは人が決めるので、問いに回す。
    """
    result = _one_row(tmp_path, "施主支給")

    assert result.assignments[1].reading == READING_QUESTION
    question = result.questions[0]
    assert question.cause == CAUSE_OWNER_SUPPLIED
    assert "手間" in question.question


@pytest.mark.parametrize("word", ["協議", "別途見積"])
def test_words_that_are_not_material_names_go_to_a_question(
    tmp_path: Path, word: str
) -> None:
    """**「協議」「別途見積」を材料名とみなさない**(K-10 2番)。

    みなすと、ありもしない「下地からやり替え」が 1 件増える。
    """
    result = _one_row(tmp_path, word)

    assert result.assignments[1].reading == READING_QUESTION
    assert result.questions[0].cause == CAUSE_UNKNOWN_BASE_WORD


@pytest.mark.parametrize("word", ["流用", "再使用"])
def test_words_that_mean_the_base_stays_are_read_as_existing(
    tmp_path: Path, word: str
) -> None:
    """**「流用」「再使用」は下地が残る**(K-10 2番)。仕上に材料名があるので
    「仕上だけやり替え」になる。"""
    result = _one_row(tmp_path, word)

    assert result.assignments[1].reading == READING_FINISH_ONLY
    assert [i.work_kind for i in result.assignments[1].items] == [WORK_ALTERED]


@pytest.mark.parametrize("word", ["更新", "新替"])
def test_words_that_mean_the_base_is_swapped_are_read_as_replacement(
    tmp_path: Path, word: str
) -> None:
    """**「更新」「新替」は取り替え**(K-10 2番)。撤去と新設の 2 行になる。"""
    result = _one_row(tmp_path, word)

    assert result.assignments[1].reading == READING_REPLACE
    assert [i.work_kind for i in result.assignments[1].items] == [
        WORK_REMOVAL,
        WORK_NEW,
    ]


def test_the_word_lists_do_not_overlap() -> None:
    """**同じ語が 2 つの一覧に入っていると、どちらに読まれるか順番で決まる。**

    読み方の表は上から順に当てるので、重なりがあると一覧を並べ替えた瞬間に
    読みが変わる。重なりを作らないことをここで固定する。
    """
    lists = {
        "現況のまま": BASE_EXISTING,
        "取り替える": BASE_REPLACED,
        "該当なし": BASE_NOT_APPLICABLE,
        "材料名とみなさない": BASE_UNDETERMINED,
        "同上": BASE_SAME_AS_ABOVE,
        "施主支給": BASE_OWNER_SUPPLIED,
    }
    for left, left_words in lists.items():
        for right, right_words in lists.items():
            if left < right:
                assert not (left_words & right_words), (left, right)


def test_same_as_above_is_no_longer_in_the_undetermined_list() -> None:
    """**「同上」は「まだ決まっていない」ではない**(K-10 1番、おーちゃんの訂正)。"""
    assert "同上" not in BASE_UNDETERMINED
    assert "同上" in BASE_SAME_AS_ABOVE


@pytest.mark.parametrize("word", ["同上", "〃"])
def test_the_ditto_mark_is_read_the_same_way_as_the_word(
    tmp_path: Path, word: str
) -> None:
    """**「〃」も「同上」と同じ問いに回す**(札、2026-09-23 23:47)。

    表で「同上」と書くか「〃」と書くかは書き手の癖でしかない。**片方だけ
    問いに回すと、同じ意味の行が図面によって別の扱いになる。**
    引き継がないところも同じで、上の行の下地は当てない。
    """
    result = _one_row(tmp_path, word)

    assert result.assignments[1].reading == READING_QUESTION
    question = result.questions[0]
    assert question.cause == CAUSE_SAME_AS_ABOVE
    assert question.question == "この行の下地は、上の行と同じですか"
    assert question.previous_base == "軸組新設"
    assert [i.work_kind for i in result.assignments[1].items] == [WORK_UNDECIDED]


# ---------------------------------------------------------------------------
# 8. 下地が既存で仕上の欄が空欄(K-45、おーちゃん 2026-09-26、案 B)
# ---------------------------------------------------------------------------


def test_a_blank_finish_over_an_existing_base_is_a_question_not_no_work(
    tmp_path: Path,
) -> None:
    """**空欄は「工事なし」ではなく「何も書かれていない」。**

    元の読み方の表(K-05)では「工事なし」だった。おーちゃんが K-45 で
    決め直した(案 B): 仕上の記載が見当たらないとして問いに回す。
    **既存のまま(工事なし)の要素にしない。**
    """
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    blank = result.assignments[4]
    assert blank.finish in (None, "")
    assert blank.reading == READING_QUESTION
    assert "仕上の記載が見当たりません" in blank.reason
    assert [item.work_kind for item in blank.items] == [WORK_UNDECIDED]
    assert WORK_AS_IS not in [item.work_kind for item in blank.items]

    assert blank.question is not None
    assert blank.question.cause == CAUSE_FINISH_BLANK
    assert blank.question.question == (
        "洋室1・廻縁の仕上の記載が見当たりません。この部位の工事はどうなりますか。"
    )
    assert blank.question in result.questions


def test_the_blank_finish_question_carries_no_recommendation(tmp_path: Path) -> None:
    """**推奨を付けない**(K-45)。答えを先に置くと、記載が無いことを根拠に
    「既存のまま」を通すことになる。問いの文にも想定を書かない。"""
    result = assign_finish_schedule_scope(_schedule(tmp_path / "a.pdf", SIX_READINGS))

    question = result.assignments[4].question
    assert question is not None
    assert question.recommended_answer is None
    assert question.as_dict()["recommended_answer"] is None
    for word in ("想定", "既存のまま", "工事なし", "よいですか"):
        assert word not in question.question
