"""周6〜周8 の採点の道具のテスト。**合成データだけ。**実図面も実際の正解も使わない。"""

from __future__ import annotations

from benchmarks.score_maker_drawing_reading import dice, fold, normalize, score_place


def test_空白だけを落とす() -> None:
    assert normalize("あ い\tう") == "あいう"
    assert normalize("UW 161 3") == "UW1613"


def test_全角と半角はそのまま一致では別物() -> None:
    """**判定はそのまま一致でする。**括弧の全半角が違えば外れ。"""
    result = score_place("洋室（1）", ["洋室(1)"])
    assert result["そのまま一致"] is False
    assert result["近い"] is True


def test_全角と半角をそろえるのは近いを数えるときだけ() -> None:
    assert fold("ＵＷ８００") == "UW800"


def test_ばらばらに返った文字列を繋いで当てる() -> None:
    """読み取りは 1 つの語を分けて返すことがある。繋いだものも候補にする。"""
    result = score_place("WD2 廊下物入", ["WD2", "廊下物入"])
    assert result["そのまま一致"] is True


def test_1文字違うだけなら近いに入る() -> None:
    result = score_place("固定枠見込み90mm", ["固定枠見込み9Omm"])
    assert result["そのまま一致"] is False
    assert result["近い"] is True


def test_何も返らなければ届いていない() -> None:
    result = score_place("UW800×UH2300", [])
    assert result["届いた"] is False
    assert result["そのまま一致"] is False
    assert result["近い"] is False


def test_まったく違う文字列は近いにも入らない() -> None:
    result = score_place("固定枠見込み90mm", ["代理店様名"])
    assert result["近い"] is False


def test_2gramのDice() -> None:
    assert dice("あいう", "あいう") == 1.0
    assert dice("あいう", "かきく") == 0.0
