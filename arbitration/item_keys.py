"""突き合わせの鍵を揃える。**向きで揃え方を変える。**

なぜ向きで分けるのか
--------------------
経路を突き合わせる層(`arbitration/multi_path_reconciler`)は、鍵が同じ 2 件を
**一致**として扱う。**一致は根拠を強める向きに働く。**
したがって**別の行を同じ鍵にまとめてしまうと、黙って確定する側に効く。**

33 周目(`docs/b_item_key_break_report.md`)で、公共建築工事内訳書標準書式の
品目名から機械的に作った 360 件の鍵(組は 64,620 通り)で測った結果:

============================  ==========  ==========
揃え方                        真の一致    偽の一致
============================  ==========  ==========
記号と仮名を揃えるだけ            0.395    **0**
語順を無視し送り仮名も落とす      0.992     4 / 64,620
============================  ==========  ==========

**壊れる 4 件はすべて同じ形である。**

    「壁軽量鉄骨天井下地撤去」 と 「天井軽量鉄骨壁下地撤去」

**壁に付いている天井下地**と、**天井に付いている壁下地**は**別の行**である。
文字は同じで、並びだけが違う。動作(撤去・張り・塗り・敷き)の 4 通りで同じことが起きる。
「軽量鉄骨壁下地」も「軽量鉄骨天井下地」も、**どちらも内訳書標準書式に実在する品目**で、
作り話ではない。

そこで、PR #6 で決めた「**誤りが人へ回る向きでは広く、黙って確定させる向きでは狭く**」を
そのまま当てはめる。

- `SCOPE_CONFIRM`(**黙って確定させる向き**)… 記号と仮名のゆれだけ揃える。
  **語順は動かさない。括弧も動かさない。**
- `SCOPE_TO_HUMAN`(**人へ回す向き**。質問を作る・漏れを見つける)…
  括弧の中身を外に出し、語順を無視し、送り仮名も落とす。
  **まとめすぎることを承知で使う。人が見るので取り返せる。**

**呼ぶ側が揃え方を選べるようにしていない。** 向きを指定したら揃え方が決まる。
選べるようにすると、いつか誰かが確定させる向きに広いほうを渡す。

**既定は狭いほう**(`SCOPE_CONFIRM`)である。何も指定しなければ安全側に倒れる。

この層が決めないこと
--------------------
- 一致した 2 件のどちらが正しいか(突き合わせの層の仕事)
- 独立したデータ源の数(`axis_quality_firewall.AxisEvidence.independence_key` の仕事)
- 階層(仲裁層の仕事)
"""

from __future__ import annotations

import re
import unicodedata

#: 黙って確定させる向き。**狭く揃える。**
SCOPE_CONFIRM = "確定"

#: 人へ回す向き(質問を作る・漏れを見つける)。**広く揃える。**
SCOPE_TO_HUMAN = "人へ"

SCOPES: tuple[str, ...] = (SCOPE_CONFIRM, SCOPE_TO_HUMAN)

#: 落とす記号。中黒・ハイフン・長音・区切り・空白。
_SYMBOLS = re.compile(r"[ー―‐\-−ｰ・･/／\\、,.。\s_]+")

#: 括弧とその中身。
_BRACKETED = re.compile(r"[(（\[［{｛<＜【]([^)）\]］}｝>＞】]*)[)）\]］}｝>＞】]")

#: 送り仮名のゆれ。**広い向きでだけ落とす。**
_OKURIGANA: tuple[tuple[str, str], ...] = (("張り", "張"), ("塗り", "塗"), ("敷き", "敷"))


def _to_katakana(text: str) -> str:
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in text)


def _narrow(text: str) -> str:
    """記号と仮名のゆれだけ揃える。**語順も括弧も動かさない。**"""
    out = unicodedata.normalize("NFKC", text)
    out = _SYMBOLS.sub("", out)
    return _to_katakana(out).upper()


def _wide(text: str) -> str:
    """括弧を外に出し、送り仮名を落とし、語順を無視する。"""
    out = text
    for long, short in _OKURIGANA:
        out = out.replace(long, short)
    inner = "".join(_BRACKETED.findall(out))
    outer = _BRACKETED.sub("", out)
    return "".join(sorted(_narrow(outer + inner)))


def normalise_item_key(text: str, *, scope: str = SCOPE_CONFIRM) -> str:
    """鍵を、向きに応じて揃える。

    `scope` を指定しなければ `SCOPE_CONFIRM`(狭いほう)になる。
    知らない向きは `ValueError`。**黙って広いほうに倒さない。**
    """
    if scope not in SCOPES:
        raise ValueError(
            f"知らない向きです: {scope!r}。"
            f"使えるのは {SCOPE_CONFIRM!r}(黙って確定させる向き)か "
            f"{SCOPE_TO_HUMAN!r}(人へ回す向き)です"
        )
    if not text or not text.strip():
        raise ValueError("鍵が空です。空の鍵は突き合わせに入れません")
    return _narrow(text) if scope == SCOPE_CONFIRM else _wide(text)


def same_item_key(left: str, right: str, *, scope: str = SCOPE_CONFIRM) -> bool:
    """2 つの鍵が、その向きで同じものとして扱われるか。"""
    return normalise_item_key(left, scope=scope) == normalise_item_key(right, scope=scope)
