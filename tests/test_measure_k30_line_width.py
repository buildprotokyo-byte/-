"""線の太さを数える道具の試験。**合成データだけ**を使う。

基準は `docs/k30_line_width_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

from collections import Counter

import pymupdf
import pytest

from benchmarks.measure_k30_line_width import (
    _prune_leaves,
    closed_ratio,
    line_widths,
    peaks,
)


class Test太さの山:
    def test_JISの1対2対4は3つの山に分かれる(self):
        groups = peaks(Counter({0.25: 10, 0.5: 10, 1.0: 10}))
        assert [len(group) for group in groups] == [1, 1, 1]

    def test_近い太さは同じ山になる(self):
        """**1.5 倍未満は同じ山。**重ね描きのゆらぎで山が増えないようにする。"""
        groups = peaks(Counter({0.24: 10, 0.26: 10, 0.3: 10}))
        assert len(groups) == 1

    def test_太さが1種類なら山は1つ(self):
        assert len(peaks(Counter({0.24: 100}))) == 1

    def test_太さが記録されていない線は山に入れない(self):
        """**既定値で埋めない。**0 は「記録が無い」であって「細い」ではない。"""
        assert peaks(Counter({0.0: 100})) == []

    def test_線が無ければ山も無い(self):
        assert peaks(Counter()) == []

    def test_本数は見ていない(self):
        """**基準に書いたとおり、太さの種類だけで切る。**
        1 本しかない太さも 1 つの山になる。報告でこの緩さを断っている。"""
        groups = peaks(Counter({0.25: 9999, 1.0: 1}))
        assert len(groups) == 2


class Test太さを数える:
    def test_描いた線の太さが取れる(self, tmp_path):
        path = tmp_path / "w.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=400)
        page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(100, 10), width=1.0)
        page.draw_line(pymupdf.Point(10, 20), pymupdf.Point(100, 20), width=4.0)
        doc.save(path)
        doc.close()
        with pymupdf.open(path) as opened:
            counts = line_widths(opened.load_page(0))
        assert set(counts) == {1.0, 4.0}


class Test輪になっている線を数える:
    def test_四角は全部が輪に残る(self):
        nodes = {}
        edges = [((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))]
        assert _prune_leaves(nodes, edges) == 4

    def test_開いた形は全部落ちる(self):
        nodes = {}
        edges = [((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1))]
        assert _prune_leaves(nodes, edges) == 0

    def test_輪から伸びた枝は落ちる(self):
        nodes = {}
        edges = [
            ((0, 0), (1, 0)),
            ((1, 0), (1, 1)),
            ((1, 1), (0, 1)),
            ((0, 1), (0, 0)),
            ((1, 1), (5, 5)),
        ]
        assert _prune_leaves(nodes, edges) == 4

    def test_その太さの線が無ければ0本(self, tmp_path):
        """**曲線しか無い太さは 0 / 0 になる。**報告で「測れていない」と書いた形。"""
        path = tmp_path / "c.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=400)
        page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(100, 10), width=1.0)
        doc.save(path)
        doc.close()
        with pymupdf.open(path) as opened:
            assert closed_ratio(opened.load_page(0), {4.0}) == (0, 0)

    def test_閉じた矩形は輪として数える(self, tmp_path):
        path = tmp_path / "r.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=400)
        page.draw_rect(pymupdf.Rect(50, 50, 200, 150), width=2.0)
        doc.save(path)
        doc.close()
        with pymupdf.open(path) as opened:
            closed, total = closed_ratio(opened.load_page(0), {2.0})
        assert total > 0
        assert closed == total


def test_出すのは数だけ(tmp_path):
    """**実案件の数字を画面に出さない。**返るのは件数と太さの値だけ。"""
    path = tmp_path / "x.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(100, 10), width=1.0)
    doc.save(path)
    doc.close()
    with pymupdf.open(path) as opened:
        counts = line_widths(opened.load_page(0))
    assert all(isinstance(value, int) for value in counts.values())
    assert all(isinstance(key, float) for key in counts)
