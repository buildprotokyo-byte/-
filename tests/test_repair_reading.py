"""周10 の直しの道具のテスト。**合成データだけ。**実際の語彙表も正解も使わない。"""

from __future__ import annotations

from benchmarks.repair_reading import repair

VOCAB = ["外枠サイズ", "固定枠見込み", "代理店様名", "品番"]


def test_見出しが崩れていたら直す() -> None:
    fixed, word = repair("外枠サイス", VOCAB)
    assert fixed == "外枠サイズ"
    assert word == "外枠サイズ"


def test_数値には触らない() -> None:
    """**直すのは先頭の見出しだけ。**値はそのまま残す。"""
    fixed, _ = repair("外枠サイス900", VOCAB)
    assert fixed == "外枠サイズ900"


def test_英数字で始まる文字列は直さない() -> None:
    fixed, word = repair("UW800", VOCAB)
    assert fixed == "UW800"
    assert word is None


def test_1文字の先頭は直さない() -> None:
    """1 文字は何にでも近くなるので触らない。"""
    fixed, word = repair("品9", VOCAB)
    assert word is None
    assert fixed == "品9"


def test_遠すぎる語には当てない() -> None:
    fixed, word = repair("あいうえお", VOCAB)
    assert word is None
    assert fixed == "あいうえお"


def test_すでに正しい見出しは置き換えない() -> None:
    fixed, word = repair("品番", VOCAB)
    assert word is None
    assert fixed == "品番"


def test_語彙表が空なら何もしない() -> None:
    fixed, word = repair("外枠サイス", [])
    assert word is None
    assert fixed == "外枠サイス"
