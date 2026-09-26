"""本番の入口(`intake/drawing_intake.py`)に建具表・内装仕上表をつないだ通しテスト。

既にある `tests/test_drawing_intake.py` は、縮尺・面積・開き戸の 3 本が
1 本の経路でつながっていることを固定している。こちらは、そこに
**表として読んだ建具表と内装仕上表**が加わったあとの振る舞いを固定する。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**

守りたいのは 6 つ。

1. 建具表の数量が、根拠つきで仲裁層まで届くこと。
2. **円弧では拾えない引戸・折戸が、建具表からは拾えること。**
   この作業の意味はここにある(P011 の新設建具は全部これだった)。
3. **建具表の数量と開き戸の検出数を、入口が突き合わせて確定させないこと。**
   別々の対象として出し、食い違いは人が見る。
4. 内装仕上表は**数量にならない**こと。室の輪郭を取る実装が無いので、
   面積は出せない。構造化した対応だけを結果に載せる。
5. **ページをまたいで足さないこと。** 同じ建具番号が 2 ページにあり
   数量が違えば、どちらかを選ばずレンジにする。
6. **表が加わっても 1 件も自動確定しないこと。** 新しい 2 手法もどちらも
   未校正で、図面 PDF は 1 つのデータ源のままである。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from axes.image_axis.schedule_tables import (
    METHOD_DOOR_SCHEDULE,
    METHOD_FINISH_SCHEDULE,
    is_arc_blind,
)
from intake.drawing_intake import IntakeConfig, read_drawing
from intake.start_kit import PageDeclaration, StartKit
from tests.test_pdf_tables import draw_table

DOOR_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("建具番号", "種別", "幅", "高さ", "数量"),
    ("WD-01", "引戸", "1650", "2000", "2"),
    ("WD-02", "折戸", "1200", "2000", "1"),
    ("WD-03", "開き戸", "780", "2000", "3"),
)

FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室1", "床", "フローリング"),
    (None, "壁", "ビニルクロス"),
    ("浴室", "床", "タイル"),
)


def _schedule_page(
    doc: pymupdf.Document,
    *,
    door_rows: tuple[tuple[str | None, ...], ...] | None = DOOR_ROWS,
    finish_rows: tuple[tuple[str | None, ...], ...] | None = FINISH_ROWS,
) -> pymupdf.Page:
    """建具表と内装仕上表が載っているページ(実図面でいう「建具表」の紙)。"""
    page = doc.new_page(width=1190, height=842)
    page.insert_text(
        pymupdf.Point(850, 800), "縮尺 1/50", fontname="japan", fontsize=11
    )
    if door_rows is not None:
        draw_table(
            page,
            origin=(80.0, 120.0),
            col_widths=(110.0, 90.0, 80.0, 80.0, 70.0),
            row_height=24.0,
            rows=door_rows,
            caption="建具表",
        )
    if finish_rows is not None:
        draw_table(
            page,
            origin=(80.0, 400.0),
            col_widths=(120.0, 90.0, 180.0),
            row_height=24.0,
            rows=finish_rows,
            caption="内装仕上表",
        )
    return page


def _pdf(tmp_path: Path, build) -> Path:
    path = tmp_path / "synthetic_schedules.pdf"
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
        case_id="TEST-SCHEDULE",
        answers_path=tmp_path / "answers.json",
        start_kit=start_kit,
    )


@pytest.fixture()
def schedules(tmp_path: Path) -> Path:
    return _pdf(tmp_path, lambda doc: _schedule_page(doc))


# ---------------------------------------------------------------------------
# 1. 数量が根拠つきで届く
# ---------------------------------------------------------------------------


def test_door_schedule_quantities_reach_the_arbitration_layer(
    schedules: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(schedules, tmp_path))

    quantities = {
        finding.target: finding.value_range
        for finding in result.findings
        if finding.method_id == METHOD_DOOR_SCHEDULE
    }
    assert quantities == {
        "建具数量::WD-01": (2.0, 2.0),
        "建具数量::WD-02": (1.0, 1.0),
        "建具数量::WD-03": (3.0, 3.0),
    }
    assert {decision.target for decision in result.decisions} >= set(quantities)


def test_the_provenance_of_a_schedule_quantity_points_at_the_cell(
    schedules: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(schedules, tmp_path))
    finding = next(
        item for item in result.findings if item.target == "建具数量::WD-01"
    )

    occurrence = finding.provenance["occurrences"][0]
    assert occurrence["page_number"] == 1
    assert occurrence["caption"] == "建具表"
    assert occurrence["cells"]["数量"]["source_text"] == "2"
    x0, y0, x1, y1 = occurrence["cells"]["数量"]["rect_pt"]
    assert x1 > x0 and y1 > y0


def test_the_structured_rows_are_kept_on_the_result(
    schedules: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(schedules, tmp_path))

    assert [row.mark for row in result.door_schedule_rows] == [
        "WD-01",
        "WD-02",
        "WD-03",
    ]
    assert [(row.room, row.part, row.finish) for row in result.finish_schedule_rows] == [
        ("洋室1", "床", "フローリング"),
        ("洋室1", "壁", "ビニルクロス"),
        ("浴室", "床", "タイル"),
    ]


# ---------------------------------------------------------------------------
# 2. 円弧では拾えない建具が表からは拾える
# ---------------------------------------------------------------------------


def test_sliding_and_folding_doors_are_picked_up_from_the_table(
    schedules: Path, tmp_path: Path
) -> None:
    """**この作業の意味。** 引戸・折戸は円弧を描かないので図形からは拾えない。"""
    result = read_drawing(_config(schedules, tmp_path))

    assert result.arc_blind_doors() == ("WD-01", "WD-02")
    # 開き戸は円弧でも拾える種別なので、ここには入らない。
    assert "WD-03" not in result.arc_blind_doors()


def test_an_unknown_kind_is_not_reported_as_detectable(tmp_path: Path) -> None:
    """種別が読めない建具を「円弧で拾える」と言わない。"""
    assert is_arc_blind("引違い戸") is True
    assert is_arc_blind("開き戸") is False
    assert is_arc_blind(None) is None
    assert is_arc_blind("") is None
    # 知らない書き方を False(円弧で拾える)に倒さない。倒すと
    # 「図形で拾えているはず」という前提で見落としが隠れる。
    assert is_arc_blind("ガラリ") is None
    assert is_arc_blind("ＦＩＸ窓") is None


def test_the_door_arc_count_and_the_schedule_are_separate_targets(
    tmp_path: Path,
) -> None:
    """**入口が突き合わせて確定させない。** 別々の対象として出す。

    同じページに建具表と開き戸の円弧があっても、数が合うか合わないかを
    ここで判定しない。判定は仲裁層の仕事で、そこへ渡す前に
    片方を捨てたり合わせたりしない。
    """
    path = _pdf(
        tmp_path,
        lambda doc: (
            _schedule_page(doc),
            _draw_one_arc(doc.load_page(0)),
        ),
    )
    result = read_drawing(_config(path, tmp_path))

    targets = {finding.target for finding in result.findings}
    assert "建具数量::WD-01" in targets
    assert "開き戸::ページ1" in targets


def _draw_one_arc(page: pymupdf.Page) -> None:
    """1/50 の図面に幅 800mm の開き戸を 1 つ描く。"""
    radius_pt = 800.0 * (1 / 50) / 25.4 * 72
    shape = page.new_shape()
    shape.draw_sector(
        pymupdf.Point(300.0, 700.0), pymupdf.Point(300.0 + radius_pt, 700.0), 90
    )
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


# ---------------------------------------------------------------------------
# 3. 内装仕上表は数量にならない
# ---------------------------------------------------------------------------


def test_the_finish_table_produces_no_quantity(
    schedules: Path, tmp_path: Path
) -> None:
    """室の輪郭を取る実装が無いので、仕上げからは面積が出せない。"""
    result = read_drawing(_config(schedules, tmp_path))

    assert not [
        finding
        for finding in result.findings
        if finding.method_id == METHOD_FINISH_SCHEDULE
    ]
    # それでも読んだ内容は落とさない。
    assert result.finish_schedule_rows


def test_a_carried_forward_room_name_is_visible_in_the_result(
    schedules: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(schedules, tmp_path))

    sources = [row.room_source for row in result.finish_schedule_rows]
    assert sources == ["cell", "merged_cell", "cell"]


# ---------------------------------------------------------------------------
# 4. ページをまたいで足さない
# ---------------------------------------------------------------------------


def test_the_same_mark_on_two_pages_with_different_counts_becomes_a_range(
    tmp_path: Path,
) -> None:
    other: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "5"),
    )
    path = _pdf(
        tmp_path,
        lambda doc: (
            _schedule_page(doc, finish_rows=None),
            _schedule_page(doc, door_rows=other, finish_rows=None),
        ),
    )
    result = read_drawing(_config(path, tmp_path))
    finding = next(
        item for item in result.findings if item.target == "建具数量::WD-01"
    )

    # 2 + 5 = 7 にしない。どちらかを選びもしない。
    assert finding.value_range == (2.0, 5.0)
    assert "note" in finding.provenance
    assert len(finding.provenance["occurrences"]) == 2
    # **ページの宣言が無いので、現況とも計画とも名乗らない。**
    # ここでレンジになるのは「同じものを 2 回読んで値が違った」からであって、
    # 現況と計画を混ぜてよいからではない(下の 2 件がそれを分けている)。
    assert finding.meaning is not None
    assert finding.meaning.phase == "不明"


def test_the_same_mark_in_two_phases_becomes_two_targets(tmp_path: Path) -> None:
    """**現況の建具表と計画の建具表を、1 つのレンジに混ぜない**(2026-09-23)。

    それまでは建具番号だけでまとめていたので、現況 2 件と計画 5 件が
    `(2.0, 5.0)` という 1 つのレンジになっていた。**それは値の幅ではなく
    意味の違いで、この形のままでは差分(工事内容)の層から現況も計画も見えない**
    (`docs/principles/scope_of_work_diff.md` 3-1)。

    **足さない・選ばないという元の約束は変えていない。** 分けるだけである。
    """
    planned: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "5"),
    )
    path = _pdf(
        tmp_path,
        lambda doc: (
            _schedule_page(doc, finish_rows=None),
            _schedule_page(doc, door_rows=planned, finish_rows=None),
        ),
    )
    result = read_drawing(
        _config(
            path,
            tmp_path,
            start_kit=StartKit(
                page_declarations=(
                    PageDeclaration(page_number=1, kind="建具表", phase="現況"),
                    PageDeclaration(page_number=2, kind="建具表", phase="計画"),
                )
            ),
        )
    )

    quantities = {
        item.target: item.value_range
        for item in result.findings
        if item.method_id == METHOD_DOOR_SCHEDULE and "WD-01" in item.target
    }
    assert quantities == {
        "建具数量::WD-01::現況": (2.0, 2.0),
        "建具数量::WD-01::計画": (5.0, 5.0),
    }

    # **どちらも 7 になっていない。** 足さないという約束はそのままである。
    assert sum(1 for value in quantities.values() if value == (7.0, 7.0)) == 0

    for target, phase in (
        ("建具数量::WD-01::現況", "現況"),
        ("建具数量::WD-01::計画", "計画"),
    ):
        finding = next(item for item in result.findings if item.target == target)
        assert finding.meaning is not None
        assert finding.meaning.phase == phase


def test_an_undeclared_page_is_not_folded_into_a_declared_phase(
    tmp_path: Path,
) -> None:
    """**宣言が無いページを「計画」に寄せない。**

    寄せると、現況か計画か決まっていない数量が計画の数量として差分に入り、
    見ただけでは気づけない(`scope_of_work_diff.md` 5 節)。
    """
    planned: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "5"),
    )
    path = _pdf(
        tmp_path,
        lambda doc: (
            _schedule_page(doc, finish_rows=None),
            _schedule_page(doc, door_rows=planned, finish_rows=None),
        ),
    )
    result = read_drawing(
        _config(
            path,
            tmp_path,
            start_kit=StartKit(
                page_declarations=(
                    PageDeclaration(page_number=2, kind="建具表", phase="計画"),
                )
            ),
        )
    )

    quantities = {
        item.target: item.value_range
        for item in result.findings
        if item.method_id == METHOD_DOOR_SCHEDULE and "WD-01" in item.target
    }
    assert quantities == {
        "建具数量::WD-01": (2.0, 2.0),
        "建具数量::WD-01::計画": (5.0, 5.0),
    }


def test_the_same_mark_with_the_same_count_stays_one_value(tmp_path: Path) -> None:
    path = _pdf(
        tmp_path,
        lambda doc: (
            _schedule_page(doc, finish_rows=None),
            _schedule_page(doc, finish_rows=None),
        ),
    )
    result = read_drawing(_config(path, tmp_path))
    finding = next(
        item for item in result.findings if item.target == "建具数量::WD-01"
    )

    assert finding.value_range == (2.0, 2.0)


def test_a_row_whose_quantity_cannot_be_read_produces_no_finding(
    tmp_path: Path,
) -> None:
    """**読めなかった数量を 0 で埋めない。** 行そのものは結果に残す。"""
    rows: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "別途"),
    )
    path = _pdf(tmp_path, lambda doc: _schedule_page(doc, door_rows=rows, finish_rows=None))
    result = read_drawing(_config(path, tmp_path))

    assert not [
        item for item in result.findings if item.method_id == METHOD_DOOR_SCHEDULE
    ]
    assert [row.mark for row in result.door_schedule_rows] == ["WD-01"]


# ---------------------------------------------------------------------------
# 5. 読めなかったページ
# ---------------------------------------------------------------------------


def test_a_scanned_schedule_page_is_recorded_as_unsupported(tmp_path: Path) -> None:
    def build(doc: pymupdf.Document) -> None:
        page = doc.new_page(width=1190, height=842)
        pixmap = pymupdf.Pixmap(pymupdf.csGRAY, 10, 10, bytes([255] * 100), False)
        page.insert_image(pymupdf.Rect(0, 0, 1190, 842), pixmap=pixmap)

    result = read_drawing(_config(_pdf(tmp_path, build), tmp_path))

    assert result.unsupported_pages == (1,)
    assert result.door_schedule_rows == ()
    assert result.finish_schedule_rows == ()


def test_a_page_without_tables_says_so_without_guessing(tmp_path: Path) -> None:
    def build(doc: pymupdf.Document) -> None:
        page = doc.new_page(width=1190, height=842)
        page.insert_text(
            pymupdf.Point(850, 800), "縮尺 1/50", fontname="japan", fontsize=11
        )
        shape = page.new_shape()
        shape.draw_line(pymupdf.Point(100, 100), pymupdf.Point(400, 100))
        shape.finish(color=(0, 0, 0), width=0.5)
        shape.commit()

    result = read_drawing(_config(_pdf(tmp_path, build), tmp_path))

    assert result.door_schedule_rows == ()
    assert any("建具表" in note for note in result.pages[0].notes)


def test_縮尺が読めなくても表は読める(tmp_path: Path) -> None:
    """**縮尺が読めないページでも表は読める。**

    `intake/drawing_intake.py` の `_extract()` はそうコメントに書いているのに、
    **それを固定したテストが 1 件も無かった**(45 周目に実測して分かった)。
    開き戸は `if scale is not None:` で塞いであるので、`_read_schedules` を
    同じ塊へ動かしても**全件テストは通ってしまう。**

    **それが起きると失うものは小さくない。** 実案件 P011 の匿名化 v2 では、
    内装仕上表が読める 2 ページが**どちらも縮尺の読めないページ**で、
    表に縮尺の依存を付けた瞬間に**仕上表の行が全部消える**
    (45 周目の測定、`docs/c_schedule_scale_independence_report.md`)。

    だからここで塞ぐ。表は印字された文字を読むだけなので、縮尺は要らない。
    """

    def build(doc: pymupdf.Document) -> None:
        page = doc.new_page(width=1190, height=842)
        # **縮尺は書かない。** それ以外は `_schedule_page` と同じ紙。
        draw_table(
            page,
            origin=(80.0, 120.0),
            col_widths=(110.0, 90.0, 80.0, 80.0, 70.0),
            row_height=24.0,
            rows=DOOR_ROWS,
            caption="建具表",
        )
        draw_table(
            page,
            origin=(80.0, 400.0),
            col_widths=(120.0, 90.0, 180.0),
            row_height=24.0,
            rows=FINISH_ROWS,
            caption="内装仕上表",
        )

    result = read_drawing(_config(_pdf(tmp_path, build), tmp_path))

    # 前提: このページの縮尺は読めていない。
    assert result.pages[0].scale is None
    # それでも表は読めている。**ここが本題。**
    assert [row.mark for row in result.door_schedule_rows] == ["WD-01", "WD-02", "WD-03"]
    assert [row.part for row in result.finish_schedule_rows] == ["床", "壁", "床"]


# ---------------------------------------------------------------------------
# 6. 表が加わっても自動確定しない
# ---------------------------------------------------------------------------


def test_the_schedule_methods_are_registered_as_uncalibrated() -> None:
    """表から読んだ値も、独立データで校正されるまでは強い軸にしない。"""
    assert DEFAULT_METHOD_POLICIES[METHOD_DOOR_SCHEDULE].calibrated is False
    assert DEFAULT_METHOD_POLICIES[METHOD_FINISH_SCHEDULE].calibrated is False
    assert DEFAULT_METHOD_POLICIES[METHOD_FINISH_SCHEDULE].max_strength == "weak"


def test_nothing_is_auto_confirmed_even_with_the_schedules(
    schedules: Path, tmp_path: Path
) -> None:
    """**表を読めるようにしても確定数量は 0 件のまま。**

    図面 PDF は 1 ファイル = 1 つのデータ源で、独立性はファイル内容の指紋で
    判定される。そこから何種類の手法で読んでも独立数は 1 にしかならない。
    これは精度の問題ではないので、表が読めても変わらない。
    どちらかの手法を `calibrated=True` にしたら、このテストが必ず落ちる。
    """
    result = read_drawing(_config(schedules, tmp_path))

    assert result.decisions
    assert result.confirmed_targets == ()
    assert all(decision.tier == 3 for decision in result.decisions)
    assert all(not decision.is_invalid for decision in result.decisions)
