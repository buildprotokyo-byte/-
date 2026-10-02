"""K-66 2 節(a): 機械で揃える。**AI を呼ばない。意味を変えない。**

揃えるのは書き方だけである。`箇所` と `m2` は別の単位のまま、`外壁` と `内壁` は
別の部位のままにする(揃えてよいのは「同じものの書き方の違い」だけ)。

`draft/normalize.py` の `_flat()` を土台にして、K-66 1 節の (a) が挙げた
「全角半角・括弧・`洋室1`/`洋室(1)`・単位・`材工`/`工`/`材` の接尾・記号」を足した。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

#: 同じ語の書き方の違い。**意味は変えない。**
SPELLINGS: tuple[tuple[str, str], ...] = (
    ("ビニール", "ビニル"),
    ("ヴィニル", "ビニル"),
    ("貼", "張"),
    ("幅木", "巾木"),
    ("取り付", "取付"),
    ("取り外", "取外"),
    ("張り替", "張替"),
    ("塗り替", "塗替"),
    ("打ち替", "打替"),
    ("重ね張", "重張"),
    ("壁紙", "クロス"),
    ("クロース", "クロス"),
)

#: 単位の書き方 → 正規形。**同じ量の単位だけをまとめる。**
UNITS: dict[str, str] = {
    "m2": "m2", "㎡": "m2", "m²": "m2", "平米": "m2", "平方メートル": "m2",
    "m3": "m3", "㎥": "m3", "立米": "m3", "立方メートル": "m3",
    "m": "m", "ｍ": "m", "メートル": "m", "mm": "mm", "cm": "cm",
    "箇所": "箇所", "か所": "箇所", "ヶ所": "箇所", "カ所": "箇所", "ケ所": "箇所", "ヵ所": "箇所",
    "個": "個", "コ": "個", "ヶ": "個",
    "台": "台", "枚": "枚", "本": "本", "式": "式", "組": "組", "セット": "組",
    "灯": "灯", "面": "面", "回路": "回路", "人": "人", "日": "日", "кг": "kg", "kg": "kg", "t": "t",
}

#: 単価の付き方の接尾(`材工共` など)。**工事の中身ではないので落とす。**
PRICE_SUFFIXES: tuple[str, ...] = (
    "材工共", "材工とも", "材工共り", "材工", "工共", "手間のみ", "手間", "工のみ", "材のみ", "材料のみ", "支給品",
)

#: 送り仮名の違い。**末尾だけを落とす**(「張り」→「張」。「張り替え」は上の SPELLINGS で先に揃う)。
OKURIGANA: tuple[tuple[str, str], ...] = (
    ("張り", "張"), ("塗り", "塗"), ("敷き", "敷"), ("組み", "組"), ("付け", "付"),
    ("替え", "替"), ("外し", "外"), ("入れ", "入"), ("出し", "出"), ("上げ", "上"),
)

#: 室名の漢数字。**室名の中だけで使う**(品名では使わない。「一部」などを壊すため)。
KANJI_DIGITS: dict[str, str] = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
}

_DROP = re.compile(r"[\s　・、,，/／\\|｜\-ー−―‐~〜_　()（）「」『』\[\]［］{}｛｝<>＜＞【】]")
_BRACKETED_DIGITS = re.compile(r"[(（\[［]\s*(\d+)\s*[)）\]］]")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def flatten(value: Any) -> str:
    """比べるための平らな文字列。**NFKC → 書き方を揃える → 接尾を落とす → 記号を落とす。**

    括弧の中の数字は括弧を外して残す(`洋室(1)` と `洋室1` を同じにするため)。
    括弧ごと落とすと `洋室(1)` と `洋室(2)` が同じになってしまうので、先に外す。
    """
    text = unicodedata.normalize("NFKC", _text(value))
    text = _BRACKETED_DIGITS.sub(r"\1", text)
    for long, short in SPELLINGS:
        text = text.replace(long, short)
    for suffix in PRICE_SUFFIXES:
        text = text.replace(suffix, "")
    for long, short in OKURIGANA:
        text = text.replace(long, short)
    return _DROP.sub("", text)


def canonical_unit(value: Any) -> str:
    """単位の正規形。**知らない単位は平らにしただけで返す**(勝手にまとめない)。"""
    text = flatten(value)
    if not text:
        return ""
    return UNITS.get(text, text)


def same_unit(a: Any, b: Any) -> bool:
    """同じ量の単位か。**片方が空なら False**(空を「同じ」にしない)。"""
    ca, cb = canonical_unit(a), canonical_unit(b)
    return bool(ca) and ca == cb


def room_key(value: Any) -> str:
    """室名の正規形。`洋室(1)`・`洋室1`・`洋室一` を同じにする。**番号は残す。**"""
    text = flatten(value)
    for kanji, digit in KANJI_DIGITS.items():
        text = text.replace(kanji, digit)
    return text
