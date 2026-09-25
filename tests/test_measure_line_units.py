"""周27 の道具(`benchmarks/measure_line_units.py`)の試験。

**実案件の出力は使わない。**手で作った行で、数え方だけを固定する。
"""

from __future__ import annotations

from benchmarks.measure_line_units import LINE1_SHARE, census, compare, judge

ROWS = [
    {"単位": "㎡", "数量": None, "人の入力待ち": True, "場所": "室A", "道": "仕上表"},
    {"単位": "㎡", "数量": None, "人の入力待ち": True, "場所": "室A", "道": "仕上表"},
    {"単位": "m", "数量": None, "人の入力待ち": True, "場所": "室B", "道": "仕上表"},
    {"単位": "箇所", "数量": 3, "人の入力待ち": False, "場所": "", "道": "凡例の記号"},
]


def test_単位ごとに数える() -> None:
    got = census(ROWS)
    assert got["行"] == 4
    assert got["単位ごとの行"] == {"m": 1, "箇所": 1, "㎡": 2}
    assert got["数量が入っている行"] == 1
    assert got["単位ごとの数量が入っている行"] == {"箇所": 1}


def test_人の入力待ちが指す場所を重複なく数える() -> None:
    got = census(ROWS)
    assert got["人の入力待ちの行"] == 3
    assert got["人の入力待ちが指す場所"] == 2
    assert got["場所あたりの行数"] == {1: 1, 2: 1}


def test_行が無ければ通過しない() -> None:
    assert judge(census([]))["線1_面積が無くても数量が出ている行"]["通過"] is False


def test_3割に届かなければ不通過() -> None:
    got = judge(census(ROWS))
    assert got["線1_面積が無くても数量が出ている行"]["通過"] is False


def test_3割に届けば通過() -> None:
    rows = [{"単位": "箇所", "数量": 1, "人の入力待ち": False, "場所": "", "道": "凡例の記号"}]
    assert judge(census(rows))["線1_面積が無くても数量が出ている行"]["通過"] is True


def test_面積の行に数量が入れば予想が外れる() -> None:
    rows = [{"単位": "㎡", "数量": 2.0, "人の入力待ち": False, "場所": "室A", "道": "仕上表"}]
    got = judge(census(rows))["線2_㎡の行に数量は入っているか"]
    assert got["本物"] == 1
    assert got["予想どおり"] is False


def test_同じ出力どうしなら変わっていないと出る() -> None:
    counted = census(ROWS)
    assert compare(counted, counted)["行の側は変わったか"] is False


def test_数量が増えれば変わったと出る() -> None:
    after = census(
        ROWS[:3] + [{"単位": "㎡", "数量": 1.0, "人の入力待ち": False, "場所": "室A", "道": "仕上表"}]
    )
    assert compare(census(ROWS), after)["行の側は変わったか"] is True


def test_合格の線は基準のまま() -> None:
    assert LINE1_SHARE == 0.3
