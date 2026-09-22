"""v8 3-3節: 「読んだ事実」と「一般則で埋めた値」を区別し、後者を階層1に使わせない。

**なぜこの規則が要るのか。** トライアル15の実測で、数え方の決まりを受け渡しで
落とした読み取りは、**棄権しなかった。** もっともらしい一般則で穴を埋めて、
自信を持って違う値を出した(94.532 / 正解 100.332、21.00 / 正解 18.00)。
3段階確信度階層は `status="abstained"`(軸が黙ったこと)は拾えるが、
**「軸が自信を持って違う値を出したこと」は拾えない。** 軸の側が
「これは読んだ値か、埋めた値か」を申告する以外に、この失敗を捕まえる手が無い。
詳細は `docs/trial15_two_stage_reading_report.md` 3節・6節。

このテストが守る性質:
  1. `assumed` は確信度がいくら高くても階層1に入らない。
  2. `derived` は、根拠に1つでも `assumed` があれば `assumed` に落ちる。
  3. 由来は必ず申告させる(既定値で黙って通る経路を作らない)。
  4. 登録簿はきつくする方向にだけ効き、緩める方向には働かない。
負の対照(同じ証拠を `read` にすれば階層1に届くこと)を必ず対にして置く。
規則が効いているのか、そもそも階層1に届かない条件なのかを区別するため。
"""

from __future__ import annotations

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy
from arbitration.method_policies import clamp_to_defaults

TARGET = "wall_area"


def _evidence(
    value: int,
    *,
    source_id: str,
    axis_id: str = "image",
    method_id: str = "vision",
    derivation: str = "read",
    derivation_basis: tuple[str, ...] = (),
    model_confidence: float | None = None,
) -> AxisEvidence:
    return AxisEvidence(
        unit="count",
        target=TARGET,
        count_range=(value, value),
        source_id=source_id,
        axis_id=axis_id,
        method_id=method_id,
        derivation=derivation,
        derivation_basis=derivation_basis,
        model_confidence=model_confidence,
    )


# --- 1. assumed は階層1に入らない -------------------------------------------


def test_読んだ事実が2つ揃えば階層1に届く() -> None:
    """負の対照。この条件で階層1に届くからこそ、下のテストに意味がある。"""
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table"),
        ]
    )
    assert decision.tier == 1
    assert decision.action == "auto_confirm"


def test_一般則で埋めた値は2つ揃っても階層1に入らない() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision",
                      derivation="assumed"),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="assumed"),
        ]
    )
    assert decision.tier != 1
    assert decision.action != "auto_confirm"
    assert decision.independent_strong_source_count == 0


def test_確信度が最高でも一般則で埋めた値は昇格しない() -> None:
    """原則の「確信度の高さに関わらず」の部分。"""
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision",
                      derivation="assumed", model_confidence=1.0),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="assumed", model_confidence=1.0),
        ]
    )
    assert decision.tier != 1


def test_一致して間違えた2つの一般則を自動確定しない() -> None:
    """トライアル15で実際に起きた外れ方の回帰。

    方式Bの段階2は3回とも同じ 94.532(正解 100.332)を出した。
    値が揃っていることは、正しさの証拠にならない。
    """
    decision = AxisQualityFirewall().assess(
        [
            _evidence(94, source_id="reader-1", axis_id="text", method_id="table",
                      derivation="assumed", model_confidence=0.95),
            _evidence(94, source_id="reader-2", axis_id="image", method_id="vision",
                      derivation="assumed", model_confidence=0.95),
        ]
    )
    assert decision.action == "requires_review"


def test_一般則で埋めた値でも支持証拠にはなれる() -> None:
    """階層1から外すだけで、参考情報としては使う(意図した挙動)。"""
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="assumed"),
            _evidence(5, source_id="rule-A", axis_id="rules", method_id="ifc",
                      derivation="assumed"),
        ]
    )
    assert decision.tier == 2
    assert decision.action == "provisional_audit"


def test_人が読む理由文に一般則で埋めたことが出る() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="assumed"),
        ]
    )
    assert any("一般則" in reason for reason in decision.reasons)


# --- 2. derived は根拠まで遡る ----------------------------------------------


def test_読んだ値だけから計算した値は階層1に使える() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision",
                      derivation="derived", derivation_basis=("read", "read")),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="derived", derivation_basis=("read", "derived")),
        ]
    )
    assert decision.tier == 1


def test_根拠に一般則が1つでも混じれば計算結果も一般則扱いになる() -> None:
    """等式を1回通すだけで「読んだ事実」に化ける経路を塞ぐ。"""
    item = _evidence(5, source_id="drawing-A", derivation="derived",
                     derivation_basis=("read", "assumed"))
    assert item.effective_derivation == "assumed"
    assert not item.is_hard_eligible

    decision = AxisQualityFirewall().assess(
        [
            item,
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table",
                      derivation="derived", derivation_basis=("read", "assumed")),
        ]
    )
    assert decision.tier != 1


