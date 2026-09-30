"""K-54: 社内の 99 細目を公開書式(B-08)に置き換えた結果を数える道具。**正解はこの道具に入っていない。**

正解(社内の 99 細目と、それを公開書式に置き換えた対応)はパソコン側だけが持つ。クラウドは開かない(K-58 の A)。
この道具は、パソコン側が作る対応のファイルを読み、公開書式の決まり(`estimating.breakdown`)で内訳書に組み、件数だけを出す。

対応のファイル(パソコン側が作る)::

    {"G001": {"数量": 3.0, "単位": "箇所",
              "候補": [[{"科目": "...", "中科目": "", "区分": "撤去", "工事項目": "...", "数量": 3.0, "単位": "箇所"}]]},
     "G002": {"数量": 10.0, "単位": "㎡", "候補": []}, ...}

- ``候補`` は公開書式の置き場所の案。案 1 つは行の並び(1 行 = 割れていない、2 行以上 = 割れた)。
  案が 2 つ以上 = 「複数候補」(K-54 の 9 行)。**どれか 1 つが整合すれば正しい。**
- ``候補`` が空 = 公開書式に置き場所が無かった細目。

割れた行の判定(おーちゃん K-54): 割れた行がどれも公開の位置にあり(案に書かれていること)、かつ数量が整合する
(足すと社内の数量になる、または社内の数量が共通の数量で、割れた行がどれもそれと同じ)なら正しい。
個数は ±1、面積・長さは ±5%(K-49 と同じ)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from estimating.breakdown import build_breakdown

COUNT_UNITS = ("個", "箇所", "台", "本", "枚", "組", "ヶ所", "カ所", "セット")


def close(a: float | None, b: float | None, unit: str) -> bool:
    if a is None or b is None:
        return False
    if unit in COUNT_UNITS:
        return abs(a - b) <= 1
    return abs(a - b) <= 0.05 * abs(b) if b else a == 0


def consistent(rows: list[Mapping[str, Any]], quantity: float | None, unit: str) -> bool:
    """割れた行の数量が社内の数量と整合するか。**数量の無い行は 0 として足さない。**"""
    quantities = [r.get("数量") for r in rows]
    if not rows or any(q is None for q in quantities):
        return False
    if close(sum(quantities), quantity, unit):
        return True
    return len(rows) > 1 and all(close(q, quantity, unit) for q in quantities)


def judge(item: Mapping[str, Any]) -> dict[str, Any]:
    plans = [list(p) for p in item.get("候補") or [] if p]
    unit = str(item.get("単位") or "")
    ok = [consistent(p, item.get("数量"), unit) for p in plans]
    chosen = next((p for p, good in zip(plans, ok) if good), plans[0] if plans else [])
    return {
        "置けた": bool(plans),
        "複数候補": len(plans) > 1,
        "割れた": len(chosen) > 1,
        "数量が整合": any(ok),
        "採った案": chosen,
    }


def frames(breakdown: Mapping[str, Any]) -> dict[str, Any]:
    """中科目が立った科目と、細目 0 件の空の枠の数。"""
    with_middle, empty = [], 0
    for shumoku in breakdown.get("種目", []):
        for kamoku in shumoku.get("科目", []):
            middles = kamoku.get("中科目")
            if middles is not None:
                with_middle.append(kamoku.get("名称"))
                empty += sum(1 for m in middles if not m.get("細目")) + (0 if middles else 1)
            elif not kamoku.get("細目"):
                empty += 1
    return {"中科目が立った科目": with_middle, "空の枠": empty}


def score(mapping: Mapping[str, Mapping[str, Any]], official_middles: Mapping[str, Any] | None = None) -> dict[str, Any]:
    per = {g: judge(item) for g, item in mapping.items()}
    rows = [r for v in per.values() for r in v["採った案"]]
    book = build_breakdown(rows, official_middles=official_middles).as_dict()
    return {
        "社内の細目": len(per),
        "公開書式に置けた": sum(v["置けた"] for v in per.values()),
        "割れた": sum(v["割れた"] for v in per.values()),
        "割れて数量が整合": sum(v["割れた"] and v["数量が整合"] for v in per.values()),
        "割れずに数量が整合": sum((not v["割れた"]) and v["置けた"] and v["数量が整合"] for v in per.values()),
        "複数候補": sum(v["複数候補"] for v in per.values()),
        **frames(book),
        "細目ごと": {g: {k: v for k, v in r.items() if k != "採った案"} for g, r in per.items()},
        "内訳書": book,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mapping", help="対応のファイル(パソコン側)")
    p.add_argument("out")
    p.add_argument("--official-middles", help="科目 → 正式な中科目の名前の一覧(JSON)。無ければ行の中科目を使う")
    a = p.parse_args(argv)
    mapping = json.loads(Path(a.mapping).read_text(encoding="utf-8"))
    official = json.loads(Path(a.official_middles).read_text(encoding="utf-8")) if a.official_middles else None
    result = score(mapping, official)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("細目ごと", "内訳書")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
