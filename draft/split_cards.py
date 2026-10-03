"""割れた値を人に並べて見せるカード(K-70 作業 3。K-68 B の次の 1 周)。**AI は呼ばない。**

基準は `docs/k70_split_value_cards_criteria.md`(測る前にコミットした)。

考え(おーちゃん): 質問を増やしても未確定が減らないのは、人が選べる形になっていないから。
**3 回の読みで割れた値を、そのまま並べて、人に選んでもらう。**

- 割れの鍵は K-66 の `agreement_key`(K-68 B 周 1 の新規則)。名前だけの違いは鍵が同じになるのでカードにしない。
- 割れた鍵を 1 つずつ、`数量` → `状態` → `室` → `部位` → `有無だけの割れ` のどれか 1 つに入れる。
- 選択肢 = 3 回の値(同じ文字は 1 つ)+ 機械の値 + 縮尺で換算した値 +「どれでもない」「分からない」。
  **どれが何回目の読みか・機械の値かは画面に出さない**(`値の出どころ` の欄にだけ残す)。
- **未取得を値にしない。**数量が無い・0 以下・取れていない欄からは選択肢を作らない。
- カードにできなかった組は、理由と一緒に残す(**黙って落とさない**)。
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from draft import uncertainty as unc
from draft.stages import UNDECIDED, UNKNOWN, nfkc

DIMENSIONS = ("数量", "状態", "室", "部位")
PRESENCE_ONLY = "有無だけの割れ"
#: 単位の書き方だけが違う割れ(「か所」と「箇所」など)。**K-66 の辞書で揃えると値が同じなので、カードにしない。**
#: 1 回目の測りでカードになっていた(基準 (a) の「名前だけの違いはカードにしない」に合わせて直した)。
UNIT_NAME_ONLY = "名前だけの違い(単位の書き方)"
#: K-71 作業 2 (d): 数が同じで単位だけ違う(`sameness.quantity.new_unit` で揃えると 1 つになる)数量のカード。外す。
UNIT_DROPPED = "単位の同値で外した"
def spelling(text: Any) -> str:
    """室の名前を K-66 の辞書の室の鍵(`sameness.rows.row_room`。横棒の字・括弧・空白などを揃える)にした形。

    2 回目の測りで、目で見て、横棒の字だけ違う室の書き方が別の選択肢になっていたのを見つけた。
    鍵(K-66)では同じ室なのに、選択肢を元の文字で分けていた。**選択肢も K-66 の室の鍵でまとめる。**
    """
    from sameness.rows import row_room

    return row_room({"場所": nfkc(text)})
#: カードにできなかった理由(この 3 つだけ。上から順に見る)。
REASONS = ("回の中で対応が 1 つに決まらない", "値が 1 つしか無い", "切り抜きが無い")

NONE_OF_THESE = "どれでもない(現地・設計者に確認する)"
DONT_KNOW = "分からない"
#: 選択肢の最後に必ず付ける 2 つ(この順)。
TAIL = (NONE_OF_THESE, DONT_KNOW)

#: 値の出どころ(画面には出さない)。
SOURCES = ("3回の値", "機械の値", "縮尺で換算した値")

#: `細目` の鍵 ``("細目", 工事の種類, 部位, 状態, 材料, 室)`` の中の位置。
_INDEX = {"部位": 2, "状態": 3}
_PREFIX = {"部位": "BU_", "状態": "JO_"}

#: 室を写すときの欄(`sameness.rows.ROOM_FIELDS` と同じ欄)。
ROOM_FIELDS = ("室", "場所", "室名", "部屋")
#: 状態・部位を写すときの欄(鍵が読む欄から室の欄を除いたもの)。
SHAPE_FIELDS = tuple(f for f in unc._KEY_FIELDS if f not in ROOM_FIELDS)

#: 切り抜きの数の上限(回によって位置が違うとき、重ならない位置ごとに 1 枚)。
MAX_CROPS = 3


def _missing(text: Any) -> bool:
    return nfkc(text) in ("", UNDECIDED, UNKNOWN, "None")


def coarse_key(key: tuple[Any, ...], dim: str) -> tuple[Any, ...] | None:
    """鍵から ``dim`` の欄を外した「粗い鍵」。外せない鍵(品名の鍵の状態・部位)は None。"""
    if dim == "室":
        return tuple(key[:-1]) + ("*",)
    if key[0] != "細目" or dim not in _INDEX:
        return None
    k = list(key)
    k[_INDEX[dim]] = "*"
    return tuple(k)


def quantity_text(value: float, unit: str) -> str:
    """数量の選択肢の書き方(K-68 B 周 3 と同じ)。**単位は K-66 の辞書で揃える**(「か所」→「箇所」)。"""
    from sameness.quantity import canonical_unit

    return f"{value:g}{canonical_unit(unit) or nfkc(unit)}"


def _unit_name_only(rows_by_run: Sequence[Sequence[Mapping[str, Any]]]) -> bool:
    """3 回とも数量があり、単位を K-66 の辞書で揃えると単位も数量(許容差の中)も合う。"""
    from sameness import quantity_verdict
    from sameness.quantity import canonical_unit

    if not rows_by_run or any(not rows for rows in rows_by_run):
        return False
    if any(it.get("数量") is None for rows in rows_by_run for it in rows):
        return False
    units = {canonical_unit(it.get("単位")) for rows in rows_by_run for it in rows}
    if len(units) != 1:
        return False
    unit = next(iter(units))
    sums = [float(sum(it["数量"] for it in rows)) for rows in rows_by_run]
    return all(quantity_verdict(sums[0], v, unit).value != "違う" for v in sums[1:])


def _qty(value: Any) -> float | None:
    """使える数量。**無い・0 以下・数でないものは None**(0 を作らない)。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def dim_value(item: Mapping[str, Any], dim: str) -> str | None:
    """行の ``dim`` の値(画面に出す文字)。**取れていなければ None**(値にしない)。"""
    if dim == "数量":
        q = _qty(item.get("数量"))
        return None if q is None else quantity_text(q, nfkc(item.get("単位")))
    if dim == "室":
        for f in ROOM_FIELDS:
            if not _missing(item.get(f)):
                return nfkc(item.get(f))
        return None
    key = unc.agreement_item_key(item)
    if key[0] != "細目":
        return None
    raw = key[_INDEX[dim]]
    if raw is None or _missing(raw):
        return None
    text = str(raw)
    return text[len(_PREFIX[dim]):] if text.startswith(_PREFIX[dim]) else text


