"""合計金額サニティチェックの閾値の根拠を、原本の階級分布から集計する。

``docs/proposal_industry_statistics_repositioning.md`` 3節の表を出力する。
提案した閾値(p99)と、素朴な「桁違い=中央値の10倍」が閾値として使えない
ことを、実データから確かめるためのスクリプト。

使い方::

    python -m benchmarks.analyze_amount_distribution

ネットワークは使わない。`axes/industry_statistics_axis/sources/` に同梱して
ある原本(.xlsx)だけを読む。
"""

from __future__ import annotations

from axes.industry_statistics_axis._xlsx import Workbook
from axes.industry_statistics_axis.fetch import (
    SOURCE_DIR,
    SOURCE_FILE_NAME,
    TABLES,
    AmountBand,
    percentile,
    read_bands,
)

#: 「桁違い」を素朴に1桁(10倍)と置いた場合の倍率。3-1節で否定される。
NAIVE_ORDER_OF_MAGNITUDE = 10.0

#: 提案する上振れの閾値。
PROPOSED_QUANTILE = 0.99


def share_below(bands: list[AmountBand], use: str, amount: float) -> float | None:
    """``amount`` 未満の受注件数の割合。階級内は一様分布を仮定する。

    ``amount`` が上限なしの最上位階級の内側に落ちる場合は、その階級の内訳が
    統計に載っていないため ``None`` を返す(推測で埋めない)。
    """
    total = sum(band.counts.get(use, 0.0) for band in bands)
    if total <= 0:
        return None

    accumulated = 0.0
    for band in bands:
        count = band.counts.get(use, 0.0)
        if count <= 0 or amount <= band.low:
            continue
        if band.high is None:
            if amount > band.low:
                return None
            continue
        if amount >= band.high:
            accumulated += count
        else:
            accumulated += count * (amount - band.low) / (band.high - band.low)
    return accumulated / total


def _format_share(share: float | None) -> str:
    return "算出不可" if share is None else f"{share * 100:.2f}%"


def _format_amount(amount: float | None) -> str:
    return "算出不可" if amount is None else f"{amount:,.1f}"


def main() -> int:
    workbook = Workbook(SOURCE_DIR / SOURCE_FILE_NAME)

    header = (
        f"{'用途':38s} {'中央値':>9s} {'中央x10超の実在割合':>12s} "
        f"{'p99':>10s} {'p99/中央':>8s} {'最下位階級の占有率':>12s} {'最上位階級':>12s}"
    )
    print(header)
    print("-" * len(header))

    for sheet_name, _table_number, category in TABLES:
        headers, bands, _totals = read_bands(workbook.rows(sheet_name))
        bottom, top = bands[0], bands[-1]
        for use in headers.values():
            total = sum(band.counts.get(use, 0.0) for band in bands)
            if total <= 0:
                continue
            median = percentile(bands, use, 0.50)
            if median is None:
                continue

            naive = share_below(bands, use, median * NAIVE_ORDER_OF_MAGNITUDE)
            over_naive = None if naive is None else 1.0 - naive
            proposed = percentile(bands, use, PROPOSED_QUANTILE)
            ratio = "算出不可" if proposed is None else f"{proposed / median:.1f}倍"
            bottom_share = bottom.counts.get(use, 0.0) / total
            top_share = top.counts.get(use, 0.0) / total

            print(
                f"{category + ':' + use:38s} {median:9.1f} {_format_share(over_naive):>12s} "
                f"{_format_amount(proposed):>10s} {ratio:>8s} "
                f"{bottom_share * 100:11.1f}% "
                f"{top.low:,.0f}万円〜 {top_share * 100:.3f}%"
            )

    print()
    print(
        "読み方: 「中央x10超の実在割合」が 2〜12% あるため、素朴な"
        f"「桁違い={NAIVE_ORDER_OF_MAGNITUDE:.0f}倍」は閾値にならない"
        "(正常な案件をその割合だけ人手確認に回すことになる)。"
    )
    print(
        "「最下位階級の占有率」が 59〜82% あるため、下限側の分位点は階級内一様分布の"
        "仮定だけから出た値で、下振れの検出には使えない。"
    )
    print("「最上位階級」は上限が無く、そこに落ちた金額について統計はこれ以上何も言えない。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
