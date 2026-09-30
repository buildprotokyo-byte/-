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


def _chain_page():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    sh = page.new_shape()
    # 短い線 4 本がつながって 1 本の長い折れ線になる(1 本ずつは 8 画素未満)
    # 1 本ずつ別の線として描く(1 つの線に 5 本以上の平行線があるとハッチングになるため)
    for i in range(4):
        sh.draw_line((100 + i, 100), (101 + i, 100))
        sh.finish(color=(0, 0, 0), width=1, closePath=False)
    # 離れた短い線(繋がらない)
    sh.draw_line((300, 200), (301, 200))
    sh.finish(color=(0, 0, 0), width=1, closePath=False)
    sh.commit()
    return doc, page


def test_chaining_joins_touching_small_segments():
    doc, page = _chain_page()
    prims = ec.extract_primitives(page, 1)
    ec.mark_exclusions(prims, *(page.rect.width * ec.WIDTH_PX / page.rect.width,
                                page.rect.height * ec.WIDTH_PX / page.rect.width))
    assert sum(1 for p in prims if p.category == "点・小さい図形") == 5
    units = ec.chain_lines(prims)
    assert sorted(len(u) for u in units) == [1, 4]
    s = ec.summarize_chained(prims)
    assert s["種類ごと(数える/落ちた)"]["線"] == [1, 1]
    assert s["種類ごと(数える/落ちた)"]["点・小さい図形"] == [1, 1]
    assert s["繋ぐと8画素以上の線の一部になる点・小さい図形の部品"] == 4


def test_chained_line_marked_by_length_share():
    doc, page = _chain_page()
    s = ec.WIDTH_PX / page.rect.width
    # 4 本のうち 2 本だけを囲む → 長さの半分で拾えた
    els = [{"位置": [99 * s, 99 * s, 101.5 * s, 101 * s]}]
    prims, _ = ec.check_page(page, 1, els)
    assert sum(p.marked for p in prims) == 2
    out = ec.summarize_chained(prims)
    assert out["種類ごと(数える/落ちた)"]["線"] == [1, 0]


def test_classify_lines_rules():
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=600)
    sh = page.new_shape()
    sh.draw_line((100, 100), (300, 100))   # 寸法線(上に数字)
    sh.draw_line((100, 100.3), (100, 130))  # 寸法補助線(端点が寸法線に接する)
    sh.draw_line((100, 300), (300, 300))   # 壁(平行な 2 本)
    sh.draw_line((100, 306), (300, 306))
    sh.draw_line((400, 400), (450, 440))   # 引出線(片端だけ文字の近く)
    sh.draw_line((500, 100), (560, 100))   # その他
    sh.finish(color=(0, 0, 0), width=1)
    sh.commit()
    page.insert_text((180, 97), "1820", fontsize=8)
    page.insert_text((453, 448), "PB t12.5", fontsize=8)
    prims = ec.extract_primitives(page, 1)
    kinds = ec.classify_lines(prims)
    s = ec.WIDTH_PX / page.rect.width

    def at(x, y):
        st = [p for p in prims if p.kind == "直線"]
        p = min(st, key=lambda p: abs(p.points[0][0] - x * s) + abs(p.points[0][1] - y * s))
        return kinds.get(p.id)
    assert at(100, 100) == "寸法線"
    assert at(100, 100.3) == "寸法補助線"
    assert at(100, 300) == "壁" and at(100, 306) == "壁"
    assert at(400, 400) == "引出線"
    assert at(500, 100) == "その他"
