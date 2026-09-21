"""用途(住宅・事務所など)から、スナップショットの項目名を引く。

`reading.py`(この軸を対象要素の数量レンジを出す弱い軸として
``AxisQualityFirewall`` に参加させていたモジュール)は削除した。統計が持つのは
個別工事の受注額(万円/件)の分布だけで、部材の数量には答えられず、粒度も
「1件の工事全体」であって対象要素ではないため、軸間照合の参加者として
成り立たないことが実データで確認された(`docs/design_v8.md` 5-2節、
`docs/proposal_industry_statistics_repositioning.md` 1節)。

**金額のレンジを ``SymbolCountReading`` の ``count_range``(個数の型)に
入れる経路は、意図的に存在しない。** それが「3〜246万円が建具4本を支持した」
という誤判定の入口だった。この統計を使うのは
`arbitration/total_amount_sanity_check.py` の出口検査だけである。
"""

from __future__ import annotations

from axes.industry_statistics_axis.fetch import metric_name
from axes.industry_statistics_axis.snapshot import StatisticsSnapshot

#: 建物の大分類。表4-1(住宅)・表4-2(非住宅建築物)に対応する。
CATEGORIES = ("住宅", "非住宅建築物")


def metric_name_for_use(category: str, use: str) -> str:
    """大分類と用途から、スナップショットの項目名を作る。

    例: ``metric_name_for_use("住宅", "住宅 計")`` →
    ``"個別工事の受注額:住宅:住宅 計"``
    """
    if category not in CATEGORIES:
        raise ValueError(f"大分類は {CATEGORIES} のいずれか。受け取った値: {category!r}")
    return metric_name(category, use)


def available_uses(snapshot: StatisticsSnapshot) -> tuple[str, ...]:
    """スナップショットが分布を持っている項目名の一覧(用途の選択肢)。"""
    return tuple(sorted(snapshot.metrics))
