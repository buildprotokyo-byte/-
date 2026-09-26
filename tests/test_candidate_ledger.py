"""候補台帳(K-23)の試験。**合成の PDF だけを使う。** 実図面は使わない。

固定したいこと:
- 意味を当てず、線・閉領域・小輪郭を 0〜1000 の座標で残す(ベクターもラスターも同じ形)
- 回転したページでも座標が 0〜1000 に収まる
- 上限に当たったら決まった順で残し、当たったことを記録する
- 読めなかったページを「候補 0 個・passed」にしない
- 同じ入力なら毎回同じ中身になり、線を 1 本足せば中身の指紋が変わる
- キャッシュは同じ条件なら使い回し、設定が変われば別の鍵になる
- 本番の取り込み経路には繋がっていない
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from axes.image_axis import candidate_ledger as cl
from axes.image_axis.candidate_ledger import (
    REASON_CAP,
    REASON_EMPTY,
    REASON_ERROR,
    REASON_NO_SCALE,
    REASON_NO_TEXT,
    REASON_ZERO,
    KindCount,
    LedgerCache,
    LedgerSettings,
    TitleBlock,
    build_ledger,
    gate_page,
)
from tests.scan_fixtures import scan_pdf

ROOT = Path(__file__).resolve().parent.parent
W, H = 1190.0, 842.0


def _title(page: pymupdf.Page, *, scale: str = "1/50（A3）", number: str = "A-3") -> None:
    """P011 匿名化v2 と同じ並び: 値が見出しの右下に半行ずれて置かれている(中身は架空)。"""
    page.insert_text(pymupdf.Point(835, 785), "図面名称", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(890, 792), "架空平面図", fontname="japan", fontsize=9)
    page.insert_text(pymupdf.Point(1083, 760), "図面番号", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(1094, 783), number, fontname="japan", fontsize=9)
    page.insert_text(pymupdf.Point(1013, 785), "縮尺", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(1027, 794), scale, fontname="japan", fontsize=8)
    # 改訂の履歴(2 桁の年)。「1/21」を縮尺として拾わないことも一緒に確かめる。
    page.insert_text(pymupdf.Point(638, 760), "年/月/日", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(636, 770), "21/01/21", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(636, 780), "23/03/30", fontname="japan", fontsize=7)
    page.insert_text(pymupdf.Point(636, 790), "22/12/13", fontname="japan", fontsize=7)


def _body(page: pymupdf.Page, *, extra_line: bool = False) -> None:
    shape = page.new_shape()
    # 長い線 4 本(室の輪郭のつもりだが、台帳は意味を持たない)
    shape.draw_line((100, 100), (700, 100))
    shape.draw_line((700, 100), (700, 500))
    shape.draw_line((700, 500), (100, 500))
    shape.draw_line((100, 500), (100, 100))
    shape.finish(color=(0, 0, 0), width=1.0)
    # 閉じた矩形 2 つ
    shape.draw_rect(pymupdf.Rect(150, 150, 350, 300))
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.draw_rect(pymupdf.Rect(400, 150, 650, 450))
    shape.finish(color=(0, 0, 0), width=1.0)
    # 小さな丸 3 つ(記号の大きさ)
    for x in (200, 260, 320):
        shape.draw_circle((x, 400), 5)
        shape.finish(color=(0, 0, 0), width=0.8)
    if extra_line:
        shape.draw_line((100, 600), (900, 600))
        shape.finish(color=(0, 0, 0), width=1.0)
    shape.commit()


def _vector_pdf(path: Path, *, rotation: int = 0, extra_line: bool = False, title: bool = True) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _body(page, extra_line=extra_line)
    if title:
        _title(page)
    if rotation:
        page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def _all_coords(page) -> list[float]:
    out: list[float] = []
    for c in page.lines:
        out += [c.x0, c.y0, c.x1, c.y1]
    for c in (*page.regions, *page.small):
        out += [c.x0, c.y0, c.x1, c.y1]
    return out


# ---------------------------------------------------------------------------
# ベクター
# ---------------------------------------------------------------------------


def test_vector_page_keeps_shapes_without_meaning(tmp_path):
    ledger = build_ledger(_vector_pdf(tmp_path / "v.pdf"))
    page = ledger.pages[0]
    assert page.source == "vector"
    # 閉じた 4 本の線と矩形 2 つは、どれも閉領域になり、辺は線になる(3 x 4 本)。
    assert page.counts["lines"].found == 12
    assert page.counts["regions"].found == 3
    assert page.counts["small"].found == 3
    # 0〜1000 に揃っている。600pt の線は 1190pt 幅の紙で 504 単位。
    top = [c for c in page.lines if c.y0 == c.y1 and abs(c.y0 - 100 / H * 1000) < 0.2]
    assert len(top) == 1
    assert top[0].x0 == pytest.approx(100 / W * 1000, abs=0.1)
    assert top[0].x1 == pytest.approx(700 / W * 1000, abs=0.1)
    assert all(0 <= v <= 1000 for v in _all_coords(page))
    # 候補に「何か」を表す欄は無い。
    assert not hasattr(page.small[0], "label")
    assert not hasattr(page.small[0], "name")


def test_title_block_is_read_from_label_neighbours(tmp_path):
    page = build_ledger(_vector_pdf(tmp_path / "v.pdf")).pages[0]
    title = page.title
    assert title is not None and title.read
    assert title.drawing_name == "架空平面図"
    assert title.drawing_number == "A-3"
    assert title.scale_denominator == 50.0
    # 「21/01/21」の中の「1/21」を縮尺として拾っていない。
    assert title.scale_text == "1/50"
    # 改訂日は履歴のうちいちばん新しいもの。
    assert title.revision_date == "23/03/30"
    assert page.gate.status == "passed"


def test_scale_label_without_scale_value_is_not_guessed(tmp_path):
    path = tmp_path / "v.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _body(page)
    _title(page, scale="")
    doc.save(path)
    doc.close()
    page = build_ledger(path).pages[0]
    assert page.title.scale_denominator is None
    assert REASON_NO_SCALE in page.gate.reasons
    assert page.gate.status == "needs_review"


def test_no_scale_statement_is_recorded_as_such(tmp_path):
    path = tmp_path / "v.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _body(page)
    _title(page, scale="NON SCALE")
    doc.save(path)
    doc.close()
    page = build_ledger(path).pages[0]
    assert page.title.scale_denominator is None
    assert page.title.scale_stated_none
    assert REASON_NO_SCALE not in page.gate.reasons


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_stays_in_0_to_1000(tmp_path, rotation):
    straight = build_ledger(_vector_pdf(tmp_path / "a.pdf")).pages[0]
    rotated = build_ledger(_vector_pdf(tmp_path / "b.pdf", rotation=rotation)).pages[0]
    assert rotated.rotation == rotation
    assert all(0 <= v <= 1000 for v in _all_coords(rotated))
    assert {k: v.found for k, v in rotated.counts.items()} == {
        k: v.found for k, v in straight.counts.items()
    }
    # 文字も表示の向きに直している(P011 匿名化v2 の回転したページでは、直さないと縦が 1000 を超えた)。
    doc = pymupdf.open(tmp_path / "b.pdf")
    words = cl._page_words(doc[0])
    doc.close()
    assert words
    assert all(0 <= v <= 1000 for w in words for v in (w.x0, w.y0, w.x1, w.y1))


def test_rotation_mapping_matches_displayed_page(tmp_path):
    page = build_ledger(_vector_pdf(tmp_path / "b.pdf", rotation=90)).pages[0]
    # 回転後の紙は縦長(842 x 1190)。元の上辺 y=100 の横線は、表示では x=842-100 の縦線になる。
    assert page.width_pt == pytest.approx(H, abs=0.1)
    assert page.height_pt == pytest.approx(W, abs=0.1)
    vertical = [c for c in page.lines if c.x0 == c.x1 and abs(c.x0 - (H - 100) / H * 1000) < 0.2]
    assert len(vertical) == 1


# ---------------------------------------------------------------------------
# ラスター
# ---------------------------------------------------------------------------


def test_raster_page_goes_through_the_fixed_pipeline(tmp_path):
    source = _vector_pdf(tmp_path / "v.pdf")
    scanned = scan_pdf(source, tmp_path / "s.pdf")
    page = build_ledger(scanned).pages[0]
    assert page.source == "raster"
    assert page.counts["lines"].found > 0
    assert page.counts["regions"].found > 0
    assert all(0 <= v <= 1000 for v in _all_coords(page))
    # 文字の層が無いので blocked。だが候補の数は記録されている(0 に潰していない)。
    assert page.gate.status == "blocked"
    assert REASON_NO_TEXT in page.gate.reasons
    assert page.has_text is False


def test_raster_does_not_depend_much_on_scan_resolution(tmp_path):
    """長いほうの辺を決まった画素数に揃えてから処理する。

    **同じ件数にはならない**(読み取りの解像度が違うと、揃えたあとの画素の細部が変わる)。
    ここで固定するのは「桁は変わらない」ことだけ。
    """
    source = _vector_pdf(tmp_path / "v.pdf")
    small = scan_pdf(source, tmp_path / "s1.pdf", dpi=150)
    large = scan_pdf(source, tmp_path / "s2.pdf", dpi=300)
    a = build_ledger(small).pages[0].counts
    b = build_ledger(large).pages[0].counts
    assert a["lines"].found > 0 and b["lines"].found > 0
    assert abs(a["lines"].found - b["lines"].found) <= max(a["lines"].found, b["lines"].found) * 0.5


# ---------------------------------------------------------------------------
# 上限
# ---------------------------------------------------------------------------


def test_cap_keeps_longest_lines_and_records_the_hit(tmp_path):
    settings = replace(LedgerSettings(), max_lines=2, max_small=1)
    page = build_ledger(_vector_pdf(tmp_path / "v.pdf"), settings=settings).pages[0]
    assert page.counts["lines"] == KindCount(found=12, kept=2, limit=2, cap_hit=True)
    assert len(page.lines) == 2
    assert page.counts["small"].cap_hit
    assert REASON_CAP in page.gate.reasons
    assert page.gate.status == "needs_review"
    # 長さの順(同じ長さは位置の順)。400pt の縦線より 600pt の横線が先に残る。
    assert all(c.y0 == c.y1 for c in page.lines)


def test_no_cap_hit_when_under_limits(tmp_path):
    page = build_ledger(_vector_pdf(tmp_path / "v.pdf")).pages[0]
    assert not any(c.cap_hit for c in page.counts.values())
    assert REASON_CAP not in page.gate.reasons


# ---------------------------------------------------------------------------
# 品質ゲート
# ---------------------------------------------------------------------------

_GOOD_TITLE = TitleBlock(True, "名称", "A-1", "1/50", 50.0, False, None, ("name",))
_SOME = {"lines": KindCount(1, 1, 1800, False)}
_NONE = {"lines": KindCount(0, 0, 1800, False)}


def test_gate_never_passes_zero_candidates():
    gate = gate_page(
        error=None, source="vector", has_text=True, title=_GOOD_TITLE, counts=_NONE,
        unprocessed_images=False,
    )
    assert gate.status != "passed"
    assert REASON_ZERO in gate.reasons


def test_gate_blocks_error_and_empty_and_textless():
    assert gate_page(
        error="X", source="vector", has_text=True, title=_GOOD_TITLE, counts=_SOME,
        unprocessed_images=False,
    ).reasons == (REASON_ERROR,)
    assert gate_page(
        error=None, source="none", has_text=False, title=None, counts=_NONE,
        unprocessed_images=False,
    ).reasons == (REASON_EMPTY,)
    textless = gate_page(
        error=None, source="vector", has_text=False, title=_GOOD_TITLE, counts=_SOME,
        unprocessed_images=False,
    )
    assert textless.status == "blocked"


def test_gate_passes_only_when_nothing_is_missing():
    assert gate_page(
        error=None, source="vector", has_text=True, title=_GOOD_TITLE, counts=_SOME,
        unprocessed_images=False,
    ).status == "passed"
    assert gate_page(
        error=None, source="vector", has_text=True, title=_GOOD_TITLE, counts=_SOME,
        unprocessed_images=True,
    ).status == "needs_review"


def test_empty_pdf_page_is_blocked_not_zero_passed(tmp_path):
    path = tmp_path / "e.pdf"
    doc = pymupdf.open()
    doc.new_page(width=W, height=H)
    doc.save(path)
    doc.close()
    page = build_ledger(path).pages[0]
    assert page.gate.status == "blocked"
    assert page.gate.reasons == (REASON_EMPTY,)


def test_processing_error_is_recorded_and_next_page_continues(tmp_path, monkeypatch):
    path = tmp_path / "two.pdf"
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=W, height=H)
        _body(page)
        _title(page)
    doc.save(path)
    doc.close()
    calls = {"n": 0}
    original = cl._vector_candidates

    def flaky(page, settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("壊れたページ")
        return original(page, settings)

    monkeypatch.setattr(cl, "_vector_candidates", flaky)
    ledger = build_ledger(path)
    assert ledger.pages[0].gate.status == "blocked"
    assert ledger.pages[0].error is not None
    assert ledger.pages[1].gate.status == "passed"


# ---------------------------------------------------------------------------
# 安定と指紋
# ---------------------------------------------------------------------------


def test_same_input_gives_same_digest(tmp_path):
    path = _vector_pdf(tmp_path / "v.pdf")
    scanned = scan_pdf(path, tmp_path / "s.pdf")
    for pdf in (path, scanned):
        digests = {build_ledger(pdf).pages[0].content_digest() for _ in range(3)}
        assert len(digests) == 1


def test_one_more_line_changes_the_digest(tmp_path):
    a = build_ledger(_vector_pdf(tmp_path / "a.pdf")).pages[0]
    b = build_ledger(_vector_pdf(tmp_path / "b.pdf", extra_line=True)).pages[0]
    assert b.counts["lines"].found == a.counts["lines"].found + 1
    assert a.content_digest() != b.content_digest()


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------


def test_cache_reuses_same_page_and_matches_fresh_result(tmp_path):
    pdf = _vector_pdf(tmp_path / "v.pdf")
    cache = LedgerCache(tmp_path / "cache")
    first = build_ledger(pdf, cache=cache).pages[0]
    second = build_ledger(pdf, cache=cache).pages[0]
    fresh = build_ledger(pdf).pages[0]
    assert first.from_cache is False
    assert second.from_cache is True
    assert first.cache_key == second.cache_key
    assert second.content_digest() == fresh.content_digest()
    assert second.gate == fresh.gate
    assert second.title == fresh.title


def test_cache_key_changes_with_settings_and_content(tmp_path):
    pdf_a = _vector_pdf(tmp_path / "a.pdf")
    pdf_b = _vector_pdf(tmp_path / "b.pdf", extra_line=True)
    cache = LedgerCache(tmp_path / "cache")
    base = build_ledger(pdf_a, cache=cache).pages[0].cache_key
    other_settings = build_ledger(
        pdf_a, cache=cache, settings=replace(LedgerSettings(), canny_low=40)
    ).pages[0]
    other_content = build_ledger(pdf_b, cache=cache).pages[0]
    assert other_settings.cache_key != base and other_settings.from_cache is False
    assert other_content.cache_key != base and other_content.from_cache is False


def test_cache_key_is_sha256_of_image_and_settings():
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    gray = np.full((10, 10), 255, dtype=np.uint8)
    key = LedgerCache.key(gray, page, LedgerSettings())
    gray2 = gray.copy()
    gray2[0, 0] = 254
    assert len(key) == 64
    assert LedgerCache.key(gray2, page, LedgerSettings()) != key


# ---------------------------------------------------------------------------
# 本番に繋がっていないこと
# ---------------------------------------------------------------------------


def test_ledger_is_not_imported_by_production_paths():
    """台帳は候補を拾うだけで、数量にも判定にも使わない(K-23)。繋ぐときはこの試験ごと見直す。"""
    production_dirs = ["intake", "arbitration", "estimating", "killer_question", "axes"]
    offenders = []
    for directory in production_dirs:
        for path in (ROOT / directory).rglob("*.py"):
            if path.name == "candidate_ledger.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                if any("candidate_ledger" in n for n in names):
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# K-24: 上限ではなく理由で捨てる
# ---------------------------------------------------------------------------


def test_default_caps_are_safety_valves_not_filters():
    settings = LedgerSettings()
    assert settings.max_small >= 10000
    assert settings.max_lines >= 10000
    assert settings.max_regions >= 10000


def test_drop_reasons_are_recorded(tmp_path):
    path = tmp_path / "d.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _body(page)
    _title(page)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(900, 100, 900.5, 100.5))  # 点の汚れ
    shape.finish(color=(0, 0, 0), width=0.2)
    # 大きな図形の中の短い辺: 細長い長方形の短辺(5pt ≒ 6 単位)は短すぎる線として落ちる
    shape.draw_rect(pymupdf.Rect(100, 650, 700, 655))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    doc.save(path)
    doc.close()
    ledger_page = build_ledger(path).pages[0]
    assert ledger_page.dropped.get(cl.DROP_SPECK) == 1
    assert ledger_page.dropped.get(cl.DROP_SHORT_LINE) == 2
    assert cl.DROP_CAP not in ledger_page.dropped


def test_cap_drops_are_counted_as_a_reason(tmp_path):
    settings = replace(LedgerSettings(), max_lines=2)
    page = build_ledger(_vector_pdf(tmp_path / "v.pdf"), settings=settings).pages[0]
    assert page.dropped[cl.DROP_CAP] == 10


def test_closed_line_back_stroke_is_counted_as_duplicate(tmp_path):
    a = build_ledger(_vector_pdf(tmp_path / "a.pdf", extra_line=True)).pages[0]
    assert a.dropped.get(cl.DROP_DUPLICATE_LINE, 0) >= 1


# ---------------------------------------------------------------------------
# K-24: 表題欄と図面リストの食い違いを、両方の値で人に見せる
# ---------------------------------------------------------------------------


def _list_row(page, y, sheet, number, name):
    page.insert_text(pymupdf.Point(60, y), sheet, fontname="japan", fontsize=9)
    page.insert_text(pymupdf.Point(100, y), number, fontname="japan", fontsize=9)
    page.insert_text(pymupdf.Point(160, y), name, fontname="japan", fontsize=9)


def _listed_pdf(path: Path, rows: list[tuple[str, str, str]], title_number: str = "A-3") -> Path:
    doc = pymupdf.open()
    listing = doc.new_page(width=W, height=H)
    for i, (sheet, number, name) in enumerate(rows):
        _list_row(listing, 100 + 20 * i, sheet, number, name)
    page = doc.new_page(width=W, height=H)
    _body(page)
    _title(page, number=title_number)
    doc.save(path)
    doc.close()
    return path


_FILLER = [("3", "A-4", "架空断面図"), ("4", "A-5", "架空立面図"), ("5", "A-6", "架空詳細図"), ("6", "A-7", "架空展開図")]


def test_list_rows_are_read_with_sheet_and_name(tmp_path):
    pdf = _listed_pdf(tmp_path / "l.pdf", [("2", "A-3", "架空平面図"), *_FILLER])
    with pymupdf.open(pdf) as doc:
        entries = cl.read_drawing_list(doc[0])
        assert cl.find_drawing_list_page(doc) == 0
    assert entries[0] == cl.DrawingListEntry("A-3", "架空平面図", 2)


def test_matching_list_gives_no_conflict(tmp_path):
    pdf = _listed_pdf(tmp_path / "l.pdf", [("2", "A-3", "架空平面図"), *_FILLER])
    page = build_ledger(pdf).pages[1]
    assert page.title_conflicts == ()
    assert page.gate.status == "passed"


def test_different_name_is_shown_with_both_values_and_not_resolved(tmp_path):
    pdf = _listed_pdf(tmp_path / "l.pdf", [("2", "A-3", "架空配置図"), *_FILLER])
    page = build_ledger(pdf).pages[1]
    (conflict,) = page.title_conflicts
    assert conflict.kind == cl.CONFLICT_NAME_WORDS
    assert conflict.title_value == "架空平面図"
    assert conflict.list_value == "架空配置図"
    # どちらかを選んで表題欄を書き換えない。
    assert page.title.drawing_name == "架空平面図"
    assert page.gate.status == "needs_review"
    assert cl.REASON_TITLE_CONFLICT in page.gate.reasons


def test_notation_only_difference_is_recorded_but_does_not_gate(tmp_path):
    pdf = _listed_pdf(tmp_path / "l.pdf", [("2", "Aー3", "架空・平面図"), *_FILLER])
    page = build_ledger(pdf).pages[1]
    kinds = {c.kind for c in page.title_conflicts}
    assert kinds == {cl.CONFLICT_NUMBER_NOTATION, cl.CONFLICT_NAME_NOTATION}
    assert page.gate.status == "passed"


def test_sheet_and_missing_number_conflicts(tmp_path):
    wrong_sheet = build_ledger(
        _listed_pdf(tmp_path / "s.pdf", [("7", "A-3", "架空平面図"), *_FILLER])
    ).pages[1]
    assert [c.kind for c in wrong_sheet.title_conflicts] == [cl.CONFLICT_SHEET]
    missing = build_ledger(
        _listed_pdf(tmp_path / "m.pdf", [("2", "A-9", "架空平面図"), *_FILLER])
    ).pages[1]
    assert [c.kind for c in missing.title_conflicts] == [cl.CONFLICT_NUMBER_MISSING]
    assert missing.gate.status == "needs_review"


def test_conflicts_survive_the_cache(tmp_path):
    pdf = _listed_pdf(tmp_path / "l.pdf", [("2", "A-3", "架空配置図"), *_FILLER])
    cache = LedgerCache(tmp_path / "c")
    build_ledger(pdf, cache=cache)
    again = build_ledger(pdf, cache=cache).pages[1]
    assert again.from_cache
    assert [c.kind for c in again.title_conflicts] == [cl.CONFLICT_NAME_WORDS]
