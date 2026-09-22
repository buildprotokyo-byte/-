"""各値の「何に基づくか」。

おーちゃんの原則5(`docs/principles/start_kit.md`)。

    前提が揃うまで止めるのではなく、何に基づくかを区別して出す：
    確定／推論に基づく／仮説に基づく／現地確認が必要

**これは確からしさの目盛りではなく、値の出どころの区別である。**
「推論に基づく」が「仮説に基づく」より確からしいのではなく、
**寄りかかっている前提が少ない**というだけである。

ここが守ること
--------------
1. **「確定」は仲裁層(`arbitration/`)だけが作れる。** この層は格上げしない。
   `QuantityItem.is_confirmed`(階層1かつ確定した値がある)が真のときだけ。
2. **弱いほうが勝つ。** 1 つの行が複数の基づきから来たら、いちばん弱いものに
   なる。`axes/reading/protocol.py` の「計算した値も、根拠に『一般則で補った』
   が1つでもあれば『一般則で補った』に落ちる」と同じ向きである。
3. **既定を返さない。** 何も無いところから基づきを作らない
   (`weakest([])` は例外)。既定は静かに嘘をつく。

由来(`read` / `derived` / `assumed`)との対応
--------------------------------------------
仲裁層は由来を3つに分けている(`arbitration/inference_orchestrator.py`)。
この層の4つとは次のように対応する。

===================  ==========================================
仲裁層の由来          この層の基づき
===================  ==========================================
``assumed``          仮説に基づく(一般則で補った＝仮説を置いた)
``derived``          推論に基づく(読んだ値から計算した)
``read``             推論に基づく(読めてはいるが、まだ確かめていない)
===================  ==========================================

**``read`` が「確定」にならないのが要点である。** 図面から読めたことと、
その読みが正しいと確かめられたことは別で、確かめるのは仲裁層の仕事である。

**言葉の上での無理を1つ、そのまま残しておく(2026-09-22):** 読んだだけの値を
「推論に基づく」と呼ぶのは正確ではない。読むことと推論することは別である。
原則5の4区分に「**読めたが、まだ確かめていない**」に当たる箱が無いので、
いちばん近い箱に寄せた、というのがここでの判断である。
4区分を増やす、あるいは別の置き方にする判断があれば、ここを変える。

**設計の申し送り:** `docs/principles/queue3_redesign.md` では ``assumed`` も
「推論に基づく」に寄せていたが、``assumed`` は原則3-3の「仮説を置いて進む」
そのものなので「仮説に基づく」に変えた。こうすると、既存の「``assumed`` は
階層1に入らない」という守りと向きがそろう。
"""

from __future__ import annotations

from typing import Iterable

#: 確定。**仲裁層が自動確定させた値だけ。**
BASIS_CONFIRMED = "確定"

#: 推論に基づく。読んだ値から計算した、またはまだ確かめていない読み。
BASIS_INFERRED = "推論に基づく"

#: 仮説に基づく。置いた仮説、または一般則で補った値に寄りかかっている。
BASIS_HYPOTHETICAL = "仮説に基づく"

#: 現地確認が必要。図面からは決められない。
#:
#: 原則8(おーちゃんの回答): **システムが根拠を付けて提案し、人が確認する。**
#: 根拠の無い「現地確認が必要」は作れないようにしてある
#: (`QuantityItem.site_survey_reason` が空なら、この基づきにならない)。
BASIS_NEEDS_SITE_SURVEY = "現地確認が必要"

#: 強いほうから弱いほうへ。`weakest()` はこの並びの**後ろ**を採る。
BASIS_STRONGEST_FIRST: tuple[str, ...] = (
    BASIS_CONFIRMED,
    BASIS_INFERRED,
    BASIS_HYPOTHETICAL,
    BASIS_NEEDS_SITE_SURVEY,
)


def weakest(values: Iterable[str]) -> str:
    """いくつかの基づきのうち、いちばん弱いものを返す。

    **空の並びは受け付けない。** 何も無いときに既定を返すと、根拠の無い
    値が「推論に基づく」を名乗って下流へ行く。
    **知らない値も受け付けない。** 綴り違いが黙って通ると、弱いほうが
    勝つという約束がその値について効かなくなる。
    """
    ranked = []
    for value in values:
        if value not in BASIS_STRONGEST_FIRST:
            raise ValueError(
                f"知らない基づきです: {value!r}"
                f"(使えるのは {list(BASIS_STRONGEST_FIRST)})"
            )
        ranked.append(BASIS_STRONGEST_FIRST.index(value))
    if not ranked:
        raise ValueError("基づきが1つもありません(既定は返しません)")
    return BASIS_STRONGEST_FIRST[max(ranked)]


def basis_for(
    *,
    is_confirmed: bool,
    effective_derivation: str,
    hypothesis_premise_ids: tuple[str, ...] = (),
    site_survey_reason: str | None = None,
) -> str:
    """1 つの値の基づきを決める。

    **この関数は仲裁層の判定を読むだけで、やり直さない。**
    `is_confirmed` は `QuantityItem` が仲裁層の `action` と確定した値から
    そのまま作ったものである。
    """
    candidates = [BASIS_CONFIRMED if is_confirmed else BASIS_INFERRED]
    if effective_derivation == "assumed" or hypothesis_premise_ids:
        candidates.append(BASIS_HYPOTHETICAL)
    if site_survey_reason:
        candidates.append(BASIS_NEEDS_SITE_SURVEY)
    return weakest(candidates)
