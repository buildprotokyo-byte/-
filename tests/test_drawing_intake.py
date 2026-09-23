"""本番の入口(`intake/drawing_intake.py`)の通しテスト。

**テスト用の PDF は、この中で組み立てる合成のベクター PDF である。**
顧客の図面は匿名化済みであってもリポジトリに置かない決まりなので、
テストが実図面に依存してはならない。

守りたいのは 6 つ。

1. 入口が PDF のパスだけで動き、結果を返すこと。
2. ラスターのページを**黙って捨てず**、未対応として記録すること。
3. 縮尺・面積・開き戸が 1 本の経路でつながっていること。
4. 根拠(ページ番号・座標・元の文字列)が仲裁層まで落ちずに届くこと。
5. 開き戸が**強い軸にならない**こと。
6. 面積の選択は案件ごとに人へ 1 回だけ聞き、**回答が無い間は数量を出さない**こと。

あわせて、**この経路だけでは 1 件も自動確定しない**ことを固定する。
図面 PDF は 1 つのデータ源なので独立した強い軸が 2 つ揃わず、
2 つの手法はどちらも実測校正を通っていない。将来どちらかを
`calibrated=True` にしたときに、このテストが必ず気づく。
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from axes.image_axis.pdf_vector_symbols import (
    METHOD_DOOR_ARC,
    METHOD_TEXT_AREA,
    find_area_labels,
)
from intake.case_answers import (
    AREA_BASIS_OPTIONS,
    QUESTION_AREA_BASIS,
    AnswerError,
    AnswerStore,
)
from intake.drawing_intake import (
    TARGET_WORK_FLOOR_AREA,
    IntakeConfig,
    IntakeError,
    file_fingerprint,
    read_drawing,
)

#: 実寸 1mm が 1/50 の図面で何ポイントか。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72

#: 合成図面に書き込む面積。P011 で実際に読めた2値と同じ並びにしてある
#: (**値そのものは図面に印字された一般的な数字で、正解データではない**)。
PRIVATE_AREA_SQM = 95.54
CONSTRUCTION_AREA_SQM = 90.61


def _draw_quarter_arc(page: pymupdf.Page, x: float, y: float, radius_pt: float) -> None:
    """(x, y) を中心とする四分円を、半径 2 本とあわせて扇形として描く。"""
    start = pymupdf.Point(x + radius_pt, y)
    shape = page.new_shape()
    shape.draw_sector(pymupdf.Point(x, y), start, 90)
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _draw_sliding_door(page: pymupdf.Page, x: float, y: float, width_pt: float) -> None:
    """引戸。**円弧を描かない**ので、この手法では原理的に拾えない。

    実案件 P011 の新設建具は全部これと折戸で、だから新設分は 0 件になる。
    テストの期待値にその前提を織り込むために描いておく。
    """
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + width_pt, y))
    shape.draw_line(
        pymupdf.Point(x + width_pt / 2, y - 2), pymupdf.Point(x + width_pt * 1.5, y - 2)
    )
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _vector_plan_page(
    doc: pymupdf.Document,
    *,
    scale_text: str = "1/50",
    door_widths_mm: tuple[float, ...] = (800.0, 750.0),
    sliding_doors: int = 2,
    areas: bool = True,
) -> pymupdf.Page:
    """CAD から出したような平面図のページを 1 枚作る。"""
    page = doc.new_page(width=1190, height=842)  # A3 横
    # 表題欄。`fontname="japan"` を使うと、埋め込み文字として取り出せる。
    page.insert_text(
        pymupdf.Point(850, 780), f"縮尺 {scale_text}", fontname="japan", fontsize=11
    )
    if areas:
        page.insert_text(
            pymupdf.Point(850, 800),
            f"専有延床面積 {PRIVATE_AREA_SQM} ㎡",
            fontname="japan",
            fontsize=11,
        )
        page.insert_text(
            pymupdf.Point(850, 820),
            f"施工床面積 {CONSTRUCTION_AREA_SQM} ㎡",
            fontname="japan",
            fontsize=11,
        )
    x = 100.0
    for width_mm in door_widths_mm:
        _draw_quarter_arc(page, x, 300.0, width_mm * PT_PER_MM_AT_50)
        x += 200.0
    for index in range(sliding_doors):
        _draw_sliding_door(page, 150.0 + index * 180.0, 600.0, 1600.0 * PT_PER_MM_AT_50)
    return page


def _scanned_page(doc: pymupdf.Document) -> pymupdf.Page:
    """紙をスキャンしただけのページ(線も文字も図形データとして持たない)。"""
    page = doc.new_page(width=1190, height=842)
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, 10, 10, bytes([255] * 100), False)
    page.insert_image(pymupdf.Rect(0, 0, 1190, 842), pixmap=pixmap)
    return page


@pytest.fixture()
def drawing(tmp_path: Path) -> Path:
    """1 ページ目がベクターの平面図、2 ページ目がスキャンの図面。"""
    path = tmp_path / "synthetic_plan.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    _scanned_page(doc)
    doc.save(path)
    doc.close()
    return path


def _config(drawing: Path, tmp_path: Path) -> IntakeConfig:
    return IntakeConfig(
        pdf_path=drawing,
        case_id="TEST-001",
        answers_path=tmp_path / "answers.json",
    )


# ---------------------------------------------------------------------------
# 1. 入口が PDF のパスだけで動く
# ---------------------------------------------------------------------------


def test_the_entry_point_takes_a_pdf_path_and_returns_a_result(
    drawing: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(drawing, tmp_path))

    assert result.case_id == "TEST-001"
    assert result.source_fingerprint == file_fingerprint(drawing)
    assert len(result.pages) == 2
    # 要約が人の読める形で出ること(報告にそのまま貼るため)。
    assert "案件: TEST-001" in result.summary()


def test_a_missing_pdf_is_reported_not_guessed(tmp_path: Path) -> None:
    config = IntakeConfig(pdf_path=tmp_path / "none.pdf", case_id="TEST-001")
    with pytest.raises(IntakeError):
        read_drawing(config)


def test_the_fingerprint_is_the_file_content_not_its_name(tmp_path: Path) -> None:
    """指紋にファイル名を混ぜない。名前は匿名化で変わるし案件名が入りうる。"""
    doc = pymupdf.open()
    _vector_plan_page(doc)
    first = tmp_path / "a.pdf"
    doc.save(first)
    doc.close()
    second = tmp_path / "設計図面_匿名化済み_v2.pdf"
    second.write_bytes(first.read_bytes())

    assert file_fingerprint(first) == file_fingerprint(second)


# ---------------------------------------------------------------------------
# 2. ラスターのページを黙って捨てない
# ---------------------------------------------------------------------------


def test_raster_pages_are_recorded_as_unsupported_not_dropped(
    drawing: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(drawing, tmp_path))

    by_number = {page.page_number: page for page in result.pages}
    assert by_number[1].status == "processed"
    assert by_number[1].content_kind == "vector"
    assert by_number[2].status == "unsupported_raster"
    assert by_number[2].content_kind == "raster"
    # 未対応のページが一覧で取れること。何ページ読めなかったのかが
    # どこにも残らないのがいちばん困る。
    assert result.unsupported_pages == (2,)
    assert by_number[2].notes


def test_pages_without_drawing_objects_are_recorded_too(tmp_path: Path) -> None:
    path = tmp_path / "with_empty.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    doc.new_page(width=595, height=842)  # 何も描かないページ
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    assert result.pages[1].status == "unsupported_empty"
    assert result.unsupported_pages == (2,)


# ---------------------------------------------------------------------------
# 3. 縮尺・面積・開き戸がつながっている
# ---------------------------------------------------------------------------


def test_scale_areas_and_door_arcs_are_connected_in_one_pass(
    drawing: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(drawing, tmp_path))

    assert result.pages[0].scale is not None
    assert result.pages[0].scale.denominator == 50.0

    by_target = {item.target: item for item in result.findings}
    assert by_target["専有延床面積"].value_range == (PRIVATE_AREA_SQM, PRIVATE_AREA_SQM)
    assert by_target["施工床面積"].value_range == (
        CONSTRUCTION_AREA_SQM,
        CONSTRUCTION_AREA_SQM,
    )
    # 開き戸は 2 件。同じページに引戸を 2 つ描いてあるが、**円弧を描かないので
    # 拾えない。** これは不具合ではなく手法の限界で、P011 の新設建具が
    # 0 件になる理由そのもの。
    assert by_target["開き戸::ページ1"].value_range == (2.0, 2.0)


def test_door_arcs_are_not_extracted_when_the_scale_cannot_be_read(
    tmp_path: Path,
) -> None:
    """縮尺が読めないページで扉幅の実寸は出せない。**推測しない。**"""
    path = tmp_path / "no_scale.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc, scale_text="")
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    assert result.pages[0].scale is None
    assert not [item for item in result.findings if item.target.startswith("開き戸")]
    assert any("縮尺が読めない" in note for note in result.pages[0].notes)


def test_door_counts_are_not_summed_across_pages(tmp_path: Path) -> None:
    """既存平面図と新設平面図を足すと同じ建具を二重に数える。"""
    path = tmp_path / "two_plans.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc, door_widths_mm=(800.0, 750.0))
    _vector_plan_page(doc, door_widths_mm=(900.0,))
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    door_targets = {
        item.target: item.value_range
        for item in result.findings
        if item.target.startswith("開き戸")
    }
    assert door_targets == {"開き戸::ページ1": (2.0, 2.0), "開き戸::ページ2": (1.0, 1.0)}


def test_the_same_area_label_on_two_pages_is_one_data_source(tmp_path: Path) -> None:
    """同じ記載が複数ページにあっても、別のデータ源として数えない。"""
    path = tmp_path / "repeated.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    _vector_plan_page(doc)
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    areas = [item for item in result.findings if item.target == "専有延床面積"]
    assert len(areas) == 1
    assert len(areas[0].provenance["occurrences"]) == 2


def test_area_labels_that_disagree_across_pages_become_a_range(tmp_path: Path) -> None:
    """ページで値が食い違ったら、片方を黙って採らずレンジにする。"""
    path = tmp_path / "conflict.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(
        pymupdf.Point(100, 100), "専有延床面積 95.54 ㎡", fontname="japan", fontsize=11
    )
    page.draw_line(pymupdf.Point(0, 0), pymupdf.Point(10, 10))  # ベクターのページにする
    second = doc.new_page(width=1190, height=842)
    second.insert_text(
        pymupdf.Point(100, 100), "専有延床面積 97.10 ㎡", fontname="japan", fontsize=11
    )
    second.draw_line(pymupdf.Point(0, 0), pymupdf.Point(10, 10))
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    area = next(item for item in result.findings if item.target == "専有延床面積")
    assert area.value_range == (95.54, 97.10)
    assert "note" in area.provenance


# ---------------------------------------------------------------------------
# 4. 根拠が仲裁層まで届く
# ---------------------------------------------------------------------------


def test_provenance_reaches_the_orchestrator(drawing: Path, tmp_path: Path) -> None:
    """ページ番号・座標・元の文字列が、入口の記録だけで終わらないこと。"""
    from arbitration.inference_orchestrator import InferenceOrchestrator
    from intake.drawing_intake import to_orchestrator_request

    fingerprint = file_fingerprint(drawing)
    result = read_drawing(_config(drawing, tmp_path))
    area = next(item for item in result.findings if item.target == "専有延床面積")

    engine = InferenceOrchestrator(
        method_policies=dict(DEFAULT_METHOD_POLICIES),
        source_registry={"TEST-001::drawing": fingerprint},
    )
    request = to_orchestrator_request(
        [area],
        case_id="TEST-001",
        sources={
            "drawing": ("TEST-001::drawing", fingerprint),
            "start_kit": ("TEST-001::start_kit", "sha256:empty"),
        },
    )
    assert engine.process(request).decision is not None

    # 入口が持つ根拠。
    occurrence = area.provenance["occurrences"][0]
    assert occurrence["page_number"] == 1
    assert "専有延床面積" in occurrence["source_text"]
    assert occurrence["rect_pt"] is not None and len(occurrence["rect_pt"]) == 4

    doors = next(item for item in result.findings if item.target.startswith("開き戸"))
    assert doors.provenance["scale"] == "1/50"
    assert len(doors.provenance["arcs"]) == 2
    assert all("center_pt" in arc for arc in doors.provenance["arcs"])


def test_the_area_label_position_is_none_when_it_cannot_be_located(
    drawing: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """位置が取れなかったことを 0 や原点で埋めない。**値は残す。**

    `page.search_for()` は表示上の文字列を探すので、字送りや改行の都合で
    当たらないことがある。当たらなかったときに原点や 0 で埋めると、
    根拠として残した座標が嘘になる。
    """
    monkeypatch.setattr(pymupdf.Page, "search_for", lambda self, *a, **k: [])

    labels = find_area_labels(drawing, 0)

    assert [label.value_sqm for label in labels] == [
        PRIVATE_AREA_SQM,
        CONSTRUCTION_AREA_SQM,
    ]
    assert all(label.rect_pt is None for label in labels)


# ---------------------------------------------------------------------------
# 5. 開き戸を強い軸として扱わない
# ---------------------------------------------------------------------------


def test_the_door_arc_method_can_never_become_a_hard_constraint() -> None:
    """独立データで校正されていないので、名乗っても強い軸にならない。"""
    policy = DEFAULT_METHOD_POLICIES[METHOD_DOOR_ARC]
    assert policy.max_strength == "weak"
    assert policy.calibrated is False


def test_the_text_area_method_is_registered_as_uncalibrated() -> None:
    """P011 で 2 値が正解と一致したが、案件1件は校正ではない。"""
    policy = DEFAULT_METHOD_POLICIES[METHOD_TEXT_AREA]
    assert policy.calibrated is False


def test_nothing_is_auto_confirmed_through_this_path(
    drawing: Path, tmp_path: Path
) -> None:
    """**この経路だけでは 1 件も確定しない。**

    図面 PDF は 1 つのデータ源なので独立した強い軸が 2 つ揃わず、
    2 手法はどちらも未校正でハード制約に入らない。実装の不具合ではなく、
    実図面で確定できた数量が 0 件だという事実と同じ形である。
    どちらかを `calibrated=True` にしたら、このテストが必ず落ちる。
    """
    result = read_drawing(_config(drawing, tmp_path))

    assert result.decisions  # 判定そのものは走っている
    assert result.confirmed_targets == ()
    assert all(decision.tier == 3 for decision in result.decisions)
    assert all(decision.action == "requires_review" for decision in result.decisions)
    assert all(not decision.is_invalid for decision in result.decisions)
    assert all(
        "empirical_calibration_missing_or_failed" in decision.reason_codes
        for decision in result.decisions
    )


# ---------------------------------------------------------------------------
# 6. 面積の選択は案件ごとに1回だけ聞く
# ---------------------------------------------------------------------------


def test_without_an_answer_the_work_floor_area_is_not_produced(
    drawing: Path, tmp_path: Path
) -> None:
    result = read_drawing(_config(drawing, tmp_path))

    assert TARGET_WORK_FLOOR_AREA not in [item.target for item in result.findings]
    assert TARGET_WORK_FLOOR_AREA not in [item.target for item in result.decisions]
    question = result.pending_questions[0]
    assert question.question_id == QUESTION_AREA_BASIS
    assert question.options == AREA_BASIS_OPTIONS
    assert dict(question.observed) == {
        "専有延床面積": PRIVATE_AREA_SQM,
        "施工床面積": CONSTRUCTION_AREA_SQM,
    }
    # 何が止まっているかを見せる。養生・墨出し・清掃はこの回答待ち。
    assert "養生" in question.blocks


def test_the_answer_is_asked_once_and_reused_for_the_same_case(
    drawing: Path, tmp_path: Path
) -> None:
    answers_path = tmp_path / "answers.json"
    store = AnswerStore(answers_path)
    store.record(
        "TEST-001",
        QUESTION_AREA_BASIS,
        "専有延床面積",
        answered_by="おーちゃん",
        options=AREA_BASIS_OPTIONS,
    )

    result = read_drawing(_config(drawing, tmp_path))

    assert result.pending_questions == ()
    work = next(
        item for item in result.findings if item.target == TARGET_WORK_FLOOR_AREA
    )
    assert work.value_range == (PRIVATE_AREA_SQM, PRIVATE_AREA_SQM)
    assert work.provenance["area_basis"]["answer"] == "専有延床面積"
    assert work.provenance["area_basis"]["answered_by"] == "おーちゃん"
    # 同じ回答を使い回す先が記録されていること。
    assert "清掃" in work.provenance["area_basis"]["reused_for"]

    # 別の案件には使い回さない。
    other = read_drawing(
        IntakeConfig(pdf_path=drawing, case_id="TEST-002", answers_path=answers_path)
    )
    assert other.pending_questions
    assert TARGET_WORK_FLOOR_AREA not in [item.target for item in other.findings]


def test_the_answer_survives_a_new_process(drawing: Path, tmp_path: Path) -> None:
    """回答はリポジトリの外のファイルに残り、次の実行で読み直される。"""
    answers_path = tmp_path / "outside" / "answers.json"
    AnswerStore(answers_path).record(
        "TEST-001",
        QUESTION_AREA_BASIS,
        "施工床面積",
        answered_by="おーちゃん",
        options=AREA_BASIS_OPTIONS,
    )

    reloaded = AnswerStore(answers_path)
    assert reloaded.get("TEST-001", QUESTION_AREA_BASIS).answer == "施工床面積"

    result = read_drawing(
        IntakeConfig(pdf_path=drawing, case_id="TEST-001", answers_path=answers_path)
    )
    work = next(
        item for item in result.findings if item.target == TARGET_WORK_FLOOR_AREA
    )
    assert work.value_range == (CONSTRUCTION_AREA_SQM, CONSTRUCTION_AREA_SQM)


def test_an_answer_outside_the_options_is_refused() -> None:
    store = AnswerStore(None)
    with pytest.raises(AnswerError):
        store.record(
            "TEST-001",
            QUESTION_AREA_BASIS,
            "延床面積",
            answered_by="おーちゃん",
            options=AREA_BASIS_OPTIONS,
        )


def test_an_existing_answer_is_not_silently_overwritten() -> None:
    store = AnswerStore(None)
    store.record(
        "TEST-001", QUESTION_AREA_BASIS, "専有延床面積", answered_by="おーちゃん"
    )
    with pytest.raises(AnswerError):
        store.record(
            "TEST-001", QUESTION_AREA_BASIS, "施工床面積", answered_by="おーちゃん"
        )


def test_no_question_is_asked_when_no_area_is_readable(tmp_path: Path) -> None:
    """面積の記載が読めない図面で、聞く意味のない質問を出さない。"""
    path = tmp_path / "no_area.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc, areas=False)
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    assert result.pending_questions == ()


def test_a_fully_scanned_drawing_yields_no_quantities(tmp_path: Path) -> None:
    """全ページがスキャンなら 0 件。**0 件を 0 件として返すこと。**

    P011 の匿名化 v1 がこの形だった(34 ページ全部がスキャン画像)。
    """
    path = tmp_path / "scanned.pdf"
    doc = pymupdf.open()
    _scanned_page(doc)
    _scanned_page(doc)
    doc.save(path)
    doc.close()

    result = read_drawing(IntakeConfig(pdf_path=path, case_id="TEST-001"))

    assert result.findings == ()
    assert result.decisions == ()
    assert result.unsupported_pages == (1, 2)
    assert "読めた数量: 0 件" in result.summary()


def test_the_door_arc_geometry_is_what_the_count_is_based_on(
    drawing: Path, tmp_path: Path
) -> None:
    """拾った円弧の実寸が、描いた扉幅と合っていること(単位の取り違え防止)。"""
    result = read_drawing(_config(drawing, tmp_path))
    doors = next(item for item in result.findings if item.target.startswith("開き戸"))
    widths = sorted(arc["width_mm"] for arc in doors.provenance["arcs"])

    assert all(math.isclose(got, want, rel_tol=0.01) for got, want in zip(widths, [750.0, 800.0]))


def _one_page(path: Path, *, scale: bool) -> Path:
    """1 ページだけの平面図。**開き戸は 1 つも描かない。**

    `scale` が False のときは縮尺の印字だけを落とし、代わりに同じだけ別の文字を置く
    (文字の有無そのものが差にならないようにする)。
    """
    doc = pymupdf.open()
    if scale:
        _vector_plan_page(
            doc, scale_text="1/50", door_widths_mm=(), sliding_doors=2, areas=False
        )
    else:
        page = doc.new_page(width=1190, height=842)
        page.insert_text(
            pymupdf.Point(850, 780), "図面名 平面図", fontname="japan", fontsize=11
        )
        _draw_sliding_door(page, 150.0, 600.0, 1600.0 * PT_PER_MM_AT_50)
        _draw_sliding_door(page, 330.0, 600.0, 1600.0 * PT_PER_MM_AT_50)
    doc.save(path)
    doc.close()
    return path


def test_the_summary_says_when_a_method_could_not_be_attempted(tmp_path: Path) -> None:
    """**「探したが 0 件」と「探せなかった」を、人が読む要約が区別すること。**

    どちらも数量は 0 件になる。違うのは理由だけで、その理由はページごとの
    記録には残っているが、**要約に載っていなければ人には届かない**
    (`docs/d_summary_distinguishes_report.md`)。

    縮尺が読めないページでは開き戸を探さない決まりがある
    (`intake/drawing_intake.py` の `if scale is not None:`)。
    **探さなかったことが要約から分かること。**
    """
    searched = read_drawing(
        IntakeConfig(pdf_path=_one_page(tmp_path / "with.pdf", scale=True), case_id="T")
    )
    not_searched = read_drawing(
        IntakeConfig(pdf_path=_one_page(tmp_path / "without.pdf", scale=False), case_id="T")
    )

    # どちらも数量は 0 件。ここが違うと理由の比較にならない。
    assert searched.findings == ()
    assert not_searched.findings == ()

    assert searched.pages_with_unattempted_methods == ()
    assert not_searched.pages_with_unattempted_methods == (1,)

    assert "試せなかった手法があるページ: 0 件" in searched.summary()
    assert "試せなかった手法があるページ: 1 件" in not_searched.summary()
    assert searched.summary() != not_searched.summary()
