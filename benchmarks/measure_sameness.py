"""K-66 3 節: **判定自体を測る。**囮の通過率・言い換えの合格率・旧規則との差。

基準は `docs/k66_sameness_criteria.md`(測る前にコミット済み)。線は
**囮の通過 0%**(1 件でも通ったら不合格)と **言い換えの合格 0.80 以上**。

**実案件も正解ファイルも実図面も使わない。**語は `draft/vocab/default.json` と
公開書式 B-08 の品目名だけから作る(`sameness/decoys.py` の冒頭を参照)。

実行::

    python -m benchmarks.measure_sameness --out docs/k66_sameness_result.json
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any

from sameness import compare, quantity_verdict
from sameness.decoys import K70_RULED_NOT_SAME, Pair, decoy_pairs, paraphrase_pairs
from sameness.terms import default_terms


def old_rule(pair: Pair) -> bool:
    """**旧規則**: `estimating/scoring.py` の対応づけ(NFKC で正規化して完全一致)。"""
    norm = lambda t: unicodedata.normalize("NFKC", t).strip()
    return norm(pair.left) == norm(pair.right)


def judge(pair: Pair) -> dict[str, Any]:
    """新規則で 1 対を判定する。数量の囮は名前と数量の両方を見る。"""
    verdict = compare(pair.left, pair.right, level=pair.level)
    row: dict[str, Any] = {
        "種類": pair.種類, "段": pair.level, "同じか": pair.同じか,
        "新規則": verdict.value, "規則": verdict.rule, "理由": verdict.reason,
        "旧規則": "○" if old_rule(pair) else "×",
    }
    if pair.unit:
        q = quantity_verdict(pair.left_quantity, pair.right_quantity, pair.unit)
        row["数量の判定"] = q.value
        row["数量の理由"] = q.reason
        # 行として当たりにするには、名前も数量も通る必要がある。
        row["行として○"] = verdict.hit and q.hit
    else:
        row["行として○"] = verdict.hit
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-66 判定自体を測る")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    decoys = [judge(p) for p in decoy_pairs()]
    paras = [judge(p) for p in paraphrase_pairs()]

    passed = [r for r in decoys if r["行として○"]]
    old_passed = [r for r in decoys if r["旧規則"] == "○"]
    hit = [r for r in paras if r["行として○"]]
    old_hit = [r for r in paras if r["旧規則"] == "○"]

    by_kind: dict[str, dict[str, int]] = {}
    for row in decoys:
        bucket = by_kind.setdefault(row["種類"], {"件数": 0, "通った": 0})
        bucket["件数"] += 1
        bucket["通った"] += 1 if row["行として○"] else 0

    # K-70: おーちゃんが「同じにしない」と決めた対と、残りの対を分けて数える(36 組そのものは変えない)。
    ruled = set(K70_RULED_NOT_SAME)
    para_pairs = list(paraphrase_pairs())
    ruled_rows = [r for p, r in zip(para_pairs, paras) if (p.left, p.right) in ruled]
    rest_rows = [r for p, r in zip(para_pairs, paras) if (p.left, p.right) not in ruled]

    para_values: dict[str, int] = {}
    for row in paras:
        para_values[row["新規則"]] = para_values.get(row["新規則"], 0) + 1

    result = {
        "辞書の件数": default_terms().counts(),
        "囮": {
            "件数": len(decoys), "通った": len(passed),
            "通過率": len(passed) / len(decoys) if decoys else None,
            "旧規則で通った": len(old_passed),
            "種類ごと": by_kind,
            "通った対": [{"左": p["理由"], "種類": p["種類"]} for p in passed],
        },
        "言い換え": {
            "件数": len(paras), "合格": len(hit),
            "合格率": len(hit) / len(paras) if paras else None,
            "旧規則の合格": len(old_hit),
            "旧規則の合格率": len(old_hit) / len(paras) if paras else None,
            "判定の内訳": para_values,
            "K-70 で同じにしないと決めた対": {
                "件数": len(ruled_rows),
                "○になった": sum(1 for r in ruled_rows if r["行として○"]),
                "判定の内訳": {v: sum(1 for r in ruled_rows if r["新規則"] == v) for v in sorted({r["新規則"] for r in ruled_rows})},
            },
            "残りの対": {"件数": len(rest_rows), "合格": sum(1 for r in rest_rows if r["行として○"])},
            "外れた対": [
                {"左": r["理由"], "新規則": r["新規則"], "規則": r["規則"]}
                for r in paras if not r["行として○"]
            ],
        },
        "判定役 3 人の一致": "未取得(この環境では判定役の AI を呼んでいない)",
        "明細": {"囮": decoys, "言い換え": paras},
    }

    print(json.dumps({k: v for k, v in result.items() if k != "明細"}, ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n書いた: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
