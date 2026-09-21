"""業界一般統計軸: e-Stat実データの取得・パース(axes/industry_statistics_axis/fetch.py)のテスト。

ネットワークには一切アクセスしない。リポジトリに同梱している原本
(``axes/industry_statistics_axis/sources/``)だけを読む。

ここで一番大事なのは
``test_shipped_snapshot_is_reproducible_from_the_shipped_source`` で、
**同梱しているスナップショットJSONが、同梱している原本から機械的に再生成
できる**ことを確かめている。手で数字を書いた項目が紛れ込んでいたら落ちる。
"""

from __future__ import annotations

from datetime import date

import pytest

from axes.industry_statistics_axis import fetch
from axes.industry_statistics_axis._xlsx import Workbook
from axes.industry_statistics_axis.snapshot import DEFAULT_SNAPSHOT_DIR, load_latest_snapshot

SOURCE_PATH = fetch.SOURCE_DIR / fetch.SOURCE_FILE_NAME


@pytest.fixture(scope="module")
def workbook() -> Workbook:
    return Workbook(SOURCE_PATH)


# ---------------------------------------------------------------------------
# 原本そのもの
# ---------------------------------------------------------------------------


def test_shipped_source_matches_the_recorded_hash() -> None:
    """同梱の原本が、取得時に記録したハッシュと一致している。"""
    assert SOURCE_PATH.exists()
    assert fetch.sha256_of(SOURCE_PATH) == fetch.SOURCE_SHA256


def test_shipped_source_contains_the_two_tables_we_use(workbook: Workbook) -> None:
    for sheet_name, _, _ in fetch.TABLES:
        assert sheet_name in workbook.sheet_names


# ---------------------------------------------------------------------------
# 金額表記・階級ラベルのパース
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50万", 50.0),
        ("1,000万", 1000.0),
        ("1億", 10000.0),
        ("5億", 50000.0),
        ("1億2,000万", 12000.0),
    ],
)
def test_parse_amount_handles_man_and_oku(text: str, expected: float) -> None:
    assert fetch.parse_amount(text) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("  　50万円未満   ", (0.0, 50.0)),
        ("　　50万円以上    100万円未満", (50.0, 100.0)),
        ("  　　　　　　　　　 100 万円未満", (0.0, 100.0)),
        ("9,000万円以上   1億円未満", (9000.0, 10000.0)),
        ("1億円以上   2億円未満", (10000.0, 20000.0)),
        ("3,000万円以上   ", (3000.0, None)),
        ("5億円以上   ", (50000.0, None)),
    ],
)
def test_parse_band_label(label: str, expected: tuple[float, float | None]) -> None:
    assert fetch.parse_band_label(label) == expected


def test_parse_band_label_returns_none_for_non_band_rows() -> None:
    assert fetch.parse_band_label("計 計") is None
    assert fetch.parse_band_label("") is None


# ---------------------------------------------------------------------------
# 表の読み取り
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sheet_name", [name for name, _, _ in fetch.TABLES])
def test_bands_are_contiguous_and_only_the_last_one_is_open_ended(
    workbook: Workbook, sheet_name: str
) -> None:
    """階級が隙間も重なりも無く並び、上限が無いのは最上位階級だけであること。"""
    _, bands, _ = fetch.read_bands(workbook.rows(sheet_name))

    assert len(bands) >= 3
    assert bands[0].low == 0.0
    assert bands[-1].high is None
    for band in bands[:-1]:
        assert band.high is not None
    for previous, following in zip(bands, bands[1:]):
        assert previous.high == following.low


@pytest.mark.parametrize("sheet_name", [name for name, _, _ in fetch.TABLES])
def test_band_counts_add_up_to_the_published_total(workbook: Workbook, sheet_name: str) -> None:
    """階級ごとの件数の合計が、表に載っている「計」と一致すること。

    見出し行や階級行の読み取りがずれていたら、ここで必ず落ちる。
    """
    headers, bands, totals = fetch.read_bands(workbook.rows(sheet_name))

    assert totals, "表の「計」の行が読めていない"
    assert set(totals) <= set(headers.values())
    for use, published in totals.items():
        summed = sum(band.counts.get(use, 0.0) for band in bands)
        assert fetch._totals_agree(summed, published), (use, summed, published)


def test_use_headers_do_not_contain_numbers(workbook: Workbook) -> None:
    """用途の見出しに数値(「計」の行)が混ざっていないこと。"""
    for sheet_name, _, _ in fetch.TABLES:
        headers, _, _ = fetch.read_bands(workbook.rows(sheet_name))
        for name in headers.values():
            assert not any(char.isdigit() for char in name), name


# ---------------------------------------------------------------------------
# パーセンタイル
# ---------------------------------------------------------------------------


def test_percentile_interpolates_inside_a_band() -> None:
    bands = [
        fetch.AmountBand(low=0.0, high=100.0, counts={"x": 80.0}),
        fetch.AmountBand(low=100.0, high=200.0, counts={"x": 20.0}),
    ]
    # 50%点は最初の階級の 50/80 の位置
    assert fetch.percentile(bands, "x", 0.5) == pytest.approx(62.5)
    # 90%点は2つ目の階級の (90-80)/20 の位置
    assert fetch.percentile(bands, "x", 0.9) == pytest.approx(150.0)


def test_percentile_refuses_to_guess_inside_an_open_ended_band() -> None:
    """上限の無い最上位階級に落ちる分位点は、上限を捏造せず None を返す。"""
    bands = [
        fetch.AmountBand(low=0.0, high=100.0, counts={"x": 50.0}),
        fetch.AmountBand(low=100.0, high=None, counts={"x": 50.0}),
    ]
    assert fetch.percentile(bands, "x", 0.9) is None


def test_percentile_returns_none_for_an_empty_column() -> None:
    bands = [fetch.AmountBand(low=0.0, high=100.0, counts={"x": 0.0})]
    assert fetch.percentile(bands, "x", 0.5) is None


def test_percentiles_of_the_shipped_source_are_ordered(workbook: Workbook) -> None:
    metrics, _ = fetch.build_metrics(workbook)

    assert metrics
    for name, metric in metrics.items():
        assert metric.low <= metric.typical <= metric.high, name


# ---------------------------------------------------------------------------
# 同梱スナップショットとの一致
# ---------------------------------------------------------------------------


def test_shipped_snapshot_is_reproducible_from_the_shipped_source() -> None:
    """同梱スナップショットが、同梱の原本から機械的に再生成できること。

    手で書いた数値・実データに無い項目が紛れ込んでいれば落ちる。
    """
    shipped = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    assert shipped is not None

    rebuilt = fetch.build_snapshot(SOURCE_PATH, fetched_at=shipped.fetched_at)

    assert rebuilt.metrics == shipped.metrics
    assert rebuilt.source_url == shipped.source_url
    assert rebuilt.source_name == shipped.source_name
    assert rebuilt.period_covered == shipped.period_covered
    assert rebuilt.is_placeholder is False
    assert shipped.is_placeholder is False
    assert rebuilt.notes == shipped.notes


def test_snapshot_notes_record_where_the_numbers_came_from() -> None:
    snapshot = fetch.build_snapshot(SOURCE_PATH, fetched_at=date(2026, 9, 21))

    assert fetch.STAT_INF_ID in snapshot.notes
    assert fetch.SOURCE_SHA256 in snapshot.notes
    assert fetch.PUBLISHED_ON.isoformat() in snapshot.notes
