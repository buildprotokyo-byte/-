"""業界一般統計軸: e-Statから実データを取得し、スナップショットを作る。

対象は国土交通省「建築物リフォーム・リニューアル調査」の**年度次(年度計)**
の統計表。e-Statのファイルダウンロード(``stat-search/file-download``)は
アプリケーションIDを必要としないため、API登録なしで取得できる。

この軸が使うのは、次の2表に入っている「個別工事の受注額の階級別 受注件数」
である。

* 表4-1 個別工事の受注額、用途別 受注件数＜住宅＞
* 表4-2 個別工事の受注額、用途別 受注件数＜非住宅建築物＞

**この2表を選んだ理由**: この調査の大半の表は「受注高の合計」や「平均受注額」
といった代表値しか載っておらず、代表値からは「あり得る値の範囲」を正直に
作れない(平均値を low=high として置くと、実態より極端に狭いレンジを主張する
ことになる)。表4-1・表4-2 だけが**分布そのもの**(金額階級ごとの件数)を
持っているため、パーセンタイルとして範囲を出せる。

出力する ``IndustryMetric`` は、用途ごとに

* ``low``     = 第5パーセンタイル
* ``typical`` = 中央値
* ``high``    = 第95パーセンタイル

とする(単位は万円/件)。階級の内側は一様分布を仮定した線形内挿で求める。
最上位階級は上限が無い(「3,000万円以上」「5億円以上」)ので、第95
パーセンタイルがそこに落ちる用途は、上限を捏造せずに**その用途の項目ごと
落とす**(``skipped_reason`` に記録する)。

使い方::

    python -m axes.industry_statistics_axis.fetch            # 取得して保存
    python -m axes.industry_statistics_axis.fetch --offline  # 同梱の原本から再生成
"""

from __future__ import annotations

import argparse
import hashlib
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from axes.industry_statistics_axis._xlsx import Row, Workbook
from axes.industry_statistics_axis.snapshot import (
    DEFAULT_SNAPSHOT_DIR,
    IndustryMetric,
    StatisticsSnapshot,
    save_snapshot,
)

#: 原本(e-Statからダウンロードした .xlsx)を置いておく場所。
SOURCE_DIR = Path(__file__).parent / "sources"

#: e-Stat上のこのデータセットの識別子。年度が変わったらこの3つを差し替える。
STAT_INF_ID = "000040463576"
SOURCE_FILE_NAME = "2025fykeirr_e-stat_excel.xlsx"
#: e-Statの「公開(更新)日」。統計データ自体がいつ公表されたか。
PUBLISHED_ON = date(2026, 6, 12)

SOURCE_URL = f"https://www.e-stat.go.jp/stat-search/file-download?statInfId={STAT_INF_ID}&fileKind=0"
SOURCE_NAME = "国土交通省 建築物リフォーム・リニューアル調査 年度次(令和7年度計)"
PERIOD_COVERED = "令和7年度(2025年4月〜2026年3月)受注分"

#: 2026-09-21 に実際にダウンロードした原本のハッシュ。取得内容が変わったら気づけるようにする。
SOURCE_SHA256 = "b5dbc6887f721c8a06cf66835f084d62ff2565709e488c5afc723556475c6d5c"

#: 使う表(シート名, 表番号, 建物の大分類)。
TABLES = (
    ("年表4-1", "表4-1", "住宅"),
    ("年表4-2", "表4-2", "非住宅建築物"),
)

#: 金額階級のラベルが入っている列と、件数が始まる列(0始まり)。
_LABEL_COLUMNS = (1, 2)
_FIRST_DATA_COLUMN = 3

_LOW_PERCENTILE = 0.05
_TYPICAL_PERCENTILE = 0.50
_HIGH_PERCENTILE = 0.95

#: 合計金額サニティチェックが上振れを「桁違い」と判定する分位点。
#: 「中央値の10倍」は閾値にならない(中央値の10倍を超える工事が実在で
#: 2.7〜11.9% ある)。根拠は `docs/proposal_industry_statistics_repositioning.md` 3-1節。
_OUTLIER_PERCENTILE = 0.99

_UNIT = "万円/件"

#: 階級の合計と表の「計」のずれをどこまで許すか(四捨五入・推計誤差の分)。
_TOTAL_TOLERANCE = 0.005

_AMOUNT = r"(?:[\d,]+\s*[億万]\s*)+"


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------


