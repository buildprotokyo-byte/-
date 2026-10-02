"""原価表の入力口(K-64 周 5)。**質問の並べ方と、内訳の数量との比べにだけ使う。正解としては使わない・数量を書き換えない。**

受け取れる形:

1. 今までの形 ``{"品番または工事": 単価}``(数量・単位なし)。
2. 行の並び ``[{"工事": "...", "品番": "...", "単位": "枚", "単価": 1200, "数量": 3, "科目": "..."}]``
   (``{"行": [...]}`` で包んでもよい)。
3. CSV(1 行目が見出し。``工事``(または ``工事項目``・``名称``)・``品番``・``単位``・``単価``・``数量``・``科目``)。

数は ``1,200`` のような桁区切りも読む。**数として読めない欄は未取得(None)にし、0 にしない。**
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 工事の名前として読む見出し(先にあるものを使う)。
WORK_HEADERS = ("工事", "工事項目", "名称")


def _nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", "" if text is None else str(text)).strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _nfkc(value).replace(",", "").replace("¥", "").replace("円", "")
    try:
        return float(text)
    except ValueError:
        return None


def _row(raw: Mapping[str, Any]) -> dict[str, Any]:
    work = next((_nfkc(raw[h]) for h in WORK_HEADERS if _nfkc(raw.get(h))), "")
    return {"工事": work, "品番": _nfkc(raw.get("品番")), "単位": _nfkc(raw.get("単位")),
            "単価": _number(raw.get("単価")), "数量": _number(raw.get("数量")), "科目": _nfkc(raw.get("科目"))}


def load_cost_table(source: str | Path | Mapping[str, Any] | Sequence[Any] | None) -> dict[str, Any] | None:
    """原価表を ``{"行": [...], "形": "..."}`` にする。無ければ None(未取得)。"""
    if source is None or source == "":
        return None
    payload: Any = source
    if isinstance(source, (str, Path)):
        path = Path(source)
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".csv":
            rows = [_row(r) for r in csv.DictReader(io.StringIO(text))]
            return {"行": [r for r in rows if r["工事"] or r["品番"]], "形": "CSV"}
        payload = json.loads(text)
    if isinstance(payload, Mapping) and isinstance(payload.get("行"), list):
        payload = payload["行"]
    if isinstance(payload, Mapping):  # 今までの形 {鍵: 単価}
        rows = [{"工事": _nfkc(k), "品番": _nfkc(k), "単位": "", "単価": _number(v), "数量": None, "科目": ""}
                for k, v in payload.items()]
        return {"行": rows, "形": "単価だけ"}
    rows = [_row(r) for r in payload if isinstance(r, Mapping)]
    return {"行": [r for r in rows if r["工事"] or r["品番"]], "形": "行の並び"}


def match(table: Mapping[str, Any] | None, hinban: Any, work: Any, unit: Any = None) -> dict[str, Any] | None:
    """品番で当て、無ければ工事の名前で当てる(揃えた文字の完全一致)。**単位が両方あって違えば当てない。**"""
    if not table:
        return None
    h, w, u = _nfkc(hinban), _nfkc(work), _nfkc(unit)
    for key, value in (("品番", h), ("工事", w)):
        if not value:
            continue
        for r in table["行"]:
            ru = _nfkc(r["単位"])
            if _nfkc(r[key]) == value and not (u and ru and ru != u):
                return r
    return None


def question_amount(q: Mapping[str, Any], items_by_id: Mapping[str, Mapping[str, Any]],
                    table: Mapping[str, Any] | None) -> tuple[float | None, str]:
    """問いに 1 つ答えると確定する金額の見込み = 関係する項目の 単価 × 数量 の合計。

    項目の数量が未取得なら、原価表の同じ行の数量で見積もる(**並べ方のためだけ。数量には書き戻さない**)。
    どれも当たらなければ未取得(None)。
    """
    if not table:
        return None, "原価表 未取得"
    ids = list(q.get("関係する項目") or [])
    targets = [items_by_id[i] for i in ids if i in items_by_id] or [q]
    total, used_table_qty, hit = 0.0, False, False
    seen: set[int] = set()
    for it in targets:
        r = match(table, it.get("品番"), it.get("工事"), it.get("単位"))
        if r is None or r["単価"] is None:
            continue
        qty = it.get("数量")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool):
            if id(r) in seen or r["数量"] is None:
                continue  # 同じ原価表の行の数量を二度足さない
            qty = r["数量"]
            used_table_qty = True
        seen.add(id(r))
        total += r["単価"] * qty
        hit = True
    if not hit:
        return None, "原価表に当たる行が無い"
    return total, "原価表の数量で見積もった所がある(並べ方のためだけ)" if used_table_qty else "項目の数量 × 原価表の単価"


def compare(rows: Sequence[Mapping[str, Any]], table: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """内訳の数量と原価表の数量を並べる。**差を見せるだけ。どちらが正しいとは言わない・内訳を書き換えない。**"""
    if not table:
        return None
    pairs: dict[int, dict[str, Any]] = {}
    only_rows = 0
    for r in rows:
        t = match(table, r.get("摘要"), r.get("工事項目"), r.get("単位"))
        if t is None:
            only_rows += 1
            continue
        p = pairs.setdefault(id(t), {"工事": t["工事"], "品番": t["品番"], "単位": t["単位"] or r.get("単位", ""),
                                     "原価表の数量": t["数量"], "内訳の数量(分かった分)": 0.0, "内訳の数量が未取得の行": 0,
                                     "内訳の行": 0})
        p["内訳の行"] += 1
        if r.get("数量") is None:
            p["内訳の数量が未取得の行"] += 1
        else:
            p["内訳の数量(分かった分)"] += r["数量"]
    out = []
    for p in pairs.values():
        mine, theirs = p["内訳の数量(分かった分)"], p["原価表の数量"]
        if p["内訳の数量が未取得の行"] == p["内訳の行"]:
            p["内訳の数量(分かった分)"] = "未取得"
            p["差"] = "未取得"
        elif theirs is None:
            p["差"] = "未取得"
        else:
            p["差"] = round(mine - theirs, 4)
            p["一致"] = abs(mine - theirs) < 1e-9 and not p["内訳の数量が未取得の行"]
        out.append(p)
    matched = {id(t) for t in table["行"]} & set(pairs)
    return {
        "注": "原価表は正解として使わない。差を並べるだけで、内訳の数量は書き換えていない",
        "並べた行": out,
        "原価表にだけある行": len(table["行"]) - len(matched),
        "内訳にだけある行": only_rows,
        "数量が同じ": sum(1 for p in out if p.get("一致")),
        "数量が違う": sum(1 for p in out if p.get("一致") is False),
        "比べられない(未取得)": sum(1 for p in out if "一致" not in p),
    }
