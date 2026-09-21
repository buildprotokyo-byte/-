"""業界一般統計軸: 取得日を明記したスナップショットの保存・読み込み。

`6軸強化オープンリソースカタログ.md`(軸5)で確認した「国土交通省 建築物
リフォーム・リニューアル調査」は、e-Statで四半期ごとに更新される。この軸は
**取得のたびに最新を見に行くのではなく**、「いつ時点のデータを見たか」を
記録した上でローカルに保存し、更新は別途明示的なタイミングで行う設計にする
(指示書ステップ1)。

2026年9月21日に e-stat.go.jp への通信が許可され、実データを取得済み。
同梱している既定のスナップショット(``snapshots/2026-09-21.json``)は
``is_placeholder=False`` で、令和7年度計(2026-06-12公開)の実データから
作った15項目を持つ。取得・再生成は ``axes/industry_statistics_axis/fetch.py``
(``python -m axes.industry_statistics_axis.fetch``)で行う。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


@dataclass(frozen=True)
class IndustryMetric:
    """業界統計の項目1つ分(あり得る値の範囲)。"""

    name: str
    unit: str
    low: float
    high: float
    typical: float | None = None
    sample_description: str = ""

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"'{self.name}': low({self.low}) > high({self.high})")


@dataclass(frozen=True)
class StatisticsSnapshot:
    """ある取得日時点での、業界統計データのスナップショット。"""

    source_name: str
    source_url: str
    fetched_at: date
    period_covered: str
    metrics: dict[str, IndustryMetric] = field(default_factory=dict)
    is_placeholder: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["fetched_at"] = self.fetched_at.isoformat()
        return payload

    @staticmethod
    def from_dict(payload: dict) -> "StatisticsSnapshot":
        metrics = {
            name: IndustryMetric(**metric) for name, metric in payload["metrics"].items()
        }
        return StatisticsSnapshot(
            source_name=payload["source_name"],
            source_url=payload["source_url"],
            fetched_at=date.fromisoformat(payload["fetched_at"]),
            period_covered=payload["period_covered"],
            metrics=metrics,
            is_placeholder=payload.get("is_placeholder", False),
            notes=payload.get("notes", ""),
        )


def save_snapshot(snapshot: StatisticsSnapshot, directory: Path = DEFAULT_SNAPSHOT_DIR) -> Path:
    """スナップショットをJSONとして保存する。ファイル名は取得日を明記する。

    同じ日に複数回保存すると上書きになる(1日1スナップショットの運用を想定)。
    """
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "_placeholder" if snapshot.is_placeholder else ""
    path = directory / f"{snapshot.fetched_at.isoformat()}{suffix}.json"
    path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_snapshot(path: Path) -> StatisticsSnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return StatisticsSnapshot.from_dict(payload)


def load_latest_snapshot(directory: Path = DEFAULT_SNAPSHOT_DIR) -> StatisticsSnapshot | None:
    """``directory`` にあるスナップショットのうち、取得日が最も新しいものを返す。

    1つも無ければ ``None``。ファイル名ではなく ``fetched_at`` の値そのもので
    比較する(ファイル名の命名規則に依存しないため)。

    同じ取得日のものが複数あるときは、``is_placeholder=False`` の方を優先する。
    実データを入れた当日にプレースホルダーが残っていても、中身の無い方を
    掴まないようにするため。
    """
    snapshots = [load_snapshot(path) for path in sorted(directory.glob("*.json"))]
    if not snapshots:
        return None
    return max(snapshots, key=lambda s: (s.fetched_at, not s.is_placeholder))
