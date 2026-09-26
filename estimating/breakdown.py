"""見積の行を、公共建築工事内訳書標準書式の段に組む(K-36 改訂版 3 節、2026-09-25)。

段は 4 つ。

======  ==========================================  ====================
段       中身                                          持つもの
======  ==========================================  ====================
種目     直接工事費と共通費                              金額
科目     種目を主要な構成に従い区分                        金額
中科目   科目をさらに区分(**分ける必要があるときだけ**)     金額
細目     工事そのもの                                    数量・単位・単価・金額・摘要
======  ==========================================  ====================

**摘要**には、材種・材質・形状・形式・寸法・工法など、単価に対応する条件を書く。
ここが変われば別の細目になる。

おーちゃんの決まり
------------------
- **科目の一覧を先に固定しない。** 読み取った行にある科目だけを立てる。空の科目は出さない。
- **中科目は、分ける必要があるときだけ立てる**(公共の書式も「区分する必要がない場合は省略」)。

ここでしないこと
----------------
- **数字を作らない。** 単価が無ければ金額は空。科目の金額は細目の金額が全部そろったときだけ足す。
- **科目を推し量らない。** 行に科目が無ければ「科目未定」に集める(黙って捨てない)。
- 判定には触らない。行を並べ替えて段を付けるだけである。

内訳(K-38 原則11、2026-09-25)
------------------------------
**数量は最小の単位(室ごと・1箇所ごと・1個ごと)で出し、内訳を持ったまま足し上げる。**
同じ科目・中科目・名称・摘要・単位の行は 1 つの細目にまとめ、行の ``場所`` ごとの数量を
内訳(``parts``)に残す。**数量の無い場所は「未取得」として残し、0 にしない。**
**未取得が 1 件でもあれば細目の合計は出さない**(金額も出さない)。

仮の判断(`docs/provisional_decisions.md` 7 節)
-----------------------------------------------
- 共通費に入る科目は、科目の名前に `COMMON_COST_WORDS` のどれかが入っているもの。
- 科目未定は直接工事費に置く。
- 中科目は、1 つの科目の中に空でない中科目が 1 つでもあれば立てる。中科目の無い行は
  「中科目なし」の中科目にまとめる(同じ科目の中で段の深さをそろえるため)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

DIRECT_COST = "直接工事費"
COMMON_COST = "共通費"
UNDECIDED_KAMOKU = "科目未定"
NO_MIDDLE = "中科目なし"

#: 共通費に入る科目の名前に含まれる語(仮の判断)。公共の書式の共通費は
#: 共通仮設費・現場管理費・一般管理費等。直接仮設は直接工事費の側に残る。
COMMON_COST_WORDS: tuple[str, ...] = ("共通仮設", "現場管理", "一般管理", "諸経費")


#: 数量の取れていない内訳の印。**0 ではない。**
MISSING = "未取得"


@dataclass(frozen=True)
class Part:
    """細目の内訳 1 つ(最小の単位)。``quantity`` が ``None`` なら未取得。"""

    place: str
    quantity: float | None
    source: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"場所": self.place, "数量": MISSING if self.quantity is None else self.quantity}


@dataclass
class Detail:
    """細目。**工事そのもの。**合計と内訳の両方を持つ。"""

    name: str
    spec: str
    quantity: float | None
    unit: str
    unit_price: float | None = None
    amount: float | None = None
    source: Mapping[str, Any] = field(default_factory=dict)
    parts: tuple[Part, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        """未取得の内訳の場所。"""
        return tuple(p.place for p in self.parts if p.quantity is None)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "名称": self.name,
            "摘要": self.spec,
            "数量": self.quantity,
            "単位": self.unit,
            "単価": self.unit_price,
            "金額": self.amount,
        }
        if self.parts:
            out["内訳"] = [p.as_dict() for p in self.parts]
            out["未取得"] = list(self.missing)
        return out


def _sum_amounts(details: list[Detail]) -> tuple[float | None, int]:
    missing = sum(1 for d in details if d.amount is None)
    if missing or not details:
        return None, missing
    return sum(d.amount for d in details), 0  # type: ignore[misc]


@dataclass
class Middle:
    """中科目。"""

    name: str
    details: list[Detail] = field(default_factory=list)

    @property
    def amount(self) -> float | None:
        return _sum_amounts(self.details)[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "名称": self.name,
            "金額": self.amount,
            "細目": [d.as_dict() for d in self.details],
        }


@dataclass
class Kamoku:
    """科目。中科目が要らなければ ``middles`` は空で、細目を直に持つ。"""

    name: str
    details: list[Detail] = field(default_factory=list)
    middles: list[Middle] = field(default_factory=list)

    def all_details(self) -> list[Detail]:
        return self.details + [d for m in self.middles for d in m.details]

    @property
    def amount(self) -> float | None:
        return _sum_amounts(self.all_details())[0]

    @property
    def details_without_amount(self) -> int:
        return _sum_amounts(self.all_details())[1]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "名称": self.name,
            "金額": self.amount,
            "金額の無い細目": self.details_without_amount,
        }
        if self.middles:
            out["中科目"] = [m.as_dict() for m in self.middles]
        else:
            out["細目"] = [d.as_dict() for d in self.details]
        return out


@dataclass
class Shumoku:
    """種目。"""

    name: str
    kamoku: list[Kamoku] = field(default_factory=list)

    @property
    def amount(self) -> float | None:
        details = [d for k in self.kamoku for d in k.all_details()]
        return _sum_amounts(details)[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "名称": self.name,
            "金額": self.amount,
            "科目": [k.as_dict() for k in self.kamoku],
        }


@dataclass
class Breakdown:
    """内訳書。"""

    shumoku: list[Shumoku] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "書式": "公共建築工事内訳書標準書式に倣う(種目・科目・中科目・細目)",
            "種目": [s.as_dict() for s in self.shumoku],
        }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def shumoku_of(kamoku: str) -> str:
    """科目の名前から種目を決める(仮の判断)。"""
    return COMMON_COST if any(w in kamoku for w in COMMON_COST_WORDS) else DIRECT_COST


def _detail(group: list[Mapping[str, Any]]) -> Detail:
    """同じ細目の行を 1 つにまとめる。**未取得があれば合計も金額も出さない。**"""
    first = group[0]
    price = _number(first.get("単価"))
    parts = tuple(
        Part(place=_text(row.get("場所")), quantity=_number(row.get("数量")), source=row)
        for row in group
    )
    if len(group) == 1 and not _text(first.get("場所")):
        # 場所の無い行 1 つは、内訳を持たない細目のまま(これまでの形)。
        quantity = parts[0].quantity
        amount = _number(first.get("金額"))
        if amount is None and price is not None and quantity is not None:
            amount = price * quantity
        return Detail(
            name=_text(first.get("工事項目")),
            spec=_text(first.get("摘要")),
            quantity=quantity,
            unit=_text(first.get("単位")),
            unit_price=price,
            amount=amount,
            source=first,
        )
    quantity = None
    if all(p.quantity is not None for p in parts):
        quantity = sum(p.quantity for p in parts)  # type: ignore[misc]
    amount = price * quantity if price is not None and quantity is not None else None
    return Detail(
        name=_text(first.get("工事項目")),
        spec=_text(first.get("摘要")),
        quantity=quantity,
        unit=_text(first.get("単位")),
        unit_price=price,
        amount=amount,
        source=first,
        parts=parts,
    )


def build_breakdown(rows: Iterable[Mapping[str, Any]]) -> Breakdown:
    """行(``科目`` ``中科目`` ``工事項目`` ``摘要`` ``数量`` ``単位`` ``単価`` ``金額``)を段に組む。

    科目と種目の並びは、行に**初めて出てきた順**(工程の順に並んだ行ならその順になる)。
    """
    kamoku_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        name = _text(row.get("科目")) or UNDECIDED_KAMOKU
        kamoku_rows.setdefault(name, []).append(row)

    shumoku: dict[str, Shumoku] = {}
    for name, members in kamoku_rows.items():
        kamoku = Kamoku(name=name)
        needs_middle = any(_text(r.get("中科目")) for r in members)
        middles: dict[str, Middle] = {}
        grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
        for row in members:
            key = (
                _text(row.get("中科目")) if needs_middle else "",
                _text(row.get("工事項目")),
                _text(row.get("摘要")),
                _text(row.get("単位")),
            )
            grouped.setdefault(key, []).append(row)
        for (middle_name, _, _, _), group in grouped.items():
            detail = _detail(group)
            if needs_middle:
                middle = middle_name or NO_MIDDLE
                middles.setdefault(middle, Middle(name=middle)).details.append(detail)
            else:
                kamoku.details.append(detail)
        kamoku.middles = list(middles.values())
        group = shumoku_of(name) if name != UNDECIDED_KAMOKU else DIRECT_COST
        shumoku.setdefault(group, Shumoku(name=group)).kamoku.append(kamoku)

    ordered = [shumoku[k] for k in (DIRECT_COST, COMMON_COST) if k in shumoku]
    return Breakdown(shumoku=ordered)
