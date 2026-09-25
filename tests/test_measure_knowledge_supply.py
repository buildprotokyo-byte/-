"""周2(その1)の数え方のテスト。**合成データだけ。**"""

from __future__ import annotations

from benchmarks.measure_knowledge_supply import checked, measure, source_key

CATALOG = {
    "elements": [{"element_id": "寸法"}, {"element_id": "凡例"}],
    "knowledge": [
        {
            "knowledge_id": "あ",
            "elements": ["寸法"],
            "point_status": "照合済",
            "source": {"document": "本A", "read_directly": True},
        },
        {
            "knowledge_id": "い",
            "elements": ["寸法", "凡例"],
            "point_status": "未照合",
            "source": {"document": "本A", "read_directly": False},
        },
        {
            "knowledge_id": "う",
            "elements": ["凡例"],
            "point_status": "未照合",
            "source": {"document": "本B", "read_directly": False},
        },
    ],
}


def test_同じ文書は1つに畳む() -> None:
    """**独立した証言の数は、畳んだあとの数である。**"""
    result = measure(CATALOG)
    dimension = result["要素ごと"][0]
    assert dimension["当たる知識の数"] == 2
    assert dimension["出典を畳んだ数"] == 1


def test_原文と照らし合わせた数を別に数える() -> None:
    result = measure(CATALOG)
    assert result["原文と照らし合わせた総数"] == 1
    assert result["要素ごと"][1]["原文と照らし合わせた数"] == 0


def test_照合済でも原文を読んでいなければ数えない() -> None:
    assert checked({"point_status": "照合済", "source": {"read_directly": False}}) is False
    assert checked({"point_status": "未照合", "source": {"read_directly": True}}) is False
    assert checked({"point_status": "照合済", "source": {"read_directly": True}}) is True


def test_出どころが無い知識も畳む鍵を持つ() -> None:
    assert source_key({}) == "(出どころ無し)"


def test_設計の下限に届かない要素は足りない数を出す() -> None:
    result = measure(CATALOG)
    assert result["下限に届いた要素の数"] == 0
    assert result["要素ごと"][0]["あと何個足りないか"] == 8


def test_当たる知識が0個の要素を数える() -> None:
    result = measure({"elements": [{"element_id": "天井高"}], "knowledge": []})
    assert result["当たる知識が0個の要素の数"] == 1
