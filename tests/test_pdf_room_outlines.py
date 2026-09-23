"""`axes/image_axis/pdf_room_outlines.py` の回帰テスト。

面積を要する見積の行(置床解体・内壁解体・天井解体・ボード・野縁組)は、
**室の輪郭を取る実装が無い**ために 1 行も判定に乗っていなかった
(`docs/real_drawing_eval_report.md`、`docs/golden_eval_result.json`)。
縮尺が決まっても数量は決まらない。要るのは輪郭である。

守りたいのは 6 つ。

1. 閉じた線で囲まれた領域を、室の候補として取り出せること
2. **壁を 2 本線で描いた図面で、壁の隙間(中身)を室として数えないこと**
3. **建具の開口で線が切れていても、室が廊下へ漏れ出さないこと。**
   ただし隙間を勝手に閉じたことは出力に残す(黙って閉じない)
4. **室名は図面に書かれている文字からしか取らない。** 文字が無ければ名前なし、
   2 つ入っていたら食い違いとして名前を付けない
5. **面積は内法か壁芯かを明示する。** どちらか分からないものを面積として出さない
6. 取り出せないときに推測しないこと(スキャンのページでは 0 件)

テスト用の PDF はその場で組み立てる(実図面はコミットしない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_room_outlines import (
    METHOD_ROOM_OUTLINE,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale

PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72
SCALE_50 = DrawingScale(denominator=50.0, source_text="1/50")


def _mm(value: float) -> float:
    """実寸ミリメートルを 1/50 の図面のポイントに直す。"""
    return value * PT_PER_MM_AT_50


def _line(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x1, y1), pymupdf.Point(x2, y2))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _new_page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    return doc, page


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


def _single_room(path: Path, w_mm: float = 4000.0, h_mm: float = 3000.0, name: str | None = None) -> Path:
    """1 本線の壁で囲んだ 1 室。内法 = 壁芯(線が 1 本しかないので区別が無い)。"""
    doc, page = _new_page()
    x0, y0 = 200.0, 200.0
    x1, y1 = x0 + _mm(w_mm), y0 + _mm(h_mm)
    _line(page, x0, y0, x1, y0)
    _line(page, x1, y0, x1, y1)
    _line(page, x1, y1, x0, y1)
    _line(page, x0, y1, x0, y0)
    if name is not None:
        page.insert_text(pymupdf.Point((x0 + x1) / 2 - 20, (y0 + y1) / 2), name, fontname="japan")
    return _save(doc, path)


def test_閉じた線で囲まれた領域を室の候補として取り出す(tmp_path: Path) -> None:
    rooms = find_room_outlines(_single_room(tmp_path / "one.pdf"), 0, SCALE_50)
    assert len(rooms) == 1, f"1 室のはず: {rooms}"
    room = rooms[0]
    assert room.area_sqm == pytest.approx(12.0, rel=0.01), room.area_sqm
    assert room.method_id == METHOD_ROOM_OUTLINE


def test_室名は図面の文字からしか取らない(tmp_path: Path) -> None:
    rooms = find_room_outlines(_single_room(tmp_path / "named.pdf", name="洋室"), 0, SCALE_50)
    assert rooms[0].name == "洋室"
    # 文字が無ければ名前なし。**位置から名前を作らない。**
    rooms2 = find_room_outlines(_single_room(tmp_path / "noname.pdf"), 0, SCALE_50)
    assert rooms2[0].name is None
    assert "文字" in rooms2[0].name_basis


def test_室名が2つ入っていたら名前を付けない(tmp_path: Path) -> None:
    doc, page = _new_page()
    x0, y0 = 200.0, 200.0
    x1, y1 = x0 + _mm(5000.0), y0 + _mm(4000.0)
    for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        _line(page, a, b, c, d)
    page.insert_text(pymupdf.Point(x0 + 30, y0 + 40), "洋室", fontname="japan")
    page.insert_text(pymupdf.Point(x0 + 30, y0 + 80), "納戸", fontname="japan")
    path = _save(doc, tmp_path / "two_names.pdf")

    rooms = find_room_outlines(path, 0, SCALE_50)
    assert len(rooms) == 1
    assert rooms[0].name is None, "食い違いを片方に決めてはいけない"
    assert "洋室" in rooms[0].name_basis and "納戸" in rooms[0].name_basis


def _double_wall_room(path: Path, wall_mm: float = 150.0) -> Path:
    """壁を 2 本線で描いた 1 室。**内側の面が内法、外側の面が壁芯の外。**

    このとき線の交差から生まれる面は
    「室の内法」「壁の中身(細長い 4 つ)」「外側」になる。
    壁の中身を室として数えてはいけない。
    """
    doc, page = _new_page()
    x0, y0 = 200.0, 200.0
    inner_w, inner_h = _mm(4000.0), _mm(3000.0)
    t = _mm(wall_mm)
    x1, y1 = x0 + inner_w, y0 + inner_h
    # 内側の四角
    for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        _line(page, a, b, c, d)
    # 外側の四角
    for a, b, c, d in (
        (x0 - t, y0 - t, x1 + t, y0 - t),
        (x1 + t, y0 - t, x1 + t, y1 + t),
        (x1 + t, y1 + t, x0 - t, y1 + t),
        (x0 - t, y1 + t, x0 - t, y0 - t),
    ):
        _line(page, a, b, c, d)
    # 四隅で内外をつなぐ(壁の小口)
    for (ax, ay), (bx, by) in (
        ((x0, y0), (x0 - t, y0 - t)),
        ((x1, y0), (x1 + t, y0 - t)),
        ((x1, y1), (x1 + t, y1 + t)),
        ((x0, y1), (x0 - t, y1 + t)),
    ):
        _line(page, ax, ay, bx, by)
    return _save(doc, path)


def test_壁の中身を室として数えない(tmp_path: Path) -> None:
    rooms = find_room_outlines(_double_wall_room(tmp_path / "double.pdf"), 0, SCALE_50)
    assert len(rooms) == 1, f"壁の中身が室として出ている: {[r.area_sqm for r in rooms]}"
    assert rooms[0].area_sqm == pytest.approx(12.0, rel=0.01)


def test_面積が内法か壁芯かを明示する(tmp_path: Path) -> None:
    rooms = find_room_outlines(_single_room(tmp_path / "basis.pdf"), 0, SCALE_50)
    assert rooms[0].area_basis in {"内法", "壁芯", "不明"}
    # 1 本線の壁では内法と壁芯を区別できない。**片方に決めてはいけない。**
    assert rooms[0].area_basis == "不明"


def _room_with_opening(path: Path, gap_mm: float) -> Path:
    """下の壁に建具の開口(線の切れ目)がある 1 室と、その下の廊下。"""
    doc, page = _new_page()
    x0, y0 = 200.0, 200.0
    x1 = x0 + _mm(4000.0)
    y1 = y0 + _mm(3000.0)
    y2 = y1 + _mm(1200.0)
    gap = _mm(gap_mm)
    mid = (x0 + x1) / 2.0
    _line(page, x0, y0, x1, y0)
    _line(page, x1, y0, x1, y1)
    _line(page, x0, y1, x0, y0)
    # 下の壁に切れ目
    _line(page, x0, y1, mid - gap / 2, y1)
    _line(page, mid + gap / 2, y1, x1, y1)
    # 廊下の外周
    _line(page, x0, y1, x0, y2)
    _line(page, x0, y2, x1, y2)
    _line(page, x1, y2, x1, y1)
    return _save(doc, path)


def test_建具の開口で室が廊下へ漏れない_閉じたことは出力に残す(tmp_path: Path) -> None:
    path = _room_with_opening(tmp_path / "opening.pdf", gap_mm=800.0)
    rooms = find_room_outlines(path, 0, SCALE_50)
    areas = sorted(round(r.area_sqm, 2) for r in rooms)
    assert len(rooms) == 2, f"室と廊下の 2 つになるはず: {areas}"
    assert areas[0] == pytest.approx(4.8, rel=0.02)   # 廊下 4.0m × 1.2m
    assert areas[1] == pytest.approx(12.0, rel=0.02)  # 室 4.0m × 3.0m
    # **勝手に閉じたことを黙っていない。**
    assert any(r.virtual_edges > 0 for r in rooms)
    assert any("開口" in r.limitation or "閉じ" in r.limitation for r in rooms)


def test_開口が広すぎるときは閉じない_漏れたことが分かる(tmp_path: Path) -> None:
    """3m の開口は建具ではない。閉じてしまうと「壁がある」と嘘をつくことになる。"""
    path = _room_with_opening(tmp_path / "wide.pdf", gap_mm=3000.0)
    rooms = find_room_outlines(path, 0, SCALE_50, max_gap_mm=1200.0)
    areas = sorted(round(r.area_sqm, 2) for r in rooms)
    # 閉じないので、室と廊下は 1 つの領域としてつながる(= 12.0 も 4.8 も出ない)。
    assert 12.0 not in areas and 4.8 not in areas, areas


def test_既定の開口の幅でも3mの切れ目は閉じない(tmp_path: Path) -> None:
    """**既定値そのものを試す。** 引数で渡した値だけを試すと、既定が緩んでも気づけない。

    壊し試験で実際に見つかった穴: `MAX_GAP_MM` を 100m にしても、
    引数を明示しているテストは全部通ってしまった。
    """
    path = _room_with_opening(tmp_path / "wide_default.pdf", gap_mm=3000.0)
    rooms = find_room_outlines(path, 0, SCALE_50)  # 引数を渡さない
    areas = sorted(round(r.area_sqm, 2) for r in rooms)
    assert 12.0 not in areas and 4.8 not in areas, areas


def test_スキャンのページでは0件_それは室が無いことではない(tmp_path: Path) -> None:
    doc, _ = _new_page()
    path = _save(doc, tmp_path / "blank.pdf")
    assert find_room_outlines(path, 0, SCALE_50) == []


def test_面積の窓から外れたものは室として出さない(tmp_path: Path) -> None:
    """図面の外枠のような大きな領域は室ではない。"""
    doc, page = _new_page()
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, 1170, 822))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    path = _save(doc, tmp_path / "frame.pdf")
    rooms = find_room_outlines(path, 0, SCALE_50)
    assert rooms == [], f"外枠が室として出ている: {[r.area_sqm for r in rooms]}"


def test_罫線の表の升目を室として出さない(tmp_path: Path) -> None:
    """**全件テストで実際に出た誤りの再現。**

    建具表の升目も「線で囲まれた閉じた領域」なので、そのままでは室になる。
    実際に建具表のページから「洋室1」という 1.79㎡ の室が出て、仲裁層まで
    届いてしまった。表として読めた範囲の中に入る領域は室としない。
    """
    doc, page = _new_page()
    x0, y0 = 200.0, 200.0
    rows, cols = 4, 3
    cell_w, cell_h = _mm(1500.0), _mm(900.0)
    for row in range(rows + 1):
        y = y0 + row * cell_h
        _line(page, x0, y, x0 + cols * cell_w, y)
    for col in range(cols + 1):
        x = x0 + col * cell_w
        _line(page, x, y0, x, y0 + rows * cell_h)
    page.insert_text(pymupdf.Point(x0 + 10, y0 + 20), "建具表", fontname="japan")
    for row in range(rows):
        page.insert_text(
            pymupdf.Point(x0 + 10, y0 + (row + 1) * cell_h - 10), f"洋室{row + 1}", fontname="japan"
        )
    path = _save(doc, tmp_path / "schedule.pdf")

    rooms = find_room_outlines(path, 0, SCALE_50)
    assert rooms == [], f"表の升目が室として出ている: {[(r.name, round(r.area_sqm, 2)) for r in rooms]}"


def test_面積は刻みで挟んで返す_黙って丸めない(tmp_path: Path) -> None:
    """**全件テストで実際に出た誤りの再現。**

    仲裁層は 1cm²(0.0001㎡)より細かい値を受け取らない。黙って丸めると
    「丸めてよいかどうか」の判断がここで勝手に決まる。だから挟む。
    """
    rooms = find_room_outlines(_single_room(tmp_path / "step.pdf"), 0, SCALE_50)
    low, high = rooms[0].area_range_sqm
    assert low <= rooms[0].area_sqm <= high
    assert round(high - low, 6) <= 0.0001
    assert round(low * 10000) == low * 10000  # 刻みに乗っている


def test_ページ番号が範囲外なら例外(tmp_path: Path) -> None:
    path = _single_room(tmp_path / "x.pdf")
    with pytest.raises(IndexError):
        find_room_outlines(path, 5, SCALE_50)
