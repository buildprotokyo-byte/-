"""中心値の検査が実際に何件の誤った自動確定を止めたかを、固定値で押さえる。

`tests/test_center_agreement.py` は「レンジが重なっているだけでは確定しない」
という**性質**を押さえている。こちらは**効果の量**を押さえる。性質のテスト
だけだと、検査が1件しか働いていなくても通ってしまうためである。

数え方
------
同じ乱数シード(0〜29)で証拠を作り、**本体のファイアウォールを2通り**で
走らせて突き合わせる。

* 検査なし … `AxisQualityFirewall(center_tolerances=CHECK_DISABLED)`。
  既にある入口へ極端に大きい相対許容差を渡して、中心値の検査が無かった頃を
  再現する。**本体に迂回用のスイッチは足していない**(足すと本番でも
  使える抜け道になる)。
* 検査あり … 既定の `AxisQualityFirewall()`。

数えるのは「**人が見ないまま採用される範囲**(階層1の自動確定と階層2の
仮採用)のうち、正解を含んでいないものの件数」である。階層3は人が必ず
確認するので、そこに残る誤りは数えない。

正直に書いておくこと
--------------------
* **検査は誤りを全部は止めない。** 強い軸2つ・重なる誤りの条件で、
  階層1に残る誤りは 97件 → 5件で、0件にはならない。中心値が近いまま
  2つの軸が揃って間違える形は、この検査では捕まえられない。
* **明確に矛盾する誤り(disjoint)では1件も変わらない。** これは
  退行が無いことの確認であって、効果ではない。

30試行を通しで回した end-to-end の数値(「階層なしを下回る試行 10/30 →
0/30」など)は、ここで回すには重すぎるので
`benchmarks/simulate_tier1_center_agreement.py` が書き出した
`docs/center_agreement_effect_result.json` を凍結して突き合わせる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbitration.axis_quality_firewall import AxisQualityFirewall
from benchmarks.run_trial789_reproduction import build_scenario, make_evidence
from benchmarks.simulate_tier1_center_agreement import (
    CHECK_DISABLED,
    RESULT_PATH,
    condition_key,
)

TRIALS = 30

#: 人が見ないまま採用される階層。階層3は人が必ず確認するので数えない。
UNREVIEWED_TIERS = (1, 2)


def _count_wrong_unreviewed(
    model: str, *, with_weak_axes: bool, check_enabled: bool
) -> dict[int, int]:
    """階層ごとに「正解を含まない範囲のまま採用された」件数を数える。"""
    scenario = build_scenario()
    firewall = AxisQualityFirewall(
        center_tolerances=None if check_enabled else CHECK_DISABLED
    )
    wrong = {tier: 0 for tier in UNREVIEWED_TIERS}
    for seed in range(TRIALS):
        evidence = make_evidence(
            scenario, seed, model,
            with_weak_axes=with_weak_axes, mixed_primary_axes=True,
        )
        for name, items in evidence.items():
            decision = firewall.assess(items)
            if decision.tier not in wrong or decision.confirmed_range is None:
                continue
            low, high = decision.confirmed_range
            if not low <= scenario.truth[name] <= high:
                wrong[decision.tier] += 1
    return wrong


@pytest.fixture(scope="module")
def measured() -> dict[tuple[str, bool, bool], dict[int, int]]:
    """3条件 x 2通りを1回だけ測る。

    `assess()` は要素ごとに Z3 を回すので、テストごとに測り直すと
    全件実行が目に見えて遅くなる。
    """
    out: dict[tuple[str, bool, bool], dict[int, int]] = {}
    for model, with_weak in (
        ("overlapping", False), ("overlapping", True), ("disjoint", False),
    ):
        for enabled in (False, True):
            out[(model, with_weak, enabled)] = _count_wrong_unreviewed(
                model, with_weak_axes=with_weak, check_enabled=enabled
            )
    return out


# ---------------------------------------------------------------------
# 効果の量(固定値)
# ---------------------------------------------------------------------


def test_強い軸2つ重なる誤りで階層1の誤りが97件から5件に減る(measured) -> None:
    """**最悪だった条件。** 階層1で確定するので監査の母集団にも入らない。"""
    before = measured[("overlapping", False, False)]
    after = measured[("overlapping", False, True)]
    assert before[1] == 97, "検査が無かった頃の誤った自動確定の件数"
    assert after[1] == 5, "検査を入れたあとに階層1へ残る誤りの件数"
    assert after[2] == 0


def test_検査は誤りを全部は止めない(measured) -> None:
    """**5件残ることを固定する。** 0件だと思い込ませないため。

    中心値が近いまま2つの軸が揃って間違える形は、この検査では捕まらない。
    """
    assert measured[("overlapping", False, True)][1] > 0


def test_強い軸1つ弱い軸2つで階層2の誤りが58件から0件になる(measured) -> None:
    """階層1だけに検査を入れても効かなかった条件。

    劣化が実測されたのはこの形で、階層1の要素は1件も生まれない。
    """
    before = measured[("overlapping", True, False)]
    after = measured[("overlapping", True, True)]
    assert before[2] == 58
    assert after[2] == 0
    assert before[1] == 0, "前提: この条件では階層1が1件も生まれない"
    assert after[1] == 0


def test_明確に矛盾する誤りでは1件も変わらない(measured) -> None:
    """**退行の確認。** 効くべきでない条件で挙動を変えていないこと。"""
    before = measured[("disjoint", False, False)]
    after = measured[("disjoint", False, True)]
    assert before == after
    assert before[1] == 9, (
        "両方の軸が中心値も揃えて間違える形は、この検査の守備範囲ではない"
    )


def test_検査なしの再現が本当に検査を止めている(measured) -> None:
    """`CHECK_DISABLED` が効いていなければ、上の全部が無意味になる。

    効いていなければ「検査なし」と「検査あり」が同じ数になる。
    """
    assert (
        measured[("overlapping", False, False)]
        != measured[("overlapping", False, True)]
    )
    assert (
        measured[("overlapping", True, False)]
        != measured[("overlapping", True, True)]
    )


def test_無効化の表が本体の単位を全部覆っている() -> None:
    """覆えていない単位があると、そこだけ検査が生き残って静かに嘘をつく。"""
    from arbitration.axis_quality_firewall import CENTER_TOLERANCES

    assert set(CHECK_DISABLED) == set(CENTER_TOLERANCES)


# ---------------------------------------------------------------------
# 通しで回した 30試行の数値(凍結)
# ---------------------------------------------------------------------


def _recorded() -> dict:
    if not RESULT_PATH.exists():  # pragma: no cover
        pytest.fail(
            f"計測結果がありません: {RESULT_PATH}\n"
            "benchmarks/simulate_tier1_center_agreement.py --trials 30 "
            "--json docs/center_agreement_effect_result.json で作り直してください"
        )
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def _condition(record: dict, model: str, with_weak_axes: bool) -> dict:
    key = condition_key(model, with_weak_axes)
    for item in record["conditions"]:
        if item["condition"] == key:
            return item
    raise AssertionError(f"条件が記録にありません: {key}")


CURRENT = "本実装(本体の中心値検査)"
BEFORE = "現行"


def test_記録は30試行ぶんある() -> None:
    record = _recorded()
    assert record["trials"] == TRIALS
    assert CURRENT in record["policies"], "本実装の列が記録にない"
    for item in record["conditions"]:
        assert len(item["baseline_per_seed"]) == TRIALS
        for name, row in item["policies"].items():
            assert len(row["per_seed"]) == TRIALS, name


def test_強い軸1つ弱い軸2つの下回りが10件から0件になった() -> None:
    """おーちゃんが指定した数値。実装前 10/30、実装後 0/30。"""
    condition = _condition(_recorded(), "overlapping", True)
    assert condition["policies"][BEFORE]["worse_than_no_tiers_before"] == 10
    assert condition["policies"][CURRENT]["worse_than_no_tiers_before"] == 0


def test_強い軸2つの下回りが25件から0件になった() -> None:
    """いちばん悪かった条件。階層を入れたほうが25/30で精度が低かった。"""
    condition = _condition(_recorded(), "overlapping", False)
    assert condition["policies"][BEFORE]["worse_than_no_tiers_before"] == 25
    assert condition["policies"][CURRENT]["worse_than_no_tiers_before"] == 0


def test_明確に矛盾する誤りでは下回りが元から0件のまま() -> None:
    record = _recorded()
    for with_weak in (True, False):
        condition = _condition(record, "disjoint", with_weak)
        assert condition["policies"][BEFORE]["worse_than_no_tiers_before"] == 0
        assert condition["policies"][CURRENT]["worse_than_no_tiers_before"] == 0


def test_本実装は予測した方針と同じ結果になった() -> None:
    """シミュレーションの予測(A' 階層2にも中心±1)を本実装が再現したこと。

    ずれていたら、実装が提案どおりになっていないか、提案の再現が
    ずれているかのどちらかである。どちらも黙って通してはいけない。
    """
    record = _recorded()
    predicted = "A' 階層2にも中心±1"
    for item in record["conditions"]:
        actual = item["policies"][CURRENT]
        forecast = item["policies"][predicted]
        for field in ("accuracy_before_audit", "worse_than_no_tiers_before"):
            assert actual[field] == pytest.approx(forecast[field], rel=1e-9), (
                f"{item['condition']} の {field} が予測と食い違う"
            )


def test_階層なしより悪くなる条件が1つも残っていない() -> None:
    """この対応の目的そのもの。"""
    record = _recorded()
    for item in record["conditions"]:
        assert item["policies"][CURRENT]["worse_than_no_tiers_before"] == 0, (
            f"{item['condition']} で階層を入れたほうが悪い試行が残っている"
        )


def test_記録が古い計測スクリプトのものでないこと() -> None:
    """実装後に壊れた「現行」の列で測った記録を、そのまま信じないための検査。

    実装を本体に入れたあと、無効化を渡さずに測ると全方針が同じ数値になる
    (2026-09-21 に実測して確認した)。その状態の記録は「現行」と「本実装」が
    一致してしまう。
    """
    record = _recorded()
    condition = _condition(record, "overlapping", False)
    assert (
        condition["policies"][BEFORE]["accuracy_before_audit"]
        != condition["policies"][CURRENT]["accuracy_before_audit"]
    ), "「現行」と「本実装」が同じ数値になっている。無効化が効いていない記録"
