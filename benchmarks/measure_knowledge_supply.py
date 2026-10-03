"""周2(その1)「当てられる知識が、そもそも何個あるか」の測定。

基準は `docs/loop_round2_knowledge_supply_criteria.md`(**測る前にコミット済み**)。

設計の周2 は「1 つの読み取りに 10〜20 の知識を同時に当てる」である。
**その決め方は「20 個が別々の知識であること」に全部乗っている。**
だから当てる前に、**要素ごとに何個当たるか**、**出典を畳むと何個か**、
**原文と照らし合わせた知識が何個か**を数える。

**本番の経路には繋がない。数えるだけ。**
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

#: 設計が求める、1 つの要素に当てる知識の数の下限。
DESIGN_MIN_KNOWLEDGE = 10

DEFAULT_CATALOG = Path("knowledge/expertise/catalog.json")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_key(knowledge: dict[str, Any]) -> str:
    """**同じ文書は 1 つに畳む**ための鍵(線2)。"""
    source = knowledge.get("source") or {}
    return str(source.get("document") or "(出どころ無し)")


def checked(knowledge: dict[str, Any]) -> bool:
    """**要点を原文と照らし合わせてあるか**(線3)。"""
    source = knowledge.get("source") or {}
    return (
        knowledge.get("point_status") == "照合済"
        and bool(source.get("read_directly"))
    )


def measure(catalog: dict[str, Any]) -> dict[str, Any]:
    knowledge = catalog.get("knowledge") or []
    elements = catalog.get("elements") or []

    per_element = []
    for element in elements:
        element_id = element["element_id"]
        hit = [
            item for item in knowledge if element_id in (item.get("elements") or [])
        ]
        sources = {source_key(item) for item in hit}
        per_element.append(
            {
                "要素": element_id,
                "当たる知識の数": len(hit),
                "出典を畳んだ数": len(sources),
                "原文と照らし合わせた数": sum(1 for item in hit if checked(item)),
                "設計の下限に届くか": len(hit) >= DESIGN_MIN_KNOWLEDGE,
                "あと何個足りないか": max(DESIGN_MIN_KNOWLEDGE - len(hit), 0),
            }
        )

    all_sources = {source_key(item) for item in knowledge}
    return {
        "知識の総数": len(knowledge),
        "出典を畳んだ総数": len(all_sources),
        "原文と照らし合わせた総数": sum(1 for item in knowledge if checked(item)),
        "要素の数": len(elements),
        "下限に届いた要素の数": sum(1 for row in per_element if row["設計の下限に届くか"]),
        "当たる知識が0個の要素の数": sum(
            1 for row in per_element if row["当たる知識の数"] == 0
        ),
        "要素ごと": per_element,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    result = measure(load(args.catalog))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
