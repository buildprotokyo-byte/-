"""15周目: 記号名の正規化と別名表のテスト。

**合成データだけを使う。** 実案件の品目名は出てこない。
"""

from __future__ import annotations

import json

import pytest

from estimating.symbol_aliases import (
    MIN_ALIAS_LENGTH,
    AliasError,
    load_alias_table,
    normalise_symbol_name,
    parse_alias_table,
)

SYNTHETIC_ALIASES = "estimating/examples/synthetic_symbol_aliases.json"


# ---------------------------------------------------------------- 正規化


def test_全角と半角をそろえる():
    assert normalise_symbol_name("ｺﾝｾﾝﾄ") == normalise_symbol_name("コンセント")


def test_英字は大文字にそろえる():
    assert normalise_symbol_name("tel") == "TEL"
    assert normalise_symbol_name("ＴＥＬ") == "TEL"


def test_ひらがなをカタカナにそろえる():
    assert normalise_symbol_name("すいっち") == normalise_symbol_name("スイッチ")


def test_長音符と中黒を落とす():
    assert normalise_symbol_name("ブレーカー") == normalise_symbol_name("ブレーカ")
    assert normalise_symbol_name("アウト・レット") == normalise_symbol_name("アウトレット")


def test_括弧の中身を落とす():
    assert normalise_symbol_name("コンセント(防水)") == "コンセント"
    assert normalise_symbol_name("コンセント（防水）") == "コンセント"
    assert normalise_symbol_name("コンセント【防水】") == "コンセント"


def test_空白を落とす():
    assert normalise_symbol_name(" コンセント 取付 ") == "コンセント取付"


def test_意味の違う語は同じにならない():
    # そろえすぎていないことの確認。**ここが同じになると別物に当たる。**
    assert normalise_symbol_name("照明") != normalise_symbol_name("証明")
    assert normalise_symbol_name("配線") != normalise_symbol_name("配管")


def test_文字列でなければ止まる():
    with pytest.raises(AliasError, match="文字列で"):
        normalise_symbol_name(12)


# ---------------------------------------------------------------- 表の読み込み


def _table(symbols):
    return parse_alias_table({"format_version": 1, "symbols": symbols})


def test_一文字の別名は名指しして止める():
    # **一文字の手がかりだけで部分一致させない。**
    with pytest.raises(AliasError, match="短すぎます"):
        _table([{"canonical": "建具", "aliases": ["戸"]}])


def test_最短の長さは二文字である():
    # 定数そのものを書く。定数どうしを比べると書き換えを検出できない。
    assert MIN_ALIAS_LENGTH == 2


def test_二文字の別名は通る():
    table = _table([{"canonical": "建具", "aliases": ["ドア"]}])
    assert table.resolve("ドア") == "建具"


def test_一文字の代表も止まる():
    with pytest.raises(AliasError, match="短すぎます"):
        _table([{"canonical": "盤", "aliases": ["配電盤"]}])


def test_同じ別名が二つの記号に付いていたら止まる():
    with pytest.raises(AliasError, match="両方に付いています"):
        _table(
            [
                {"canonical": "コンセント", "aliases": ["アウトレット"]},
                {"canonical": "TEL", "aliases": ["アウトレット"]},
            ]
        )


def test_同じ別名が同じ記号に二回出ても止まらない():
    table = _table([{"canonical": "コンセント", "aliases": ["差込口", "差込口"]}])
    assert table.resolve("差込口") == "コンセント"


def test_正規化して衝突する別名も止まる():
    # `ブレーカー` と `ブレーカ` は正規化すると同じ形になる。
    with pytest.raises(AliasError, match="両方に付いています"):
        _table(
            [
                {"canonical": "分電盤", "aliases": ["ブレーカー"]},
                {"canonical": "配線", "aliases": ["ブレーカ"]},
            ]
        )


def test_代表が二回出てきたら止まる():
    with pytest.raises(AliasError, match="2 回"):
        _table(
            [{"canonical": "コンセント"}, {"canonical": "コンセント", "aliases": ["差込口"]}]
        )


def test_代表が空なら止まる():
    with pytest.raises(AliasError, match="代表の記号名"):
        _table([{"canonical": "   "}])


def test_symbolsが無ければ止まる():
    with pytest.raises(AliasError, match="symbols"):
        parse_alias_table({"format_version": 1})


def test_辞書でなければ止まる():
    with pytest.raises(AliasError, match="辞書で"):
        parse_alias_table([1, 2, 3])


def test_aliasesが文字列なら止まる():
    with pytest.raises(AliasError, match="並びで"):
        _table([{"canonical": "コンセント", "aliases": "差込口"}])


# ---------------------------------------------------------------- 突き合わせ


def test_当たらなければNoneを返す():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert table.resolve("ぬるぽ") is None


def test_別名から代表に寄る():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert table.resolve("差込口") == "コンセント"
    assert table.resolve("ｱｳﾄﾚｯﾄ") == "コンセント"
    assert table.resolve("点滅器") == "スイッチ"


def test_部分一致で当たったものを全部返す():
    # **1 つに絞らない。** 絞ると、選ばなかったほうが黙って消える。
    table = load_alias_table(SYNTHETIC_ALIASES)
    hits = table.found_in("電話配線用アウトレット取付")
    assert set(hits) == {"TEL", "配線", "コンセント"}


def test_空の文字列には何も当たらない():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert table.found_in("   ") == ()


def test_同じ代表は二回返らない():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert table.found_in("コンセントと差込口") == ("コンセント",)


def test_代表そのものも引ける():
    table = load_alias_table(SYNTHETIC_ALIASES)
    for canonical in table.canonical_names:
        assert table.resolve(canonical) == canonical


def test_見本の表は十一語である():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert len(table.canonical_names) == 11
    assert table.table_id == "synthetic-symbol-aliases-v1"


def test_見本の表に一文字の別名は入っていない():
    table = load_alias_table(SYNTHETIC_ALIASES)
    assert all(len(key) >= MIN_ALIAS_LENGTH for key in table.lookup)


def test_言い方の並びが引ける():
    table = load_alias_table(SYNTHETIC_ALIASES)
    variants = table.variants_of("コンセント")
    assert "コンセント" in variants
    assert normalise_symbol_name("差込口") in variants


def test_見本の表は実案件の品目名から作っていないと書いてある():
    # **正解を抽出の側に持ち込まない**という約束を、ファイル自身に残しておく。
    payload = json.loads(open(SYNTHETIC_ALIASES, encoding="utf-8").read())
    assert "合成" in payload["description"]
    assert "正解ファイル" in payload["description"]
