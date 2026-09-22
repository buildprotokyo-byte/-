"""入口(`intake/drawing_intake.py`)に、記入された寸法の読み取りをつないだ通しテスト。

固定したいのは 3 つ。どれも原則3-1(`docs/principles/start_kit.md`)の
**いままで実装できなかった約束**である。

1. **基準点が無いページでも、図面に記入された寸法から縮尺が出る。**
   これまでは表題欄の印字しか無く、印字も無いページでは実寸に依存する抽出
   (開き戸)を1件も行えなかった。
2. **人が入れた基準点を、図面の寸法の数字で検算して、合わなければ警告する。**
   原則3-1の最後の1行である。突き合わせる相手が無かったので未実装だった。
3. **それでも自動確定は1件も起きない。** 寸法の読み取りは未校正の手法として
   登録してある。

テスト用の PDF はこの中で組み立てる合成のベクター PDF である
(実図面は匿名化済みでもリポジトリに置かない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_dimensions import METHOD_DIMENSION_SCALE, METHOD_DIMENSION_TEXT
from axes.reading.meaning import PURPOSE_RECEIVED_UNLINKED, PURPOSE_UNLINKED
from intake.drawing_intake import (
    KIND_HUMAN_VS_DIMENSION,
    ORIGIN_DIMENSION_TEXT,
    IntakeConfig,
    read_drawing,
)
from intake.start_kit import PageDeclaration, Purpose, ReferencePoint, StartKit

#: 実寸 1mm が 1/50 の図面で何ポイントか。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72


def _h_dimension(page: pymupdf.Page, x0: float, y: float, length_mm: float) -> float:
    """横向きの寸法を 1 組描いて、右端の x を返す。"""
    x1 = x0 + length_mm * PT_PER_MM_AT_50
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y))
    shape.draw_line(pymupdf.Point(x0, y - 5), pymupdf.Point(x0, y + 5))
    shape.draw_line(pymupdf.Point(x1, y - 5), pymupdf.Point(x1, y + 5))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    text = f"{length_mm:.0f}"
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=8)
    page.insert_text(
        pymupdf.Point((x0 + x1) / 2 - width / 2, y - 4),
        text,
        fontsize=8,
        fontname="helv",
    )
    return x1


def _quarter_arc(page: pymupdf.Page, x: float, y: float, width_mm: float) -> None:
    start = pymupdf.Point(x + width_mm * PT_PER_MM_AT_50, y)
    shape = page.new_shape()
    shape.draw_sector(pymupdf.Point(x, y), start, 90)
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _plan_pdf(
    path: Path,
    *,
    scale_text: str | None = None,
    dimensions_mm: tuple[float, ...] = (3640.0, 1820.0, 2730.0),
) -> Path:
    """表題欄の縮尺**を書かない**平面図。寸法だけが長さの出どころになる。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    if scale_text is not None:
        page.insert_text(
            pymupdf.Point(850, 780), f"縮尺 {scale_text}", fontname="japan", fontsize=11
        )
    for index, length_mm in enumerate(dimensions_mm):
        _h_dimension(page, 200.0, 700.0 + index * 40.0, length_mm)
    _quarter_arc(page, 150.0, 300.0, 800.0)
    doc.save(path)
    doc.close()
    return path


def _reference_point(length_mm: float) -> ReferencePoint:
    """1/50 で 3640mm に当たる2点を指し、実寸を `length_mm` として入れた基準点。"""
    return ReferencePoint(
        page_number=1,
        axis="horizontal",
        point_a_pt=(200.0, 500.0),
        point_b_pt=(200.0 + 3640.0 * PT_PER_MM_AT_50, 500.0),
        actual_length_mm=length_mm,
        entered_by="tester",
    )


def test_a_page_without_a_reference_point_gets_its_scale_from_the_dimensions(
    tmp_path: Path,
) -> None:
    """表題欄の縮尺も基準点も無いページで、記入された寸法から縮尺が出る。

    **これまではこのページで実寸に依存する抽出が1件もできなかった。**
    """
    path = _plan_pdf(tmp_path / "no_scale.pdf")
    result = read_drawing(IntakeConfig(pdf_path=path, case_id="case-dim"))

    page = result.pages[0]
    assert page.scale is not None, "寸法から縮尺が出ていない"
    assert page.scale.denominator == pytest.approx(50.0, rel=0.02)
    origins = [reading.origin for reading in page.scale_readings]
    assert ORIGIN_DIMENSION_TEXT in origins
    # 実寸が出せるようになったので、開き戸の抽出まで進む。
    assert any(finding.target.startswith("開き戸::") for finding in result.findings)


def test_the_dimension_numbers_become_findings_with_meaning(tmp_path: Path) -> None:
    """記入された寸法そのものが、意味の4欄と根拠つきの数量として出る。"""
    path = _plan_pdf(tmp_path / "meaning.pdf")
    result = read_drawing(IntakeConfig(pdf_path=path, case_id="case-dim"))

    dimensions = [
        finding
        for finding in result.findings
        if finding.method_id == METHOD_DIMENSION_TEXT
    ]
    assert len(dimensions) == 3
    for finding in dimensions:
        assert finding.unit == "mm"
        assert finding.meaning is not None
        assert finding.meaning.what == "図面に記入された寸法"
        assert "ページ1" in finding.meaning.where
        assert finding.meaning.phase == "不明"
        assert finding.meaning.purpose_link == PURPOSE_UNLINKED
        assert finding.meaning.is_complete is False
        assert finding.provenance["page_number"] == 1
        assert finding.provenance["source_text"]
        assert finding.provenance["start_pt"]


