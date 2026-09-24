"""スタートキット(人が最初に決める前提)の通しテスト。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**
顧客の図面はリポジトリに置かない。

守りたいのは 5 つ。

1. **前提は必須ではない。** 与えられなければ今までどおり動く
   (それは `tests/test_drawing_intake.py` が押さえている)。
2. 基準点から求めた縮尺が、表題欄の印字とは**独立した読み**として証拠になる。
3. 2 つの読みが食い違えば、**どちらも採らずに判断待ちとして記録する。**
   そのページでは実寸に依存する抽出(開き戸)を行わない。
4. **人の入力も、それだけを根拠に自動確定させない。**
5. 人が入れた前提の取り違え(縦と横の逆、存在しないページ)は、
   黙って直さずに止める。
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

from arbitration.method_policies import (
    DEFAULT_METHOD_POLICIES,
    METHOD_HUMAN_REFERENCE_POINT,
)
from axes.image_axis.pdf_vector_symbols import METHOD_TEXT_SCALE, MM_PER_POINT
from intake.case_answers import QUESTION_AREA_BASIS, AnswerStore
from intake.drawing_intake import (
    TARGET_WORK_FLOOR_AREA,
    IntakeConfig,
    IntakeError,
    read_drawing,
    start_kit_fingerprint,
)
from estimating.from_intake import quantities_from_intake
from intake.start_kit import (
    PageDeclaration,
    PagePairing,
    ReferencePoint,
    StartKit,
    StartKitError,
)
from tests.test_drawing_intake import (
    CONSTRUCTION_AREA_SQM,
    PRIVATE_AREA_SQM,
    _scanned_page,
    _vector_plan_page,
)

#: 合成図面の表題欄に印字する縮尺。
PRINTED_DENOMINATOR = 50.0


def _points_for(denominator: float, length_mm: float) -> tuple[
    tuple[float, float], tuple[float, float]
]:
    """その縮尺なら `length_mm` になるような、横向きの 2 点を作る。"""
    distance_pt = length_mm / (MM_PER_POINT * denominator)
    return (100.0, 400.0), (100.0 + distance_pt, 400.0)


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


def _reference_point(
    *, denominator: float = PRINTED_DENOMINATOR, length_mm: float = 3000.0
) -> ReferencePoint:
    a, b = _points_for(denominator, length_mm)
    return ReferencePoint(
        page_number=1,
        axis="horizontal",
        point_a_pt=a,
        point_b_pt=b,
        actual_length_mm=length_mm,
        entered_by="おーちゃん",
    )


# ---------------------------------------------------------------------------
# 1. 前提は必須ではない
# ---------------------------------------------------------------------------


def test_an_empty_start_kit_changes_nothing(drawing: Path, tmp_path: Path) -> None:
    without = read_drawing(IntakeConfig(pdf_path=drawing, case_id="TEST-001"))
    with_empty = read_drawing(
        IntakeConfig(pdf_path=drawing, case_id="TEST-001", start_kit=StartKit())
    )

    assert [item.target for item in with_empty.findings] == [
        item.target for item in without.findings
    ]
    assert with_empty.pending_decisions == ()
    assert StartKit().is_empty


# ---------------------------------------------------------------------------
# 2. 基準点から求めた縮尺が、独立した読みとして証拠になる
# ---------------------------------------------------------------------------


def test_the_reference_point_scale_is_recorded_beside_the_printed_one(
    drawing: Path,
) -> None:
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(_reference_point(),)),
        )
    )

    page = result.pages[0]
    origins = {reading.origin for reading in page.scale_readings}
    assert origins == {"表題欄の印字", "人が入れた基準点(横)"}
    # 一致しているので使える縮尺が決まる。人が入れたほうを使う。
    assert page.scale is not None
    assert math.isclose(page.scale.denominator, PRINTED_DENOMINATOR, rel_tol=1e-6)
    assert result.pending_decisions == ()


def test_the_two_readings_of_the_reference_dimension_reach_the_orchestrator(
    drawing: Path,
) -> None:
    """人の入力と図面の印字が、**別のデータ源**として 1 つの対象に届くこと。"""
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(_reference_point(),)),
        )
    )

    target = "基準寸法::ページ1::横"
    readings = [item for item in result.findings if item.target == target]
    assert {item.method_id for item in readings} == {
        METHOD_HUMAN_REFERENCE_POINT,
        METHOD_TEXT_SCALE,
    }
    assert {item.source_kind for item in readings} == {"start_kit", "drawing"}
    # 両方が 3000mm を指している。
    for item in readings:
        assert item.value_range[0] <= 3000 <= item.value_range[1]

    # 1 つの判定にまとまっていること(別々の要求に分かれていたら
    # 突き合わせが起きない)。
    decisions = [item for item in result.decisions if item.target == target]
    assert len(decisions) == 1
    assert not decisions[0].is_invalid


def test_the_shared_coordinates_caveat_is_carried_in_the_evidence(
    drawing: Path,
) -> None:
    """2 点の座標を共有していることを、根拠に残しておくこと。"""
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(_reference_point(),)),
        )
    )

    readings = [
        item for item in result.findings if item.target == "基準寸法::ページ1::横"
    ]
    assert all("independence_note" in item.provenance for item in readings)
    assert all(
        "座標の取り違えは両方に同じように効く" in item.provenance["independence_note"]
        for item in readings
    )


def test_the_start_kit_has_its_own_fingerprint() -> None:
    """人が入れた前提を書き換えたら指紋も変わること。"""
    first = StartKit(reference_points=(_reference_point(length_mm=3000.0),))
    second = StartKit(reference_points=(_reference_point(length_mm=3600.0),))

    assert start_kit_fingerprint(first) != start_kit_fingerprint(second)
    assert start_kit_fingerprint(first) == start_kit_fingerprint(
        StartKit(reference_points=(_reference_point(length_mm=3000.0),))
    )


def test_a_reference_point_works_without_a_printed_scale(tmp_path: Path) -> None:
    """印字が読めない図面でも、人が入れた基準点があれば縮尺が決まること。"""
    path = tmp_path / "no_scale.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc, scale_text="")
    doc.save(path)
    doc.close()

    result = read_drawing(
        IntakeConfig(
            pdf_path=path,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(_reference_point(),)),
        )
    )

    assert result.pages[0].scale is not None
    assert result.pending_decisions == ()
    # 縮尺が決まったので、開き戸が拾えるようになる。
    assert [item.target for item in result.findings if item.target.startswith("開き戸")]
    # 相手のいない読みは 1 件だけ(印字が無いので突き合わせはできない)。
    readings = [
        item for item in result.findings if item.target == "基準寸法::ページ1::横"
    ]
    assert [item.method_id for item in readings] == [METHOD_HUMAN_REFERENCE_POINT]


# ---------------------------------------------------------------------------
# 3. 食い違えば、どちらも採らずに判断待ちにする
# ---------------------------------------------------------------------------


def test_a_scale_disagreement_is_recorded_and_stops_length_extraction(
    drawing: Path,
) -> None:
    """印字 1/50 に対し、実測が 1/18 相当。**どちらも採らない。**

    P011 で実際に起きた形(A3 の図面を A0 で出したため、印字は 1/50 のままで
    実効の縮尺は約 1/18)。
    """
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                reference_points=(_reference_point(denominator=18.0),)
            ),
        )
    )

    page = result.pages[0]
    assert page.scale is None
    assert len(page.scale_readings) == 2
    pending = [
        item for item in result.pending_decisions if item.kind == "scale_disagreement"
    ]
    assert len(pending) == 1
    assert pending[0].page_number == 1
    assert dict(pending[0].observed).keys() == {"表題欄の印字", "人が入れた基準点(横)"}
    # 実寸に依存する抽出はしない。
    assert not [
        item for item in result.findings if item.target.startswith("開き戸")
    ]
    assert any("食い違った" in note for note in page.notes)
    assert "判断待ち: 1 件" in result.summary()


def test_a_small_difference_within_the_tolerance_is_not_a_disagreement(
    drawing: Path,
) -> None:
    """人が指す位置には誤差がある。1% のずれで止めない。"""
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                reference_points=(_reference_point(denominator=50.5),)
            ),
        )
    )

    assert result.pages[0].scale is not None
    assert result.pending_decisions == ()


def test_the_two_reference_points_are_checked_against_each_other(
    drawing: Path,
) -> None:
    """縦と横で縮尺が違えば、それも食い違いとして記録すること。"""
    horizontal = _reference_point(denominator=PRINTED_DENOMINATOR)
    vertical_a, vertical_b = _points_for(18.0, 3000.0)
    vertical = ReferencePoint(
        page_number=1,
        axis="vertical",
        point_a_pt=(200.0, 100.0),
        point_b_pt=(200.0, 100.0 + (vertical_b[0] - vertical_a[0])),
        actual_length_mm=3000.0,
        entered_by="おーちゃん",
    )

    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(horizontal, vertical)),
        )
    )

    assert result.pages[0].scale is None
    assert [item.kind for item in result.pending_decisions] == ["scale_disagreement"]
    assert len(result.pages[0].scale_readings) == 3


# ---------------------------------------------------------------------------
# 4. 人の入力だけを根拠に自動確定させない
# ---------------------------------------------------------------------------


def test_human_input_is_registered_as_uncalibrated() -> None:
    policy = DEFAULT_METHOD_POLICIES[METHOD_HUMAN_REFERENCE_POINT]
    assert policy.calibrated is False


def test_nothing_is_auto_confirmed_even_with_a_start_kit(drawing: Path) -> None:
    """人が前提を入れても、自動確定は 1 件も起きないこと。

    人の入力は図面とは別のデータ源なので、**独立した読みが 2 つ揃う。**
    それでも確定しないのは、どちらの手法も実測校正を通っていないためである。
    ここが崩れると「人が1回入れたら自動で確定する」経路ができてしまう。
    """
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(reference_points=(_reference_point(),)),
        )
    )

    assert result.decisions
    assert result.confirmed_targets == ()
    assert all(decision.tier == 3 for decision in result.decisions)
    assert all(decision.action == "requires_review" for decision in result.decisions)


# ---------------------------------------------------------------------------
# 5. 取り違えを黙って直さない
# ---------------------------------------------------------------------------


def test_an_axis_that_contradicts_the_two_points_is_refused() -> None:
    """縦の寸法を横として入れると、縮尺をそのまま間違える。"""
    with pytest.raises(StartKitError):
        ReferencePoint(
            page_number=1,
            axis="horizontal",
            point_a_pt=(100.0, 100.0),
            point_b_pt=(100.0, 400.0),  # 縦に離れている
            actual_length_mm=3000.0,
        )


def test_two_reference_points_on_the_same_axis_are_refused() -> None:
    """縦・横それぞれ 1 組まで。2 組あるとどちらを使うかを黙って決めることになる。"""
    with pytest.raises(StartKitError):
        StartKit(reference_points=(_reference_point(), _reference_point()))


def test_a_reference_point_on_a_page_that_does_not_exist_is_refused(
    drawing: Path,
) -> None:
    """存在しないページへの前提を読み飛ばすと、効いていないのに効いて見える。"""
    a, b = _points_for(PRINTED_DENOMINATOR, 3000.0)
    config = IntakeConfig(
        pdf_path=drawing,
        case_id="TEST-001",
        start_kit=StartKit(
            reference_points=(
                ReferencePoint(
                    page_number=99, axis="horizontal", point_a_pt=a, point_b_pt=b,
                    actual_length_mm=3000.0,
                ),
            )
        ),
    )
    with pytest.raises(StartKitError):
        read_drawing(config)


def test_a_zero_length_reference_point_is_refused() -> None:
    with pytest.raises(StartKitError):
        ReferencePoint(
            page_number=1,
            axis="horizontal",
            point_a_pt=(100.0, 100.0),
            point_b_pt=(100.0, 100.0),
            actual_length_mm=3000.0,
        )


def test_a_pairing_of_a_page_with_itself_is_refused() -> None:
    with pytest.raises(StartKitError):
        PagePairing(existing_page=3, planned_page=3)


# ---------------------------------------------------------------------------
# ページの種類と、現況/計画/解体の区別
# ---------------------------------------------------------------------------


def test_the_phase_is_carried_into_the_meaning(drawing: Path) -> None:
    """人が宣言した現況/計画が、読みの下流に残ること。

    **確かめ方を 2026-09-23 に変えた。** それまでは対象の名前
    (`開き戸::現況::ページ1`)で確かめていたが、対象名に入れると
    `QuantityItem.kind` が `::` の手前しか見ないため、**規則からも
    差分の層からも `現況` が見えなかった**
    (`docs/principles/scope_of_work_diff.md` 3-2)。
    現況/計画は意味の4欄(`Meaning.phase`)に入れることにしたので、
    **確かめる先をそちらに変える。狙い(人の宣言が下流に残ること)は変えない。**
    """
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                page_declarations=(
                    PageDeclaration(page_number=1, kind="平面図", phase="現況"),
                )
            ),
        )
    )

    arc = next(
        item for item in result.findings if item.target == "開き戸::ページ1"
    )
    assert arc.meaning is not None
    assert arc.meaning.phase == "現況"
    assert result.pages[0].declaration is not None
    assert result.pages[0].declaration.phase == "現況"

    # **当てはめの層まで届くこと。** 入口で付けても写されなければ意味がない。
    quantity = next(
        item
        for item in quantities_from_intake(result)
        if item.target == "開き戸::ページ1"
    )
    assert quantity.phase == "現況"


def test_the_phase_is_not_in_the_target_name(drawing: Path) -> None:
    """現況/計画を対象の名前に入れないこと(2026-09-23)。

    対象名は**仲裁層から見た対象の同一性**である。そこに現況/計画を混ぜると、
    名前を切り出さないと区別できず、`kind` を見る規則からは消える。
    """
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                page_declarations=(
                    PageDeclaration(page_number=1, kind="平面図", phase="現況"),
                )
            ),
        )
    )

    assert not [
        item.target for item in result.findings if "::現況::" in item.target
    ]


def test_an_unknown_phase_does_not_invent_a_label(drawing: Path) -> None:
    """「不明」を「計画」として扱わない。"""
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                page_declarations=(PageDeclaration(page_number=1, kind="平面図"),)
            ),
        )
    )

    # **元の検査文をそのまま残す**(おーちゃんの判断、2026-09-24)。
    # 2026-09-23 に、この行も意味の4欄を見る形に書き換えていたが、
    # **この検査文は変更の後でもそのまま通る。** 落ちていないテストを
    # 書き方をそろえるために触るのは、書き換えてよい範囲ではなかった。
    assert "開き戸::ページ1" in [item.target for item in result.findings]


# `test_door_arcs_are_not_hunted_on_a_page_declared_as_a_table` は
# **2026-09-22 に削除した。** 「建具表と宣言されたら開き戸は 0 件」を
# 正しい振る舞いとして固定していたが、その振る舞い自体が原則4の条件3
# 「図面の読み方を縛らない」に反していた(おーちゃんの判断、案A)。
# 代わりは `tests/test_page_kind_is_a_weak_hint.py` の 11 件である。
# 削除した理由と、元のテストの心配事をどれが受け持つかは、そのファイルの
# docstring と `docs/page_kind_weak_hint.md` 4 節に書いてある。


def test_the_pairing_is_recorded_but_no_difference_is_computed(
    drawing: Path,
) -> None:
    """対応は記録するが、差分(撤去・新設)の数量は作らないこと。"""
    doc = pymupdf.open()
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(
                page_pairings=(PagePairing(existing_page=1, planned_page=2),)
            ),
        )
    )
    doc.close()

    assert result.page_pairings == (PagePairing(existing_page=1, planned_page=2),)
    assert not [
        item
        for item in result.findings
        if "撤去" in item.target or "新設" in item.target
    ]


# ---------------------------------------------------------------------------
# 面積の選択も同じ入力に乗せる
# ---------------------------------------------------------------------------


def test_the_area_basis_can_be_answered_through_the_start_kit(
    drawing: Path,
) -> None:
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            start_kit=StartKit(area_basis="専有延床面積", entered_by="おーちゃん"),
        )
    )

    assert result.pending_questions == ()
    work = next(
        item for item in result.findings if item.target == TARGET_WORK_FLOOR_AREA
    )
    assert work.value_range == (PRIVATE_AREA_SQM, PRIVATE_AREA_SQM)
    assert work.provenance["area_basis"]["answered_by"] == "おーちゃん"


def test_a_start_kit_that_contradicts_a_saved_answer_is_a_pending_decision(
    drawing: Path, tmp_path: Path
) -> None:
    """保存済みの回答と食い違ったら、**どちらも採らない。**"""
    answers_path = tmp_path / "answers.json"
    AnswerStore(answers_path).record(
        "TEST-001", QUESTION_AREA_BASIS, "施工床面積", answered_by="おーちゃん"
    )

    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            answers_path=answers_path,
            start_kit=StartKit(area_basis="専有延床面積"),
        )
    )

    assert [item.kind for item in result.pending_decisions] == ["area_basis_conflict"]
    assert TARGET_WORK_FLOOR_AREA not in [item.target for item in result.findings]


def test_an_area_basis_outside_the_options_is_refused() -> None:
    with pytest.raises(StartKitError):
        StartKit(area_basis="延床面積")


def test_a_start_kit_answer_that_matches_the_saved_one_is_not_a_conflict(
    drawing: Path, tmp_path: Path
) -> None:
    answers_path = tmp_path / "answers.json"
    AnswerStore(answers_path).record(
        "TEST-001", QUESTION_AREA_BASIS, "施工床面積", answered_by="おーちゃん"
    )

    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing,
            case_id="TEST-001",
            answers_path=answers_path,
            start_kit=StartKit(area_basis="施工床面積"),
        )
    )

    assert result.pending_decisions == ()
    work = next(
        item for item in result.findings if item.target == TARGET_WORK_FLOOR_AREA
    )
    assert work.value_range == (CONSTRUCTION_AREA_SQM, CONSTRUCTION_AREA_SQM)
