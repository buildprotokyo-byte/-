"""Grounding DINO アダプタの、しきい値 → 確信度ステータス変換の回帰テスト。

実モデルは使いません(重みの取得に huggingface.co が必要なため)。ここで固定するのは
`box_threshold` / `text_threshold` をどう v8 の confident / low_confidence /
abstained に対応づけるか、という**設計の側**です。
"""

from __future__ import annotations

import numpy as np
import pytest

from axes.image_axis.grounding_dino_adapter import (
    DEFAULT_BOX_THRESHOLD,
    DEFAULT_CONFIDENT_MARGIN,
    DEFAULT_TEXT_THRESHOLD,
    CategoryConfig,
    GroundingDinoAdapter,
    RawDetection,
    StaticBackend,
)

IMAGE = np.zeros((32, 32), dtype=np.uint8)
DOOR_PROMPT = "a door symbol in a floor plan"


def _detection(box_score: float, text_score: float, i: int = 0) -> RawDetection:
    return RawDetection((i * 10.0, 0.0, i * 10.0 + 8.0, 8.0), box_score, text_score)


def _adapter(detections: list[RawDetection], **cfg) -> GroundingDinoAdapter:
    backend = StaticBackend({DOOR_PROMPT: detections})
    return GroundingDinoAdapter(
        backend, categories=[CategoryConfig("door", DOOR_PROMPT, **cfg)]
    )


# --- 個別の検出の振り分け ---------------------------------------------------


@pytest.mark.parametrize(
    ("box_score", "text_score", "expected"),
    [
        # 受理しきい値 + 余裕幅(0.45 / 0.40)を両方超える → confident
        (0.90, 0.90, "confident"),
        (0.45, 0.40, "confident"),
        # 受理はされるが余裕幅に届かない → low_confidence
        (0.44, 0.40, "low_confidence"),
        (0.45, 0.39, "low_confidence"),
        (0.30, 0.25, "low_confidence"),
        # どちらかが受理しきい値未満 → rejected
        (0.29, 0.90, "rejected"),
        (0.90, 0.24, "rejected"),
        (0.00, 0.00, "rejected"),
    ],
)
def test_classify_detection(box_score: float, text_score: float, expected: str) -> None:
    config = CategoryConfig("door", DOOR_PROMPT)
    assert config.box_threshold == DEFAULT_BOX_THRESHOLD
    assert config.text_threshold == DEFAULT_TEXT_THRESHOLD
    assert config.confident_margin == DEFAULT_CONFIDENT_MARGIN
    verdict = GroundingDinoAdapter.classify_detection(
        _detection(box_score, text_score), config
    )
    assert verdict == expected


# --- 軸全体のステータスとレンジ ---------------------------------------------


def test_all_confident_gives_confident_status_and_point_range() -> None:
    reading = _adapter([_detection(0.9, 0.9, i) for i in range(4)]).detect_category(
        IMAGE, "door"
    )
    assert reading.status == "confident"
    assert reading.count_range == (4, 4)


def test_low_confidence_widens_the_upper_bound_only() -> None:
    """low_confidence の検出はレンジの上限だけを押し上げる(下限は動かさない)。"""
    reading = _adapter(
        [_detection(0.9, 0.9, 0), _detection(0.9, 0.9, 1), _detection(0.32, 0.27, 2)]
    ).detect_category(IMAGE, "door")
    assert reading.count_range == (2, 3)
    assert reading.status == "confident"


def test_more_low_than_confident_downgrades_the_axis() -> None:
    """候補が割れている状態を confident と呼ばない。"""
    reading = _adapter(
        [_detection(0.9, 0.9, 0)] + [_detection(0.32, 0.27, i) for i in range(1, 4)]
    ).detect_category(IMAGE, "door")
    assert reading.count_range == (1, 4)
    assert reading.status == "low_confidence"


def test_only_low_confidence_gives_low_confidence() -> None:
    reading = _adapter([_detection(0.32, 0.27, i) for i in range(3)]).detect_category(
        IMAGE, "door"
    )
    assert reading.count_range == (0, 3)
    assert reading.status == "low_confidence"


def test_nothing_accepted_abstains_instead_of_claiming_zero() -> None:
    """v8「分布外の軸はレンジを主張せず棄権する」。0 個と断定してはいけない。"""
    reading = _adapter([_detection(0.10, 0.05, i) for i in range(5)]).detect_category(
        IMAGE, "door"
    )
    assert reading.status == "abstained"
    assert reading.evidence["rejected_count"] == 5


