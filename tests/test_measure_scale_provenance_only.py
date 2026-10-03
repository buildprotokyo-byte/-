"""周4 の線1 を確かめる道具のテスト。**合成データだけ。**"""

from __future__ import annotations

from benchmarks.measure_scale_provenance_only import ADDED_KEYS, measure, without_added


def test_足した欄以外は1か所も変わらない() -> None:
    result = measure()
    assert result["前と後で変わった欄があった見本の数"] == 0


def test_足した欄はどの見本でも同じ定数() -> None:
    """**書き換えられる口を持たない。**見本によって中身が変わるなら口がある。"""
    assert measure()["足した欄がすべて同じ定数か"] is True


def test_足した欄だけを外す() -> None:
    before = without_added({"a": 1, "set_by": "機械"})
    assert before == {"a": 1}
    assert ADDED_KEYS == ("set_by",)


def test_機械の欄と人の欄は名前が別() -> None:
    """**混ぜない。**人が入れた値は entered_by、機械は set_by に入る。"""
    from axes.image_axis.pdf_dimensions import DimensionScale

    provenance = DimensionScale(
        denominator=50.0,
        agreeing_count=3,
        total_count=3,
        outlier_values_mm=(),
        source_text="3000",
    ).provenance()
    assert "set_by" in provenance
    assert "entered_by" not in provenance
