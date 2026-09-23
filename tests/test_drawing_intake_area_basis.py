"""本番の入口(`intake/drawing_intake.py`)で、面積の数え方が通ることの通しテスト。

2026-09-23 におーちゃんが「面積は芯々(壁芯)で数える」と決めた
(`docs/decision_area_basis.md`)。**図面からは導けない会社のルールである。**

守りたいのは 4 つ。

1. 入口の既定が「壁芯」であり、**決定が本番の経路まで届いていること。**
   (これまで室の輪郭は入口から呼ばれてはいたが、**通しの試験が無かった。**
   「実装済み」の記録を信じずに、入口から呼んで確かめる。)
2. **壁が 1 本線の図面では、壁芯を求めても「不明」のまま出ること。**
   厚みが図面に無いところから厚みを作らない。
3. 出した数え方とその理由が、根拠としてそのまま残ること。
4. **数え方が決まっても 1 件も自動確定しないこと。** 手法は未校正のままで、
   図面 PDF は 1 つのデータ源である。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_room_outlines import METHOD_ROOM_OUTLINE
from intake.drawing_intake import IntakeConfig, IntakeError, read_drawing

PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72
ORIGIN = (200.0, 200.0)
WALL_MM = 200.0


def _mm(value: float) -> float:
    return value * PT_PER_MM_AT_50


def _line(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x1, y1), pymupdf.Point(x2, y2))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _face(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    ox, oy = ORIGIN
    _line(page, ox + _mm(x1), oy + _mm(y1), ox + _mm(x2), oy + _mm(y2))


def _two_line_plan(path: Path) -> Path:
    """壁を 2 本線で描いた 1 室。通り芯は 0/4000 と 0/3000、壁の厚みは 200。

    **壁芯の正解は 4000 x 3000 = 12.0 ㎡、内法は 3800 x 2800 = 10.64 ㎡。**
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = WALL_MM / 2.0
    for offset in (-half, half):
        _face(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _face(page, -half, 3000 + offset, 4000 + half, 3000 + offset)
        _face(page, 0 + offset, -half, 0 + offset, 3000 + half)
        _face(page, 4000 + offset, -half, 4000 + offset, 3000 + half)
    page.insert_text(
        pymupdf.Point(ORIGIN[0] + _mm(1800), ORIGIN[1] + _mm(1500)), "洋室1", fontname="japan"
    )
    doc.save(path)
    doc.close()
    return path


def _single_line_plan(path: Path) -> Path:
    """壁を 1 本線で描いた 1 室。**厚みがどこにも無い。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    _face(page, 0, 0, 4000, 0)
    _face(page, 4000, 0, 4000, 3000)
    _face(page, 4000, 3000, 0, 3000)
    _face(page, 0, 3000, 0, 0)
    page.insert_text(
        pymupdf.Point(ORIGIN[0] + _mm(1800), ORIGIN[1] + _mm(1500)), "洋室1", fontname="japan"
    )
    doc.save(path)
    doc.close()
    return path


def _rooms(path: Path, tmp_path: Path, **kwargs: object) -> list:
    result = read_drawing(
        IntakeConfig(
            pdf_path=path,
            case_id="TEST-AREA-BASIS",
            answers_path=tmp_path / "answers.json",
            **kwargs,  # type: ignore[arg-type]
        )
    )
    return [
        finding for finding in result.findings if finding.method_id == METHOD_ROOM_OUTLINE
    ], result


def test_入口の既定は壁芯で決定が本番の経路まで届く(tmp_path: Path) -> None:
    findings, _ = _rooms(_two_line_plan(tmp_path / "two.pdf"), tmp_path)
    assert findings, "室の面積が 1 件も入口から出ていない"
    finding = next(item for item in findings if "洋室1" in item.target)
    assert finding.provenance["area_basis"] == "壁芯"
    assert finding.provenance["area_basis_wanted"] == "壁芯"
    low, high = finding.value_range
    assert low <= 12.0 <= high, f"壁芯 12.0 ㎡ のはず: {finding.value_range}"


def test_内法を求めれば内法で出る(tmp_path: Path) -> None:
    findings, _ = _rooms(_two_line_plan(tmp_path / "inner.pdf"), tmp_path, area_basis="内法")
    finding = next(item for item in findings if "洋室1" in item.target)
    assert finding.provenance["area_basis"] == "内法"
    low, high = finding.value_range
    assert low <= 10.64 <= high, f"内法 10.64 ㎡ のはず: {finding.value_range}"


def test_壁が1本線なら壁芯を求めても不明のまま出る(tmp_path: Path) -> None:
    """**厚みが図面に無いところから厚みを作らない。**"""
    findings, _ = _rooms(_single_line_plan(tmp_path / "single.pdf"), tmp_path)
    finding = next(item for item in findings if "洋室1" in item.target)
    assert finding.provenance["area_basis"] == "不明"
    assert finding.provenance["area_basis_wanted"] == "壁芯"
    assert "厚み" in finding.provenance["area_basis_note"]


def test_出した数え方の理由が根拠に残る(tmp_path: Path) -> None:
    findings, _ = _rooms(_two_line_plan(tmp_path / "note.pdf"), tmp_path)
    finding = next(item for item in findings if "洋室1" in item.target)
    assert "200" in finding.provenance["area_basis_note"], "測った厚みを残すこと"


def test_数え方が決まっても自動確定しない(tmp_path: Path) -> None:
    _, result = _rooms(_two_line_plan(tmp_path / "tier.pdf"), tmp_path)
    settled = [decision for decision in result.decisions if decision.confirmed]
    assert settled == [], f"自動確定した対象がある: {[d.target for d in settled]}"


def test_知らない数え方は入口が受け付けない(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="面積の数え方"):
        IntakeConfig(
            pdf_path=tmp_path / "x.pdf", case_id="TEST", area_basis="だいたい"  # type: ignore[arg-type]
        )