def download_source(destination: Path | None = None) -> Path:
    """e-Statから原本の .xlsx を取得して保存し、そのパスを返す。"""
    path = destination or (SOURCE_DIR / SOURCE_FILE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "buildpro-estimate-ai"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    path.write_bytes(payload)
    return path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 金額階級のパース
# ---------------------------------------------------------------------------


def parse_amount(text: str) -> float:
    """"1億2,000万" のような表記を万円に直す。"""
    total = 0.0
    for number, scale in re.findall(r"([\d,]+)\s*([億万])", text):
        value = float(number.replace(",", ""))
        total += value * (10000.0 if scale == "億" else 1.0)
    return total


@dataclass(frozen=True)
class AmountBand:
    """金額階級1つ分。``high`` が ``None`` なら上限なし(最上位階級)。"""

    low: float
    high: float | None
    counts: dict[str, float]


def parse_band_label(label: str) -> tuple[float, float | None] | None:
    """"　　50万円以上 100万円未満" のようなラベルを (下限, 上限) に直す。

    金額表記が1つも無いラベル(「計」や空欄)には ``None`` を返す。
    """
    normalized = label.replace("　", " ")
    lower_match = re.search(rf"({_AMOUNT})円?\s*以上", normalized)
    upper_match = re.search(rf"({_AMOUNT})円?\s*未満", normalized)
    if lower_match is None and upper_match is None:
        return None
    low = parse_amount(lower_match.group(1)) if lower_match else 0.0
    high = parse_amount(upper_match.group(1)) if upper_match else None
    return low, high


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("　", " ")).strip()


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def read_use_headers(rows: list[Row], first_data_row: int) -> dict[int, str]:
    """用途名の見出し行を拾い、列index -> 用途名 の対応を作る。

    見出しは複数行に分かれていることがある(表4-1 の「うち 共同住宅」と
    「専有・専用部分」)。その場合は列ごとに上から順につなげる。
    列全体が同じ語(「用途」だけの行)は見出しではないので捨てる。数値が
    並んでいる行(金額階級の前に置かれている「計」の行)も見出しではない。
    """
    header_rows: list[Row] = []
    for row in rows[:first_data_row]:
        values = [_clean(cell) for cell in row[_FIRST_DATA_COLUMN:]]
        filled = [value for value in values if value]
        if len(set(filled)) <= 1:
            continue
        if any(_is_number(value) for value in filled):
            continue
        header_rows.append(row)

    headers: dict[int, str] = {}
    width = max((len(row) for row in header_rows), default=0)
    for column in range(_FIRST_DATA_COLUMN, width):
        parts: list[str] = []
        for row in header_rows:
            value = _clean(row[column]) if column < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        if parts:
            headers[column] = " ".join(parts)
    return headers


