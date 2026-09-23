"""`benchmarks/measure_group_total_multi_stop.py` が測っている性質を縛る(K-07 の6番)。

- いまの `check_group_total()` は、停止した要素が2つ以上あると、群の許容幅を超える
  誤りでも素通りさせる(直していない。測った事実をテストで固定する)
- スクリプトの中だけの候補(設計書 4-1節の式)は、それを吸収と判定する
- 候補は、停止が1つの群と陰性対照では いま と同じ判定を返す
- 群の許容幅の中の誤りは、候補でも見えない(死角。隠さずに固定する)
"""

from __future__ import annotations

from benchmarks.measure_group_total_multi_stop import measure_one


def test_two_stopped_beyond_group_tolerance_slips_now_and_candidate_catches_it() -> None:
    row = measure_one(2, 3, "tier1", -3)
    assert row["band"] == "beyond_group_tolerance"
    assert row["current"] == "sat_not_absorbed"
    assert row["current_slip"] is True
    assert row["candidate"] == "absorbed"
    assert row["candidate_by_group_formula_only"] is True
    assert row["candidate_audit"] and "door_0" not in row["candidate_audit"]
    assert "door_5" not in row["candidate_audit"]


def test_one_stopped_candidate_matches_current() -> None:
    row = measure_one(1, 2, "tier1", -2)
    assert row["current"] == "absorbed"
    assert row["candidate"] == "absorbed"
    assert row["audit_changed"] is False


def test_negative_control_candidate_adds_nothing() -> None:
    row = measure_one(4, 3, "none", 0)
    assert row["current"] == "sat_not_absorbed"
    assert row["candidate"] == "sat_not_absorbed"
    assert row["audit_changed"] is False


def test_within_group_tolerance_stays_invisible_even_for_candidate() -> None:
    row = measure_one(3, 3, "tier1", -3)
    assert row["band"] == "within_group_tolerance"
    assert row["current_slip"] is True
    assert row["candidate_slip"] is True
