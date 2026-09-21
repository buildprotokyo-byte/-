"""業界一般統計軸: スナップショットから、他の軸と同じ形式の読み取り結果を作る。

指示書ステップ1の要求どおり、出力は既存の軸(`axes/image_axis/
grounding_dino_adapter.py` の ``SymbolCountReading``)と互換の
「あり得る値の範囲 + 根拠 + 確信度ステータス」形式にしている。

恒久的に補助専用であることの保証(3重のガード)
--------------------------------------------------
`docs/design_v8.md` 5節の「過去実績軸は、サンプル数がどれだけ増えても
他の軸の答えを排除する権限を持たせない、恒久的に補助専用の軸とする」を、
業界一般統計軸にもそのまま適用する(指示書の大前提)。この軸から作る
``AxisEvidence``(`arbitration/axis_quality_firewall.py`)は、次の3つを
**呼び出し側から変更できない形で固定**している。

1. ``strength = "weak"``      … ハードな制約(積集合)の計算対象から外れる
2. ``status`` は "confident" にならない(データがあれば "low_confidence"、
   無ければ "abstained")… ``is_hard_eligible`` は ``status == "confident"``
   も要求するため、万一 (1) を将来誰かが誤って上書きしても、これがもう1つの
   防波堤になる
3. ``calibrated = False``     … 全国平均であって個別案件への実測校正では
   ないため。``is_hard_eligible`` の3つ目の条件もこれで塞がれる

(1)〜(3) のいずれか1つが破られても、残り2つが `is_hard_eligible` を
False に保つ設計にしている。テストは `tests/test_industry_statistics_axis.py`
を参照。
"""

from __future__ import annotations

import math

from arbitration.axis_quality_firewall import AxisEvidence
from axes.image_axis.grounding_dino_adapter import SymbolCountReading
from axes.industry_statistics_axis.snapshot import StatisticsSnapshot

#: `arbitration/inference_orchestrator.py` の ALLOWED_AXES に既に登録済みの軸名。
AXIS_ID = "statistical"

#: この軸の読み取りに、実測校正済みの強い軸と誤認されないための固定値。
_FORCED_STRENGTH = "weak"
_FORCED_CALIBRATED = False


def reading_for_metric(
    snapshot: StatisticsSnapshot,
    metric_name: str,
    *,
    quantity_scale: float = 1.0,
) -> SymbolCountReading:
    """指定した項目のスナップショット値を、``SymbolCountReading`` 互換の形式で返す。

    ``quantity_scale`` は、統計値(連続量。例: 床面積あたりの単価)を、
    この案件固有の整数個数レンジへ変換する係数。既定の1.0は「統計値を
    そのまま個数として扱う」場合用。

    項目がスナップショットに存在しない場合(``is_placeholder=True`` の
    既定スナップショットは常にこれに該当する)は、v8の「分布外の軸は
    レンジを主張せず棄権する」にならい ``status="abstained"`` を返す。
    **架空の数値を作って埋めることはしない。**

    データがある場合でも ``status`` は常に ``"low_confidence"`` までで、
    ``"confident"`` にはならない(モジュール冒頭のガード2を参照)。
    """
    evidence_base = {
        "source_name": snapshot.source_name,
        "source_url": snapshot.source_url,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "period_covered": snapshot.period_covered,
        "is_placeholder": snapshot.is_placeholder,
    }
    prompt = f"{snapshot.source_name}({snapshot.period_covered}時点)の『{metric_name}』"

    metric = snapshot.metrics.get(metric_name)
    if metric is None:
        return SymbolCountReading(
            category=metric_name,
            prompt=prompt,
            count_range=(0, 0),
            status="abstained",
            evidence={**evidence_base, "reason": "metric_not_available_in_snapshot"},
        )

    lower = math.floor(metric.low * quantity_scale)
    upper = math.ceil(metric.high * quantity_scale)
    return SymbolCountReading(
        category=metric_name,
        prompt=prompt,
        count_range=(lower, upper),
        status="low_confidence",
        evidence={
            **evidence_base,
            "unit": metric.unit,
            "typical": metric.typical,
            "sample_description": metric.sample_description,
        },
    )


def axis_evidence_for_metric(
    snapshot: StatisticsSnapshot,
    metric_name: str,
    *,
    target: str,
    source_id: str | None = None,
    method_id: str = "industry_statistics",
    quantity_scale: float = 1.0,
) -> AxisEvidence:
    """``AxisQualityFirewall.assess()`` にそのまま渡せる ``AxisEvidence`` を作る。

    ``strength`` / ``calibrated`` は呼び出し側の引数として公開していない
    (固定値を使う)。この軸を恒久的に補助専用にするための核となる保証。
    """
    reading = reading_for_metric(snapshot, metric_name, quantity_scale=quantity_scale)
    return AxisEvidence(
        target=target,
        count_range=reading.count_range,
        source_id=source_id or f"{snapshot.source_name}::{snapshot.fetched_at.isoformat()}",
        axis_id=AXIS_ID,
        method_id=method_id,
        strength=_FORCED_STRENGTH,
        status=reading.status,
        calibrated=_FORCED_CALIBRATED,
        evidence=dict(reading.evidence),
    )
