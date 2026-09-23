"""`axes/image_axis/room_regions.py` の回帰テスト。

実図面(P011 匿名化v2)で測ったところ、仕上表から読めた室名がのべ 45 回
図面ページに出てくるうち、**75.6% は 0.5㎡ 未満の小さすぎる面の中**にあった
(`docs/a2_real_drawing_wall_report.md`、`docs/a2_room_selection_criteria.md`)。
室は閉じすぎている。室の中に床の目地・造作・器具の線が描かれていて、
それが室の面を細かく割っている。

そこで、**仕上表から読んだ室名を種にして、割れた区画をまとめ直す。**

守りたいのは 6 つ。

1. 室の中に目地の線があっても、1 つの領域としてまとまること
2. **壁を越えないこと。** 隣の室を飲み込んだら面積が過大になる
3. **室名を 1 つしか渡していなくても、隣の室を飲み込まないこと。**
   止め札が「ほかの室名がそこにあったから」だけだと、
   仕上表に載っていない室は必ず飲み込まれる
4. **2 つ以上の室名が 1 つの領域に入ったら、面積を出さずに「決められない」として返す。**
   片方の室名を選ばない
5. **渡していない室名の領域は作らないこと**(名前を作らない)
6. まとめた領域でも壁芯の数え方が効くこと

テスト用の PDF はその場で組み立てる(実図面はコミットしない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_vector_symbols import DrawingScale
from axes.image_axis.pdf_room_outlines import PlanGraph
from axes.image_axis.room_regions import METHOD_ROOM_REGION, _grow, find_room_regions

PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72
ORIGIN = (200.0, 200.0)


def _mm(value: float) -> float:
    return value * PT_PER_MM_AT_50


SCALE_50 = DrawingScale(denominator=50.0, source_text="1/50")


def _line(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x1, y1), pymupdf.Point(x2, y2))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _seg(page: pymupdf.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    ox, oy = ORIGIN
    _line(page, ox + _mm(x1), oy + _mm(y1), ox + _mm(x2), oy + _mm(y2))


def _label(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    ox, oy = ORIGIN
    page.insert_text(pymupdf.Point(ox + _mm(x), oy + _mm(y)), text, fontname="japan", fontsize=8)


def _two_rooms(
    path: Path,
    *,
    joints: bool = True,
    joint_xs: tuple[float, ...] = (700.0, 1300.0, 2500.0, 3100.0, 3700.0),
) -> Path:
    """壁を 2 本線で描いた 2 室。**室の中に床の目地の線を引いてある。**

    通り芯は 0/4000(縦)と 0/3000/6000(横)。壁は 200。
    間仕切りの真ん中に 900 の開口(建具 1 枚ぶん)。
    壁芯の正解は上下どちらの室も 4000 x 3000 = 12.0 ㎡、
    内法は 3800 x 2800 = 10.64 ㎡。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    for offset in (-half, half):
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 6000 + offset, 4000 + half, 6000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 6000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 6000 + half)
    # 間仕切り(開口つき)
    for offset in (-half, half):
        _seg(page, -half, 3000 + offset, 1550, 3000 + offset)
        _seg(page, 2450, 3000 + offset, 4000 + half, 3000 + offset)
    if joints:
        # 床の目地。**壁ではない。** 室を細かく割る。
        for x in joint_xs:
            _seg(page, x, 100, x, 2900)
            _seg(page, x, 3100, x, 5900)
        for step in range(1, 7):
            y = 100.0 + step * 400.0
            _seg(page, 100, y, 3900, y)
            _seg(page, 100, y + 3000.0, 3900, y + 3000.0)
    _label(page, 300, 500, "洋室")
    _label(page, 300, 3500, "廊下")
    return _save(doc, path)


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


