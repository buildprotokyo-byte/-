"""連鎖(芋づる式)のグラフ(K-65 の 2)。**辺は保守的に張る。**

基準は `docs/k65_question_curve_criteria.md` の 2 節(測る前にコミットした)。

**同じ依存を 2 か所に持たない。** 科目・中科目は K-66 の `sameness.structure_key`
から取り、ここで決め直さない。Z3 の変数グラフ
(`killer_question/dependency_graph.py`)は求解のための層で、こちらは
内訳の項目の層なので別物である(あちらは変数名、こちらは項目 id)。

**深さの上限は 3。** それより下は「連鎖で決まった」と言わない。
理由: 辺を 1 本でも緩く張ると、連結成分が案件全体に広がり
「1 問で全部決まる」という嘘の数字が出る(K-65 でいちばん怖い誤報)。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from draft.stages import UNDECIDED, UNKNOWN, nfkc, room_key
from sameness import structure_key

#: 辺の種類はこの 6 つだけ(基準 2 節の表)。
EDGE_KINDS = ("科目", "室部位", "状態", "有無", "まとめ方", "資料")

#: 連鎖を辿る深さの上限。
MAX_DEPTH = 3

_REMOVE_WORDS = ("撤去", "解体", "取外", "取り外")
_NEW_WORDS = ("新設", "取付", "取り付", "設置", "新規")
_BASE_WORDS = ("下地", "胴縁", "ボード", "合板")


def _is_decided(value: Any) -> bool:
    text = nfkc(value)
    return bool(text) and text not in (UNDECIDED, UNKNOWN)


@dataclass(frozen=True)
class Chain:
    """項目 id をノード、依存を辺とする有向グラフ。"""

    nodes: frozenset[str]
    edges: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)

    def children(self, node: str) -> tuple[tuple[str, str], ...]:
        return self.edges.get(node, ())

    def closure(self, roots: Iterable[str], *, max_depth: int = MAX_DEPTH,
                kinds: Iterable[str] | None = None) -> dict[str, int]:
        """``roots`` から辿れる項目と、その深さ。**根そのものは含まない。**

        ``kinds`` を渡すと、その種類の辺だけを辿る。**質問の型が決められない種類の辺を
        辿ると「1 問で全部決まる」という嘘の数字が出る**(K-65 追記 1)ので、
        呼ぶ側は必ず型に対応する種類を渡す。
        """
        allowed = set(kinds) if kinds is not None else set(EDGE_KINDS)
        depth: dict[str, int] = {}
        seen = set(roots)
        queue = deque((r, 0) for r in roots)
        while queue:
            node, d = queue.popleft()
            if d >= max_depth:
                continue
            for child, kind in self.children(node):
                if kind not in allowed or child in seen:
                    continue
                seen.add(child)
                depth[child] = d + 1
                queue.append((child, d + 1))
        return depth

    def as_dict(self) -> dict[str, Any]:
        kinds: dict[str, int] = {k: 0 for k in EDGE_KINDS}
        for children in self.edges.values():
            for _child, kind in children:
                kinds[kind] = kinds.get(kind, 0) + 1
        return {"節の数": len(self.nodes), "辺の数": sum(len(c) for c in self.edges.values()),
                "辺の種類ごと": kinds, "深さの上限": MAX_DEPTH}


def _add(edges: dict[str, list[tuple[str, str]]], parent: str, child: str, kind: str) -> None:
    if parent == child:
        return
    row = edges.setdefault(parent, [])
    if (child, kind) not in row:
        row.append((child, kind))


def build(items: Sequence[Mapping[str, Any]], *, finish: Mapping[str, Any] | None = None) -> Chain:
    """項目から連鎖のグラフを組む。

    辺を張る条件は基準の表のとおりで、**「答えが子を 1 つに決める」ときだけ**張る。
    迷ったら張らない(張らなければ連鎖の金額が小さく出るので、保守的な側に倒れる)。
    """
    edges: dict[str, list[tuple[str, str]]] = {}
    by_kamoku: dict[str, list[str]] = {}
    by_room: dict[str, list[str]] = {}
    by_room_part: dict[tuple[str, str], list[str]] = {}
    by_work: dict[str, list[str]] = {}
    for it in items:
        key = structure_key(it)
        kamoku = nfkc(key.科目) or nfkc(it.get("科目"))
        if _is_decided(kamoku):
            by_kamoku.setdefault(kamoku, []).append(it["id"])
        room = room_key(it.get("場所"))
        if _is_decided(it.get("場所")):
            by_room.setdefault(room, []).append(it["id"])
            if _is_decided(it.get("部位")):
                by_room_part.setdefault((room, nfkc(it["部位"])), []).append(it["id"])
        for field_name in ("工事", "品番"):
            if nfkc(it.get(field_name)):
                by_work.setdefault(nfkc(it[field_name]), []).append(it["id"])

    # 科目: 同じ科目の項目は、その科目が決まれば科目欄が決まる。
    for ids in by_kamoku.values():
        head = ids[0]
        for other in ids[1:]:
            _add(edges, head, other, "科目")

    # 室部位: 室の代表 → その室の各部位の代表 → その部位の項目。
    # (室が決まれば部位の属する室が決まり、部位が決まればその部位の仕上が決まる)
    for room, room_ids in by_room.items():
        head = room_ids[0]
        for (r, _part), part_ids in by_room_part.items():
            if r != room:
                continue
            part_head = part_ids[0]
            _add(edges, head, part_head, "室部位")
            for child in part_ids[1:]:
                _add(edges, part_head, child, "室部位")

    # 状態: 同じ室・部位の中で、撤去 → 新設 → 下地。
    work = {it["id"]: nfkc(it.get("工事")) for it in items}
    for ids in by_room_part.values():
        group = {w: [i for i in ids if any(x in work.get(i, "") for x in words)]
                 for w, words in (("撤去", _REMOVE_WORDS), ("新設", _NEW_WORDS), ("下地", _BASE_WORDS))}
        for parent in group["撤去"]:
            for child in group["新設"] + group["下地"]:
                _add(edges, parent, child, "状態")
        for parent in group["新設"]:
            for child in group["下地"]:
                _add(edges, parent, child, "状態")

    # 有無: 同じ工事の語・品番の項目は、その工事をするかどうかで一緒に決まる。
    for ids in by_work.values():
        for other in ids[1:]:
            _add(edges, ids[0], other, "有無")

    # まとめ方: 「同じもの」で結ばれた項目。
    for it in items:
        for other in it.get("同じもの") or ():
            _add(edges, it["id"], other, "まとめ方")

    # 資料: 照らし合わせの行 → その室・部位の項目(仕様書と図面のどちらを採るか)。
    for row in (finish or {}).get("照らし合わせ") or ():
        key = (room_key(row.get("室")), nfkc(row.get("部位")))
        ids = by_room_part.get(key)
        if not ids:
            continue
        for other in ids[1:]:
            _add(edges, ids[0], other, "資料")

    nodes = frozenset(it["id"] for it in items)
    return Chain(nodes=nodes, edges={k: tuple(v) for k, v in edges.items()})


#: 質問の型ごとに、辿ってよい辺の種類(K-65 追記 1)。
#:
#: **測った後に足した絞り込み。**基準の 2 節のままだと、同じ科目の項目が
#: `科目` の辺で全部つながるため、1 問の連鎖が案件全体に広がった
#: (合成の 10 項目で `数え直した割合` が 1.0 になった)。
#: いまのカードの型に「どの科目か」を聞く型は無いので、`科目` の辺を辿る型は 1 つも無い。
#: **これは緩めたのではなく、連鎖の数字を小さくする側の絞り込みである。**
TRAVERSAL = {
    "工事の有無": ("有無", "状態"),
    "どの室・部位か": ("室部位",),
    "状態": ("状態",),
    "数量": ("まとめ方",),
    "まとめ方": ("まとめ方",),
    "仕様書と図面のどちらを採るか": ("資料", "室部位"),
    "同じ物か別の物か": ("まとめ方",),
}


def decided_by(chain: Chain, direct_ids: Sequence[str], *, max_depth: int = MAX_DEPTH,
               card_type: str | None = None) -> dict[str, Any]:
    """1 つの質問が決める項目。**直接と連鎖を分けて返す。**

    ``card_type`` を渡すと、その型が決められる種類の辺だけを辿る。
    渡さないときは全部の種類を辿るので、**実際の質問には必ず型を渡す。**
    """
    direct = [i for i in dict.fromkeys(direct_ids) if i in chain.nodes]
    kinds = TRAVERSAL.get(card_type) if card_type else None
    reached = chain.closure(direct, max_depth=max_depth, kinds=kinds)
    return {
        "直接": direct,
        "連鎖": sorted(reached),
        "辿った辺の種類": list(kinds) if kinds is not None else "全部(型を渡していない)",
        "連鎖の深さごと": {str(d): sorted(i for i, dd in reached.items() if dd == d)
                      for d in sorted(set(reached.values()))},
    }
