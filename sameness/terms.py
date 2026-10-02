"""K-66 2 節(b): 辞書。**差し替えられるファイルから読む。**

組(科目・中科目・部位・状態・材料・工事の種類)ごとに、`id`・`代表`・`語`(と任意の `親`)を持つ。
`親` を持つ組は上位・下位の判定に使う(`外壁` と `内壁` は `壁` の下位で、互いは別の組)。

差し替え方: `load_terms("別のファイル.json")`、または環境変数 `SAMENESS_TERMS`。
**語を足すときはこのファイルを直さず、JSON を足す。**
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from sameness.normalize import flatten

DEFAULT_TERMS = Path(__file__).with_name("terms") / "default.json"
SLOTS = ("科目", "中科目", "部位", "状態", "材料", "工事の種類")


@dataclass(frozen=True)
class Group:
    """辞書の 1 組。"""

    id: str
    代表: str
    語: tuple[str, ...]
    親: str | None = None


@dataclass
class Terms:
    """読み込んだ辞書。**語の長いほうから当てる**(`フロアタイル` を `タイル` に取られないため)。"""

    groups: dict[str, dict[str, Group]] = field(default_factory=dict)
    _by_word: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def group(self, slot: str, gid: str) -> Group | None:
        return self.groups.get(slot, {}).get(gid)

    def find(self, slot: str, text: Any) -> str | None:
        """平らにした文字列の中にある語から、組の id を 1 つ返す。**長い語が勝つ。**"""
        flat = flatten(text)
        if not flat:
            return None
        for word, gid in self._by_word.get(slot, ()):
            if word and word in flat:
                return gid
        return None

    def find_all(self, slot: str, text: Any) -> list[str]:
        """当たった組の id を長い語の順に全部返す(1 つに絞らずに見たいとき)。"""
        flat = flatten(text)
        out: list[str] = []
        for word, gid in self._by_word.get(slot, ()):
            if word and word in flat and gid not in out:
                out.append(gid)
        return out

    def exact(self, slot: str, text: Any) -> str | None:
        """語そのものと一致したときだけ返す(部分一致を使わない判定のため)。"""
        flat = flatten(text)
        for word, gid in self._by_word.get(slot, ()):
            if word == flat:
                return gid
        return None

    def ancestors(self, slot: str, gid: str | None) -> list[str]:
        """上位の組の id(自分は入れない)。輪になっていても止まる。"""
        out: list[str] = []
        seen = {gid}
        current = self.group(slot, gid) if gid else None
        while current and current.親 and current.親 not in seen:
            out.append(current.親)
            seen.add(current.親)
            current = self.group(slot, current.親)
        return out

    def relation(self, slot: str, a: str | None, b: str | None) -> str:
        """2 つの組の関係。`同じ` / `上位` (a が b の上位) / `下位` / `別` / `不明`。"""
        if not a or not b:
            return "不明"
        if a == b:
            return "同じ"
        if a in self.ancestors(slot, b):
            return "上位"
        if b in self.ancestors(slot, a):
            return "下位"
        return "別"

    def counts(self) -> dict[str, dict[str, int]]:
        """報告に出す件数(組の数と語の数)。"""
        return {
            slot: {"組": len(groups), "語": sum(len(g.語) for g in groups.values())}
            for slot, groups in self.groups.items()
        }


def _build(raw: Mapping[str, Any]) -> Terms:
    terms = Terms()
    for slot in SLOTS:
        rows = raw.get(slot) or []
        groups: dict[str, Group] = {}
        words: list[tuple[str, str]] = []
        for row in rows:
            gid = str(row["id"])
            spellings = tuple(str(w) for w in row.get("語") or ())
            groups[gid] = Group(
                id=gid,
                代表=str(row.get("代表") or gid),
                語=spellings,
                親=(str(row["親"]) if row.get("親") else None),
            )
            for word in (*spellings, str(row.get("代表") or "")):
                flat = flatten(word)
                if flat:
                    words.append((flat, gid))
        terms.groups[slot] = groups
        terms._by_word[slot] = sorted(set(words), key=lambda pair: (-len(pair[0]), pair[0]))
    return terms


def load_terms(path: str | Path | None = None) -> Terms:
    """辞書を読む。`path` が無ければ環境変数 `SAMENESS_TERMS`、無ければ既定のファイル。"""
    target = Path(path or os.environ.get("SAMENESS_TERMS") or DEFAULT_TERMS)
    return _build(json.loads(target.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _cached(target: str) -> Terms:
    return load_terms(target)


def default_terms() -> Terms:
    """既定の辞書(読み直さずに使い回す)。"""
    return _cached(str(os.environ.get("SAMENESS_TERMS") or DEFAULT_TERMS))
