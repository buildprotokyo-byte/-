"""本番の入口(`intake/drawing_intake.py`)に、スキャンのページの OCR をつないだ試験。

**テスト用の PDF はこの中で合成する**(`tests/scan_fixtures.py`)。
ベクターの図面を作り、それをラスター化して 1 枚の画像として貼り直したものを
「スキャンされた図面」として使う。実案件 P011 の 34 ページと同じ形である。

守りたいのは 7 つ。

1. **OCR を渡さないときの振る舞いを変えない。** ラスターのページは今までどおり
   「未対応」として記録される。
2. OCR を渡したときは、スキャンのページからも面積の記載が証拠として届く。
3. **スキャンのページで実寸を出さない**(最上位の原則 3-1)。歪んでいる前提の
   紙から、印字の縮尺で長さを作らない。
4. **人が入れた基準点と、OCR で読んだ印字の縮尺が食い違えば警告する**
   (原則 3-1 の検算)。どちらも採らない。
5. **OCR を足しても 1 件も自動確定しない。** 新しい 2 手法も未校正である。
6. **意味の 4 欄が必ず付く**(原則 2 / おーちゃんの回答9)。
7. **エンジンどうしが食い違った読みは数量にならない。** 記録は残る。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from axes.image_axis.ocr_readings import METHOD_OCR_TEXT_AREA, METHOD_OCR_TEXT_SCALE
from axes.reading.meaning import PHASE_UNKNOWN, PURPOSE_UNESTABLISHED
from intake.case_answers import AnswerStore
from intake.drawing_intake import IntakeConfig, IntakeError, OcrSettings, read_drawing
from intake.start_kit import PageDeclaration, ReferencePoint, StartKit
from tests.scan_fixtures import (
    SCAN_DPI,
    ScriptedOcrBackend,
    build_vector_pdf,
    scan_pdf,
    traced_backend,
)

TITLE_BLOCK = (
    ((820.0, 760.0), "縮尺 1/50"),
    ((820.0, 780.0), "専有延床面積 95.54 ㎡"),
    ((820.0, 800.0), "施工床面積 90.61 ㎡"),
)


@pytest.fixture()
def scanned(tmp_path: Path) -> tuple[Path, Path]:
    source = build_vector_pdf(tmp_path / "vector.pdf", title_block=TITLE_BLOCK)
    return source, scan_pdf(source, tmp_path / "scanned.pdf", dpi=SCAN_DPI)


def _settings(source: Path, *, names=("zh", "ja")) -> OcrSettings:
    """同じ読みを返すエンジン 2 つ(= 突き合わせが通る理想的な場合)。"""
    return OcrSettings(
        backends=tuple(
            traced_backend(source, name=name, dpi=SCAN_DPI) for name in names
        ),
        dpi=SCAN_DPI,
    )


def _read(scanned_path: Path, tmp_path: Path, **kwargs):
    config = IntakeConfig(
        pdf_path=scanned_path, case_id="TEST-OCR", dpi=72, **kwargs
    )
    return read_drawing(config, answers=AnswerStore(None))


# ---------------------------------------------------------------------------
# 1. OCR を渡さないときは今までどおり
# ---------------------------------------------------------------------------


def test_without_ocr_a_scanned_page_is_still_recorded_as_unsupported(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    _, scanned_path = scanned
    result = _read(scanned_path, tmp_path)

    assert [page.status for page in result.pages] == ["unsupported_raster"]
    assert result.findings == ()
    assert result.ocr_pages == ()
    assert result.unsupported_pages == (1,)


# ---------------------------------------------------------------------------
# 2. OCR を渡すと、スキャンから面積の記載が届く
# ---------------------------------------------------------------------------


def test_area_labels_are_read_from_a_scanned_page(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    source, scanned_path = scanned
    result = _read(scanned_path, tmp_path, ocr=_settings(source))

    assert [page.status for page in result.pages] == ["processed_ocr"]
    assert len(result.ocr_pages) == 1

    areas = {
        finding.target: finding
        for finding in result.findings
        if finding.method_id == METHOD_OCR_TEXT_AREA
    }
    assert set(areas) == {"専有延床面積", "施工床面積"}
    assert areas["専有延床面積"].value_range == pytest.approx((95.54, 95.54))
    assert areas["施工床面積"].value_range == pytest.approx((90.61, 90.61))

    provenance = areas["専有延床面積"].provenance
    assert provenance["engines"] == ("zh", "ja")
    assert provenance["cross_checked"] is True
    assert "95.54" in provenance["occurrences"][0]["source_text"]


# ---------------------------------------------------------------------------
# 3. スキャンのページで実寸を出さない
# ---------------------------------------------------------------------------


def test_no_real_world_length_is_derived_on_a_scanned_page(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    """印字の縮尺が読めても、開き戸も長さも出さない(原則 3-1)。"""
    source, scanned_path = scanned
    result = _read(scanned_path, tmp_path, ocr=_settings(source))

    assert not [
        finding for finding in result.findings if finding.target.startswith("開き戸")
    ]
    assert not [finding for finding in result.findings if finding.unit == "mm"]

    page = result.pages[0]
    assert any("歪" in note or "実寸" in note for note in page.notes)
    # 読んだ縮尺そのものは、突き合わせの材料として残す。
    assert any(
        reading.origin.startswith("スキャンを OCR") for reading in page.scale_readings
    )


# ---------------------------------------------------------------------------
# 4. 人が入れた基準点との検算
# ---------------------------------------------------------------------------


def test_reference_point_disagreeing_with_the_ocr_scale_is_a_pending_decision(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    source, scanned_path = scanned
    # 紙の上で 100pt の 2 点に「実寸 10000mm」= 1/283.5 相当。印字の 1/50 とは
    # 大きく食い違う(A3 の図面を A0 で出した P011 と同じ形)。
    start_kit = StartKit(
        reference_points=(
            ReferencePoint(
                page_number=1,
                axis="horizontal",
                point_a_pt=(100.0, 400.0),
                point_b_pt=(200.0, 400.0),
                actual_length_mm=10000.0,
                entered_by="テスト",
            ),
        )
    )
    result = _read(
        scanned_path, tmp_path, ocr=_settings(source), start_kit=start_kit
    )

    kinds = [pending.kind for pending in result.pending_decisions]
    assert "scale_disagreement" in kinds
    assert result.pages[0].scale is None


def test_reference_point_agreeing_with_the_ocr_scale_still_does_not_confirm(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    """一致しても自動確定しない。**人の入力と印字が揃っただけでは根拠にならない。**"""
    source, scanned_path = scanned
    # 紙の上 100pt = 35.28mm。1/50 なら実寸 1763.9mm。
    start_kit = StartKit(
        reference_points=(
            ReferencePoint(
                page_number=1,
                axis="horizontal",
                point_a_pt=(100.0, 400.0),
                point_b_pt=(200.0, 400.0),
                actual_length_mm=1763.9,
                entered_by="テスト",
            ),
        )
    )
    result = _read(
        scanned_path, tmp_path, ocr=_settings(source), start_kit=start_kit
    )
    assert [p.kind for p in result.pending_decisions] == []
    assert result.confirmed_targets == ()


# ---------------------------------------------------------------------------
# 5. 自動確定しない
# ---------------------------------------------------------------------------


def test_nothing_is_auto_confirmed_from_ocr(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    source, scanned_path = scanned
    result = _read(scanned_path, tmp_path, ocr=_settings(source))
    assert result.confirmed_targets == ()
    assert all(decision.tier == 3 for decision in result.decisions)


def test_the_ocr_methods_are_registered_as_uncalibrated() -> None:
    for method_id in (METHOD_OCR_TEXT_AREA, METHOD_OCR_TEXT_SCALE):
        policy = DEFAULT_METHOD_POLICIES[method_id]
        assert policy.calibrated is False
        # 文字化けを確信度が知らせないので、強い軸の上限を与えない。
        assert policy.max_strength == "weak"


# ---------------------------------------------------------------------------
# 6. 意味の 4 欄
# ---------------------------------------------------------------------------


def test_every_ocr_finding_carries_the_four_meaning_columns(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    source, scanned_path = scanned
    result = _read(scanned_path, tmp_path, ocr=_settings(source))

    ocr_findings = [
        finding
        for finding in result.findings
        if finding.method_id in {METHOD_OCR_TEXT_AREA, METHOD_OCR_TEXT_SCALE}
    ]
    assert ocr_findings
    for finding in ocr_findings:
        assert finding.meaning is not None
        assert finding.meaning.what
        assert finding.meaning.where
        assert finding.meaning.phase == PHASE_UNKNOWN
        assert finding.meaning.purpose_link == PURPOSE_UNESTABLISHED
        assert finding.provenance["meaning"]["is_complete"] is False


def test_the_declared_phase_is_carried_into_the_meaning(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    """人が「このページは現況」と宣言していれば、意味の欄に入る。"""
    source, scanned_path = scanned
    start_kit = StartKit(
        page_declarations=(
            PageDeclaration(page_number=1, kind="平面図", phase="現況"),
        )
    )
    result = _read(
        scanned_path, tmp_path, ocr=_settings(source), start_kit=start_kit
    )
    areas = [
        finding
        for finding in result.findings
        if finding.method_id == METHOD_OCR_TEXT_AREA
    ]
    assert areas
    assert all(finding.meaning.phase == "現況" for finding in areas)


# ---------------------------------------------------------------------------
# 7. エンジンどうしが食い違った読み
# ---------------------------------------------------------------------------


def test_a_reading_the_engines_disagree_on_does_not_become_a_quantity(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    source, scanned_path = scanned

    def broken(text: str) -> str:
        # 実測で起きた化け方: 数字が別の字になる。
        return "9ｓ.54" if text == "95.54" else text

    settings = OcrSettings(
        backends=(
            traced_backend(source, name="zh", dpi=SCAN_DPI),
            traced_backend(source, name="ja", dpi=SCAN_DPI, mutate=broken),
        ),
        dpi=SCAN_DPI,
    )
    result = _read(scanned_path, tmp_path, ocr=settings)

    targets = {finding.target for finding in result.findings}
    assert "専有延床面積" not in targets
    # 読めた側の値を黙って採らない。食い違いは記録に残る。
    assert "施工床面積" in targets
    assert result.ocr_pages[0].conflicts
    assert any("食い違" in note for note in result.pages[0].notes)


def test_an_ocr_page_that_reads_nothing_is_not_called_empty(
    scanned: tuple[Path, Path], tmp_path: Path
) -> None:
    _, scanned_path = scanned
    # エンジン 1 つだけなので、はっきり求める必要がある(2026-09-23 の判断)。
    settings = OcrSettings(
        backends=(ScriptedOcrBackend("empty", []),),
        dpi=SCAN_DPI,
        allow_single_backend=True,
    )
    result = _read(scanned_path, tmp_path, ocr=settings)

    assert result.findings == ()
    assert any(
        "文字が無いという意味ではない" in note for note in result.pages[0].notes
    )


def test_a_single_engine_is_refused_unless_it_is_asked_for_explicitly() -> None:
    """**エンジン 1 つだけの読みは、はっきり求めない限り受け付けない。**

    2026-09-23、おーちゃんの判断。「2 つのモデルが同じに読んだ語だけを通す」を
    既定にする。実測では、2 つ一致に絞ると誤りは 0 件だが、29 語のうち
    7〜8 語しか通らない(`docs/ocr_scanned_pages_report.md`)。
    それでも、文字化けを数量側へ持ち込まないほうを既定に置く。

    **1 つだけで読む道は塞がない。** 塞ぐと、片方のエンジンしか入らない
    環境で何も読めなくなる。**黙って通さず、呼ぶ側に書かせる。**
    """
    one = ScriptedOcrBackend("only", [])

    with pytest.raises(IntakeError) as caught:
        OcrSettings(backends=(one,), dpi=SCAN_DPI)
    assert "突き合わせ" in str(caught.value)

    # はっきり求めれば通る。証拠に「突き合わせていない」ことが残る。
    settings = OcrSettings(backends=(one,), dpi=SCAN_DPI, allow_single_backend=True)
    assert settings.backends == (one,)
    assert settings.allow_single_backend is True

    # 2 つ渡せば、何も足さずに通る。
    two = OcrSettings(
        backends=(one, ScriptedOcrBackend("second", [])), dpi=SCAN_DPI
    )
    assert two.allow_single_backend is False
