"""`axes/image_axis/wall_network.py` の回帰テスト。

**室名を種にしない。** 壁の中身の面だけを境にして、そのまわりをまとめる。
7周目(`docs/a2_room_selection_report.md`)でいちばん重かった不合格は
「室名を 1 つだけ渡すと 12 件中 4 件の面積が変わった」ことだった。
室名を使って止めないので、**その外れ方は原理的に起きない。**

守りたいのは 6 つ。

1. 室の中に目地の線があっても、1 つのまとまりになること
2. **壁を越えないこと。** 越えたら 2 室が 1 つになり面積が倍に出る
3. **建具の開口を越えないこと**
4. **室名を 1 つも渡さなくても、室の形が出ること**(種に頼らない)
5. **1 つのまとまりに室名が 2 つ以上入ったら、面積を出さずに返すこと**
6. **紙の上で見えない細長い面(同じ線が二重に引かれてできる面)を壁と呼ばないこと**

テスト用の PDF はその場で組み立てる(実図面はコミットしない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_vector_symbols import DrawingScale
from axes.image_axis.wall_network import METHOD_WALL_NETWORK, find_regions_between_walls

PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72
ORIGIN = (200.0, 200.0)
SCALE_50 = DrawingScale(denominator=50.0, source_text="1/50")


def _mm(value: float) -> float:
    return value * PT_PER_MM_AT_50


def _seg(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    ox, oy = ORIGIN
    shape = page.new_shape()
    shape.draw_line(
        pymupdf.Point(ox + _mm(x1), oy + _mm(y1)), pymupdf.Point(ox + _mm(x2), oy + _mm(y2))
    )
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _label(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    ox, oy = ORIGIN
    page.insert_text(
        pymupdf.Point(ox + _mm(x), oy + _mm(y)), text, fontname="japan", fontsize=8
    )


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


def _two_rooms(
    path: Path,
    *,
    joints: bool = True,
    doubled_lines: bool = False,
    labels: tuple[str, ...] = ("洋室", "廊下"),
) -> Path:
    """壁を 2 本線で描いた 2 室。**室の中に床の目地の線がある。**

    通り芯は 0/4000(縦)と 0/3000/6000(横)。壁は 200。
    間仕切りの真ん中に 900 の開口。内法はどちらの室も 3800 x 2800 = 10.64 ㎡。

    ``doubled_lines`` を立てると、**同じ線をわずかにずらして二重に引く。**
    実図面にあるこの描き方は、紙の上では見えない細長い面を作る。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    offsets = (-half, half)
    for offset in offsets:
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 6000 + offset, 4000 + half, 6000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 6000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 6000 + half)
        _seg(page, -half, 3000 + offset, 1550, 3000 + offset)
        _seg(page, 2450, 3000 + offset, 4000 + half, 3000 + offset)
    if doubled_lines:
        # 同じ壁の線を 15mm(紙の上で 0.4pt)ずらしてもう一度引く。
        for offset in offsets:
            _seg(page, -half, 0 + offset + 15.0, 4000 + half, 0 + offset + 15.0)
            _seg(page, 0 + offset + 15.0, -half, 0 + offset + 15.0, 6000 + half)
    if joints:
        for x in (700.0, 1300.0, 2500.0, 3100.0, 3700.0):
            _seg(page, x, 100, x, 2900)
            _seg(page, x, 3100, x, 5900)
        for step in range(1, 7):
            y = 100.0 + step * 400.0
            _seg(page, 100, y, 3900, y)
            _seg(page, 100, y + 3000.0, 3900, y + 3000.0)
    if labels:
        _label(page, 300, 500, labels[0])
        if len(labels) > 1:
            _label(page, 300, 3500, labels[1])
    return _save(doc, path)


