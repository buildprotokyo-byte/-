"""記号名の表記のゆれを、正規化と別名表で吸収する。

2026-09-23、15周目。14 周目で、詰まっている場所が経路ではないと分かった
(`docs/a1_symbol_count_intake_report.md`)。人が書く言葉と見積の品目名の言葉が
違うこと(表記のゆれ)と、語彙そのものが足りないことの 2 つが残っている。

**この層がするのは突き合わせだけで、数量は作らない。**

正規化で何をするか(**意味は変えない**)
--------------------------------------
1. NFKC(全角の英数と半角カナをそろえる)
2. 英字は大文字にそろえる(`tel` と `TEL`)
3. ひらがなをカタカナにそろえる(`すいっち` と `スイッチ`)
4. 長音符と中黒を落とす(`ブレーカー` と `ブレーカ`)
5. 括弧とその中身を落とす(`コンセント(防水)` と `コンセント`)
6. 空白と区切りの記号を落とす

**4 は取り違えを作りうる。** 長音を落とすと別の語と同じ形になることがある。
それでも入れているのは、`ブレーカー` `ブレーカ` `ブレーカｰ` の 3 通りが
実際に混ざるためである。**落とした結果が衝突したら、読み込みのときに止める。**

一文字の別名は受け付けない
--------------------------
`戸` `窓` `盤` `扉` のような一文字の別名は、部分一致で当てると
**関係のない語にいくらでも当たる**(`戸` は `戸建` にも当たる)。
**一文字の手がかりだけで当てるのは、単一の指標で決めるのと同じ**(原則③)。
`MIN_ALIAS_LENGTH` 未満の別名は、**読み込みのときに名指しして止める。**

同じ別名を 2 つの記号に割り当てない
-----------------------------------
`配線` を `配線` と `電話` の両方の別名にすると、どちらに寄せたかが
表からは分からなくなる。**読み込みのときに止める。** 選べないものを
黙って片方に寄せない。

実案件の別名表はリポジトリに置かない
------------------------------------
このリポジトリに入れるのは合成の見本
(`estimating/examples/synthetic_symbol_aliases.json`)だけである。
実案件の表はパスで渡す。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 別名として受け付ける最短の長さ(正規化したあと)。
MIN_ALIAS_LENGTH = 2

#: 落とす記号。長音符・中黒・区切り・空白。
_DROPPED = re.compile(r"[ー―‐\-−ｰ・･/／\\、,.。\s_]+")

#: 括弧とその中身。
_BRACKETED = re.compile(r"[(（\[［{｛<＜【][^)）\]］}｝>＞】]*[)）\]］}｝>＞】]")

_HIRAGANA_START, _HIRAGANA_END = 0x3041, 0x3096
_KATAKANA_OFFSET = 0x60


class AliasError(Exception):
    """別名表がそのままでは使えない形だった。**黙って直さない。**"""


def normalise_symbol_name(text: str) -> str:
    """突き合わせに使う形にそろえる。**元の文字列は捨てない(呼ぶ側が持つ)。**"""
    if not isinstance(text, str):
        raise AliasError(f"記号名は文字列で渡してください(入った値: {text!r})")
    value = unicodedata.normalize("NFKC", text)
    value = _BRACKETED.sub("", value)
    value = "".join(_to_katakana(char) for char in value)
    value = _DROPPED.sub("", value)
    return value.upper().strip()


def _to_katakana(char: str) -> str:
    code = ord(char)
    if _HIRAGANA_START <= code <= _HIRAGANA_END:
        return chr(code + _KATAKANA_OFFSET)
    return char


@dataclass(frozen=True)
class AliasTable:
    """代表の記号名と、その別名。**正規化した形を鍵にする。**"""

    canonical_names: tuple[str, ...]
    lookup: Mapping[str, str]
    """正規化した別名(代表そのものを含む) → 代表の記号名。"""

    table_id: str = ""

    def resolve(self, text: str) -> str | None:
        """文字列が表のどの記号に当たるか。**当たらなければ `None`。**"""
        return self.lookup.get(normalise_symbol_name(text))

    def found_in(self, text: str) -> tuple[str, ...]:
        """文字列の中に現れる記号(部分一致)。**当たった代表の名前を並べる。**

        当たりを 1 つに絞らない。2 つ当たったら 2 つ返す。
        **どちらかを黙って選ぶと、選ばなかったほうが消える。**
        """
        # 別名は 2 文字以上(`MIN_ALIAS_LENGTH`)なので、正規化した鍵が空になることは
        # ない。空文字には何も当たらない。**空を先に弾く分岐は置かない。**
        # 置いても動きは変わらず(壊し試験15周目の見逃し 1 件がこれだった)、
        # 「空のときだけ違う扱いをしている」と読めてしまうためである。
        haystack = normalise_symbol_name(text)
        hits: list[str] = []
        for key, canonical in self.lookup.items():
            if key in haystack and canonical not in hits:
                hits.append(canonical)
        return tuple(hits)

    def variants_of(self, canonical: str) -> tuple[str, ...]:
        """その代表に寄せられる、正規化した言い方。"""
        return tuple(
            sorted(key for key, value in self.lookup.items() if value == canonical)
        )


def load_alias_table(path: str | Path) -> AliasTable:
    source = Path(path)
    return parse_alias_table(
        json.loads(source.read_text(encoding="utf-8")), source_path=source
    )


def parse_alias_table(payload: Any, *, source_path: Path | None = None) -> AliasTable:
    where = f"({source_path})" if source_path else ""
    if not isinstance(payload, Mapping):
        raise AliasError(f"別名表{where}は辞書で書いてください")
    symbols = payload.get("symbols")
    if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes)):
        raise AliasError(f"別名表{where}に symbols の並びがありません")

    canonical_names: list[str] = []
    lookup: dict[str, str] = {}

    for raw in symbols:
        if not isinstance(raw, Mapping):
            raise AliasError(f"別名表{where}の symbols の要素は辞書で書いてください")
        canonical = raw.get("canonical")
        if not isinstance(canonical, str) or not canonical.strip():
            raise AliasError(f"別名表{where}に代表の記号名が入っていない要素があります")
        canonical = canonical.strip()
        if canonical in canonical_names:
            raise AliasError(f"代表の記号名 {canonical!r} が 2 回出てきます")
        canonical_names.append(canonical)

        aliases = raw.get("aliases") or []
        if isinstance(aliases, (str, bytes)) or not isinstance(aliases, Sequence):
            raise AliasError(f"{canonical!r} の aliases は並びで書いてください")

        for word in (canonical, *aliases):
            if not isinstance(word, str):
                raise AliasError(f"{canonical!r} の別名は文字列で書いてください")
            key = normalise_symbol_name(word)
            if len(key) < MIN_ALIAS_LENGTH:
                raise AliasError(
                    f"{canonical!r} の別名 {word!r} は短すぎます"
                    f"(正規化して {len(key)} 文字。{MIN_ALIAS_LENGTH} 文字以上にしてください)。"
                    "一文字の手がかりだけで部分一致させると、関係のない語に当たります"
                )
            already = lookup.get(key)
            if already is not None and already != canonical:
                raise AliasError(
                    f"別名 {word!r} が {already!r} と {canonical!r} の両方に付いています。"
                    "**どちらに寄せるかをこちらでは決められません**"
                )
            lookup[key] = canonical

    return AliasTable(
        canonical_names=tuple(canonical_names),
        lookup=dict(lookup),
        table_id=str(payload.get("table_id") or ""),
    )


__all__ = [
    "AliasError",
    "AliasTable",
    "MIN_ALIAS_LENGTH",
    "load_alias_table",
    "normalise_symbol_name",
    "parse_alias_table",
]
