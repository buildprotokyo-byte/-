"""周39 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

import json
from pathlib import Path

import benchmarks.measure_symbol_naming as m
from estimating.quantities import QuantityItem


def 数量(target="記号::名前不明12mm::ページ3", name=None):
    return QuantityItem(
        target=target,
        value_range=(12.0, 12.0),
        unit="箇所",
        axis_id="drawing",
        method_id="test",
        derivation="read",
        provenance={"page_number": 3, "symbol_name": name},
    )


def test_対照表のページを読む(tmp_path: Path):
    """**記憶からではなく、人が作った対照表から取る。**"""
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "work_marks": [{"source_page": 6}, {"source_page": 22}],
                "symbols": [{"source_page": 22}],
                "line_colors": [{"source_page": 6}],
                "line_styles": [],
            }
        ),
        encoding="utf-8",
    )
    assert m.legend_pages_from(path) == (6, 22)


def test_ページを名乗らない行は数えない(tmp_path: Path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"symbols": [{"code": "A"}]}), encoding="utf-8")
    assert m.legend_pages_from(path) == ()


def test_囮は凡例のページを避ける():
    decoy = m.decoy_pages(34, (6, 22), 2)
    assert len(decoy) == 2
    assert 6 not in decoy
    assert 22 not in decoy


def test_囮は同じ種で同じページを引く():
    assert m.decoy_pages(34, (6, 22), 2) == m.decoy_pages(34, (6, 22), 2)


def test_囮は本物と同じ枚数():
    assert len(m.decoy_pages(34, (6, 22, 30), 3)) == 3


def test_宣言するのは渡したページだけ():
    宣言 = m.declarations_for((6, 22))
    assert [d.page_number for d in 宣言] == [6, 22]
    assert all(d.kind == m.LEGEND_KIND for d in 宣言)


def test_名前はprovenanceから数える():
    assert m.named([数量(name="換気扇"), 数量(name=None)]) == 1


def test_名前が空でなければ付いたと数える():
    """`symbol_name` が `None` でないかだけを見る。**中身は見ない**(6 節)。"""
    assert m.named([数量(name="何か")]) == 1


def test_記号の数量を種類で数える():
    assert m.symbols([数量(), 数量(target="開き戸::ページ3")]) == 1


def test_線1は囮の2倍を求める():
    assert m.LINE1_DECOY_FACTOR == 2


def test_線3の合格は1割():
    assert m.LINE3_MAX_LOST_SHARE == 0.10
