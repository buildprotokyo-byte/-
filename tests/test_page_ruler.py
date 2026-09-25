"""目盛りの試験。**合成データだけ**を使う。実図面の寸法はここに書かない。

基準は `docs/k29_area_expert_reading_criteria.md` 追記 5(**作る前にコミット済み**)。
"""

from __future__ import annotations

import math

import pymupdf
import pytest

from axes.image_axis.page_ruler import (
    SOURCE_DIMENSIONS,
    SOURCE_HUMAN,
    SOURCE_PRINTED_SCALE,
    PageRuler,
    RulerConflict,
    RulerError,
    agree,
    group_by_agreement,
    reconcile,
    ruler_from_page_scale,
    ruler_from_printed_scale,
    ruler_from_reference_length,
)

#: 1/50 の図面での 1pt あたりのミリ数。50 / 72 * 25.4。
MM_PER_PT_AT_50 = 50 * 25.4 / 72

TOLERANCE = 0.05


class Test人が入れた基準の長さ:
    def test_2点と実寸から比が出る(self):
        ruler = ruler_from_reference_length(0, (100.0, 100.0), (200.0, 100.0), 5000.0)
        assert ruler.mm_per_point == pytest.approx(50.0)
        assert ruler.source == SOURCE_HUMAN

    def test_斜めの2点でも長さで測る(self):
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (30.0, 40.0), 500.0)
        assert ruler.mm_per_point == pytest.approx(10.0)

    def test_同じ点を2つ指したら作らない(self):
        with pytest.raises(RulerError):
            ruler_from_reference_length(0, (10.0, 10.0), (10.0, 10.0), 1000.0)

    def test_実寸が0以下なら作らない(self):
        with pytest.raises(RulerError):
            ruler_from_reference_length(0, (0.0, 0.0), (10.0, 0.0), 0.0)

    def test_紙の縮尺を見ていない(self):
        """**用紙が拡大縮小されていても効く。**比は 2 点と実寸だけで決まる。"""
        normal = ruler_from_reference_length(0, (0.0, 0.0), (100.0, 0.0), 5000.0)
        enlarged = ruler_from_reference_length(0, (0.0, 0.0), (141.4, 0.0), 5000.0)
        assert normal.mm_per_point != pytest.approx(enlarged.mm_per_point)


class Test校正済みにはできない:
    def test_人の入力もcalibratedはFalse(self):
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (100.0, 0.0), 5000.0)
        assert ruler.calibrated is False

    def test_記入された寸法もcalibratedはFalse(self):
        ruler = ruler_from_printed_scale(0, 50.0)
        assert ruler.calibrated is False

    def test_校正済みにしようとすると撥ねる(self):
        """**ここを True にすると、人が入れた値と印字が合っただけで階層1に届く。**"""
        with pytest.raises(RulerError):
            PageRuler(
                page_index=0,
                mm_per_point=1.0,
                source=SOURCE_HUMAN,
                calibrated=True,
            )