def _box(item: Mapping[str, Any]) -> list[float] | None:
    box = item.get("囲み")
    if not box or len(box) != 4 or item.get("ページ") in (None, ""):
        return None
    try:
        return [float(v) for v in box]
    except (TypeError, ValueError):
        return None


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    ax0, ax1, ay0, ay1 = min(a[0], a[2]), max(a[0], a[2]), min(a[1], a[3]), max(a[1], a[3])
    bx0, bx1, by0, by1 = min(b[0], b[2]), max(b[0], b[2]), min(b[1], b[3]), max(b[1], b[3])
    w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    h = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = w * h
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def crops_of(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """組の行の切り抜き(ページと位置)。**位置は幅 2000 画素の画像の座標のまま**(描くときに PDF の点へ戻す)。

    同じページで重なる位置(重なりの割合 0.3 以上)は 1 つにまとめる。最大 3 枚。
    """
    out: list[dict[str, Any]] = []
    for it in rows:
        box = _box(it)
        if box is None:
            continue
        page = int(it["ページ"])
        if any(c["ページ"] == page and _overlap(c["位置"], box) >= 0.3 for c in out):
            continue
        out.append({"ページ": page, "位置": box, "座標": "画像の画素(幅 2000)"})
        if len(out) >= MAX_CROPS:
            break
    return out


def card_id(dim: str, group_key: tuple[Any, ...]) -> str:
    """カードの鍵。**名前を含めない**(結果の JSON に名前を書かないため)。"""
    return f"割れ:{dim}:" + hashlib.sha1(repr(group_key).encode("utf-8")).hexdigest()[:10]


def _unit_ok(row_units: set[str], unit: Any) -> bool:
    from draft.questioning import unit_key

    u = unit_key(unit)
    return not row_units or not u or u in row_units


def find_splits(runs: Mapping[str, Sequence[Mapping[str, Any]]], *,
                machine: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]] | None = None,
                scale: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]] | None = None,
                match: str | None = None, unit_equivalence: bool = False,
                threshold: float | None = None) -> dict[str, Any]:
    """3 回の読みの割れた鍵を入れ先に分け、カードにできる組をカードの形にする。**値は 1 つも書き換えない。**

    ``runs`` は回の名前 → 項目の並び(順が回の順)。``machine``・``scale`` は回の名前 → 項目 id → 値の並び
    (`draft.questioning.machine_values_from`・`scale_values_from` の形)。
    """
    if threshold is None:
        from draft.position_match import THRESHOLD as threshold
    names = list(runs)
    lists = [list(runs[n]) for n in names]
    split = unc.readings_split(lists, rule="新")
    split_keys: set[tuple[Any, ...]] = set(split["割れた鍵"])
    per_run: list[dict[tuple[Any, ...], list[Mapping[str, Any]]]] = []
    for run in lists:
        g: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for it in run:
            g.setdefault(unc.agreement_item_key(it), []).append(it)
        per_run.append(g)
    union: set[tuple[Any, ...]] = set().union(*(g.keys() for g in per_run)) if per_run else set()

    assigned: dict[tuple[Any, ...], str] = {}
    groups: list[dict[str, Any]] = []

    # 数量: 3 回すべてにある割れた鍵。
    for key in sorted(split_keys, key=repr):
        if all(key in g for g in per_run):
            if _unit_name_only([g[key] for g in per_run]):
                assigned[key] = UNIT_NAME_ONLY
                continue
            assigned[key] = "数量"
            groups.append({"次元": "数量", "組の鍵": key, "鍵たち": [key]})
    # 状態・室・部位: 粗い鍵が同じで、2 回以上に行があり、その欄だけが違う。
    for dim in ("状態", "室", "部位"):
        coarse: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {}
        for key in union:
            c = coarse_key(key, dim)
            if c is not None:
                coarse.setdefault(c, set()).add(key)
        for c in sorted(coarse, key=repr):
            keys = coarse[c]
            if len(keys) < 2:
                continue
            free = sorted((k for k in keys if k in split_keys and k not in assigned), key=repr)
            if not free:
                continue
            present = sum(1 for g in per_run if any(k in g for k in keys))
            if present < 2:
                continue
            for k in free:
                assigned[k] = dim
            groups.append({"次元": dim, "組の鍵": c, "鍵たち": sorted(keys, key=repr), "割れた鍵たち": free})
    presence = [k for k in split_keys if k not in assigned]

    cards: list[dict[str, Any]] = []
    not_made: list[dict[str, Any]] = []
    unit_dropped: list[dict[str, Any]] = []
    key_dest: dict[tuple[Any, ...], str] = {}
    positional: list[list[list[Mapping[str, Any]]]] = []
    matching: dict[str, Any] = {"使った組": 0, "鎖": 0, "鎖の線": {"回の行を 2 行以上持つ鎖": 0, "2 つ以上の鎖に入った行": 0},
                                "行の行き先": {}, "1 行以下の組のカードで位置も重なる": 0,
                                "1 行以下の組のカードで位置が重ならない": 0}
    for grp in groups:
        dim = grp["次元"]
        keys = set(grp["鍵たち"])
        counted = grp.get("割れた鍵たち") or grp["鍵たち"]
        rows_by_run = [sorted((it for k in keys for it in g.get(k, ())), key=lambda it: str(it.get("id")))
                       for g in per_run]
        cid = card_id(dim, grp["組の鍵"])
        if not any(len(rows) >= 2 for rows in rows_by_run):
            made = _make_card(dim, cid, [rows[0] if rows else None for rows in rows_by_run], names,
                              machine, scale, unit_equivalence)
            if match == "位置" and "カード" in made:
                from draft import position_match as pm

                present = [rows[0] for rows in rows_by_run if rows]
                ok = all(pm.overlap(present[0], o) >= pm.THRESHOLD for o in present[1:])
                matching["1 行以下の組のカードで位置も重なる" if ok else "1 行以下の組のカードで位置が重ならない"] += 1
            _settle(made, cid, dim, counted, cards, not_made, unit_dropped, key_dest, set(counted))
            continue
        if match != "位置":
            not_made.append({"鍵": cid, "次元": dim, "理由": REASONS[0], "割れた鍵の数": len(counted)})
            continue
        from draft import position_match as pm

        matching["使った組"] += 1
        positional.append(rows_by_run)
        got = pm.chains(rows_by_run, threshold=threshold)
        check = pm.check_chains(rows_by_run, got["鎖"])
        for k2, v in check.items():
            matching["鎖の線"][k2] += v
        for dest in got["行の行き先"].values():
            matching["行の行き先"][dest] = matching["行の行き先"].get(dest, 0) + 1
        matching["鎖"] += len(got["鎖"])
        # 割れた鍵ごとに、行の行き先を集める(上の表の上のものを書く)。
        rank: dict[tuple[Any, ...], int] = {}
        order_of = {"カード": 0, UNIT_DROPPED: 1, REASONS[1]: 2, REASONS[2]: 3,
                    pm.TIED: 4, pm.NO_PARTNER: 5}
        def note(key: tuple[Any, ...], dest: str) -> None:
            if key in counted_set and (key not in rank or order_of[dest] < rank[key]):
                rank[key] = order_of[dest]
        counted_set = set(counted)
        for (r, j), dest in got["行の行き先"].items():
            if dest != "鎖":
                note(unc.agreement_item_key(rows_by_run[r][j]), dest)
        for chain in got["鎖"]:
            picked = [rows_by_run[r][j] if j is not None else None for r, j in enumerate(chain)]
            ids = tuple(str(it["id"]) if it is not None else "" for it in picked)
            ccid = card_id(dim, (grp["組の鍵"], ids))
            made = _make_card(dim, ccid, picked, names, machine, scale, unit_equivalence)
            chain_keys = {unc.agreement_item_key(it) for it in picked if it is not None}
            dest = "カード" if "カード" in made else (UNIT_DROPPED if "外した" in made else made["理由"])
            for k2 in chain_keys:
                note(k2, dest)
            if "カード" in made:
                made["カード"]["割れた鍵の数"] = len(chain_keys & counted_set)
                made["カード"]["組"] = cid
                cards.append(made["カード"])
            elif "外した" in made:
                unit_dropped.append({"鍵": ccid, "組": cid, "次元": dim})
        inv = {v: k for k, v in order_of.items()}
        per_reason: dict[str, int] = {}
        for k2 in counted:
            dest = inv[rank[k2]] if k2 in rank else pm.NO_PARTNER
            key_dest[k2] = dest
            if dest not in ("カード", UNIT_DROPPED):
                per_reason[dest] = per_reason.get(dest, 0) + 1
        for reason, n in per_reason.items():
            not_made.append({"鍵": cid, "次元": dim, "理由": reason, "割れた鍵の数": n})
    return {
        "割れた鍵": len(split_keys), "鍵の和": split["鍵の和"], "全部の回に出た鍵": split["全部の回に出た鍵"],
        "入れ先ごとの鍵": {**{d: sum(1 for v in assigned.values() if v == d) for d in DIMENSIONS},
                     UNIT_NAME_ONLY: sum(1 for v in assigned.values() if v == UNIT_NAME_ONLY),
                     PRESENCE_ONLY: len(presence)},
        "カード": cards, "カードにできなかった組": not_made,
        "単位の同値で外したカード": unit_dropped,
        "割れた鍵の行き先": _count_dest(key_dest),
        "対応づけ": matching if match == "位置" else None,
        "_位置で対応づけた組": positional,
        "名前だけの違い(辞書で解いた)": name_only(per_run, split_keys),
        "_回": names,
    }


