"""見積の行の機械の検算(K-40 1 番、2026-09-26)。**知らせるだけで、数量は変えない。**

同じ室の同じ部位に、同じ工事(新設・撤去など)の行が 2 行以上あり、どれも数量を持っているとき、
**同じ面を 2 回以上数えている**おそれがある。実図面(K-38)では、LDK の壁にクロスとタイルが
両方とも壁全面の面積(72.03㎡)で載っていた。仕上表が 1 つの部位に仕上を 2 つ書くと起きる。

撤去と新設は同じ面でも別の工事なので数えない。数量の無い行も数えない。
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping


def _nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def same_surface_counted_twice(rows: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """``{"場所", "工事項目", "数量", "単位"}`` の行から、同じ面を 2 回以上数えていそうな組を知らせる。

    工事項目は「部位 [仕上] 工事」の形(先頭が部位、末尾が工事の種類)として読む。
    """
    groups: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("数量") is None:
            continue
        words = _nfkc(row.get("工事項目")).split()
        if len(words) < 2:
            continue
        key = (_nfkc(row.get("場所")), words[0], words[-1], _nfkc(row.get("単位")))
        groups[key].append(row)
    warnings: list[str] = []
    for (place, part, kind, unit), found in groups.items():
        if len(found) < 2 or not place:
            continue
        items = "、".join(f"{_nfkc(r['工事項目'])} {r['数量']}{unit}" for r in found)
        warnings.append(
            f"[同じ面を2回以上数えているおそれ] {place} の{part}({kind})に {len(found)} 行: {items}。"
            "仕上が面の一部だけなら、面積を分ける必要がある"
        )
    return tuple(warnings)


__all__ = ["same_surface_counted_twice"]
