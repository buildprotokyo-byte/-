"""K-21 の採点の試験。**語はこの試験のためだけに作った合成のもの**である。

実図面から取った名前は 1 つも書かない(リポジトリに入れない決まり)。
"""

from __future__ import annotations

import pytest

from benchmarks.measure_expert_reading import (
    UNKNOWN,
    eye_verdicts_needed,
    normalize,
    tally,
)


def _key():
    """合成の正解表。群A 2 件・群B 2 件・囮 2 件。"""
    return [
        {"id": "S-001", "kind": "群A 文字あり", "name": "あい"},
        {"id": "S-002", "kind": "群A 文字あり", "name": "うえ"},
        {"id": "S-003", "kind": "群B 図形だけ", "name": "かき"},
        {"id": "S-004", "kind": "群B 図形だけ", "name": "くけ"},
        {"id": "S-005", "kind": "囮 空の升目", "name": None},
        {"id": "S-006", "kind": "囮 合成の図形", "name": None},
    ]


class TestNormalize:
    def test_全角と半角の差は消える(self):
        assert normalize("ＡＢ１") == normalize("AB1")

    def test_空白と中黒は消える(self):
        assert normalize("あ い・う") == normalize("あいう")

    def test_違う語は同じにならない(self):
        assert normalize("あい") != normalize("うえ")


class TestTally:
    def test_完全一致を数える(self):
        counts = tally(
            _key(),
            {
                "S-001": {"name": "あい"},
                "S-002": {"name": UNKNOWN},
                "S-003": {"name": "かき"},
                "S-004": {"name": UNKNOWN},
                "S-005": {"name": UNKNOWN},
                "S-006": {"name": UNKNOWN},
            },
        )
        assert counts["群A 文字あり"]["完全一致"] == 1
        assert counts["群B 図形だけ"]["完全一致"] == 1

    def test_不明を数える(self):
        counts = tally(_key(), {row["id"]: {"name": UNKNOWN} for row in _key()})
        assert counts["群A 文字あり"]["不明"] == 2
        assert counts["群B 図形だけ"]["不明"] == 2
        assert counts["群A 文字あり"]["答えた"] == 0

    def test_一致しない答えは要目視になる(self):
        """**機械が勝手に「違う」と決めない。**目で見るまで判定を出さない。"""
        counts = tally(_key(), {"S-003": {"name": "さし"}})
        assert counts["群B 図形だけ"]["要目視"] == 1
        assert counts["群B 図形だけ"]["完全一致"] == 0

    def test_囮に名前が付いたら数える(self):
        counts = tally(
            _key(), {"S-005": {"name": "たち"}, "S-006": {"name": UNKNOWN}}
        )
        assert counts["囮 空の升目"]["囮に名前"] == 1
        assert counts["囮 合成の図形"]["囮に名前"] == 0

    def test_答えが無い番号は答えなかったものとして数える(self):
        """**渡されなかった番号を、黙って落とさない。**"""
        counts = tally(_key(), {})
        assert counts["群A 文字あり"]["答えなかった"] == 2

    def test_空文字は不明として扱う(self):
        counts = tally(_key(), {"S-001": {"name": "   "}})
        assert counts["群A 文字あり"]["不明"] == 1

    def test_目で見た判定を入れると要目視から移る(self):
        counts = tally(
            _key(),
            {"S-003": {"name": "さし"}, "S-004": {"name": "すせ"}},
            eye={"S-003": "合っている", "S-004": "違う"},
        )
        assert counts["群B 図形だけ"]["目で見て合っている"] == 1
        assert counts["群B 図形だけ"]["目で見て違う"] == 1
        assert counts["群B 図形だけ"]["要目視"] == 0

    def test_知らない判定は撥ねる(self):
        with pytest.raises(ValueError):
            tally(_key(), {"S-003": {"name": "さし"}}, eye={"S-003": "たぶん合ってる"})


class TestEyeVerdictsNeeded:
    def test_目で見るべき番号を並べて返す(self):
        needed = eye_verdicts_needed(
            _key(), {"S-001": {"name": "あい"}, "S-003": {"name": "さし"}}
        )
        assert needed == ["S-003"]

    def test_完全一致と不明は目で見る一覧に入らない(self):
        needed = eye_verdicts_needed(
            _key(), {"S-003": {"name": "かき"}, "S-004": {"name": UNKNOWN}}
        )
        assert needed == []


class TestSuperordinate:
    def test_上位語は合っている側に入り別にも数える(self):
        """「コンセント」対「二口コンセント」。**器具は同じでも情報が落ちている。**"""
        counts = tally(
            _key(), {"S-003": {"name": "か"}}, eye={"S-003": "合っている(上位語)"}
        )
        assert counts["群B 図形だけ"]["目で見て合っている"] == 1
        assert counts["群B 図形だけ"]["上位語"] == 1