# --- 3. 由来は必ず申告させる ------------------------------------------------


def test_由来を書かない証拠は作れない() -> None:
    with pytest.raises(TypeError):
        AxisEvidence(  # type: ignore[call-arg]
            unit="count", target=TARGET, count_range=(5, 5),
            source_id="drawing-A", axis_id="image", method_id="vision",
        )


def test_知らない由来は拒否される() -> None:
    with pytest.raises(ValueError):
        _evidence(5, source_id="drawing-A", derivation="guessed")


def test_根拠を書かない計算値は拒否される() -> None:
    """根拠なしの derived を許すと、assumed を derived と名乗って通せてしまう。"""
    with pytest.raises(ValueError):
        _evidence(5, source_id="drawing-A", derivation="derived")


def test_計算値以外に根拠は書けない() -> None:
    with pytest.raises(ValueError):
        _evidence(5, source_id="drawing-A", derivation="read",
                  derivation_basis=("read",))


# --- 4. 入口での強制 ---------------------------------------------------------


def _request(overrides: dict) -> dict:
    evidence = {
        "target": TARGET,
        "count_range": [5, 5],
        "source_id": "drawing-A",
        "source_fingerprint": "sha256:drawing-A",
        "axis_id": "image",
        "method_id": "vision",
        "strength": "strong",
        "status": "confident",
        "calibrated": True,
        "unit": "count",
    }
    evidence.update(overrides)
    return {
        "trace_id": "trace-1",
        "element_id": "wall-1",
        "evidence": [evidence],
        "relations": [],
    }


def _orchestrator(policies: dict | None = None) -> InferenceOrchestrator:
    return InferenceOrchestrator(
        policies or {"vision": MethodPolicy(calibrated=True, max_strength="strong")},
        {"drawing-A": "sha256:drawing-A"},
    )


def _failures(result) -> set[str]:
    return {event.failure_type for event in result.events}


def _reason_codes(result) -> set[str]:
    return {code for event in result.events for code in event.reason_codes}


def test_入口は由来の申告が無い入力を受け付けない() -> None:
    result = _orchestrator().process(_request({}))
    assert "invalid_input" in _failures(result)
    assert any("invalid_derivation" in code for code in _reason_codes(result))


def test_入口は根拠の無い計算値を受け付けない() -> None:
    result = _orchestrator().process(_request({"derivation": "derived"}))
    assert any("derived_requires_basis" in code for code in _reason_codes(result))


def test_入口は読んだ値に根拠を書かせない() -> None:
    result = _orchestrator().process(
        _request({"derivation": "read", "derivation_basis": ["read"]})
    )
    assert any(
        "derivation_basis_not_allowed" in code for code in _reason_codes(result)
    )


def test_入口を通った由来がファイアウォールまで届く() -> None:
    result = _orchestrator().process(_request({"derivation": "assumed"}))
    assert result.decision is not None
    assert result.decision.independent_strong_source_count == 0


# --- 5. 登録簿はきつくする方向にだけ効く ------------------------------------


def test_登録簿が常に一般則と宣言した手法は呼び出し側の申告に勝つ() -> None:
    policies = {"vision": MethodPolicy(
        calibrated=True, max_strength="strong", always_assumed=True
    )}
    result = _orchestrator(policies).process(_request({"derivation": "read"}))
    assert result.decision is not None
    assert result.decision.independent_strong_source_count == 0
    assert any(
        "derivation_downgraded_by_policy" in note
        for note in result.normalization_notes
    )


def test_登録簿は一般則を読んだ事実に引き上げない() -> None:
    policies = {"vision": MethodPolicy(
        calibrated=True, max_strength="strong", always_assumed=False
    )}
    result = _orchestrator(policies).process(_request({"derivation": "assumed"}))
    assert result.decision is not None
    assert result.decision.independent_strong_source_count == 0


def test_上限の合成は常に厳しい側を採る() -> None:
    from arbitration.method_policies import DEFAULT_METHOD_POLICIES

    for method_id in DEFAULT_METHOD_POLICIES:
        loose = MethodPolicy(calibrated=True, max_strength="strong",
                             always_assumed=False)
        clamped = clamp_to_defaults(method_id, loose)
        ceiling = DEFAULT_METHOD_POLICIES[method_id]
        assert clamped.always_assumed == ceiling.always_assumed
        strict = MethodPolicy(calibrated=True, max_strength="strong",
                              always_assumed=True)
        assert clamp_to_defaults(method_id, strict).always_assumed is True
