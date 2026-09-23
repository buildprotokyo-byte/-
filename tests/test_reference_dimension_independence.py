"""基準寸法の3つの読みを、独立したデータ源として数えない。

2026-09-23、おーちゃんの判断。「人が入れた基準点と、図面に印字された縮尺の
一致を、独立した2つの根拠として数えるのをやめる」。

**理由は、コードが前から書いていたとおり。** 3つの読みは**2点の座標を共有**
していて、独立なのは縮尺の部分だけである。しかも人は印字された縮尺を見ながら
入力できるので、**写した値でも一致する**(`docs/d_human_independence_report.md`)。

49周目に測ったとおり、この但し書きは `provenance` に残るだけで判定を
1ミリも動かしていなかった(`docs/d_independence_note_report.md`)。
**ここで初めて効かせる。**
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from intake.drawing_intake import IntakeConfig, read_drawing
from intake.start_kit import ReferencePoint, StartKit

PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72


def _evidence(*, source_id: str, fingerprint: str, group: str, method: str) -> AxisEvidence:
    return AxisEvidence(
        target="基準寸法::ページ1::横",
        count_range=(1000, 1000),
        unit="mm",
        source_id=source_id,
        source_fingerprint=fingerprint,
        axis_id="image",
        method_id=method,
        strength="strong",
        status="confident",
        calibrated=True,
        derivation="read",
        independence_group=group,
    )


def test_readings_in_the_same_group_count_as_one_source() -> None:
    """**同じ群の読みは、指紋が違っても 1 つのデータ源として数える。**

    この検査は `calibrated=True` で行う。いまはどの手法も未校正なので
    自動確定は起きないが、**校正済みにした瞬間に通ってしまう**のが
    47〜50周目に測った「危ない裏返し」だった。そこを塞ぐ。
    """
    group = "基準寸法::ページ1::横"
    grouped = [
        _evidence(source_id="drawing", fingerprint="aaa", group=group, method="pdf_text_scale"),
        _evidence(source_id="start_kit", fingerprint="bbb", group=group,
                  method="human_reference_point"),
    ]
    verdict = AxisQualityFirewall().assess(grouped)
    assert verdict.tier != 1
    assert any("2つ未満" in reason for reason in verdict.reasons)

    # 群を外せば、同じ値でも 2 つのデータ源として数える(検査が効いている証拠)。
    ungrouped = [
        _evidence(source_id="drawing", fingerprint="aaa", group="", method="pdf_text_scale"),
        _evidence(source_id="start_kit", fingerprint="bbb", group="",
                  method="human_reference_point"),
    ]
    assert AxisQualityFirewall().assess(ungrouped).tier == 1


def test_the_intake_puts_the_reference_dimension_readings_in_one_group(
    tmp_path: Path,
) -> None:
    """入口が作る基準寸法の読みは、全部同じ群に入る。"""
    path = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(850, 780), "縮尺 1/50", fontname="japan", fontsize=11)
    # 図形が 1 つも無いページは「空」として扱われ、抽出に回らない。
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(100, 400), pymupdf.Point(600, 400))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    doc.save(path)
    doc.close()

    start_kit = StartKit(
        reference_points=(
            ReferencePoint(
                page_number=1,
                axis="horizontal",
                point_a_pt=(100.0, 100.0),
                point_b_pt=(100.0 + 1000.0 * PT_PER_MM_AT_50, 100.0),
                actual_length_mm=1000.0,
                entered_by="tester",
            ),
        )
    )
    result = read_drawing(
        IntakeConfig(pdf_path=path, case_id="T", start_kit=start_kit)
    )

    readings = [f for f in result.findings if f.target.startswith("基準寸法::")]
    assert len(readings) >= 2
    groups = {f.independence_group for f in readings}
    assert groups == {readings[0].target}
    assert "" not in groups


@pytest.mark.parametrize("group", ["", "基準寸法::ページ1::横"])
def test_the_group_never_splits_one_source_into_two(group: str) -> None:
    """**群は数を減らす向きにしか働かない。**同じ指紋なら 1 つのままである。"""
    same = [
        _evidence(source_id="drawing", fingerprint="aaa", group=group, method="pdf_text_scale"),
        _evidence(source_id="drawing", fingerprint="aaa", group=group,
                  method="pdf_dimension_scale"),
    ]
    assert AxisQualityFirewall().assess(same).tier != 1