def test_the_declared_phase_and_a_received_purpose_reach_the_meaning(
    tmp_path: Path,
) -> None:
    """人が宣言した現況/計画と、目的が渡されているかどうかが意味に入る。

    目的が渡されていても、**その目的と個々の寸法の結び付けはまだ無い**ので
    「受領済み・結び付けは未実装」の印が入る。方向性の自由記述は写さない。
    """
    path = _plan_pdf(tmp_path / "phase.pdf")
    start_kit = StartKit(
        page_declarations=(PageDeclaration(page_number=1, kind="平面図", phase="現況"),),
        purpose=Purpose(direction="戸建ての水回りリフォーム"),
    )
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="case-dim", start_kit=start_kit)
    )

    dimensions = [
        finding
        for finding in result.findings
        if finding.method_id == METHOD_DIMENSION_TEXT
    ]
    assert dimensions
    for finding in dimensions:
        assert finding.meaning is not None
        assert finding.meaning.phase == "現況"
        assert finding.meaning.purpose_link == PURPOSE_RECEIVED_UNLINKED
        # 目的が来ていても、結び付けが無いので意味は完成していない。
        assert finding.meaning.is_complete is False
        assert "戸建ての水回りリフォーム" not in finding.meaning.purpose_link


def test_a_reference_point_that_agrees_with_the_dimensions_is_kept(
    tmp_path: Path,
) -> None:
    """人が入れた基準点が寸法と合っていれば、そのまま使う(警告は出ない)。"""
    path = _plan_pdf(tmp_path / "agree.pdf")
    start_kit = StartKit(reference_points=(_reference_point(3640.0),))
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="case-dim", start_kit=start_kit)
    )

    assert result.pending_decisions == ()
    page = result.pages[0]
    assert page.scale is not None
    assert page.scale.denominator == pytest.approx(50.0, rel=0.02)


def test_a_reference_point_that_disagrees_with_the_dimensions_is_warned(
    tmp_path: Path,
) -> None:
    """人のクリックや入力の誤りを、図面の寸法の数字で検算して警告する。

    原則3-1の最後の1行。**合わなければどちらも採らず、実寸に依存する抽出を
    しない。**
    """
    path = _plan_pdf(tmp_path / "disagree.pdf")
    # 同じ2点に、実寸 3640mm ではなく 5000mm と入れてしまった場合。
    start_kit = StartKit(reference_points=(_reference_point(5000.0),))
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="case-dim", start_kit=start_kit)
    )

    kinds = [pending.kind for pending in result.pending_decisions]
    assert KIND_HUMAN_VS_DIMENSION in kinds
    warning = next(
        pending
        for pending in result.pending_decisions
        if pending.kind == KIND_HUMAN_VS_DIMENSION
    )
    assert "人" in warning.detail
    assert warning.page_number == 1
    # どちらも採らないので、このページの縮尺は未確定のまま。
    page = result.pages[0]
    assert page.scale is None
    assert not any(finding.target.startswith("開き戸::") for finding in result.findings)


def test_the_dimension_scale_checks_the_reference_length_as_a_third_reading(
    tmp_path: Path,
) -> None:
    """人が入れた基準の長さに、寸法から求めた縮尺での読みが並ぶ。

    同じ対象に複数の読みが並ぶので、突き合わせは仲裁層でも起きる。
    """
    path = _plan_pdf(tmp_path / "third.pdf", scale_text="1/50")
    start_kit = StartKit(reference_points=(_reference_point(3640.0),))
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="case-dim", start_kit=start_kit)
    )

    target = "基準寸法::ページ1::横"
    methods = {
        finding.method_id for finding in result.findings if finding.target == target
    }
    assert METHOD_DIMENSION_SCALE in methods, "寸法から求めた縮尺での検算が無い"
    assert "human_reference_point" in methods
    assert "pdf_text_scale" in methods


def test_nothing_is_auto_confirmed_even_with_dimension_readings(
    tmp_path: Path,
) -> None:
    """寸法が読めても自動確定は1件も起きない。

    `pdf_dimension_text` / `pdf_dimension_scale` を `calibrated=True` に
    したら、このテストが必ず落ちる。
    """
    path = _plan_pdf(tmp_path / "confirm.pdf", scale_text="1/50")
    start_kit = StartKit(reference_points=(_reference_point(3640.0),))
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="case-dim", start_kit=start_kit)
    )
    assert result.confirmed_targets == ()


def test_the_page_keeps_the_dimension_readings_it_used(tmp_path: Path) -> None:
    """読めた寸法をページの記録に残す。**読まなかった数字も残す。**"""
    path = _plan_pdf(tmp_path / "kept.pdf")
    result = read_drawing(IntakeConfig(pdf_path=path, case_id="case-dim"))
    page = result.pages[0]
    assert len(page.dimension_readings) == 3
    assert page.dimension_scale is not None
    assert page.dimension_scale.agreeing_count == 3


def test_a_page_with_no_dimensions_behaves_as_before(tmp_path: Path) -> None:
    """寸法が1件も読めないページは、今までどおり印字の縮尺で動く。"""
    path = _plan_pdf(tmp_path / "none.pdf", scale_text="1/50", dimensions_mm=())
    result = read_drawing(IntakeConfig(pdf_path=path, case_id="case-dim"))
    page = result.pages[0]
    assert page.dimension_readings == ()
    assert page.dimension_scale is None
    assert page.scale is not None
    assert page.scale.denominator == 50.0