def test_室の中に目地があっても1つの領域にまとまる(tmp_path: Path) -> None:
    result = find_room_regions(
        _two_rooms(tmp_path / "joints.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    got = {region.room_name: region for region in result.regions}
    assert set(got) == {"洋室", "廊下"}, f"2 室のはず: {[r.room_name for r in result.regions]}"
    for name, region in got.items():
        assert region.area_sqm == pytest.approx(10.64, rel=0.02), f"{name}: 内法 10.64 ㎡ のはず"
        assert region.face_count > 1, f"{name}: 目地で割れた区画がまとまっていない"
        assert region.method_id == METHOD_ROOM_REGION


def test_壁を越えて隣の室を飲み込まない(tmp_path: Path) -> None:
    result = find_room_regions(
        _two_rooms(tmp_path / "wall.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    total = sum(region.area_sqm for region in result.regions)
    assert total == pytest.approx(10.64 * 2, rel=0.02), "2 室が 1 つにつながっている"
    assert result.ambiguous == (), f"取り違え: {result.ambiguous}"


def test_室名を1つしか渡さなくても隣の室を飲み込まない(tmp_path: Path) -> None:
    """**止め札が「ほかの室名」だけだと、仕上表に載っていない室を必ず飲み込む。**"""
    both = find_room_regions(
        _two_rooms(tmp_path / "both.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    alone = find_room_regions(
        _two_rooms(tmp_path / "alone.pdf"), 0, SCALE_50, room_names=("洋室",)
    )
    assert [r.room_name for r in alone.regions] == ["洋室"]
    reference = next(r for r in both.regions if r.room_name == "洋室")
    assert alone.regions[0].area_sqm == pytest.approx(reference.area_sqm, rel=0.001), (
        "隣に室名が無いと飲み込んでいる"
    )


def test_知らない室名の領域は作らない(tmp_path: Path) -> None:
    result = find_room_regions(
        _two_rooms(tmp_path / "unknown.pdf"), 0, SCALE_50, room_names=("洋室",)
    )
    assert all(region.room_name == "洋室" for region in result.regions)
    assert "廊下" not in {region.room_name for region in result.regions}


def test_でたらめな室名では領域が出ない(tmp_path: Path) -> None:
    """**負の対照。** 室名を見ずにまとめていないことの確かめ。"""
    result = find_room_regions(
        _two_rooms(tmp_path / "nonsense.pdf"), 0, SCALE_50,
        room_names=("架空室", "存在しない室", "だみー"),
    )
    assert result.regions == ()
    assert set(result.missing_names) == {"架空室", "存在しない室", "だみー"}


def test_2つの室名が1つの領域に入ったら面積を出さない(tmp_path: Path) -> None:
    """**片方の室名を選ばない。** つないだ面積は正解より大きく出る。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    # 壁で仕切られていない 1 つの部屋に、室名を 2 つ書いてある図面
    for a, b, c, d in ((0, 0, 4000, 0), (4000, 0, 4000, 3000), (4000, 3000, 0, 3000), (0, 3000, 0, 0)):
        _seg(page, a, b, c, d)
    _label(page, 500, 1000, "洋室")
    _label(page, 2500, 2000, "廊下")
    path = _save(doc, tmp_path / "two_names.pdf")

    result = find_room_regions(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    assert result.regions == (), f"面積を出してはいけない: {result.regions}"
    assert len(result.ambiguous) == 1
    assert set(result.ambiguous[0].room_names) == {"洋室", "廊下"}
    assert "決められない" in result.ambiguous[0].reason


def test_まとめた領域でも壁芯の数え方が効く(tmp_path: Path) -> None:
    result = find_room_regions(
        _two_rooms(tmp_path / "center.pdf"), 0, SCALE_50,
        room_names=("洋室", "廊下"), area_basis="壁芯",
    )
    got = {region.room_name: region for region in result.regions}
    assert set(got) == {"洋室", "廊下"}
    for name, region in got.items():
        assert region.area_basis == "壁芯", f"{name}: {region.area_basis_note}"
        assert region.area_sqm == pytest.approx(12.0, rel=0.02), f"{name}: 壁芯 12.0 ㎡ のはず"


def test_線が無いページでは0件(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(300, 300), "洋室", fontname="japan")
    path = _save(doc, tmp_path / "empty.pdf")
    result = find_room_regions(path, 0, SCALE_50, room_names=("洋室",))
    assert result.regions == ()
    assert result.missing_names == ("洋室",)


def test_室名を渡さなければ何も出ない(tmp_path: Path) -> None:
    result = find_room_regions(_two_rooms(tmp_path / "none.pdf"), 0, SCALE_50, room_names=())
    assert result.regions == ()
    assert result.missing_names == ()


def test_同じページを2回読むと同じ結果(tmp_path: Path) -> None:
    path = _two_rooms(tmp_path / "twice.pdf")
    first = find_room_regions(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    second = find_room_regions(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    assert [(r.room_name, round(r.area_sqm, 6)) for r in first.regions] == [
        (r.room_name, round(r.area_sqm, 6)) for r in second.regions
    ]


def test_知らない面積の数え方は受け付けない(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="面積の数え方"):
        find_room_regions(
            _two_rooms(tmp_path / "bad.pdf"), 0, SCALE_50,
            room_names=("洋室",), area_basis="だいたい",
        )


def test_開口に線が突き出ている図面では面積を出さない(tmp_path: Path) -> None:
    """**安全側に外れることの確かめ。**

    床の目地の線が建具の開口のまん中(x=2000)まで伸びていると、開口を仮の線で
    閉じきれない。それでも**2 室が 1 つにつながってはいけない。**
    つないだ面積は正解(10.64 ㎡)のほぼ 2 倍になる。

    このとき面積は**少なめに出る**(まとまりきらなかった区画のぶん)。
    少なめに外れるのは、倍に出るよりましである。
    """
    result = find_room_regions(
        _two_rooms(tmp_path / "dangling.pdf", joint_xs=(700.0, 1300.0, 2000.0, 2500.0, 3100.0, 3700.0)),
        0,
        SCALE_50,
        room_names=("洋室", "廊下"),
    )
    assert {region.room_name for region in result.regions} == {"洋室", "廊下"}
    for region in result.regions:
        assert region.area_sqm < 10.64 * 1.2, f"{region.room_name}: 2 室がつながっている"
        assert region.area_sqm < 10.64, f"{region.room_name}: 少なめに出るはず"


def _single_line_rooms(
    path: Path, *, second_room: bool = True, doorway: float = 0.0, names: tuple[str, ...] = ("洋室", "廊下")
) -> Path:
    """壁を **1 本線**で描いた 1〜2 室。壁の中身が無いので、止め札は開口と室名だけ。

    通り芯は 0/4000(縦)と 0/3000/6000(横)。面積はどちらも 4000 x 3000 = 12.0 ㎡。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    bottom = 6000.0 if second_room else 3000.0
    _seg(page, 0, 0, 4000, 0)
    _seg(page, 4000, 0, 4000, bottom)
    _seg(page, 4000, bottom, 0, bottom)
    _seg(page, 0, bottom, 0, 0)
    _label(page, 500, 1000, names[0])
    if second_room:
        if doorway > 0:
            mid = 2000.0
            _seg(page, 0, 3000, mid - doorway / 2, 3000)
            _seg(page, mid + doorway / 2, 3000, 4000, 3000)
        else:
            _seg(page, 0, 3000, 4000, 3000)
        if len(names) > 1:
            _label(page, 500, 4000, names[1])
    return _save(doc, path)


def test_壁が1本線の1室でも外へ漏れない(tmp_path: Path) -> None:
    """**図面の外側を飲み込まないことの確かめ。** 壁の中身が無いので止め札は外側だけ。"""
    result = find_room_regions(
        _single_line_rooms(tmp_path / "one.pdf", second_room=False), 0, SCALE_50,
        room_names=("洋室",),
    )
    assert [r.room_name for r in result.regions] == ["洋室"]
    assert result.regions[0].area_sqm == pytest.approx(12.0, rel=0.01)


def test_壁が1本線でも隣の室を飲み込まない(tmp_path: Path) -> None:
    """**別の室名が入っている面を飲み込まないことの確かめ。**

    壁が 1 本線なので壁の中身が無い。2 つの面は 1 本の線で直に接している。
    """
    result = find_room_regions(
        _single_line_rooms(tmp_path / "two.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    got = {region.room_name: region.area_sqm for region in result.regions}
    assert set(got) == {"洋室", "廊下"}
    for name, area in got.items():
        assert area == pytest.approx(12.0, rel=0.01), f"{name}: 隣とつながっている"


def test_開口を越えて広げない(tmp_path: Path) -> None:
    """**建具の開口は室の境目である。** 隣に室名が無くても越えない。"""
    result = find_room_regions(
        _single_line_rooms(tmp_path / "door.pdf", doorway=900.0, names=("洋室",)),
        0,
        SCALE_50,
        room_names=("洋室",),
    )
    assert [r.room_name for r in result.regions] == ["洋室"]
    assert result.regions[0].area_sqm == pytest.approx(12.0, rel=0.01), (
        "開口を越えて隣まで広がっている"
    )
    assert result.regions[0].virtual_edges >= 1, "開口を仮に閉じたことを残すこと"


def test_図面枠があっても室ごとに分かれる(tmp_path: Path) -> None:
    """**室名はいちばん小さい入れ物のものとして数える。**

    図面枠の内側の面は、室名を 2 つとも含んでしまう。いちばん大きい面を
    種にすると、2 室が 1 つの「決められない」領域になる。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    # 図面枠
    for a, b, c, d in (
        (-2000, -2000, 8000, -2000), (8000, -2000, 8000, 9000),
        (8000, 9000, -2000, 9000), (-2000, 9000, -2000, -2000),
    ):
        _seg(page, a, b, c, d)
    for a, b, c, d in (
        (0, 0, 4000, 0), (4000, 0, 4000, 6000), (4000, 6000, 0, 6000), (0, 6000, 0, 0),
        (0, 3000, 4000, 3000),
    ):
        _seg(page, a, b, c, d)
    _label(page, 500, 1000, "洋室")
    _label(page, 500, 4000, "廊下")
    path = _save(doc, tmp_path / "frame.pdf")

    result = find_room_regions(path, 0, SCALE_50, room_names=("洋室", "廊下"))
    got = {region.room_name: region.area_sqm for region in result.regions}
    assert set(got) == {"洋室", "廊下"}, f"決められないになっている: {result.ambiguous}"
    for area in got.values():
        assert area == pytest.approx(12.0, rel=0.01)


def test_室の中に柱があると面積を出さない(tmp_path: Path) -> None:
    """**輪郭が 1 本の輪にならない領域は出さない。**

    独立柱があると室の輪郭に穴が開く。穴のある領域の面積を外周だけから
    出すと、柱のぶんだけ大きく出る。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    for a, b, c, d in ((0, 0, 4000, 0), (4000, 0, 4000, 3000), (4000, 3000, 0, 3000), (0, 3000, 0, 0)):
        _seg(page, a, b, c, d)
    # 室のまん中の独立柱(どの壁にも触れていない)
    for a, b, c, d in (
        (1800, 1300, 2200, 1300), (2200, 1300, 2200, 1700),
        (2200, 1700, 1800, 1700), (1800, 1700, 1800, 1300),
    ):
        _seg(page, a, b, c, d)
    _label(page, 500, 500, "洋室")
    path = _save(doc, tmp_path / "column.pdf")

    result = find_room_regions(path, 0, SCALE_50, room_names=("洋室",))
    assert result.regions == (), f"穴のある領域の面積を出してはいけない: {result.regions}"
    assert any("独立柱" in note for note in result.notes), result.notes


def _hand_built_graph() -> PlanGraph:
    """**手で組んだ平面グラフ。**

    実図面から組み直したグラフでは、開口の向こうにあるのは
    壁の中身(壁を 2 本線で描いた図面)か、隣の室そのもの(1 本線の図面)に
    なってしまい、**開口の止め札だけが効く形を図面からは作れない。**
    そこで、止め札そのものを確かめるために、面を 2 つだけ手で組む。

    面 0 は 4.0 ㎡ の室、面 1 は 0.3 ㎡ の小さな区画。
    2 つは 1 本の辺で接していて、**その辺は「こちらが仮に閉じた辺」である。**
    """
    coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (2.0, 0.0), (2.0, 1.0)]
    cycles = [[0, 1, 2, 3], [1, 4, 5, 2]]
    owner: dict[tuple[int, int], int] = {}
    for index, cycle in enumerate(cycles):
        for position in range(len(cycle)):
            owner[(cycle[position], cycle[(position + 1) % len(cycle)])] = index
    return PlanGraph(
        coords=coords,
        cycles=cycles,
        virtual_counts=[0, 0],
        owner=owner,
        virtual_edges={(1, 2), (2, 1)},
        spans=[],
        table_rects=[],
        mm_per_pt=1.0,
        tolerance_pt=0.1,
    )


def test_開口の向こうが室より小さくても越えない() -> None:
    """**開口が止め札として効いていることの確かめ(止め札そのものを見る)。**

    開口の向こうが物入のように室より小さく、壁の中身も無いとき、
    ほかの止め札はどれも効かない。ここで越えると、
    その区画のぶんだけ室の面積が大きく出る。
    """
    graph = _hand_built_graph()
    metrics = [(4.0, 8000.0, 1000.0), (0.3, 2600.0, 230.0)]
    region, names, _ = _grow(
        0, graph, metrics, set(), {0: {"洋室"}}, min_sqm=0.5, max_sqm=200.0
    )
    assert region == {0}, "開口を越えて小さい区画を飲み込んでいる"
    assert names == {"洋室"}


def test_開口でなければ小さい区画はまとまる() -> None:
    """**対になる確かめ。** 同じ形で、辺が開口でなければまとまること。

    これが無いと、上のテストは「そもそも広がらないだけ」でも通ってしまう。
    """
    graph = _hand_built_graph()
    graph = PlanGraph(
        coords=graph.coords,
        cycles=graph.cycles,
        virtual_counts=graph.virtual_counts,
        owner=graph.owner,
        virtual_edges=set(),
        spans=graph.spans,
        table_rects=graph.table_rects,
        mm_per_pt=graph.mm_per_pt,
        tolerance_pt=graph.tolerance_pt,
    )
    metrics = [(4.0, 8000.0, 1000.0), (0.3, 2600.0, 230.0)]
    region, _, _ = _grow(
        0, graph, metrics, set(), {0: {"洋室"}}, min_sqm=0.5, max_sqm=200.0
    )
    assert region == {0, 1}, "開口でない辺でも広がっていない"


def _shallow_two_rooms(path: Path) -> Path:
    """壁を 2 本線で描いた浅い 2 室。**壁の中身が室より小さい。**

    通り芯は 0/2000/4000(縦)と 0/2000(横)。壁は 200。
    間仕切りの中身は 200 x 1800 = 0.36 ㎡、左右の壁の中身は 200 x 2200 = 0.44 ㎡ で、
    どちらも室の下限 0.5 ㎡ より小さい。**「室の大きさで止まる」では止まらない。**
    内法はどちらの室も 1800 x 1800 = 3.24 ㎡。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    half = 100.0
    for offset in (-half, half):
        _seg(page, -half, 0 + offset, 4000 + half, 0 + offset)
        _seg(page, -half, 2000 + offset, 4000 + half, 2000 + offset)
        _seg(page, 0 + offset, -half, 0 + offset, 2000 + half)
        _seg(page, 2000 + offset, -half, 2000 + offset, 2000 + half)
        _seg(page, 4000 + offset, -half, 4000 + offset, 2000 + half)
    _label(page, 300, 1000, "洋室")
    _label(page, 2300, 1000, "廊下")
    return _save(doc, path)


def test_壁の中身が室より小さくても飲み込まない(tmp_path: Path) -> None:
    """**壁の中身が止め札として効いていることの確かめ。**

    短い間仕切りの中身は 0.36 ㎡ しかなく、室の下限より小さい。
    ここで飲み込むと、壁のぶんだけ面積が大きく出る。
    """
    result = find_room_regions(
        _shallow_two_rooms(tmp_path / "shallow.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    got = {region.room_name: region.area_sqm for region in result.regions}
    assert set(got) == {"洋室", "廊下"}, f"{result.notes} / {result.ambiguous}"
    for name, area in got.items():
        assert area == pytest.approx(3.24, rel=0.01), f"{name}: 壁の中身を飲み込んでいる"
        assert area < 3.24 * 1.05, f"{name}: 面積が大きく出ている"


def _one_area_split_by_joints(path: Path) -> Path:
    """**壁で仕切られていない 1 つの広間**を目地で細かく割り、室名を 2 つ書いた図面。

    どちらの種から広げても、相手の室名の区画で止まるが、**あいだの区画は
    両方から取り合いになる。** どちらのものとも決められない。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    for a, b, c, d in (
        (0, 0, 4000, 0), (4000, 0, 4000, 3000), (4000, 3000, 0, 3000), (0, 3000, 0, 0),
    ):
        _seg(page, a, b, c, d)
    for step in range(1, 8):
        _seg(page, step * 500.0, 0, step * 500.0, 3000)
    for step in range(1, 6):
        _seg(page, 0, step * 500.0, 4000, step * 500.0)
    _label(page, 100, 400, "洋室")
    _label(page, 3100, 2400, "廊下")
    return _save(doc, path)


def test_壁が無い広間に室名が2つあると取り合いになって面積を出さない(tmp_path: Path) -> None:
    """**2 つの領域が重なったら、どちらのものとも決めない。**

    室名の区画で止まるので 1 つの領域に室名が 2 つ入ることはないが、
    あいだの区画は両方の領域に入る。ここで黙ってどちらかに寄せると、
    **どちらの室の面積も正解より大きく出る。**
    """
    result = find_room_regions(
        _one_area_split_by_joints(tmp_path / "tug.pdf"), 0, SCALE_50, room_names=("洋室", "廊下")
    )
    assert result.regions == (), f"面積を出してはいけない: {result.regions}"
    assert len(result.ambiguous) == 2, result.ambiguous
    for ambiguous in result.ambiguous:
        assert "重なっている" in ambiguous.reason, ambiguous.reason


def _ring_around_an_inner_room(path: Path) -> Path:
    """**まん中の区画をぐるりと囲む**、目地で割られた通路。

    外は 2000 x 2000、まん中に 800 x 800 の区画がある。
    通路は 8 つの区画に割れていて、どれも 0.5 ㎡ 未満。
    まとめると**輪の中に穴が開いた領域**になる。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    for a, b, c, d in (
        (0, 0, 2000, 0), (2000, 0, 2000, 2000), (2000, 2000, 0, 2000), (0, 2000, 0, 0),
    ):
        _seg(page, a, b, c, d)
    for a, b, c, d in (
        (600, 600, 1400, 600), (1400, 600, 1400, 1400),
        (1400, 1400, 600, 1400), (600, 1400, 600, 600),
    ):
        _seg(page, a, b, c, d)
    # まん中の区画と外をつなぐ目地。これで通路が 8 つに割れる。
    for x in (600.0, 1400.0):
        _seg(page, x, 0, x, 600)
        _seg(page, x, 1400, x, 2000)
    for y in (600.0, 1400.0):
        _seg(page, 0, y, 600, y)
        _seg(page, 1400, y, 2000, y)
    _label(page, 150, 350, "廊下")
    return _save(doc, path)


def test_まん中に穴が開いた領域は面積を出さない(tmp_path: Path) -> None:
    """**輪郭が 1 本の輪にならない領域は出さない。**

    外周だけで数えると、まん中の区画のぶんまで足してしまう
    (3.36 ㎡ の通路が 4.0 ㎡ になる)。
    """
    result = find_room_regions(
        _ring_around_an_inner_room(tmp_path / "ring.pdf"), 0, SCALE_50, room_names=("廊下",)
    )
    assert result.regions == (), f"穴のある領域の面積を出してはいけない: {result.regions}"
    assert any("輪郭" in note for note in result.notes), result.notes
