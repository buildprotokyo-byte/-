"""単位・粒度の取り違えを止める仕組みの回帰テスト。

`docs/top_priority_unit_safety_defect.md` の最優先課題に対応する。
2026-09-21 の点検で、**単位の違うレンジが数値として重なっただけで「支持」と
判定され、確信度階層が上がる経路**が実在することを実測した。このファイルは
その経路が塞がっていることを縛る。

実測した修正前の挙動::

    強い軸(画像軸)     : door_count = (4, 4)      ← 個数
    過去実績軸(弱い)    : door_count = (3, 5)      ← 個数
    業界一般統計軸(弱い) : 住宅 計   = (3, 246)    ← 万円/件
    → tier = 2 / provisional_audit(自動採用+抜き取り監査)
"""

from __future__ import annotations

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from axes.image_axis.grounding_dino_adapter import SymbolCountReading

TARGET = "door_count"


def _evidence(
    count_range: tuple[int, int],
    *,
    unit: str = "count",
    granularity: str = "element",
    source: str = "drawing-A",
    axis: str = "image",
    strength: str = "strong",
    calibrated: bool = True,
) -> AxisEvidence:
    return AxisEvidence(
        target=TARGET,
        count_range=count_range,
        source_id=source,
        axis_id=axis,
        method_id=f"method_{source}",
        unit=unit,
        granularity=granularity,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        calibrated=calibrated,
    )


# --- 単位は必須で、黙って省略できない ---------------------------------------


def test_unit_is_required() -> None:
    """``unit`` に既定値を与えていないこと。

    既定値があると、単位を意識せずに書かれた呼び出しが黙って通り、
    検査の迂回経路が残る。
    """
    with pytest.raises(TypeError):
        AxisEvidence(  # type: ignore[call-arg]
            target=TARGET, count_range=(4, 4), source_id="s",
            axis_id="image", method_id="m",
        )


def test_an_empty_unit_is_rejected() -> None:
    with pytest.raises(ValueError):
        _evidence((4, 4), unit="")


def test_an_unknown_granularity_is_rejected() -> None:
    with pytest.raises(ValueError):
        _evidence((4, 4), granularity="建物")


# --- ファイアウォールが単位・粒度の不一致を止める ---------------------------


def test_the_measured_tier2_promotion_no_longer_happens() -> None:
    """実測した「金額のレンジが個数を支持して階層2へ」が再現しないこと。

    このテストが、最優先課題そのものの回帰テストである。
    """
    decision = AxisQualityFirewall().assess([
        _evidence((4, 4)),  # 画像軸: 個数
        _evidence((3, 5), unit="count", source="自社実績DB",
                  axis="history", strength="weak"),
        # 業界統計: 万円/件 のレンジを個数のターゲットに載せようとした場合
        _evidence((3, 246), unit="yen_man", source="mlit-survey",
                  axis="history", strength="weak", calibrated=False),
    ])

    assert decision.tier == 3, "単位が違うのに階層が上がった"
    assert decision.action == "requires_review"
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "unit_mismatch"


def test_mixed_units_escalate_rather_than_being_dropped_silently() -> None:
    """単位が混在したら、黙って無視せずエスカレーションすること。

    取り違えは人が直すべき入力の誤りなので、静かに落としてはならない。
    """
    decision = AxisQualityFirewall().assess([
        _evidence((4, 4), unit="count"),
        _evidence((4, 4), unit="m", source="spec-A", axis="text"),
    ])

    assert decision.tier == 3
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "unit_mismatch"
    assert "単位" in decision.escalation.reason
    assert decision.confirmed_range is None


def test_mixed_granularity_escalates() -> None:
    """粒度(対象要素1つ / 工事1件)の混在も止めること。"""
    decision = AxisQualityFirewall().assess([
        _evidence((4, 4), granularity="element"),
        _evidence((4, 4), granularity="job", source="spec-A", axis="text"),
    ])

    assert decision.tier == 3
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "unit_mismatch"
    assert "粒度" in decision.escalation.reason


def test_matching_units_still_reach_tier1() -> None:
    """単位が揃っていれば、従来どおり階層1に到達すること。

    単位検査が正当な自動確定まで止めていないことの確認。
    """
    decision = AxisQualityFirewall().assess([
        _evidence((4, 4), source="drawing-A", axis="image"),
        _evidence((4, 4), source="spec-A", axis="text"),
    ])

    assert decision.tier == 1
    assert decision.action == "auto_confirm"
    assert decision.escalation is None


def test_non_count_units_are_accepted_when_consistent() -> None:
    """単位は "count" に限定されない(長さ・面積も揃っていれば通る)。"""
    for unit in ("m", "m2", "yen"):
        decision = AxisQualityFirewall().assess([
            _evidence((10, 12), unit=unit, source="drawing-A", axis="image"),
            _evidence((10, 12), unit=unit, source="spec-A", axis="text"),
        ])
        assert decision.tier == 1, f"unit={unit} で階層1に到達しなかった"


# --- 整合性ソルバーが「整合」と誤報告しない --------------------------------


def test_the_solver_does_not_claim_agreement_across_units() -> None:
    """単位の違う参考情報を「整合」と報告しないこと。

    修正前は「(3, 246) で、強い軸の解 (4, 4) と整合」と人に報告していた。
    """
    solver = ConsistencySolver()
    solver.add_variable(TARGET, 4, 4, axis="image", unit="count")
    solver.add_advisory_reading(
        TARGET,
        SymbolCountReading(
            category="住宅 計", prompt="受注額", count_range=(3, 246),
            status="low_confidence", evidence={},
        ),
        axis="history",
        unit="yen_man",
    )

    notes = solver.solve().advisories
    assert len(notes) == 1
    assert notes[0].agrees is None, "単位が違うのに整合と判定された"
    assert "単位が違うため比較不可" in notes[0].message


def test_the_solver_still_compares_when_units_match() -> None:
    """単位が揃っていれば、従来どおり整合判定を行うこと。"""
    solver = ConsistencySolver()
    solver.add_variable(TARGET, 4, 4, axis="image", unit="count")
    solver.add_advisory_reading(
        TARGET,
        SymbolCountReading(
            category="door", prompt="戸の数", count_range=(3, 5),
            status="low_confidence", evidence={},
        ),
        axis="history",
        unit="count",
    )

    notes = solver.solve().advisories
    assert notes[0].agrees is True
    assert "整合" in notes[0].message


def test_the_orchestrator_puts_the_validated_unit_on_the_evidence() -> None:
    """実運用の入口が、検証した単位を ``AxisEvidence.unit`` に載せること。

    以前は evidence 辞書に入るだけで、照合する側は読んでいなかった。
    """
    from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy

    orchestrator = InferenceOrchestrator(
        {"det": MethodPolicy(calibrated=True, max_strength="strong")},
        {"drawing-A": "sha256:drawing-A"},
    )
    result = orchestrator.process({
        "trace_id": "unit-1",
        "element_id": "door-1",
        "evidence": [{
            "target": TARGET, "count_range": [4, 4], "unit": "個",
            "source_id": "drawing-A", "source_fingerprint": "sha256:drawing-A",
            "axis_id": "image", "method_id": "det",
            "strength": "strong", "status": "confident", "calibrated": True,
        }],
        "relations": [],
    })

    assert not result.is_invalid
    assert "unit_normalized:個->count" in result.normalization_notes