def _count_dest(key_dest: Mapping[tuple[Any, ...], str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in key_dest.values():
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _settle(made: Mapping[str, Any], cid: str, dim: str, counted: Sequence[Any], cards: list, not_made: list,
            unit_dropped: list, key_dest: dict, keys: set) -> None:
    """回の中で 1 行以下の組(前の周と同じ)の行き先を書く。"""
    if "カード" in made:
        made["カード"]["割れた鍵の数"] = len(counted)
        made["カード"]["組"] = cid
        cards.append(made["カード"])
        dest = "カード"
    elif "外した" in made:
        unit_dropped.append({"鍵": cid, "組": cid, "次元": dim})
        dest = UNIT_DROPPED
    else:
        not_made.append({"鍵": cid, "次元": dim, "理由": made["理由"], "割れた鍵の数": len(counted)})
        dest = made["理由"]
    for k in keys:
        key_dest[k] = dest


def same_by_new_unit(options: Sequence[str]) -> list[str]:
    """数量の選択肢を `sameness.quantity.new_unit` の単位と数で揃え、同じになるものを先に出た 1 つにまとめる。

    **単位の同値はここに表を置かない**(`new_unit` だけを使う)。数は等しいときだけ同じ(許容は使わない)。
    """
    from sameness.quantity import new_unit

    seen: dict[tuple[float, str], str] = {}
    out: list[str] = []
    for o in options:
        m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", nfkc(o))
        sig = (float(m.group(1)), new_unit(m.group(2).strip())) if m else (float("nan"), o)
        if m and sig in seen:
            continue
        seen[sig] = o
        out.append(o)
    return out


def _make_card(dim: str, cid: str, picked: Sequence[Mapping[str, Any] | None], names: Sequence[str],
               machine: Any, scale: Any, unit_equivalence: bool) -> dict[str, Any]:
    """回ごとに 1 行まで(無ければ None)の行から、カードを作る。``{"カード": …}`` か ``{"理由": …}`` か ``{"外した": …}``。"""
    sources: dict[str, list[dict[str, Any]]] = {}
    order: dict[str, tuple[float, str]] = {}
    row_of: dict[str, str] = {}
    rows: list[Mapping[str, Any]] = []
    for name, it in zip(names, picked):
        if it is None:
            continue
        rows.append(it)
        row_of[name] = it["id"]
        v = dim_value(it, dim)
        if v is None:
            continue
        if dim == "室":
            # K-66 の室の鍵で同じ室は 1 つの選択肢にまとめる(先に出た書き方で見せる)。
            v = next((seen for seen in sources if spelling(seen) == spelling(v)), v)
        sources.setdefault(v, []).append({"出どころ": "3回の値", "辿る": {
            "回": name, "項目": it["id"], "ページ": it.get("ページ")}})
        order[v] = (_qty(it.get("数量")) or 0.0, v) if dim == "数量" else (0.0, v)
    if dim == "数量":
        from draft.questioning import unit_key

        units = {unit_key(r.get("単位")) for r in rows if unit_key(r.get("単位"))}
        for source, table in (("機械の値", machine), ("縮尺で換算した値", scale)):
            for name, it in zip(names, picked):
                if it is None:
                    continue
                for m in ((table or {}).get(name) or {}).get(it["id"], ()):
                    q = _qty(m.get("値"))
                    if q is None or not _unit_ok(units, m.get("単位")):
                        continue
                    unit = nfkc(m.get("単位")) or nfkc(it.get("単位"))
                    text = quantity_text(q, unit)
                    sources.setdefault(text, []).append({"出どころ": source, "辿る": {
                        "回": name, "項目": it["id"], **dict(m.get("辿る") or {})}})
                    order[text] = (q, text)
    if len(sources) < 2:
        return {"理由": REASONS[1]}
    values = sorted(sources, key=lambda t: order[t])
    if dim == "数量" and unit_equivalence:
        kept = same_by_new_unit(values)
        if len(kept) < 2:
            return {"外した": UNIT_DROPPED}
        if len(kept) < len(values):
            # 揃えると同じになる選択肢は、先に出た書き方 1 つにまとめる(出どころは足し合わせる)。
            from sameness.quantity import new_unit

            def sig(o: str) -> tuple[float, str]:
                m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", nfkc(o))
                return (float(m.group(1)), new_unit(m.group(2).strip())) if m else (float("nan"), o)

            merged: dict[str, list[dict[str, Any]]] = {}
            for o in values:
                head = next((k for k in kept if sig(k) == sig(o)), o)
                merged.setdefault(head, []).extend(sources[o])
            sources = merged
            values = kept
    crops = crops_of(rows)
    if not crops:
        return {"理由": REASONS[2]}
    head = rows[0]
    what = nfkc(head.get("何")) or nfkc(head.get("工事"))
    question = {
        "数量": f"「{what}」の数量は、どれですか",
        "状態": f"「{what}」は、どの工事ですか(撤去・新設など)",
        "室": f"「{what}」は、どの室の工事ですか",
        "部位": f"「{what}」は、どの部位の工事ですか",
    }[dim]
    return {"カード": {
        "鍵": cid, "次元": dim, "問い": question,
        "選択肢": [*values, *TAIL],
        "値の出どころ": sources,
        "行": row_of,
        "切り抜き": crops,
        "見る所": sorted({c["ページ"] for c in crops}),
        "推奨": None, "数字の入力": False, "自由記述": False, "見込み": None,
    }}


def name_only(per_run: Sequence[Mapping[tuple[Any, ...], Sequence[Mapping[str, Any]]]],
              split_keys: set[tuple[Any, ...]]) -> int:
    """全部の回に出て値も合う(割れていない)鍵のうち、名前の文字が回ごとに違うもの。**カードにしない。**"""
    count = 0
    common = set.intersection(*(set(g) for g in per_run)) if per_run else set()
    for key in common - split_keys:
        names = {tuple(sorted(nfkc(it.get("何")) or nfkc(it.get("工事")) for it in g[key])) for g in per_run}
        if len(names) > 1:
            count += 1
    return count


def real_options(card: Mapping[str, Any]) -> list[str]:
    return [o for o in card.get("選択肢") or () if o not in TAIL]


# --- 並べ方 ---------------------------------------------------------------


def _decide(it: dict[str, Any], text: str) -> None:
    """人の回答の印(K-64 周 4 と同じ決め方)。"""
    it["人の回答"] = text
    it["状態"] = "観測"
    it["確度"] = "高"
    it["根拠の種類"] = "人の回答"
    it.pop("確度の上限", None)


def apply(runs: Mapping[str, list[dict[str, Any]]], cards: Sequence[Mapping[str, Any]],
          answers: Mapping[str, str]) -> dict[str, Any]:
    """カードの答えを 3 回の行に戻す(**AI は呼ばない**)。``runs`` の行をその場で書き換える。

    - 値を選んだ: その組の 3 回の行すべてで、その欄をその値にする。数量は数量と単位。室は室の欄を、
      状態・部位は選んだ値を読んだ行の形の欄(工事・何・部位・区分など)を写す。人の回答の印を付ける。
      機械の値・縮尺の値は数量と単位だけ。
    - 「どれでもない」「分からない」: **何も決めない**(書くだけ)。
    - カードの選択肢に無い答えは `戻せなかった答え` に残す(**黙って捨てない**)。
    """
    by_key = {c["鍵"]: c for c in cards}
    index = {name: {it["id"]: it for it in rows} for name, rows in runs.items()}
    decided: list[dict[str, Any]] = []
    recorded: list[str] = []
    refused: list[dict[str, Any]] = []
    for key, choice in answers.items():
        card = by_key.get(key)
        if card is None:
            refused.append({"鍵": key, "理由": "カードが無い"})
            continue
        if choice in TAIL:
            recorded.append(key)
            continue
        src = (card.get("値の出どころ") or {}).get(choice)
        if choice not in (card.get("選択肢") or ()) or not src:
            refused.append({"鍵": key, "理由": "カードの選択肢に無い答え"})
            continue
        targets = [index[name][i] for name, i in (card.get("行") or {}).items()
                   if name in index and i in index[name]]
        if not targets:
            refused.append({"鍵": key, "理由": "戻す行が無い"})
            continue
        dim = card["次元"]
        first = src[0]
        donor = None
        if first["出どころ"] == "3回の値":
            donor = index.get(first["辿る"]["回"], {}).get(first["辿る"]["項目"])
        if dim == "数量":
            m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", nfkc(choice))
            if not m:
                refused.append({"鍵": key, "理由": "数量の形が読めない"})
                continue
            fields: dict[str, Any] = {"数量": float(m.group(1)), "単位": m.group(2).strip()}
        elif donor is None:
            refused.append({"鍵": key, "理由": "値を読んだ行が無い"})
            continue
        else:
            names = ROOM_FIELDS if dim == "室" else SHAPE_FIELDS
            fields = {f: donor.get(f) for f in names if f in donor}
        snapshot = deepcopy(fields)
        for it in targets:
            before = {k: it.get(k) for k in (*fields, "確度", "状態")}
            for k, v in snapshot.items():
                it[k] = deepcopy(v)
            _decide(it, choice)
            decided.append({"鍵": key, "項目": it["id"], "前": before,
                            "後": {k: it.get(k) for k in before}})
    return {"決めた行": decided, "書くだけの答え": recorded, "戻せなかった答え": refused}


def state_of(runs: Mapping[str, Sequence[Mapping[str, Any]]], base: str,
             finish: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """基準の回の 3 状態(ほかの回を「ほかの回」にする。新規則)。"""
    others = [list(v) for k, v in runs.items() if k != base]
    return unc.classify(list(runs[base]), finish=finish, other_runs=others, rule="新")


def settles(runs: Mapping[str, Sequence[Mapping[str, Any]]], card: Mapping[str, Any], base: str,
            finish: Mapping[str, Any] | None = None,
            before: Mapping[str, Any] | None = None) -> int:
    """このカードに値で答えたら、基準の回の行が `確定` になる数(**どの値を選んでも形は同じ**なので 1 番で試す)。"""
    pick = real_options(card)
    if not pick or base not in (card.get("行") or {}):
        return 0
    target_ids = {i for i in card["行"].values()}
    trial = {name: [deepcopy(it) if it["id"] in target_ids else it for it in rows] for name, rows in runs.items()}
    apply(trial, [card], {card["鍵"]: pick[0]})
    after = state_of(trial, base, finish)
    item = card["行"][base]
    row = next((r for r in after["項目ごと"] if r["id"] == item), None)
    return 1 if row is not None and row["3状態"] == unc.CONFIRMED else 0


def order(cards: Sequence[Mapping[str, Any]], runs: Mapping[str, Sequence[Mapping[str, Any]]], base: str, *,
          finish: Mapping[str, Any] | None = None, frames: Sequence[Mapping[str, Any]] | None = None
          ) -> list[dict[str, Any]]:
    """並べる: ①1 つの答えで確定する ②金額の大きい順(**原価表が無ければ未取得**。値が決まる行の数で代える)③鍵。

    枠(工事チェック表)も付ける(`枠ごと` にまとめて出せるように)。
    """
    from draft import work_checklist

    frames = list(frames) if frames is not None else work_checklist.load_frames()
    index = {name: {it["id"]: it for it in rows} for name, rows in runs.items()}
    out = []
    for c in cards:
        card = dict(c)
        card["1つの答えで確定する行"] = settles(runs, card, base, finish)
        card["金額"] = "未取得(原価表なし)。値が決まる行の数で並べている"
        card["値が決まる行の数"] = len(card.get("行") or {})
        name, item_id = next(iter((card.get("行") or {}).items()))
        card["枠"] = work_checklist.frame_of(index[name][item_id], frames)["枠"]
        out.append(card)
    out.sort(key=lambda c: (-c["1つの答えで確定する行"], -c["値が決まる行の数"], c["鍵"]))
    for i, c in enumerate(out, 1):
        c["順位"] = i
    return out


def by_frame(cards: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """工事チェック表の枠ごとにまとめる(枠の中は並べた順のまま)。"""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for c in cards:
        out.setdefault(c.get("枠") or "その他", []).append(c)
    return out


def numbered(cards: Sequence[Mapping[str, Any]], count: int | None = None, *, group_by_frame: bool = False
             ) -> list[dict[str, Any]]:
    """画面に出すカードに番号を付ける(カードは 1 から、選択肢も 1 から)。``group_by_frame`` なら枠ごとに並べ直す。"""
    picked = list(cards[:count] if count is not None else cards)
    if group_by_frame:
        groups = by_frame(picked)
        picked = [c for cs in groups.values() for c in cs]
    out = []
    for i, c in enumerate(picked, 1):
        card = dict(c)
        card["番号"] = i
        card["番号つきの選択肢"] = [{"番号": n, "文字": o} for n, o in enumerate(c["選択肢"], 1)]
        out.append(card)
    return out
