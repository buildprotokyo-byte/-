"""K-73 作業2: 採点に内訳の足し上げを入れる(原則 11)。**正解を開かない層**と、正解の行と組を当てる層。

基準は `docs/k73_flags_sum_criteria.md` 3 節(測る前にコミット)。

原則 11(K-38、`estimating/breakdown.py` の「内訳」の節): 数量は最小の単位(室ごと・1 箇所ごと・1 個ごと)で出し、
内訳を持ったまま足し上げる。**数量の無い場所は「未取得」として残し、0 にしない。未取得が 1 件でもあれば合計は出さない。**

- `group_rows(rows)` … 同じ鍵(構造のキー+科目+新の単位。室は入れない)の行を 1 組にまとめる。まとめる前の行は内訳に残す。
- `Group.total` … 内訳が全部数量を持つときだけ合計。1 つでも未取得なら `None`(状態は「未取得あり」)。
- `group_line(group)` … 組を、今の突き合わせ(`estimating.scoring.score_lines`)にそのまま渡せる 1 行にする。
- `grain_upgrade(...)` … K-66 の `△粒度` の格上げを、**足し上げのあとで**効かせる(合計と位置が合ったときだけ `○`)。
- `flag_view(draft, how)` … 旗の部品の数を採点用の行に入れる見方(「埋める」「足す」)。**出力そのものは変えない。**
- `output_counts(rows)` … 正解を使わずに数えられる数。

数量の許容と単位の同値はここに置かない(`sameness.quantity` だけ)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from sameness.normalize import flatten
from sameness.quantity import new_unit

#: 組の合計の状態。
TOTAL = "合計あり"
HAS_MISSING = "未取得あり"
"""内訳に数量の無い行が 1 つでもある。**合計は出さない(0 として足さない)。**"""

VIEWS = ("旗オフ", "旗オン(埋める)", "旗オン(足す)")


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def row_key(row: Mapping[str, Any], position: int, *, terms: Any = None) -> tuple[Any, ...]:
    """足し上げの鍵。**室(場所)は入れない。**工事の種類が取れなければ揃えた品名、それも無ければ 1 行で 1 組。"""
    from sameness.keys import structure_key

    unit = new_unit(row.get("単位"))
    key = structure_key(dict(row), terms=terms)
    if key.工事の種類:
        return ("構造", key.工事の種類, key.部位, key.状態, key.材料, key.科目, unit)
    name = flatten(row.get("工事項目") or row.get("工事") or "")
    if name:
        return ("品名", name, key.科目, unit)
    return ("1行", position)


@dataclass
class Group:
    key: tuple[Any, ...]
    rows: list[Mapping[str, Any]] = field(default_factory=list)
    positions: list[int] = field(default_factory=list)

    @property
    def missing(self) -> int:
        return sum(1 for r in self.rows if _number(r.get("数量")) is None)

    @property
    def total(self) -> float | None:
        """内訳が全部数量を持つときだけ合計。**未取得が 1 つでもあれば None**(0 として足さない)。"""
        if not self.rows or self.missing:
            return None
        return round(sum(_number(r.get("数量")) or 0.0 for r in self.rows), 6)

    @property
    def status(self) -> str:
        return HAS_MISSING if self.missing else TOTAL

    def parts(self) -> list[dict[str, Any]]:
        """内訳(まとめる前の行)。行の番号・場所・数量・単位だけ。"""
        return [{"行の番号": p, "場所": r.get("場所", ""), "数量": r.get("数量"), "単位": r.get("単位", "")}
                for p, r in zip(self.positions, self.rows)]


def group_rows(rows: Sequence[Mapping[str, Any]], *, terms: Any = None) -> list[Group]:
    """同じ鍵の行を 1 組にまとめる。組の並びは、組の最初の行の並び。"""
    from sameness.terms import default_terms

    terms = terms or default_terms()
    groups: dict[tuple[Any, ...], Group] = {}
    for n, row in enumerate(rows):
        key = row_key(row, n, terms=terms)
        g = groups.setdefault(key, Group(key=key))
        g.rows.append(row)
        g.positions.append(n)
    return list(groups.values())


def group_line(group: Group) -> dict[str, Any]:
    """組を 1 行にする。名前・科目・区分・単位は内訳の先頭の行、数量は組の合計(未取得ありなら None)。"""
    head = dict(group.rows[0])
    items: list[str] = []
    for r in group.rows:
        items += [i for i in r.get("項目") or () if i not in items]
    head.update({"数量": group.total, "項目": items, "合計の状態": group.status, "内訳": group.parts(),
                 "内訳の行の番号": list(group.positions), "場所": "" if len(group.rows) > 1 else head.get("場所", "")})
    return head


def merge(groups: Sequence[Group]) -> Group:
    """格上げで集めた組を 1 つにまとめる(内訳は全部残す)。"""
    out = Group(key=("格上げ",) + tuple(g.key for g in groups))
    for g in groups:
        out.rows += g.rows
        out.positions += g.positions
    return out


def grain_upgrade(gold_fields: Mapping[str, Any], candidates: Sequence[Group], *, gold_quantity: Any,
                  gold_unit: Any, nature: Any, terms: Any = None) -> dict[str, Any]:
    """K-66 の `△粒度` の格上げを、足し上げのあとで効かせる(基準 3.2 の 2)。

    `candidates` = まだ当たっていない組。正解の行と `△粒度` で、**科目の段が `○`(位置が合う)**の組を全部集めて合計する。
    合計が性質ごとの線で「合う」とき(性質 1〜3)だけ `promote()` で `○` にする。
    返す: `{"判定": Verdict または None, "組": [番号], "合計": 数 / None, "理由": 文}`。
    """
    from sameness.judge import GRAIN, SAME, compare, compare_keys
    from sameness.keys import structure_key
    from sameness.quantity import MATCH, quantity_verdict
    from sameness.terms import default_terms

    terms = terms or default_terms()
    gold_key = structure_key(dict(gold_fields), terms=terms)
    picked: list[int] = []
    verdict = None
    for n, g in enumerate(candidates):
        v = compare_keys(structure_key(dict(g.rows[0]), terms=terms), gold_key, terms=terms)
        if v.value != GRAIN:
            continue
        place = compare({"科目": g.rows[0].get("科目") or ""}, {"科目": gold_fields.get("科目") or ""},
                        level="科目", terms=terms)
        if place.value != SAME:
            continue
        picked.append(n)
        verdict = verdict or v
    if not picked:
        return {"判定": None, "組": [], "合計": None, "理由": "△粒度で位置の合う組が無い"}
    merged = merge([candidates[n] for n in picked])
    if nature not in (1, 2, 3):
        return {"判定": None, "組": picked, "合計": merged.total, "理由": f"性質 {nature} は数量で格上げしない"}
    if merged.total is None:
        return {"判定": None, "組": picked, "合計": None, "理由": HAS_MISSING + "(合計を出さないので格上げしない)"}
    q = quantity_verdict(merged.total, gold_quantity, merged.rows[0].get("単位"), unit_b=gold_unit, side="新",
                         nature=nature)
    promoted = verdict.promote(totals_match=q.value == MATCH, place_match=True)
    return {"判定": promoted if promoted.hit else None, "組": picked, "合計": merged.total,
            "理由": promoted.reason if promoted.hit else f"合計が線の外({q.value})"}


# --------------------------------------------------------------------------- 旗の見方(採点用。出力は変えない)


def _flag_entries(draft: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    parts = ((draft.get("旗の部品") or {}).get("部品")) or {}
    return [(name, a) for name, part in parts.items() for a in part.get("足したもの") or ()
            if a.get("数量が増える") and _number(a.get("数量")) is not None]


def flag_items(draft: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """旗が足したものを、理解の項目と同じ形(id・状態・確度)で返す(理由と確度の計算に足すため)。"""
    return {a["id"]: {"id": a["id"], "状態": a.get("状態"), "確度": a.get("確度"), "検算": [],
                      "根拠の種類": "旗の部品", "理由": ""} for _, a in _flag_entries(draft)}


def flag_view(draft: Mapping[str, Any], how: str) -> dict[str, Any]:
    """採点用の行(基準 2.1)。`how` は「旗オフ」「旗オン(埋める)」「旗オン(足す)」。**元の行は書き換えない(写しを返す)。**"""
    from draft import flags as fp

    rows = [dict(r) for r in draft["組み立て"]["内訳の行"]]
    counts: dict[str, Any] = {"見方": how}
    if how == "旗オフ":
        return {"行": rows, "数": counts}
    entries = _flag_entries(draft)
    counts["数量が増える所(数量あり)"] = len(entries)
    if how == "旗オン(足す)":
        parts = ((draft.get("旗の部品") or {}).get("部品")) or {}
        added = fp.increase_rows(parts)
        counts["後ろに足した行"] = len(added)
        return {"行": rows + added, "数": counts}
    if how != "旗オン(埋める)":
        raise ValueError(f"見方は {VIEWS} のどれか: {how!r}")
    owner: dict[str, int] = {}
    for n, r in enumerate(rows):
        for i in r.get("項目") or ():
            owner.setdefault(i, n)
    linked: dict[int, list[tuple[str, Mapping[str, Any]]]] = {}
    unlinked: list[tuple[str, Mapping[str, Any]]] = []
    spanning = 0
    for name, a in entries:
        targets = {owner[i] for i in a.get("理解の項目") or () if i in owner}
        if len(targets) > 1:
            spanning += 1  # 2 行以上にまたがる: 使わない(二重に数えない)
            continue
        if not targets:
            unlinked.append((name, a))
            continue
        linked.setdefault(targets.pop(), []).append((name, a))
    filled = kept_qty = mixed_parts = mixed_units = 0
    for n, found in linked.items():
        row = rows[n]
        if row.get("数量") is not None:
            kept_qty += 1  # 数量のある行は書き換えない
            continue
        names = {name for name, _ in found}
        units = {new_unit(a.get("単位")) for _, a in found}
        if len(names) > 1:
            mixed_parts += 1
            continue
        if len(units) != 1 or (new_unit(row.get("単位")) and new_unit(row.get("単位")) not in units):
            mixed_units += 1
            continue
        row["数量"] = round(sum(_number(a["数量"]) or 0.0 for _, a in found), 6)
        if not row.get("単位"):
            row["単位"] = found[0][1].get("単位", "")
        a0 = found[0][1]
        row["項目"] = list(row.get("項目") or ()) + [a["id"] for _, a in found]
        row["メモ"] = ((row.get("メモ") or "") + f" 旗で埋めた({names.pop()}・{a0.get('状態')}・確度{a0.get('確度')})").strip()
        row["旗で埋めた"] = True
        filled += 1
    added = []
    for name, a in unlinked:
        added.append({"科目": "", "区分": "", "工事項目": a.get("工事", ""), "摘要": "", "場所": a.get("場所", ""),
                      "数量": a["数量"], "単位": a.get("単位", ""), "項目": [a["id"], *(a.get("理解の項目") or ())],
                      "メモ": f"旗の部品「{name}」が足した({a.get('状態')}・確度{a.get('確度')})"})
    counts.update({"数量が入った行": filled, "結べずに後ろに足した行": len(added),
                   "2 行以上にまたがって使わなかった足したもの": spanning,
                   "数量のある行に結ばれた(書き換えない)": kept_qty,
                   "部品をまたいで使わなかった行": mixed_parts, "単位が合わず使わなかった行": mixed_units})
    return {"行": rows + added, "数": counts}


# --------------------------------------------------------------------------- 出力側の数(正解を使わない)


def output_counts(rows: Sequence[Mapping[str, Any]], *, terms: Any = None) -> dict[str, Any]:
    """足し上げの出力側の数(基準 3.5)。"""
    groups = group_rows(rows, terms=terms)
    multi = [g for g in groups if len(g.rows) > 1]
    totals = [g for g in groups if g.status == TOTAL]
    missing = [g for g in groups if g.status == HAS_MISSING]
    return {
        "行の数": len(rows),
        "数量のある行": sum(1 for r in rows if _number(r.get("数量")) is not None),
        "組の数": len(groups),
        "2 行以上の組": len(multi),
        "組に入った行の最大": max((len(g.rows) for g in groups), default=0),
        "2 行以上の組に入った行": sum(len(g.rows) for g in multi),
        "合計が出た組": len(totals),
        "うち 2 行以上を足した組": sum(1 for g in totals if len(g.rows) > 1),
        "未取得ありの組": len(missing),
        "うち数量のある行も入っている組": sum(1 for g in missing if g.missing < len(g.rows)),
        "未取得ありの組に入った数量のある行": sum(len(g.rows) - g.missing for g in missing),
    }


def decoy_rows(rows: Sequence[Mapping[str, Any]], seed: int = 73) -> list[dict[str, Any]]:
    """囮(基準 3.4): 数量を、同じ新の単位の行のあいだで入れ替える。**未取得の位置は変えない。**"""
    import random

    out = [dict(r) for r in rows]
    by_unit: dict[str, list[int]] = {}
    for n, r in enumerate(out):
        if _number(r.get("数量")) is not None:
            by_unit.setdefault(new_unit(r.get("単位")), []).append(n)
    rng = random.Random(seed)
    for idx in by_unit.values():
        values = [out[n]["数量"] for n in idx]
        rng.shuffle(values)
        for n, v in zip(idx, values):
            out[n]["数量"] = v
    return out


def all_reasons(group_line_row: Mapping[str, Any], members: Sequence[Mapping[str, Any]],
                reasons_of: Callable[[Mapping[str, Any], int], list[str]]) -> list[str]:
    """組の理由: **内訳の全部の行に理由があるときだけ**、その和(基準 3.2 の 4。1 行でも理由が無ければ空)。"""
    positions = group_line_row.get("内訳の行の番号") or []
    found: list[str] = []
    for member, pos in zip(members, positions):
        r = reasons_of(member, pos)
        if not r:
            return []
        found += [x for x in r if x not in found]
    return found
