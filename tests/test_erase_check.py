"""K-51 消して確かめる(benchmarks/erase_check.py)のテスト。合成の PDF だけを使う。"""
import pymupdf
import pytest

from benchmarks import erase_check as ec


def _page(rotate=0):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    sh = page.new_shape()
    sh.draw_line((50, 50), (150, 50))          # 直線 A
    sh.draw_line((50, 100), (150, 100))        # 直線 B
    sh.finish(color=(0, 0, 0), width=1)
    sh.draw_circle((300, 150), 5)              # 曲線(記号の部品)
    sh.finish(color=(1, 0, 0), width=1)
    for i in range(6):                          # ハッチング(平行な 6 本)
        sh.draw_line((200 + i * 5, 200), (200 + i * 5, 240))
    sh.finish(color=(0, 0, 1), width=0.5)
    sh.draw_rect(pymupdf.Rect(5, 5, 395, 295))  # 図面枠
    sh.finish(color=(0, 0, 0), width=1)
    sh.commit()
    page.insert_text((60, 150), "WD1", fontsize=10)
    if rotate:
        page.set_rotation(rotate)
    return doc, page


def _px(page, x0, y0, x1, y1):
    s = ec.WIDTH_PX / page.rect.width
    return [x0 * s, y0 * s, x1 * s, y1 * s]


def test_counts_and_marks_only_what_was_read():
    doc, page = _page()
    els = [{"種類": "線", "位置": _px(page, 45, 45, 155, 55)}]  # 直線 A だけを読んだ
    prims, s = ec.check_page(page, 1, els)
    kinds = sorted(p.kind for p in prims if not p.excluded)
    assert kinds.count("ハッチング") == 1  # 6 本を 1 つに数える
    assert s["除外"].get("図面枠", 0) >= 1
    marked = [p for p in prims if p.marked]
    assert len(marked) == 1 and marked[0].kind == "直線"
    assert s["落ちた"] == s["数える図形"] - 1


def test_large_elements_do_not_mark():
    doc, page = _page()
    whole = [{"種類": "図", "位置": _px(page, 0, 0, 400, 300)}]
    _, s = ec.check_page(page, 1, whole)
    assert s["拾えた"] == 0
    _, s2 = ec.check_page(page, 1, whole, area_cap=1.0)
    assert s2["落ちた"] == 0


def test_text_and_symbol_categories():
    doc, page = _page()
    prims, _ = ec.check_page(page, 1, [])
    cats = {p.kind: p.category for p in prims if not p.excluded}
    assert cats["文字"] == "文字"
    assert cats["ハッチング"] == "線"
    assert any(p.kind == "曲線" and p.category in ("記号", "点・小さい図形") for p in prims)


@pytest.mark.parametrize("rot", [90, 270])
def test_rotated_page_coordinates_match_render(rot):
    doc, page = _page(rotate=rot)
    prims = ec.extract_primitives(page, 1)
    s = ec.WIDTH_PX / page.rect.width
    W, H = page.rect.width * s, page.rect.height * s
    for p in prims:
        x0, y0, x1, y1 = p.bbox
        assert -1 <= x0 <= W + 1 and -1 <= y1 <= H + 1
    # 文字の位置に、描いた画像でもインクがある
    import numpy as np
    word = [p for p in prims if p.kind == "文字"][0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s))
    assert abs(pix.width - W) < 2
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    x0, y0, x1, y1 = (int(v) for v in word.bbox)
    assert img[y0:y1, x0:x1, :3].min() < 128
