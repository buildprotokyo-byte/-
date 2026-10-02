"""K-66 2 節(c): AI に 1 回だけ聞いて、同じ品名には同じキーを返す。

**AI を呼べる環境でなければ何もしない。**聞けなかった品名は `None` のまま残す
(「未取得を 0 にしない」。推測で埋めない)。

キャッシュは JSON 1 枚。鍵は平らにした品名なので、書き方が違っても 1 回で済む。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from sameness.normalize import flatten

DEFAULT_CACHE = Path("構造のキー_キャッシュ.json")


class KeyCache:
    """品名 → 構造のキーのキャッシュ。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DEFAULT_CACHE)
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, name: str) -> Mapping[str, Any] | None:
        return self.data.get(flatten(name))

    def put(self, name: str, value: Mapping[str, Any] | None) -> None:
        self.data[flatten(name)] = dict(value) if value else None

    def has(self, name: str) -> bool:
        return flatten(name) in self.data

    def save(self) -> Path:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.path


def ask_once(
    name: str,
    *,
    ask_ai: Callable[[str], Mapping[str, Any] | None] | None = None,
    cache: KeyCache | None = None,
) -> Mapping[str, Any] | None:
    """キャッシュにあればそれを返す。無ければ 1 回だけ `ask_ai` に聞いて覚える。"""
    if cache is not None and cache.has(name):
        return cache.get(name)
    if ask_ai is None:
        return None
    answer = ask_ai(name)
    if cache is not None:
        cache.put(name, answer)
    return answer
