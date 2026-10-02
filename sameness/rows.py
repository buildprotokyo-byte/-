"""K-66 5 節: 3 回の一致の比較を、この部品に通す口。**文字の比較をしない。**

旧: 鍵は `(科目, 工事項目, 揃えた室名)` の文字列。だから「壁クロス張替」と
「壁ビニルクロス貼替」が別の鍵になり、3 回の一致が低く出ていた
(K-63 R2: 割れた鍵 1,367 のうち 610(45%)が同義語・言い換え)。

新: 鍵は**構造のキー**(工事の種類・部位・状態・材料)と**揃えた室名**。
同じ意味の行は同じ鍵になる。

K-64 側への口(関数名と使い方)::

    from sameness.rows import agreement_key, agreement

    agreement_key(row)                  # 1 行の鍵(ハッシュできる tuple)
    agreement_key(row, with_room=False) # 室を見ない粗い鍵
    agreement([rows1, rows2, rows3])    # 3 回の一致(旧規則の数も一緒に返す)
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping, Sequence

from sameness.keys import StructureKey, _name_text, structure_key
from sameness.normalize import canonical_unit, flatten, room_key
from sameness.terms import Terms, default_terms

#: 室が入っている欄(読み取りの出力と見積の行で名前が違う)。
ROOM_FIELDS = ("室", "場所", "室名", "部屋")


def row_room(row: Mapping[str, Any]) -> str:
    for field in ROOM_FIELDS:
        value = row.get(field)
        if value:
            return room_key(value)
    return ""


def agreement_key(
    row: Mapping[str, Any],
    *,
    with_room: bool = True,
    with_unit: bool = False,
    terms: Terms | None = None,
    key: StructureKey | None = None,
) -> tuple[Any, ...]:
    """1 行の鍵。**構造のキーで作る。品名の文字は入らない。**

    取れなかった欄は `None` のまま鍵に入る(**埋めない**)。工事の種類が取れていない行は
    構造では比べられないので、**揃えた品名そのもの**で鍵を作る(規則 8。字まで同じ行だけが
    同じ鍵になり、違う行は違う鍵のまま残る)。**語彙に無い工事をひとかたまりにしない。**
    """
    sk = key or structure_key(row, terms=terms or default_terms())
    if not sk.工事の種類:
        parts: list[Any] = ["品名", flatten(_name_text(row))]
        if with_room:
            parts.append(row_room(row))
        if with_unit:
            parts.append(canonical_unit(row.get("単位")))
        return tuple(parts)
    parts: list[Any] = ["細目", sk.工事の種類, sk.部位, sk.状態, sk.材料]
    if with_room:
        parts.append(row_room(row))
    if with_unit:
        parts.append(canonical_unit(row.get("単位")))
    return tuple(parts)


def old_agreement_key(row: Mapping[str, Any], *, with_room: bool = True) -> tuple[Any, ...]:
    """**旧規則**(K-63 2 節 4): `(科目, 工事項目, 揃えた室名)` の文字列。比べるために残す。"""
    norm = lambda v: unicodedata.normalize("NFKC", str(v or "")).strip()
    parts: list[Any] = [norm(row.get("科目")), norm(row.get("工事項目") or row.get("工事"))]
    if with_room:
        parts.append(room_key(row_room(row)))
    return tuple(parts)


def agreement(
    runs: Sequence[Sequence[Mapping[str, Any]]],
    *,
    with_room: bool = True,
    terms: Terms | None = None,
) -> dict[str, Any]:
    """何回かの読みの一致。**旧規則と新規則を並べて返す。**

    一致の割合 = 全部の回に出た鍵 / 和集合(K-63 2 節 4 と同じ数え方)。
    """
    terms = terms or default_terms()
    out: dict[str, Any] = {"回数": len(runs), "各回の行数": [len(r) for r in runs]}
    for name, keyer in (("新規則", lambda r: agreement_key(r, with_room=with_room, terms=terms)),
                        ("旧規則", lambda r: old_agreement_key(r, with_room=with_room))):
        per_run = [{keyer(row) for row in rows} for rows in runs]
        union: set[Any] = set().union(*per_run) if per_run else set()
        common = set.intersection(*per_run) if per_run else set()
        out[name] = {
            "鍵の和": len(union),
            "全部の回に出た鍵": len(common),
            "一致の割合": (len(common) / len(union)) if union else None,
            "各回の鍵の数": [len(s) for s in per_run],
        }
    return out
