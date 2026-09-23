"""突き合わせ役の試験(トライアルB「多方向からのアプローチ」)。

マスタープロンプト 3 節の決まりを、そのまま試験にしたもの。

- 一致が根拠を強めるのは、経路が**本当に独立している**ときだけ
- 食い違いは**どちらも選ばない**
- 片方にしか無いものは**捨てない**
"""

from __future__ import annotations

import pytest

from arbitration.multi_path_reconciler import (
    PATH_KINDS,
    PathItem,
    coverage_by_path,
    path_specialties,
    reconcile,
)


def _item(
    path_id: str,
    item_key: str,
    *,
    path_kind: str = "下から",
    source: str = "pdf-A",
    value: tuple[float, float] | None = None,
    unit: str | None = None,
    category: str = "",
    evidence: str = "ページ1の線",
) -> PathItem:
    return PathItem(
        path_id=path_id,
        path_kind=path_kind,  # type: ignore[arg-type]
        source_fingerprint=source,
        item_key=item_key,
        item_category=category,
        value_range=value,
        unit=unit,
        evidence=evidence,
    )


# --- 独立性の数え方 -------------------------------------------------------


def test_同じPDFから来た一致は独立した証言として数えない() -> None:
    """同じ図面を線から読んでも表から読んでも、データ源は 1 つ。"""
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item("表から", "コンセント", path_kind="横から", source="pdf-A", value=(12, 12), unit="個"),
        ]
    )
    (item,) = result.items
    assert item.status == "一致"
    assert item.independent_source_count == 1
    assert item.strengthens is False
    assert result.strengthened() == ()
    assert "独立した証言ではない" in item.reason


def test_人の入力と図面がそろえば独立した経路が2つになる() -> None:
    """人の入力(スタートキット)は図面とは別のデータ源。"""
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item(
                "人の入力",
                "コンセント",
                path_kind="上から",
                source="start-kit-001",
                value=(12, 12),
                unit="個",
                evidence="スタートキットの記入欄",
            ),
        ]
    )
    (item,) = result.items
    assert item.independent_source_count == 2
    assert item.strengthens is True
    assert result.strengthened() == (item,)


def test_独立が2つそろっても値は確定しない() -> None:
    """この層は階層を決めない。報告するだけ。"""
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item("人の入力", "コンセント", path_kind="上から", source="start-kit-001", value=(12, 12), unit="個"),
        ]
    )
    (item,) = result.items
    # 突き合わせ結果に「確定」に当たる情報は無い。あるのは独立の数だけ。
    assert not hasattr(item, "settled")
    assert not hasattr(item, "tier")
    assert item.independent_source_count == 2


# --- 食い違い -------------------------------------------------------------


def test_食い違いはどちらも選ばない() -> None:
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item("表から", "コンセント", path_kind="横から", source="表-B", value=(30, 30), unit="個"),
        ]
    )
    (item,) = result.items
    assert item.status == "食い違い"
    assert item.agreed_range is None
    assert item.strengthens is False
    assert result.conflicting() == (item,)


def test_単位が違うものは一致にしない() -> None:
    result = reconcile(
        [
            _item("線から", "内壁ボード", source="pdf-A", value=(40, 40), unit="m2"),
            _item("表から", "内壁ボード", path_kind="横から", source="表-B", value=(40, 40), unit="m"),
        ]
    )
    (item,) = result.items
    assert item.status == "食い違い"


def test_許容差の中の差は一致とみなす() -> None:
    result = reconcile(
        [
            _item("線から", "床面積", source="pdf-A", value=(20.0, 20.0), unit="m2"),
            _item("別の図面", "床面積", path_kind="別の角度", source="pdf-B", value=(20.8, 20.8), unit="m2"),
        ],
        relative_tolerance=0.05,
    )
    (item,) = result.items
    assert item.status == "一致"


def test_許容差を超える差は食い違いにする() -> None:
    result = reconcile(
        [
            _item("線から", "床面積", source="pdf-A", value=(20.0, 20.0), unit="m2"),
            _item("別の図面", "床面積", path_kind="別の角度", source="pdf-B", value=(22.0, 22.0), unit="m2"),
        ],
        relative_tolerance=0.05,
    )
    (item,) = result.items
    assert item.status == "食い違い"


def test_値を出さない経路は値について食い違わない() -> None:
    """「この行があるはず」とだけ言う経路は、値については何も主張していない。"""
    result = reconcile(
        [
            _item("線から", "墨出し", source="pdf-A", value=(1, 1), unit="式"),
            _item(
                "見積の型から",
                "墨出し",
                path_kind="後ろから",
                source="見積書式-C",
                value=None,
                evidence="見積の書式にこの行がある",
            ),
        ]
    )
    (item,) = result.items
    assert item.status == "一致"
    assert item.independent_source_count == 2


# --- 片方にしか無いもの ---------------------------------------------------


