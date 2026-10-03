"""未確定を正直に出す(K-65 の 1)。**AI の確度を見ない信号だけで 3 状態に分ける。**

基準は `docs/k65_question_curve_criteria.md` の 1 節(測る前にコミットした)。

なぜ確度を見ないか。項目の「確度」は AI 自身の自己申告である。信号に混ぜると
「自信があると言ったから確定」になり、**自己申告が自分を確定させる**。
K-67 で `確認できた` の的中率が測れなかったのと同じ形の穴になる。

ここで決めるのは状態だけで、**項目の値は 1 つも書き換えない。**
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from draft.stages import UNDECIDED, UNKNOWN, nfkc, room_key
from sameness import quantity_verdict

CONFIRMED = "確定"
HYPOTHESIS = "仮説"
UNSETTLED = "未確定"

#: 立てる信号はこの 9 つだけ(基準 1 節)。順番も変えない。
SIGNALS = (
    "3回の読みの割れ",
    "機械とAIの食い違い",
    "仕様書にあって図面で見つからない",
    "図面にあって仕様書に無い",
    "検算の不一致",
    "室が決まらない",
    "数量が取れない",
    "原価表との食い違い",
    "同じ物が2枚の図にある疑い",
)

#: 影響小の線(**仮の判断**)。原価表があるときは総額比、無いときは連鎖で決まる項目数。
SMALL_AMOUNT_SHARE = 0.001
SMALL_CHAIN_ITEMS = 1

#: 抜き取り(確定の項目から混ぜる問い)の 1 段階あたりの数。
SPOT_CHECKS_PER_MODE = 2


def _machine_vs_ai(item: Mapping[str, Any]) -> bool:
    """機械が数えた記号の数と AI の数量が食い違ったか。

    検算の覚書のうち、この 1 行だけを別の信号として取り出す(ほかの覚書は
    `検算の不一致` に入る)。覚書の文は `draft/stages.check_item` が書いている。
    """
    return any("機械の数えと食い違う" in nfkc(note) for note in item.get("検算") or ())


def item_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """**旧規則**の鍵(K-65)。`(工事, 揃えた室名, 部位, 品番)` の文字列。**ページと id は入れない。**

    K-68 B 周 1 で 3 回の割れは K-66 の鍵(`agreement_item_key`)に替えた。
    旧と新を並べるために残す(`鍵の規則="旧"`)。
    """
    return (nfkc(item.get("工事")), room_key(item.get("場所")), nfkc(item.get("部位")),
            nfkc(item.get("品番")))


#: 3 回の割れを揃える鍵の規則。**既定は新(K-66 の鍵)。**旧は比べるためだけに残す。
KEY_RULES = ("新", "旧")
DEFAULT_KEY_RULE = "新"

#: K-66 の鍵が読む欄(`sameness.keys.NAME_FIELDS` と、部位・区分・科目・細目・室の欄)。
_KEY_FIELDS = ("工事項目", "工事", "品名", "名前", "摘要", "何", "仕様", "細目", "部位", "区分", "科目",
               "中科目", "室", "場所", "室名", "部屋")
_KEY_CACHE: dict[tuple[str, ...], tuple[Any, ...]] = {}


def agreement_item_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """**新規則**の鍵(K-68 B 周 1)。K-66 の `sameness.rows.agreement_key` をそのまま使う。

    構造のキー(工事の種類・部位・状態・材料)と揃えた室名。**品名の文字は入らない**ので、
    言い換えは同じ鍵になる。工事の種類が取れない行は、揃えた品名そのものが鍵になる
    (K-66 の規則 8。語彙に無い工事をひとかたまりにしない)。

    同じ名前の行は何度も出るので、鍵が読む欄の文字だけで覚えておく(結果は同じ)。
    """
    from sameness.rows import agreement_key

    memo = tuple(str(item.get(f) or "") for f in _KEY_FIELDS)
    key = _KEY_CACHE.get(memo)
    if key is None:
        key = agreement_key({f: item.get(f) for f in _KEY_FIELDS})
        _KEY_CACHE[memo] = key
    return key


def key_of(item: Mapping[str, Any], rule: str = DEFAULT_KEY_RULE) -> tuple[Any, ...]:
    if rule not in KEY_RULES:
        raise ValueError(f"鍵の規則は {KEY_RULES} のどれか: {rule}")
    return agreement_item_key(item) if rule == "新" else item_key(item)


def readings_split(runs: Sequence[Sequence[Mapping[str, Any]]], *,
                   rule: str = DEFAULT_KEY_RULE) -> dict[str, Any]:
    """3 回の読みの割れと、**鍵の和**(割合を書くときは必ず並べる。K-68 の決まり)。

    - 旧規則: K-65 のまま(同じ鍵の行を回をまたいで全部並べ、1 つ目と比べる)。
    - 新規則: **回ごとに**同じ鍵の行をまとめてから回どうしを比べる。K-66 の鍵は品名の文字を
      持たないので、1 回の読みの中で 2 行が同じ鍵になることがある(同じ室の壁を 2 行に分けた等)。
      そのまま並べると「同じ回の中の 2 行」を割れと数えてしまうので、回ごとに数量を足してから比べる。
      **回の中で数量のある行と無い行が混ざっていたら、足さずに割れとする**(無い数量を 0 にしない)。
    """
    if len(runs) < 2:
        return {"割れた鍵": set(), "鍵の和": 0, "全部の回に出た鍵": 0, "鍵の規則": rule}
    per_run: list[dict[tuple[Any, ...], list[Mapping[str, Any]]]] = []
    for run in runs:
        groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for it in run:
            groups.setdefault(key_of(it, rule), []).append(it)
        per_run.append(groups)
    union: set[tuple[Any, ...]] = set().union(*(g.keys() for g in per_run))
    common = [k for k in union if all(k in g for g in per_run)]
    out: set[tuple[Any, ...]] = {k for k in union if k not in set(common)}
    for key in common:
        if rule == "旧":
            items = [it for g in per_run for it in g[key]]
            if _split_rows(items):
                out.add(key)
            continue
        units = {nfkc(it.get("単位")) for g in per_run for it in g[key]}
        if len(units) > 1:
            out.add(key)
            continue
        unit = next(iter(units), "")
        values: list[float | None] = []
        mixed = False
        for g in per_run:
            qs = [it.get("数量") for it in g[key]]
            if all(q is None for q in qs):
                values.append(None)
            elif any(q is None for q in qs):
                mixed = True
                break
            else:
                values.append(float(sum(qs)))
        if mixed or (any(v is None for v in values) and any(v is not None for v in values)):
            out.add(key)
            continue
        numbers = [v for v in values if v is not None]
        if numbers and any(quantity_verdict(numbers[0], v, unit).value == "違う" for v in numbers[1:]):
            out.add(key)
    return {"割れた鍵": out, "鍵の和": len(union), "全部の回に出た鍵": len(common), "鍵の規則": rule}


def _split_rows(items: Sequence[Mapping[str, Any]]) -> bool:
    """旧規則の割れ(K-65 のまま)。単位・数量の有無・数量(許容差の外)のどれかが割れたら割れ。"""
    units = {nfkc(it.get("単位")) for it in items}
    if len(units) > 1:
        return True
    unit = next(iter(units), "")
    values = [it.get("数量") for it in items]
    if any(v is None for v in values) and any(v is not None for v in values):
        return True
    numbers = [v for v in values if v is not None]
    return bool(numbers) and any(
        quantity_verdict(numbers[0], v, unit).value == "違う" for v in numbers[1:])


def readings_disagree(runs: Sequence[Sequence[Mapping[str, Any]]], *,
                      rule: str = DEFAULT_KEY_RULE) -> set[tuple[Any, ...]]:
    """3 回の読みで割れた鍵。**出てこなかった回があることも「割れ」に数える。**

    値・工事・室のどれかが割れたら割れ。数量は K-66 の許容差(個数 ±1・連続量 ±5%)で見る。
    鍵の和も要るときは `readings_split` を使う。
    """
    return readings_split(runs, rule=rule)["割れた鍵"]


def _cost_row(item: Mapping[str, Any], cost_rows: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for field in ("品番", "工事"):
        row = cost_rows.get(nfkc(item.get(field)))
        if row:
            return row
    return None


def signals_for(item: Mapping[str, Any], *, disagreed: set[tuple[str, str, str, str]] | None = None,
                finish_state: str = "", cost_rows: Mapping[str, Mapping[str, Any]] | None = None,
                same_item_split: bool = False, rule: str = DEFAULT_KEY_RULE) -> list[str]:
    """1 項目に立っている信号。**確度は読まない。**"""
    out: list[str] = []
    if disagreed and key_of(item, rule) in disagreed:
        out.append("3回の読みの割れ")
    if _machine_vs_ai(item):
        out.append("機械とAIの食い違い")
    if finish_state == "原本のみ":
        out.append("仕様書にあって図面で見つからない")
    if finish_state == "ひな型のみ":
        out.append("図面にあって仕様書に無い")
    if item.get("検算"):
        out.append("検算の不一致")
    place = nfkc(item.get("場所"))
    if place in ("", UNDECIDED, UNKNOWN):
        out.append("室が決まらない")
    if item.get("数量") is None:
        out.append("数量が取れない")
    row = _cost_row(item, cost_rows or {})
    if row is not None:
        unit = nfkc(item.get("単位"))
        cost_unit = nfkc(row.get("単位"))
        if cost_unit and unit and cost_unit != unit:
            out.append("原価表との食い違い")
        elif (item.get("数量") is not None and row.get("数量") is not None
              and quantity_verdict(row["数量"], item["数量"], cost_unit or unit).value == "違う"):
            out.append("原価表との食い違い")
    if same_item_split:
        out.append("同じ物が2枚の図にある疑い")
    return out


def _same_item_split(item: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    others = [by_id[i] for i in item.get("同じもの") or () if i in by_id]
    if not others:
        return False
    group = [item, *others]
    unit = nfkc(item.get("単位"))
    values = [g.get("数量") for g in group]
    if any(v is None for v in values) and any(v is not None for v in values):
        return True
    numbers = [v for v in values if v is not None]
    return any(quantity_verdict(numbers[0], v, unit).value == "違う" for v in numbers[1:])


def _finish_states(finish: Mapping[str, Any] | None) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for row in (finish or {}).get("照らし合わせ") or ():
        out[(room_key(row.get("室")), nfkc(row.get("部位")))] = nfkc(row.get("照らし合わせ"))
    return out


def classify(items: Sequence[Mapping[str, Any]], *, finish: Mapping[str, Any] | None = None,
             other_runs: Sequence[Sequence[Mapping[str, Any]]] = (),
             cost_rows: Mapping[str, Mapping[str, Any]] | None = None,
             rule: str = DEFAULT_KEY_RULE) -> dict[str, Any]:
    """項目を 確定 / 仮説 / 未確定 に分ける。

    ``rule`` は 3 回の割れを揃える鍵の規則(`新` = K-66 の鍵、`旧` = K-65 の文字列の鍵)。

    - **確定** = 独立した根拠が 2 つ以上あり、信号が 1 つも立っていない。
    - **仮説** = 状態が「仮説」か「推論」(K-44 の印をそのまま使う。印を外さない)。
    - **未確定** = 信号が 1 つ以上。

    返すのは項目ごとの行だけで、**元の項目は書き換えない。**
    """
    by_id = {it["id"]: it for it in items}
    split = readings_split([items, *other_runs], rule=rule) if other_runs else None
    disagreed = split["割れた鍵"] if split else set()
    states = _finish_states(finish)
    rows: list[dict[str, Any]] = []
    for it in items:
        key = (room_key(it.get("場所")), nfkc(it.get("部位")))
        sig = signals_for(it, disagreed=disagreed, finish_state=states.get(key, ""),
                          cost_rows=cost_rows, same_item_split=_same_item_split(it, by_id), rule=rule)
        if sig:
            state = UNSETTLED
            why = ""
        elif nfkc(it.get("状態")) in ("仮説", "推論"):
            state = HYPOTHESIS
            why = "状態が仮説・推論(K-44 の印)"
        elif len(it.get("要素") or ()) >= 2:
            state = CONFIRMED
            why = "独立した根拠が 2 つ以上あり、信号が立っていない"
        else:
            # **根拠が 1 つ以下なら確定とは言わない。**信号は立っていないので、
            # 聞く相手も無い。「未確定(根拠が足りない)」として残す。
            state = UNSETTLED
            why = "根拠の要素が 1 つ以下(確定は 2 つ以上という決まり)"
        rows.append({"id": it["id"], "3状態": state, "理由": why,
                     "信号": sig, "不確実さ": round(len(sig) / len(SIGNALS), 4),
                     "科目": nfkc(it.get("科目")), "場所": nfkc(it.get("場所")),
                     "部位": nfkc(it.get("部位")), "数量": it.get("数量"), "単位": nfkc(it.get("単位"))})
    counts = {s: sum(1 for r in rows if r["3状態"] == s) for s in (CONFIRMED, HYPOTHESIS, UNSETTLED)}
    by_signal = {s: sum(1 for r in rows if s in r["信号"]) for s in SIGNALS}
    return {
        "但し書き": "信号は AI の確度を見ない(自己申告が自分を確定させないため)",
        "項目ごと": rows,
        "3状態の分布": counts,
        "信号ごとの件数": by_signal,
        "3回の読みで割れた鍵": len(disagreed),
        # **一致の割合を書くときは鍵の和を必ず並べる**(K-68 の決まり)。
        "鍵の和": split["鍵の和"] if split else 0,
        "全部の回に出た鍵": split["全部の回に出た鍵"] if split else 0,
        "鍵の規則": rule,
    }


def spot_check_ids(classified: Mapping[str, Any], *, count: int = SPOT_CHECKS_PER_MODE,
                   seed: int = 65) -> list[str]:
    """確定の項目から抜き取りを選ぶ。**確度が高いのに外れた分を拾うための問い。**

    金額の順では選ばない(確定済みなので連鎖の金額は 0。**順位で選ぶと
    いつも同じ項目ばかり抜き取ることになる**ので、種を固定した無作為にする)。
    """
    import random

    pool = sorted(r["id"] for r in classified["項目ごと"] if r["3状態"] == CONFIRMED)
    if not pool:
        return []
    return random.Random(seed).sample(pool, min(count, len(pool)))


def small_effect(amount: float | None, total: float | None, chain_items: int) -> bool:
    """影響が小さいか(聞かずに「未確定(影響小)」で残す)。**仮の判断の線。**"""
    if amount is not None and total:
        return amount / total < SMALL_AMOUNT_SHARE
    return chain_items <= SMALL_CHAIN_ITEMS