class Test測ること:
    def _ruler(self) -> PageRuler:
        return ruler_from_printed_scale(0, 50.0)

    def test_長さを実寸に直す(self):
        assert self._ruler().length_mm(72.0) == pytest.approx(50 * 25.4)

    def test_矩形の幅と高さ(self):
        width, height = self._ruler().rect_mm((0.0, 0.0, 72.0, 36.0))
        assert width == pytest.approx(50 * 25.4)
        assert height == pytest.approx(25 * 25.4)

    def test_矩形の面積(self):
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (1.0, 0.0), 1000.0)
        assert ruler.rect_area_sqm((0.0, 0.0, 3.0, 2.0)) == pytest.approx(6.0)

    def test_多角形の面積は閉じていなくてよい(self):
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (1.0, 0.0), 1000.0)
        square = ((0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0))
        assert ruler.polygon_area_sqm(square) == pytest.approx(6.0)

    def test_L字の多角形も測れる(self):
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (1.0, 0.0), 1000.0)
        shape = ((0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (2.0, 1.0), (2.0, 3.0), (0.0, 3.0))
        assert ruler.polygon_area_sqm(shape) == pytest.approx(8.0)  # 4x1 + 2x2

    def test_点が3つ未満なら測らない(self):
        with pytest.raises(RulerError):
            self._ruler().polygon_area_sqm(((0.0, 0.0), (1.0, 1.0)))

    def test_縮尺の分母を印字と比べられる(self):
        assert ruler_from_printed_scale(0, 50.0).denominator == pytest.approx(50.0)


class Test回転で値が変わらない:
    """**長さは回転で変わらない。**K-26 で踏んだ轍(回転前の座標を前提にした値)を
    同じ形で踏まないよう、回転 0・90・270 の紙で固定する。"""

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_回した紙でも2点の距離は同じ(self, rotation: int, tmp_path) -> None:
        path = tmp_path / f"r{rotation}.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=1190, height=842)
        page.set_rotation(rotation)
        doc.save(path)
        doc.close()

        with pymupdf.open(path) as opened:
            loaded = opened.load_page(0)
            start = pymupdf.Point(100.0, 100.0)
            end = pymupdf.Point(200.0, 100.0)
            shown_start = start * loaded.rotation_matrix
            shown_end = end * loaded.rotation_matrix

        ruler = ruler_from_reference_length(0, start, end, 5000.0)
        assert ruler.distance_mm(shown_start, shown_end) == pytest.approx(5000.0)

    def test_回転は比そのものに掛からない(self):
        """比は長さの割り算なので、回転行列は約分されて消える。"""
        ruler = ruler_from_reference_length(0, (0.0, 0.0), (100.0, 0.0), 5000.0)
        turned = ruler_from_reference_length(0, (0.0, 0.0), (0.0, 100.0), 5000.0)
        assert ruler.mm_per_point == pytest.approx(turned.mm_per_point)


class Test食い違いを平均しない:
    def _pair(self, first_mm: float, second_mm: float) -> tuple[PageRuler, PageRuler]:
        return (
            PageRuler(page_index=3, mm_per_point=first_mm, source=SOURCE_DIMENSIONS),
            PageRuler(page_index=3, mm_per_point=second_mm, source=SOURCE_PRINTED_SCALE),
        )

    def test_揃っていれば1つ選ぶ(self):
        chosen = reconcile(self._pair(17.639, 17.637), tolerance=TOLERANCE)
        assert isinstance(chosen, PageRuler)
        assert chosen.source == SOURCE_DIMENSIONS

    def test_人の基準の長さが一番優先される(self):
        rulers = [
            PageRuler(page_index=0, mm_per_point=17.64, source=SOURCE_PRINTED_SCALE),
            PageRuler(page_index=0, mm_per_point=17.63, source=SOURCE_HUMAN),
            PageRuler(page_index=0, mm_per_point=17.65, source=SOURCE_DIMENSIONS),
        ]
        chosen = reconcile(rulers, tolerance=TOLERANCE)
        assert isinstance(chosen, PageRuler)
        assert chosen.source == SOURCE_HUMAN

    def test_食い違ったら平均せず食い違いを返す(self):
        outcome = reconcile(self._pair(10.0, 20.0), tolerance=TOLERANCE)
        assert isinstance(outcome, RulerConflict)
        assert outcome.spread == pytest.approx(2 / 3, rel=1e-3)
        assert "食い違っている" in outcome.describe()

    def test_食い違いには両方の出どころが残る(self):
        outcome = reconcile(self._pair(10.0, 20.0), tolerance=TOLERANCE)
        assert isinstance(outcome, RulerConflict)
        assert {ruler.source for ruler in outcome.rulers} == {
            SOURCE_DIMENSIONS,
            SOURCE_PRINTED_SCALE,
        }

    def test_目盛りが1つも無ければ測らない(self):
        with pytest.raises(RulerError):
            reconcile([], tolerance=TOLERANCE)

    def test_別のページの目盛りは突き合わせない(self):
        rulers = [
            PageRuler(page_index=0, mm_per_point=17.6, source=SOURCE_DIMENSIONS),
            PageRuler(page_index=1, mm_per_point=17.6, source=SOURCE_PRINTED_SCALE),
        ]
        with pytest.raises(RulerError):
            reconcile(rulers, tolerance=TOLERANCE)


class Test同じ比で足りる組にまとめる:
    def test_近い比は1つの組になる(self):
        rulers = [
            PageRuler(page_index=index, mm_per_point=value, source=SOURCE_DIMENSIONS)
            for index, value in enumerate((17.60, 17.64, 17.66))
        ]
        groups = group_by_agreement(rulers, tolerance=TOLERANCE)
        assert len(groups) == 1

    def test_離れた比は別の組になる(self):
        rulers = [
            PageRuler(page_index=index, mm_per_point=value, source=SOURCE_DIMENSIONS)
            for index, value in enumerate((10.58, 17.64, 10.60))
        ]
        groups = group_by_agreement(rulers, tolerance=TOLERANCE)
        assert sorted(len(group) for group in groups) == [1, 2]


class Test作れないもの:
    def test_比が0以下なら作らない(self):
        with pytest.raises(RulerError):
            PageRuler(page_index=0, mm_per_point=0.0, source=SOURCE_HUMAN)

    def test_比が無限なら作らない(self):
        with pytest.raises(RulerError):
            PageRuler(page_index=0, mm_per_point=math.inf, source=SOURCE_HUMAN)

    def test_知らない出どころは撥ねる(self):
        with pytest.raises(RulerError):
            PageRuler(page_index=0, mm_per_point=1.0, source="どこか")

    def test_mm_per_pointを持たないものからは作らない(self):
        with pytest.raises(RulerError):
            ruler_from_page_scale(0, object())

    def test_印字の縮尺が0以下なら作らない(self):
        with pytest.raises(RulerError):
            ruler_from_printed_scale(0, 0.0)

    def test_負の長さは測らない(self):
        with pytest.raises(RulerError):
            ruler_from_printed_scale(0, 50.0).length_mm(-1.0)


class Test記入された寸法から作る:
    class _Scale:
        mm_per_point = MM_PER_PT_AT_50
        reading_count = 12

    def test_一致した読みの件数が但し書きに残る(self):
        ruler = ruler_from_page_scale(7, self._Scale())
        assert ruler.source == SOURCE_DIMENSIONS
        assert ruler.mm_per_point == pytest.approx(MM_PER_PT_AT_50)
        assert "12" in ruler.note


class Test許容差に既定値を置かない:
    def test_許容差は呼び出し側が渡す(self):
        first = PageRuler(page_index=0, mm_per_point=17.60, source=SOURCE_DIMENSIONS)
        second = PageRuler(page_index=0, mm_per_point=18.40, source=SOURCE_PRINTED_SCALE)
        assert agree(first, second, tolerance=0.05) is True
        assert agree(first, second, tolerance=0.01) is False

    def test_負の許容差は撥ねる(self):
        first = PageRuler(page_index=0, mm_per_point=17.6, source=SOURCE_DIMENSIONS)
        with pytest.raises(RulerError):
            agree(first, first, tolerance=-0.1)