def test_1つの経路だけが見つけたものは捨てない() -> None:
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item("表から", "コンセント", path_kind="横から", source="pdf-A", value=(12, 12), unit="個"),
            _item(
                "見積の型から",
                "仮設水道",
                path_kind="後ろから",
                source="見積書式-C",
                evidence="見積の書式にこの行がある",
            ),
        ]
    )
    keys = {item.item_key: item for item in result.items}
    assert keys["仮設水道"].status == "片方にしか無い"
    assert keys["仮設水道"].path_ids == ("見積の型から",)
    assert "捨てない" in keys["仮設水道"].reason
    assert len(result.single_path()) == 1


def test_同じ経路が同じ鍵を2回出しても片方にしか無いまま() -> None:
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個", evidence="ページ1"),
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個", evidence="ページ2"),
        ]
    )
    (item,) = result.items
    assert item.status == "片方にしか無い"
    assert item.path_ids == ("線から",)


# --- 全体 -----------------------------------------------------------------


def test_入力の順番を変えても結果は変わらない() -> None:
    items = [
        _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
        _item("表から", "コンセント", path_kind="横から", source="表-B", value=(12, 12), unit="個"),
        _item("見積の型から", "仮設水道", path_kind="後ろから", source="見積書式-C", evidence="書式"),
        _item("線から", "床面積", source="pdf-A", value=(20, 21), unit="m2"),
    ]
    first = reconcile(items)
    second = reconcile(list(reversed(items)))
    assert first == second
    assert [i.item_key for i in first.items] == sorted(i.item_key for i in first.items)


def test_経路ごとの得意分野の表が出る() -> None:
    items = [
        _item("線から", "コンセント", source="pdf-A", category="記号を数える行", value=(12, 12), unit="個"),
        _item("線から", "スイッチ", source="pdf-A", category="記号を数える行", value=(6, 6), unit="個"),
        _item("線から", "床面積", source="pdf-A", category="形から出す行", value=(20, 20), unit="m2"),
        _item("見積の型から", "仮設水道", path_kind="後ろから", source="見積書式-C", category="会社のルールの行", evidence="書式"),
    ]
    table = path_specialties(items)
    assert table["線から"]["記号を数える行"] == 2
    assert table["線から"]["形から出す行"] == 1
    assert table["見積の型から"]["会社のルールの行"] == 1
    assert "会社のルールの行" not in table["線から"]


def test_経路ごとの見つけた鍵が出る() -> None:
    items = [
        _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
        _item("見積の型から", "仮設水道", path_kind="後ろから", source="見積書式-C", evidence="書式"),
    ]
    coverage = coverage_by_path(items)
    assert coverage["線から"] == {"コンセント"}
    assert coverage["見積の型から"] == {"仮設水道"}
    union = set().union(*coverage.values())
    assert union == {"コンセント", "仮設水道"}


def test_何も渡さなければ何も出ない() -> None:
    result = reconcile([])
    assert result.items == ()
    assert result.agreed() == ()
    assert result.conflicting() == ()
    assert result.single_path() == ()


def test_要約に分母が入る() -> None:
    result = reconcile(
        [
            _item("線から", "コンセント", source="pdf-A", value=(12, 12), unit="個"),
            _item("表から", "コンセント", path_kind="横から", source="pdf-A", value=(12, 12), unit="個"),
            _item("見積の型から", "仮設水道", path_kind="後ろから", source="見積書式-C", evidence="書式"),
        ]
    )
    summary = result.summary()
    assert "突き合わせた鍵: 2 件" in summary
    assert "一致: 1 件" in summary
    assert "独立した経路が 2 つ以上: 0 件" in summary
    assert "片方にしか無いもの(捨てない): 1 件" in summary


# --- 入力の検査 -----------------------------------------------------------


def test_根拠が空の件は突き合わせに入れない() -> None:
    with pytest.raises(ValueError, match="根拠が空"):
        _item("線から", "コンセント", evidence="")


def test_データ源の指紋が空だと作れない() -> None:
    with pytest.raises(ValueError, match="source_fingerprint"):
        _item("線から", "コンセント", source="")


def test_知らない経路の種類は受け付けない() -> None:
    with pytest.raises(ValueError, match="知らない経路の種類"):
        _item("線から", "コンセント", path_kind="斜めから")


def test_値があるのに単位が無いと作れない() -> None:
    with pytest.raises(ValueError, match="単位がありません"):
        _item("線から", "コンセント", value=(12, 12), unit=None)


def test_値の上下が逆だと作れない() -> None:
    with pytest.raises(ValueError, match="下限が上限を超えて"):
        _item("線から", "コンセント", value=(12, 10), unit="個")


def test_許容差に負の数は渡せない() -> None:
    with pytest.raises(ValueError, match="許容差"):
        reconcile([], relative_tolerance=-0.1)


def test_経路の種類は6つ() -> None:
    assert PATH_KINDS == ("上から", "下から", "横から", "別の角度", "後ろから", "外から")