def test_目地で割れていても室ごとに1つのまとまりになる(tmp_path: Path) -> None:
    result = find_regions_between_walls(
        _two_rooms(tmp_path / "joints.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    got = {region.room_name: region for region in result.regions if region.room_name}
    assert set(got) == {"洋室", "廊下"}, f"{[r.room_name for r in result.regions]} / {result.notes}"
    for name, region in got.items():
        assert region.area_sqm == pytest.approx(10.64, rel=0.02), f"{name}: 内法 10.64 ㎡ のはず"
        assert region.face_count > 1, f"{name}: 目地で割れた区画がまとまっていない"
        assert region.method_id == METHOD_WALL_NETWORK


def test_室名を1つも渡さなくても室の形が出る(tmp_path: Path) -> None:
    """**種に頼らないことの確かめ。** 7周目の負の対照4が原理的に起きない。"""
    named = find_regions_between_walls(
        _two_rooms(tmp_path / "named.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    blind = find_regions_between_walls(
        _two_rooms(tmp_path / "blind.pdf"), 0, SCALE_50, room_names=()
    )
    named_areas = sorted(round(r.area_sqm, 6) for r in named.regions)
    blind_areas = sorted(round(r.area_sqm, 6) for r in blind.regions)
    assert named_areas == blind_areas, "室名を渡すかどうかで形が変わっている"


def test_壁を越えない(tmp_path: Path) -> None:
    result = find_regions_between_walls(
        _two_rooms(tmp_path / "wall.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    for region in result.regions:
        assert region.area_sqm < 10.64 * 1.5, "2 室が 1 つにつながっている"


def test_室名が2つ入ったまとまりは面積を出さない(tmp_path: Path) -> None:
    """壁で仕切られていない 1 つの広間に室名が 2 つある図面。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    for a, b, c, d in (
        (0, 0, 4000, 0), (4000, 0, 4000, 3000), (4000, 3000, 0, 3000), (0, 3000, 0, 0),
    ):
        _seg(page, a, b, c, d)
    _label(page, 500, 1000, "洋室")
    _label(page, 2500, 2000, "廊下")
    path = _save(doc, tmp_path / "two_names.pdf")

    result = find_regions_between_walls(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    assert all(not region.room_name for region in result.regions) or result.regions == ()
    assert len(result.ambiguous) == 1, f"{result.regions} / {result.ambiguous}"
    assert set(result.ambiguous[0].room_names) == {"洋室", "廊下"}


def test_同じ線が二重に引いてあっても壁の数が増えない(tmp_path: Path) -> None:
    """**紙の上で見えない細長い面を壁と呼ばないことの確かめ。**

    実図面では同じ線が少しずれて二重に引いてある。既定の許容差では寄りきらず、
    **厚みがほぼゼロの細長い面**ができて、それが全部「壁」と判定されていた
    (実図面で 3,837 個の細長い面のうち 1,473 個)。
    """
    plain = find_regions_between_walls(
        _two_rooms(tmp_path / "plain.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    doubled = find_regions_between_walls(
        _two_rooms(tmp_path / "doubled.pdf", doubled_lines=True), 0, SCALE_50,
        room_names=("洋室", "廊下"),
    )
    assert doubled.wall_faces <= plain.wall_faces + 2, (
        f"二重の線で壁が増えている: {plain.wall_faces} → {doubled.wall_faces}"
    )
    got = {r.room_name: r.area_sqm for r in doubled.regions if r.room_name}
    assert set(got) == {"洋室", "廊下"}, f"{doubled.notes}"


def test_線が無いページでは0件(tmp_path: Path) -> None:
    """**負の対照1。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(300, 300), "洋室", fontname="japan")
    path = _save(doc, tmp_path / "empty.pdf")
    result = find_regions_between_walls(path, 0, SCALE_50, room_names=("洋室",))
    assert result.regions == ()
    assert result.ambiguous == ()


def test_同じページを2回読むと同じ結果(tmp_path: Path) -> None:
    """**負の対照3。**"""
    path = _two_rooms(tmp_path / "twice.pdf")
    first = find_regions_between_walls(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    second = find_regions_between_walls(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    assert [(r.room_name, round(r.area_sqm, 6)) for r in first.regions] == [
        (r.room_name, round(r.area_sqm, 6)) for r in second.regions
    ]


def test_知らない面積の数え方は受け付けない(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="面積の数え方"):
        find_regions_between_walls(
            _two_rooms(tmp_path / "bad.pdf"), 0, SCALE_50, area_basis="だいたい"
        )


def _single_line_rooms(path: Path) -> Path:
    """壁を **1 本線**で描いた 2 室。**壁の中身が無い。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    for a, b, c, d in (
        (0, 0, 4000, 0), (4000, 0, 4000, 6000), (4000, 6000, 0, 6000), (0, 6000, 0, 0),
        (0, 3000, 4000, 3000),
    ):
        _seg(page, a, b, c, d)
    _label(page, 500, 1000, "洋室")
    _label(page, 500, 4000, "廊下")
    return _save(doc, path)


def test_壁が1本線の図面では2室を別々に出さない(tmp_path: Path) -> None:
    """**負の対照2。原理的に効かないことを、こちらから先に示す。**

    壁の中身の面が無いので、2 室は直に接して 1 つのかたまりに溶ける。
    **溶けた結果を「室」として 2 つ出したら、この方法は嘘をついている。**
    """
    result = find_regions_between_walls(
        _single_line_rooms(tmp_path / "single.pdf"), 0, SCALE_50,
        room_names=("洋室", "廊下"),
    )
    named = [region for region in result.regions if region.room_name]
    assert len(named) <= 1, f"溶けているのに 2 室出している: {[r.room_name for r in named]}"
    assert len(result.ambiguous) == 1, f"{result.regions} / {result.ambiguous}"
    assert set(result.ambiguous[0].room_names) == {"洋室", "廊下"}


def test_紙の上で見えない面は壁と呼ばない() -> None:
    """止め札そのものの確かめ。**厚みだけを変えて、判定が変わること。**"""
    from axes.image_axis.pdf_room_outlines import is_wall_cavity

    # 3800 x 200mm の壁の中身。1/50 なら紙の上で 200 / 17.64 = 11.3pt
    mm_per_pt = 25.4 / 72 * 50
    wall = (3800.0 * 200.0 / 1_000_000.0, 2.0 * (3800.0 + 200.0), 190.0)
    assert is_wall_cavity(wall, 400.0, mm_per_pt, 1.0), "本物の壁を落としている"

    # 3800 x 10mm。紙の上で 0.57pt しかない(線が二重に引いてあるだけ)
    sliver = (3800.0 * 10.0 / 1_000_000.0, 2.0 * (3800.0 + 10.0), 10.0)
    assert not is_wall_cavity(sliver, 400.0, mm_per_pt, 1.0), "見えない面を壁と呼んでいる"
    assert is_wall_cavity(sliver, 400.0), "厚みを見ない古い判定では壁のままのはず"


def _room_with_a_shallow_wedge(path: Path) -> Path:
    """室の中を、**ごく浅い角度で交わる 2 本の線**が横切っている図面。

    交わる角度が浅いと、紙の上では 1 本の線にしか見えないのに、
    面としては**細長い楔**ができる。実図面にはこれが大量にある。
    厚みを見ないと、この楔は「狭くて長い」ので**壁と判定され、室を 2 つに割る。**
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    for offset in (-half, half):
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 3000 + offset, 4000 + half, 3000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 3000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 3000 + half)
    # 楔(左端で重なり、右端で 30mm 開く)。紙の上の厚みは 0.85pt。
    _seg(page, 100, 1500, 3900, 1500)
    _seg(page, 100, 1500, 3900, 1530)
    _seg(page, 3900, 1500, 3900, 1530)
    _label(page, 300, 700, "洋室")
    return _save(doc, path)


def test_紙の上で見えない楔は室を割らない(tmp_path: Path) -> None:
    """**この周の中身そのもの。**

    楔を壁と数えると、室が上下 2 つに割れて面積が半分ずつになる。
    """
    result = find_regions_between_walls(
        _room_with_a_shallow_wedge(tmp_path / "wedge.pdf"), 0, SCALE_50,
        room_names=("洋室",),
    )
    named = [region for region in result.regions if region.room_name == "洋室"]
    assert len(named) == 1, f"室が割れている: {[round(r.area_sqm, 2) for r in result.regions]}"
    assert named[0].area_sqm == pytest.approx(10.64, rel=0.03), (
        "楔を壁と数えて面積が減っている"
    )


def _room_and_a_small_box(path: Path) -> Path:
    """室の**外に**、室より小さい箱(内法 0.04㎡)が離れて置いてある図面。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    for offset in (-half, half):
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 3000 + offset, 4000 + half, 3000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 3000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 3000 + half)
    for offset in (-half, half):
        _seg(page, 6000 - half, 0 + offset, 6400 + half, 0 + offset)
        _seg(page, 6000 - half, 400 + offset, 6400 + half, 400 + offset)
        _seg(page, 6000 + offset, -half, 6000 + offset, 400 + half)
        _seg(page, 6400 + offset, -half, 6400 + offset, 400 + half)
    _label(page, 300, 700, "洋室")
    return _save(doc, path)


def test_室より小さい箱は出さない(tmp_path: Path) -> None:
    """**面積の窓が効いていることの確かめ。** 0.04㎡ を室として出さない。"""
    result = find_regions_between_walls(
        _room_and_a_small_box(tmp_path / "box.pdf"), 0, SCALE_50, room_names=("洋室",)
    )
    areas = [round(region.area_sqm, 3) for region in result.regions]
    assert all(area >= 0.5 for area in areas), f"窓の外のものを出している: {areas}"
    assert any(region.room_name == "洋室" for region in result.regions), result.notes


def test_室の中に柱があると面積を出さない(tmp_path: Path) -> None:
    """**輪郭が 1 本の輪にならないかたまりは出さない。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    for offset in (-half, half):
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 3000 + offset, 4000 + half, 3000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 3000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 3000 + half)
    for a, b, c, d in (
        (1800, 1300, 2200, 1300), (2200, 1300, 2200, 1700),
        (2200, 1700, 1800, 1700), (1800, 1700, 1800, 1300),
    ):
        _seg(page, a, b, c, d)
    _label(page, 300, 700, "洋室")
    path = _save(doc, tmp_path / "column.pdf")

    result = find_regions_between_walls(path, 0, SCALE_50, room_names=("洋室",))
    assert result.regions == (), f"穴のあるかたまりの面積を出してはいけない: {result.regions}"
    assert any("独立柱" in note for note in result.notes), result.notes


def _hand_built_graph() -> "PlanGraph":
    """**手で組んだ平面グラフ。** 開口の止め札そのものを確かめるため。

    実図面から組み直したグラフでは、開口の向こうは必ず壁の中身か隣の室になり、
    ほかの止め札が先に効いてしまって、開口だけが効く形を図面から作れない。
    """
    from axes.image_axis.pdf_room_outlines import PlanGraph

    coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (2.0, 0.0), (2.0, 1.0)]
    cycles = [[0, 1, 2, 3], [1, 4, 5, 2]]
    owner: dict[tuple[int, int], int] = {}
    for index, cycle in enumerate(cycles):
        for position in range(len(cycle)):
            owner[(cycle[position], cycle[(position + 1) % len(cycle)])] = index
    return PlanGraph(
        coords=coords, cycles=cycles, virtual_counts=[0, 0], owner=owner,
        virtual_edges={(1, 2), (2, 1)}, spans=[], table_rects=[],
        mm_per_pt=1.0, tolerance_pt=0.1,
    )


def test_開口を越えてつながない() -> None:
    """**建具の開口は室の境目である。**"""
    from axes.image_axis.pdf_room_outlines import PlanGraph
    from axes.image_axis.wall_network import _merge_between_walls

    graph = _hand_built_graph()
    metrics = [(4.0, 8000.0, 1000.0), (4.0, 8000.0, 1000.0)]
    components = _merge_between_walls([0, 1], set(), graph, metrics)
    assert len(components) == 2, "開口を越えて 2 室がつながっている"

    open_graph = PlanGraph(
        coords=graph.coords, cycles=graph.cycles, virtual_counts=graph.virtual_counts,
        owner=graph.owner, virtual_edges=set(), spans=graph.spans,
        table_rects=graph.table_rects, mm_per_pt=graph.mm_per_pt,
        tolerance_pt=graph.tolerance_pt,
    )
    assert len(_merge_between_walls([0, 1], set(), open_graph, metrics)) == 1, (
        "開口でない辺でもつながっていない"
    )