def test_no_detection_at_all_abstains() -> None:
    reading = _adapter([]).detect_category(IMAGE, "door")
    assert reading.status == "abstained"
    assert reading.evidence["raw_detection_count"] == 0


# --- レンジは必ず正解を含みうる形になっているか -----------------------------


def test_range_is_never_inverted() -> None:
    reading = _adapter(
        [_detection(0.9, 0.9, 0), _detection(0.31, 0.26, 1), _detection(0.1, 0.1, 2)]
    ).detect_category(IMAGE, "door")
    low, high = reading.count_range
    assert low <= high
    assert high == len(reading.accepted_detections)
    assert low == len(reading.confident_detections)


# --- しきい値の上書きとチューニング -----------------------------------------


def test_threshold_override_changes_the_verdict() -> None:
    """しきい値を上げると、同じ検出が confident から外れる。"""
    adapter = _adapter([_detection(0.5, 0.5, 0)])
    assert adapter.detect_category(IMAGE, "door").status == "confident"

    strict = adapter.detect_category(IMAGE, "door", box_threshold=0.45, text_threshold=0.45)
    assert strict.status == "low_confidence"
    assert strict.count_range == (0, 1)

    too_strict = adapter.detect_category(IMAGE, "door", box_threshold=0.6)
    assert too_strict.status == "abstained"


def test_override_does_not_mutate_the_registered_category() -> None:
    adapter = _adapter([_detection(0.5, 0.5, 0)])
    adapter.detect_category(IMAGE, "door", box_threshold=0.9)
    assert adapter.categories["door"].box_threshold == DEFAULT_BOX_THRESHOLD


def test_zero_margin_makes_every_accepted_detection_confident() -> None:
    reading = _adapter(
        [_detection(0.30, 0.25, 0), _detection(0.9, 0.9, 1)], confident_margin=0.0
    ).detect_category(IMAGE, "door")
    assert reading.count_range == (2, 2)
    assert reading.status == "confident"


# --- 入力の検証 -------------------------------------------------------------


def test_invalid_threshold_is_rejected() -> None:
    with pytest.raises(ValueError):
        CategoryConfig("door", DOOR_PROMPT, box_threshold=1.5)
    with pytest.raises(ValueError):
        CategoryConfig("door", DOOR_PROMPT, text_threshold=-0.1)
    with pytest.raises(ValueError):
        CategoryConfig("door", DOOR_PROMPT, confident_margin=-0.1)


def test_unknown_category_raises() -> None:
    with pytest.raises(KeyError):
        _adapter([]).detect_category(IMAGE, "outlet")


# --- バックエンドの配線 -----------------------------------------------------


def test_backend_receives_the_category_prompt() -> None:
    backend = StaticBackend()
    adapter = GroundingDinoAdapter(
        backend,
        categories=[
            CategoryConfig("door", DOOR_PROMPT),
            CategoryConfig("window", "a window symbol in a floor plan"),
        ],
    )
    adapter.detect_all(IMAGE)
    prompts = [prompt for _shape, prompt in backend.calls]
    assert prompts == [DOOR_PROMPT, "a window symbol in a floor plan"]


def test_huggingface_backend_reports_instead_of_silently_returning_nothing() -> None:
    """重みが取れない環境で 0 件を返して「検出できなかった」ように見せないこと。"""
    from axes.image_axis.grounding_dino_adapter import HuggingFaceGroundingDinoBackend

    backend = HuggingFaceGroundingDinoBackend(model_id="does-not-exist/none-at-all")
    with pytest.raises(RuntimeError):
        backend._ensure_loaded()


def test_grounding_dino_confidence_is_not_calibrated_for_symbol_detection() -> None:
    """docs/design_v8.md 11章の決定を固定するトリップワイヤー。

    docs/stage_a_report.md 9章・docs/sahi_tiling_report.md の実測で、図面記号
    検出における確信度スコアと正しさの逆相関が確認されている(全体画像・SAHI方式の
    タイル分割いずれでも全16条件で再現)。この値をTrueに変える場合は、11-3の
    スコア方向検査を再実施し、根拠をdocs/design_v8.md 11章に追記した上で、
    このテストを更新すること。
    """
    from axes.image_axis.grounding_dino_adapter import (
        GROUNDING_DINO_CONFIDENCE_IS_CALIBRATED_FOR_SYMBOL_DETECTION,
    )

    assert GROUNDING_DINO_CONFIDENCE_IS_CALIBRATED_FOR_SYMBOL_DETECTION is False
