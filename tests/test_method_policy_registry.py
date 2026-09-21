"""手法IDの分割と、方針登録簿の回帰テスト(v8 12-2節・12-4節)。

トライアル12で決まったのは「VTracer は壁の総延長では条件付き採用、床面積では
単独のハードな確定に使わない」という**数量の種類ごとの区別**である。
`MethodPolicy` は `method_id` だけをキーにするので、この区別は
**IDを分けることでしか表現できない。**

ここで縛るのは3つ。

1. IDが実際に分かれていること(1つに戻したら落ちる)
2. 登録簿が、壁の総延長を strong、床面積を weak / 非校正として持っていること
3. **登録簿が上限として効くこと。** 呼び出し側が床面積を strong / calibrated で
   渡しても引き下がり、実運用の入口を通しても階層1にならない

3が肝心で、「設計書にそう書いてある」だけでは事故は止まらない。
"""

from __future__ import annotations

from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy
from arbitration.method_policies import (
    DEFAULT_METHOD_POLICIES,
    clamp_to_defaults,
    with_defaults,
)
from axes.image_axis.vtracer_vectorizer import (
    METHOD_FLOOR_AREA,
    METHOD_IDS,
    METHOD_WALL_LINEWORK,
)


def test_vtracer_method_ids_are_split_by_quantity_kind() -> None:
    """性質1: 壁の総延長と床面積が別のIDであること。"""
    assert METHOD_WALL_LINEWORK != METHOD_FLOOR_AREA
    assert set(METHOD_IDS) == {METHOD_WALL_LINEWORK, METHOD_FLOOR_AREA}


def test_registry_holds_the_trial12_decision() -> None:
    """性質2: 登録簿が12-2節の決定をそのまま持っていること。"""
    wall = DEFAULT_METHOD_POLICIES[METHOD_WALL_LINEWORK]
    assert wall.calibrated is True
    assert wall.max_strength == "strong"

    floor = DEFAULT_METHOD_POLICIES[METHOD_FLOOR_AREA]
    assert floor.calibrated is False
    assert floor.max_strength == "weak"


def test_clamp_never_raises_a_method_above_the_registry() -> None:
    """性質3: 上限として引き下げるだけで、引き上げはしない。"""
    # 床面積を強い軸で渡しても引き下がる。
    clamped = clamp_to_defaults(METHOD_FLOOR_AREA, MethodPolicy(True, "strong"))
    assert clamped.calibrated is False
    assert clamped.max_strength == "weak"

    # 壁の総延長を弱く渡した場合、登録簿が強くし返すことはない。
    clamped = clamp_to_defaults(METHOD_WALL_LINEWORK, MethodPolicy(False, "weak"))
    assert clamped.calibrated is False
    assert clamped.max_strength == "weak"

    # 登録簿に無い手法はそのまま通す(未登録手法は入口側で weak に落ちる)。
    passthrough = clamp_to_defaults("not_registered", MethodPolicy(True, "strong"))
    assert passthrough == MethodPolicy(True, "strong")


def test_with_defaults_keeps_the_registry_entries() -> None:
    merged = with_defaults({"my_detector": MethodPolicy(True, "strong")})
    assert merged["my_detector"] == MethodPolicy(True, "strong")
    assert merged[METHOD_FLOOR_AREA] == DEFAULT_METHOD_POLICIES[METHOD_FLOOR_AREA]


# =====================================================================
# 実運用の入口を通した確認
# =====================================================================

_SOURCES = {"drawing-A": "sha256:drawing-A", "spec-A": "sha256:spec-A"}


def _area_evidence(source: str, axis: str, method: str, rng: list[int]) -> dict:
    """床面積 cm² の証拠。呼び出し側は強い軸のつもりで渡している。"""
    return {
        "target": "floor_area", "count_range": rng, "unit": "cm2",
        "source_id": source, "source_fingerprint": f"sha256:{source}",
        "axis_id": axis, "method_id": method,
        "strength": "strong", "status": "confident", "calibrated": True,
    }


def _process(method: str) -> object:
    orchestrator = InferenceOrchestrator(
        {
            method: MethodPolicy(calibrated=True, max_strength="strong"),
            "spec_area": MethodPolicy(calibrated=True, max_strength="strong"),
        },
        _SOURCES,
    )
    return orchestrator.process({
        "trace_id": f"trial12-{method}", "element_id": "room-1",
        "evidence": [
            _area_evidence("drawing-A", "image", method, [1_000_000, 1_000_000]),
            _area_evidence("spec-A", "text", "spec_area", [1_000_000, 1_000_000]),
        ],
        "relations": [],
    })


def test_vtracer_floor_area_cannot_reach_tier1_through_the_real_entry_point() -> None:
    """床面積を強い軸として渡しても、階層1(自動確定)には届かない。

    これが12-2節(2)の「単独でハードな確定に使わない」の実体。呼び出し側が
    ``calibrated=True, strength="strong"`` で渡していても、登録簿の上限で
    引き下げられるため、独立した強いデータ源が2つ揃わない。
    """
    result = _process(METHOD_FLOOR_AREA)

    assert not result.is_invalid
    assert result.decision is not None
    assert result.decision.tier != 1
    assert result.decision.independent_strong_source_count == 1
    assert any("untrusted_calibration_ignored" in note for note in result.normalization_notes)
    assert any("strength_downgraded_by_policy" in note for note in result.normalization_notes)


def test_wall_linework_is_still_allowed_to_be_a_strong_axis() -> None:
    """壁の総延長は段階Aどおり条件付き採用のまま。引き下げは起きない。

    同じ入力の method_id を差し替えただけで階層1に届くことで、
    「引き下げているのは床面積だけ」だと分かる。
    """
    result = _process(METHOD_WALL_LINEWORK)

    assert not result.is_invalid
    assert result.decision is not None
    assert result.decision.tier == 1
    assert result.decision.independent_strong_source_count == 2
    assert not any("downgraded" in note for note in result.normalization_notes)