def _row_values(row: Row, headers: dict[int, str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for column, name in headers.items():
        raw = row[column] if column < len(row) else None
        if raw is None or _clean(raw) == "":
            continue
        try:
            values[name] = float(raw)
        except ValueError:
            continue
    return values


def read_published_totals(
    rows: list[Row], headers: dict[int, str], first_data_row: int
) -> dict[str, float]:
    """金額階級の手前に置かれている「計」の行を読む(階級の合計との突き合わせ用)。"""
    for row in reversed(rows[:first_data_row]):
        label = " ".join(
            _clean(row[column]) if column < len(row) else "" for column in _LABEL_COLUMNS
        )
        if "計" not in label:
            continue
        values = _row_values(row, headers)
        if values:
            return values
    return {}


def read_bands(
    rows: list[Row],
) -> tuple[dict[int, str], list[AmountBand], dict[str, float]]:
    """1シートから、用途の見出し・金額階級ごとの件数・公表されている「計」を取り出す。"""
    first_data_row: int | None = None
    parsed: list[tuple[int, tuple[float, float | None]]] = []
    for index, row in enumerate(rows):
        label = " ".join(_clean(row[column]) if column < len(row) else "" for column in _LABEL_COLUMNS)
        band = parse_band_label(label)
        if band is None:
            continue
        if first_data_row is None:
            first_data_row = index
        parsed.append((index, band))

    if first_data_row is None:
        raise ValueError("金額階級の行が1つも見つからない(表の形式が変わった可能性)")

    headers = read_use_headers(rows, first_data_row)

    bands = [
        AmountBand(low=low, high=high, counts=_row_values(rows[index], headers))
        for index, (low, high) in parsed
    ]
    bands.sort(key=lambda band: band.low)
    totals = read_published_totals(rows, headers, first_data_row)
    return headers, bands, totals


# ---------------------------------------------------------------------------
# パーセンタイル
# ---------------------------------------------------------------------------


def percentile(bands: list[AmountBand], use: str, quantile: float) -> float | None:
    """階級分布から分位点を求める。上限なしの階級に落ちたら ``None``。

    階級の内側は一様分布を仮定した線形内挿。
    """
    counts = [band.counts.get(use, 0.0) for band in bands]
    total = sum(counts)
    if total <= 0:
        return None
    target = total * quantile
    cumulative = 0.0
    for band, count in zip(bands, counts):
        if count <= 0:
            continue
        if cumulative + count >= target:
            if band.high is None:
                return None
            fraction = (target - cumulative) / count
            return band.low + fraction * (band.high - band.low)
        cumulative += count
    return None


# ---------------------------------------------------------------------------
# スナップショットの組み立て
# ---------------------------------------------------------------------------


def _totals_agree(summed: float, published: float) -> bool:
    if published == 0:
        return summed == 0
    return abs(summed - published) / abs(published) <= _TOTAL_TOLERANCE


def metric_name(category: str, use: str) -> str:
    return f"個別工事の受注額:{category}:{use}"


def build_metrics(
    workbook: Workbook,
) -> tuple[dict[str, IndustryMetric], list[str]]:
    """同梱の表から ``IndustryMetric`` を作る。落とした項目の理由も返す。"""
    metrics: dict[str, IndustryMetric] = {}
    skipped: list[str] = []

    for sheet_name, table_number, category in TABLES:
        headers, bands, published_totals = read_bands(workbook.rows(sheet_name))
        for use in headers.values():
            name = metric_name(category, use)
            total = sum(band.counts.get(use, 0.0) for band in bands)
            published = published_totals.get(use)
            if published is not None and not _totals_agree(total, published):
                skipped.append(
                    f"{name}: 階級の合計({total:,.0f}件)が表の「計」({published:,.0f}件)と"
                    "一致しないため除外(表の読み取りが崩れている可能性)"
                )
                continue
            if total <= 0:
                skipped.append(f"{name}: 受注件数が0件のため除外")
                continue
            low = percentile(bands, use, _LOW_PERCENTILE)
            typical = percentile(bands, use, _TYPICAL_PERCENTILE)
            high = percentile(bands, use, _HIGH_PERCENTILE)
            if low is None or high is None:
                skipped.append(
                    f"{name}: 分位点が上限なしの最上位階級に落ちるため除外"
                    "(上限を捏造しない)"
                )
                continue
            # 合計金額サニティチェック用の閾値。上振れの判定にしか使わない。
            # 上限の無い最上位階級に落ちる用途では outlier は None になり、
            # その場合は beyond_statistics_threshold 側だけで判定する。
            outlier = percentile(bands, use, _OUTLIER_PERCENTILE)
            open_ended = [band for band in bands if band.high is None]
            beyond = open_ended[0].low if open_ended else None
            bottom_share = bands[0].counts.get(use, 0.0) / total

            metrics[name] = IndustryMetric(
                name=name,
                unit=_UNIT,
                low=round(low, 1),
                high=round(high, 1),
                typical=round(typical, 1) if typical is not None else None,
                high_outlier_threshold=round(outlier, 1) if outlier is not None else None,
                beyond_statistics_threshold=beyond,
                bottom_band_share=round(bottom_share, 4),
                sample_description=(
                    f"{table_number} 個別工事の受注額、用途別 受注件数。"
                    f"用途「{use}」の推計受注件数 {total:,.0f} 件、金額階級 {len(bands)} 区分。"
                    f"low=第5, typical=中央, high=第95パーセンタイル"
                    "(階級内は一様分布を仮定した線形内挿)。"
                ),
            )

    return metrics, skipped


def build_snapshot(source_path: Path, *, fetched_at: date | None = None) -> StatisticsSnapshot:
    workbook = Workbook(source_path)
    metrics, skipped = build_metrics(workbook)
    digest = sha256_of(source_path)
    notes_lines = [
        f"e-Stat statInfId={STAT_INF_ID} のファイル({SOURCE_FILE_NAME})から生成。",
        f"公開(更新)日 {PUBLISHED_ON.isoformat()}、原本 sha256={digest}。",
        "表4-1(住宅)・表4-2(非住宅建築物)の「個別工事の受注額の階級別 受注件数」だけを使っている。"
        "他の表は代表値(合計・平均)しか無く、正直なレンジを作れないため使っていない。",
        "単位は万円/件。low=第5パーセンタイル、typical=中央値、high=第95パーセンタイル。"
        "階級内は一様分布を仮定した線形内挿で求めている。",
        "この値は全国の受注実績の分布であって、個別案件の数量を校正したものではない。"
        "この軸は strength=weak・calibrated=False の補助専用軸として扱う。",
    ]
    if skipped:
        notes_lines.append("除外した項目: " + " / ".join(skipped))
    return StatisticsSnapshot(
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        fetched_at=fetched_at or date.today(),
        period_covered=PERIOD_COVERED,
        metrics=metrics,
        is_placeholder=False,
        notes=" ".join(notes_lines),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ダウンロードせず、同梱している原本からスナップショットを作り直す",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help="スナップショットの保存先",
    )
    args = parser.parse_args(argv)

    source_path = SOURCE_DIR / SOURCE_FILE_NAME
    if not args.offline:
        source_path = download_source()
        print(f"取得: {SOURCE_URL} -> {source_path}")
    digest = sha256_of(source_path)
    print(f"原本 sha256: {digest}")
    if digest != SOURCE_SHA256:
        print(
            "警告: 記録しておいた sha256 と一致しない。"
            "e-Stat側でファイルが差し替わった可能性があるので、表の形式を確認すること。"
        )

    snapshot = build_snapshot(source_path)
    path = save_snapshot(snapshot, args.out_dir)
    print(f"保存: {path}(項目数 {len(snapshot.metrics)})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
