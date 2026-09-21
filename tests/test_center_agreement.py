"""中心値の一致を階層1・階層2の確定条件に加えたことの回帰テスト。

v8 10章19項の「重なる誤り」対応(2026-09-21、おーちゃんの判断で実装)。
**守りたいのは精度そのものではなく「レンジが重なっているだけで確定しない」
という性質である。** 重なる誤りは積集合が空にならないので矛盾として検出
できず、確定するとクラスタの等式で誤りが伝播する。実測では30試行のうち
25試行で、階層を入れたほうが階層なしより精度が低くなっていた
(`docs/simulation_tier1_center_agreement.md`)。

検査が「あるのに働いていない」状態を避けるため、**発火することと発火しない
ことの両方**を固定する。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from arbitration.axis_quality_firewall import (
    CENTER_TOLERANCES,
    AxisEvidence,
    AxisQualityFirewall,
    CenterTolerance,
)


def _strong(target: str, low: int, high: int, *, source: str, axis: str,
            unit: str = "count") -> AxisEvidence:
    return AxisEvidence(
        target=target, count_range=(low, high), source_id=source, axis_id=axis,
        method_id=f"method_{source}", unit=unit, calibrated=True,
    )


def _weak(target: str, low: int, high: int, *, source: str, axis: str,
          unit: str = "count") -> AxisEvidence:
    return AxisEvidence(
        target=target, count_range=(low, high), source_id=source, axis_id=axis,
        method_id=f"method_{source}", unit=unit, strength="weak", calibrated=True,
    )


# ---------------------------------------------------------------------
# 階層1(独立した強いデータ源が2つ以上)
# ---------------------------------------------------------------------


def test_agreeing_strong_sources_still_auto_confirm() -> None:
    """**退行の確認。** 中心値が揃っていれば、今までどおり階層1で確定する。"""
    decision = AxisQualityFirewall().assess([
        _strong("door", 4, 6, source="drawing-A", axis="image"),
        _strong("door", 4, 6, source="spec-A", axis="text"),
    ])
    assert decision.tier == 1
    assert decision.action == "auto_confirm"
    assert decision.confirmed_range == (4, 6)


def test_centers_one_apart_are_still_within_tolerance() -> None:
    """許容差 ±1 の**内側**は確定する。±0 にすると正当な一致を取りこぼす。

    実測でも ±0 では精度が 93.5% → 93.2% に下がった(報告書2-4節)。
    """
    decision = AxisQualityFirewall().assess([
        _strong("door", 4, 6, source="drawing-A", axis="image"),   # 中心 5
        _strong("door", 5, 7, source="spec-A", axis="text"),       # 中心 6
    ])
    assert decision.action == "auto_confirm"


def test_overlapping_but_off_centre_strong_sources_escalate() -> None:
    """**本題。** レンジは重なるが中心が離れている場合は階層1にしない。

    (3, 4) と (4, 6) は 4 で重なるので積集合は空にならず、従来は
    「独立した強い軸2つが一致した」として階層1で確定していた。
    """
    decision = AxisQualityFirewall().assess([
        _strong("door", 3, 4, source="drawing-A", axis="image"),   # 中心 3.5
        _strong("door", 4, 6, source="spec-A", axis="text"),       # 中心 5
    ])
    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None, (
        "階層3なのに確定範囲が残ると、呼び出し側が確定済みと読み違える"
    )
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "center_disagreement"


def test_the_real_floor_area_case_no_longer_auto_confirms() -> None:
    """実案件で見つかった床面積のケース(P011)。

    文章軸 99.5〜100.5 ㎡ と VTracer 100.0〜162.5 ㎡。レンジが重なるので
    **従来は階層1で自動確定し、人が一度も見なかった。** 片方のデータ源が
    62%上振れしていてもである。
    """
    decision = AxisQualityFirewall().assess([
        _strong("floor_area", 995_000, 1_005_000, source="spec-A",
                axis="text", unit="cm2"),
        _strong("floor_area", 1_000_000, 1_625_000, source="drawing-A",
                axis="image", unit="cm2"),
    ])
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None


# ---------------------------------------------------------------------
# 階層2(強い軸1つ + それを支持する弱い軸2つ以上)
# ---------------------------------------------------------------------


def test_tier2_with_agreeing_sources_is_unchanged() -> None:
    """**退行の確認。** 中心値が揃っていれば階層2のままにする。"""
    decision = AxisQualityFirewall().assess([
        _strong("door", 4, 6, source="drawing-A", axis="image"),
        _weak("door", 4, 6, source="自社実績DB", axis="history"),
        _weak("door", 4, 6, source="rule-A", axis="rules"),
    ])
    assert decision.tier == 2
    assert decision.action == "provisional_audit"
    assert decision.confirmed_range is not None


def test_tier2_escalates_when_the_strong_axis_is_off_centre() -> None:
    """**階層1だけに検査を入れても効かなかった理由がここにある。**

    劣化が実測された条件(強い軸1つ+弱い軸2つ)には階層1の要素が1件も無い。
    強い軸だけが誤った重なるレンジを出し、弱い軸2つは正解側を指している
    形で、従来は**強い軸のレンジがそのまま採用範囲**になっていた。
    """
    decision = AxisQualityFirewall().assess([
        _strong("bath", 3, 4, source="drawing-A", axis="image"),    # 中心 3.5
        _weak("bath", 4, 6, source="自社実績DB", axis="history"),    # 中心 5
        _weak("bath", 4, 6, source="rule-A", axis="rules"),         # 中心 5
    ])
    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None


def test_tier2_check_looks_at_weak_axes_too() -> None:
    """階層2の検査が**強い軸同士だけ**を見ていたら素通りすることの固定。

    強い軸は1つしか無いので、強い軸同士の比較では要素数が1になり、
    「比べる相手がいない」として必ず一致と判定されてしまう。
    """
    firewall = AxisQualityFirewall()
    evidences = [
        _strong("bath", 3, 4, source="drawing-A", axis="image"),
        _weak("bath", 4, 6, source="自社実績DB", axis="history"),
        _weak("bath", 4, 6, source="rule-A", axis="rules"),
    ]
    strong_only = [e.count_range for e in evidences if e.strength == "strong"]
    assert firewall._centers_disagree(strong_only, "count") == (False, None), (
        "前提: 強い軸だけを見ると1件しか無いので離れようがない"
    )
    all_sources = firewall._union_ranges_by_source(evidences)
    disagree, note = firewall._centers_disagree(all_sources, "count")
    assert disagree and note is not None


# ---------------------------------------------------------------------
# 単位ごとの許容差(判断事項④)
# ---------------------------------------------------------------------


def test_counts_use_the_decided_absolute_tolerance() -> None:
    """個数の ±1 は暫定ではなく決まった値なので、変えたら落とす。"""
    assert CENTER_TOLERANCES["count"] == CenterTolerance(absolute=1)


def test_continuous_units_use_a_relative_tolerance() -> None:
    """長さ・面積・金額に絶対値の許容差を当てない(±1mm では意味がない)。"""
    for unit in ("mm", "cm2", "yen"):
        tolerance = CENTER_TOLERANCES[unit]
        assert tolerance.absolute is None, f"{unit} に絶対値の許容差が入っている"
        assert tolerance.relative is not None


def test_an_unknown_unit_is_still_checked() -> None:
    """表に無い単位でも検査を**飛ばさない**。

    飛ばすと「検査はあるのに未知の単位では働かない」状態になり、
    集計表を見ても気づけない(v8 8章の穴2)。
    """
    firewall = AxisQualityFirewall()
    disagree, note = firewall._centers_disagree([(10, 10), (20, 20)], "未知の単位")
    assert disagree
    assert note is not None and "許容差の表に無い" in note, (
        "保険を使ったことが判定理由に残らないと、黙って緩い基準が当たる"
    )


def test_the_tolerance_table_can_be_replaced() -> None:
    """運用側が種類ごとの許容差を決められる入口があること(判断事項④)。"""
    loose = AxisQualityFirewall(
        center_tolerances={"count": CenterTolerance(absolute=5)}
    )
    decision = loose.assess([
        _strong("door", 3, 4, source="drawing-A", axis="image"),
        _strong("door", 4, 6, source="spec-A", axis="text"),
    ])
    assert decision.action == "auto_confirm", "許容差を広げれば確定する"


def test_a_tolerance_must_specify_exactly_one_kind() -> None:
    """絶対値と相対値の両方(または両方なし)は、設定ミスとして弾く。"""
    with pytest.raises(ValueError):
        CenterTolerance()
    with pytest.raises(ValueError):
        CenterTolerance(absolute=1, relative=Fraction(1, 20))


def test_the_comparison_does_not_drift_with_float_error() -> None:
    """境界の判定が入力によって揺れないこと。

    中心値は 0.5 きざみになるので、float で持つと比較が入力に依存して
    揺れる。2倍した整数と Fraction で比べているので、境界は1単位ちょうどで
    切り替わる。

    相対許容差は**中心値の平均**に対する割合である。中心 a, b (a<b) で
    ``b - a > 0.05 * (a + b) / 2`` なので、a = 1,000,000 のときの境界は
    ``0.975 b > 1,025,000`` すなわち b > 1,051,282.05… になる。
    """
    firewall = AxisQualityFirewall()
    assert firewall._centers_disagree(
        [(1_000_000, 1_000_000), (1_051_282, 1_051_282)], "cm2"
    ) == (False, None), "境界の内側は「離れている」にしない"
    disagree, _ = firewall._centers_disagree(
        [(1_000_000, 1_000_000), (1_051_283, 1_051_283)], "cm2"
    )
    assert disagree, "境界を1単位でも超えたら弾く"
