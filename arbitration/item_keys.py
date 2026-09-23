"""突き合わせの鍵を揃える(**まだ何もしていない足場**)。

**いまの `arbitration/multi_path_reconciler.PathItem.item_key` は文字列そのままで
比べている。** この足場は、その「いまの振る舞い」をそのまま関数にしたものである。
34 周目の決まり②(修正は再現する失敗テストを先に書く)のために先に置く。
"""

from __future__ import annotations

SCOPE_CONFIRM = "確定"
SCOPE_TO_HUMAN = "人へ"


def normalise_item_key(text: str, *, scope: str = SCOPE_CONFIRM) -> str:
    return text


def same_item_key(left: str, right: str, *, scope: str = SCOPE_CONFIRM) -> bool:
    return normalise_item_key(left, scope=scope) == normalise_item_key(right, scope=scope)
