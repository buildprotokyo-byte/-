"""K-66 4 節: **過去の結果を、旧規則と新規則で並べて数え直す。**

正解を使わない数え直し(3 回の一致・質問の一致)だけをここで行う。
正解を使う数え直し(科目 9 つ・科目ごとの金額・細目の当たり)は**パソコン側の手順書**
(`/mnt/project-files/reports/K-66/`)で行う。クラウドは正解ファイルを開かない。

**出すのは件数だけ。**行の名前・室名・数量・金額は出さない(実案件のため)。

実行(K-61 の 7 回のうち、壊していない 3 回)::

    python -m benchmarks.measure_sameness_recount \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R1 \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R2 \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R3 \
        --label K-61-壊していない3回 --out /tmp/k66_recount.json
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

from sameness.normalize import canonical_unit, room_key
from sameness.rows import agreement_key, old_agreement_key, row_room

#: 読む 2 つのファイル。`本番の形.json` に行、`下書き.json` に質問が入る。
ROWS_FILE = "本番の形.json"
DRAFT_FILE = "下書き.json"


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def load_run(directory: Path) -> dict[str, Any]:
    rows = json.loads((directory / ROWS_FILE).read_text(encoding="utf-8"))
    draft = json.loads((directory / DRAFT_FILE).read_text(encoding="utf-8"))
    return {"行": rows.get("工事項目") or [], "下書き": draft, "名前": directory.name}


def _quantity_key(row: Mapping[str, Any], key: tuple[Any, ...]) -> tuple[Any, ...]:
    """名前の鍵に数量と単位を足したもの(**3 回とも同じ数量・単位の鍵**を数えるため)。"""
    quantity = row.get("数量")
    return (*key, None if quantity is None else round(float(quantity), 4), canonical_unit(row.get("単位")))


def question_keys(draft: Mapping[str, Any], stage: str, limit: int, *, new: bool) -> set[tuple[Any, ...]]:
    """質問の中身の鍵(K-63 2 節 4 と同じ作り)。**項目の番号は回ごとに違うので使わない。**"""
    out: set[tuple[Any, ...]] = set()
    for question in (draft.get("質問") or {}).get("段階ごと", {}).get(stage, [])[:limit]:
        pages = tuple(sorted(question.get("見る所") or ()))
        work = question.get("工事") or ""
        room = room_key(work.split()[0]) if work.split() else ""
        row = {"工事項目": work, "科目": question.get("科目")}
        tail = agreement_key(row, with_room=False)[1:] if new else (_norm(work),)
        out.add((_norm(question.get("種類")), pages, room, *tail))
    return out


def recount(runs: Sequence[Mapping[str, Any]], *, stage: str = "通常", limit: int = 5) -> dict[str, Any]:
    out: dict[str, Any] = {"回数": len(runs), "各回の行数": [len(r["行"]) for r in runs]}
    for label, keyer in (("旧規則", old_agreement_key), ("新規則", agreement_key)):
        name_sets, quantity_sets = [], []
        for run in runs:
            names: set[tuple[Any, ...]] = set()
            quantities: set[tuple[Any, ...]] = set()
            for row in run["行"]:
                key = keyer(row)
                names.add(key)
                quantities.add(_quantity_key(row, key))
            name_sets.append(names)
            quantity_sets.append(quantities)
        union = set().union(*name_sets)
        common = set.intersection(*name_sets)
        q_common = set.intersection(*quantity_sets)
        questions = [question_keys(run["下書き"], stage, limit, new=(label == "新規則")) for run in runs]
        out[label] = {
            "鍵の和": len(union),
            "名前の一致(3 回とも出た鍵)": len(common),
            "名前の一致の割合": len(common) / len(union) if union else None,
            "数量も同じ": len(q_common),
            "質問の一致": len(set.intersection(*questions)) if questions else 0,
            "各回の鍵の数": [len(s) for s in name_sets],
            "各回の質問の数": [len(s) for s in questions],
        }
    new, old = out["新規則"], out["旧規則"]
    out["差"] = {
        "名前の一致の割合": (new["名前の一致の割合"] or 0) - (old["名前の一致の割合"] or 0),
        "名前の一致の件数": new["名前の一致(3 回とも出た鍵)"] - old["名前の一致(3 回とも出た鍵)"],
        "数量も同じ": new["数量も同じ"] - old["数量も同じ"],
        "質問の一致": new["質問の一致"] - old["質問の一致"],
        "鍵の和": new["鍵の和"] - old["鍵の和"],
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-66 過去の結果を旧規則と新規則で数え直す")
    parser.add_argument("--run", type=Path, action="append", required=True, help="通しの出力があるフォルダ")
    parser.add_argument("--label", default="")
    parser.add_argument("--stage", default="通常")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    runs = [load_run(d) for d in args.run]
    result = {"ラベル": args.label, "回": [r["名前"] for r in runs], **recount(runs, stage=args.stage, limit=args.limit)}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
