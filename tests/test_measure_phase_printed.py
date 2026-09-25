"""周32 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmarks.measure_phase_printed import (
    DECOY_WORDS,
    PHASE_WORDS,
    normalise,
    pages_of_quantity,
    pages_per_word,
    pages_with_any,
    scatter_pages,
    share_on_pages,
)


def quantity(provenance: dict) -> SimpleNamespace:
    return SimpleNamespace(provenance=provenance)


def test_本物と囮の語数はそろえてある() -> None:
    # そろっていないと、ページ数の多い少ないが語数の差で決まってしまう。
    assert len(PHASE_WORDS) == len(DECOY_WORDS)


def test_本物と囮に同じ語は入っていない() -> None:
    assert not set(PHASE_WORDS) & set(DECOY_WORDS)


def test_全角と半角の違いで取りこぼさない() -> None:
    assert normalise("ＡＢ１") == "AB1"


def test_語が出るページ番号は1始まりで返る() -> None:
    texts = ["なにもない", "ここに既存がある", "ここにもない"]
    assert pages_with_any(("既存",), texts) == {2}


def test_語が1つも出なければ空になる() -> None:
    assert pages_with_any(("既存",), ["あいうえお"]) == set()


def test_語ごとの内訳は語をそのまま鍵にする() -> None:
    texts = ["既存", "新設", ""]
    got = pages_per_word(("既存", "新設", "撤去"), texts)
    assert got == {"既存": [1], "新設": [2], "撤去": []}


def test_根拠の奥にあるページ番号もたどる() -> None:
    item = quantity({"occurrences": [{"page_number": 7}, {"page_number": 9}]})
    assert pages_of_quantity(item) == {7, 9}


def test_根拠の直下のページ番号もたどる() -> None:
    assert pages_of_quantity(quantity({"page_number": 3})) == {3}


def test_ページ番号が無ければ空になる() -> None:
    assert pages_of_quantity(quantity({"note": "なし"})) == set()


def test_ページ番号がたどれない数量も分母に入る() -> None:
    # 分母から外すと、たどれないものが多いほど割合が上がってしまう。
    items = [quantity({"page_number": 1}), quantity({})]
    assert share_on_pages(items, {1}) == pytest.approx(0.5)


def test_数量が無ければ割合はゼロ() -> None:
    assert share_on_pages([], {1}) == 0.0


def test_引く数がページ数以上なら囮Bは全ページと同じになる() -> None:
    # このとき囮Bは本物と必ず並ぶので、線3 は通らない。安全側に倒れる。
    items = [quantity({"page_number": 1}), quantity({"page_number": 2})]
    assert scatter_pages(2, 2, items, seed=1) == pytest.approx(1.0)
    assert scatter_pages(2, 5, items, seed=1) == pytest.approx(1.0)


def test_囮Bは本物に負けることがある() -> None:
    # 数量が 1 ページに固まっていれば、無作為に 1 ページ引いても当たりにくい。
    items = [quantity({"page_number": 1}) for _ in range(10)]
    assert scatter_pages(20, 1, items, seed=20260925) < 1.0


def test_囮Bは本物に勝つこともある() -> None:
    # 数量が全ページに散っていれば、無作為に引いても同じだけ当たる。
    items = [quantity({"page_number": n}) for n in range(1, 11)]
    assert scatter_pages(10, 10, items, seed=20260925) == pytest.approx(1.0)


def test_同じ種を渡せば囮Bは同じ値になる() -> None:
    items = [quantity({"page_number": n}) for n in (1, 2, 3)]
    first = scatter_pages(30, 3, items, seed=20260925)
    second = scatter_pages(30, 3, items, seed=20260925)
    assert first == second
