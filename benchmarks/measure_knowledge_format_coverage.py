"""65周目: **知識の表の形式が、集まった候補を無理なく受け取れるか。**

基準は `docs/d_knowledge_format_criteria.md`(測る前にコミット済み)。

対象は `docs/knowledge/candidates.md` の **A・A′・B′・B・C の全 25 項目**。
それを形式どおりに書き直したものが
`benchmarks/fixtures/knowledge_candidates_encoded.json` である。

**「表せた」= 新しい列を足さずに読み込みが通り、かつ形式を曲げていないこと。**
曲げた項目は、その行の `note` が「**形式に収まっていない**」で始まっている。
**数え方を人の感想にしないため、印は行そのものに付けてある。**

使い方::

    .venv/bin/python benchmarks/measure_knowledge_format_coverage.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge.table import KnowledgeError, load_knowledge, parse_knowledge  # noqa: E402

ENCODED = ROOT / "benchmarks" / "fixtures" / "knowledge_candidates_encoded.json"
EXAMPLE = ROOT / "knowledge" / "examples" / "synthetic_knowledge.json"

#: 対象にした候補の項目数(節ごと全部。**先に基準で決めた**)。
TARGET_ITEMS = 25

NOT_FIT_MARK = "**形式に収まっていない**"


def controls() -> dict[str, object]:
    """C1: 壊れた表を 3 通りとも拒むか。C2: 見本が架空だと名乗るか。"""
    payload = json.loads(ENCODED.read_text(encoding="utf-8"))

    broken: dict[str, bool] = {}

    a = copy.deepcopy(payload)
    a["entries"][0]["priority"] = "high"
    broken["知らない列"] = _refused(a)

    b = copy.deepcopy(payload)
    b["entries"][0]["source"]["clause"] = ""
    broken["出典が空"] = _refused(b)

    c = copy.deepcopy(payload)
    c["entries"][0]["kind"] = "覚え書き"
    broken["知らない種類"] = _refused(c)

    example = load_knowledge(EXAMPLE)
    return {
        "C1_壊れた表を拒んだか": broken,
        "C1_通過": all(broken.values()),
        "C2_見本が架空だと名乗るか": example.synthetic is True,
    }


def _refused(payload: object) -> bool:
    try:
        parse_knowledge(payload)
    except KnowledgeError:
        return True
    return False


def main() -> None:
    table = load_knowledge(ENCODED)
    rows = table.entries

    not_fit = [e.entry_id for e in rows if e.note.startswith(NOT_FIT_MARK)]
    # 1 項目を 2 行に分けたものは、項目としては 1 つ。
    split_rows = [e.entry_id for e in rows if e.entry_id.endswith("b")]
    items_expressed = TARGET_ITEMS - len(not_fit)

    payload: dict[str, object] = {
        "対象にした候補の項目数": TARGET_ITEMS,
        "書き直した行の数": len(rows),
        "1項目を2行に分けたもの": split_rows,
        "M1_表せた項目の数": items_expressed,
        "表せなかった項目": not_fit,
        "種類ごとの行数": {
            kind: len(table.of_kind(kind)) for kind in ("数え方", "波及", "問い")
        },
    }
    payload.update(controls())
    payload["判定"] = (
        "この形式で確定する"
        if items_expressed >= 20 and payload["C1_通過"] and payload["C2_見本が架空だと名乗るか"]
        else "基準の分岐に従って作り直す"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
