"""``ConsistencySolver`` に登録済みの変数・制約から、依存関係グラフを組み立てる。

役割分担(指示書より)
----------------------
依存関係という**"事実"**は、`arbitration/consistency_solver.py` の変数・制約の
宣言(``add_variable`` / ``add_relation`` / ``add_constraint``)にそのまま存在
している。ここで制約を再宣言したり、別のデータ構造として持ち直したりすると、
「同じ依存関係が2箇所に別の形で存在する」という、段階Bで既に一度踏んだ
落とし穴(`docs/stage_b_report.md` 6節)を繰り返すことになる。

そのため本モジュールは、``ConsistencySolver`` が公開する
``variable_names()`` / ``constraint_names()`` / ``referenced_variables()``
を使って**既存の登録内容を読み取るだけ**にし、依存関係そのものを新しく
定義することはしない。killer_question パッケージが持つのは、あくまで
「どの順で質問すべきか」という**戦略**の側だけである。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arbitration.consistency_solver import ConsistencySolver


@dataclass(frozen=True)
class DependencyGraph:
    """変数をノード、制約を辺とする無向グラフ。

    辺の向きは持たない。Z3 による大域求解では、ある変数を固定した影響は
    「直接つながっている変数」だけでなく、連結成分全体に伝播しうるため
    (`docs/killer_question_report.md` 参照)、向きよりも「同じ連結成分に
    属するかどうか」の方が実用上重要になる。
    """

    nodes: frozenset[str]
    edges: dict[str, frozenset[str]]
    constraints_by_edge: dict[frozenset[str], tuple[str, ...]] = field(default_factory=dict)

    def neighbors(self, name: str) -> frozenset[str]:
        return self.edges.get(name, frozenset())

    def connected_component(self, name: str) -> frozenset[str]:
        """``name`` と同じ連結成分に属する、他の変数の集合(``name`` 自身は含まない)。

        これが「依存関係でつながっている下流の要素」の実体で、影響度スコアの
        計算(``killer_question/engine.py``)で使う。
        """
        if name not in self.nodes:
            return frozenset()
        seen: set[str] = {name}
        frontier = [name]
        while frontier:
            current = frontier.pop()
            for neighbor in self.neighbors(current):
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        seen.discard(name)
        return frozenset(seen)

    def components(self) -> tuple[frozenset[str], ...]:
        """グラフ全体を連結成分に分割する(独立した上流クラスタの数を見るのに使う)。"""
        visited: set[str] = set()
        result: list[frozenset[str]] = []
        for node in sorted(self.nodes):
            if node in visited:
                continue
            component = self.connected_component(node) | {node}
            visited |= component
            result.append(frozenset(component))
        return tuple(result)


def build_dependency_graph(solver: ConsistencySolver) -> DependencyGraph:
    """``solver`` に登録済みの変数・制約から ``DependencyGraph`` を組み立てる。"""
    nodes = solver.variable_names()
    edges: dict[str, set[str]] = {name: set() for name in nodes}
    constraints_by_edge: dict[frozenset[str], list[str]] = {}

    for constraint_name in solver.constraint_names():
        referenced = solver.referenced_variables(constraint_name) & nodes
        if len(referenced) < 2:
            continue  # 単項の制約(値の固定など)は辺を作らない
        for a in referenced:
            for b in referenced:
                if a != b:
                    edges[a].add(b)
        constraints_by_edge.setdefault(frozenset(referenced), []).append(constraint_name)

    return DependencyGraph(
        nodes=nodes,
        edges={name: frozenset(neighbors) for name, neighbors in edges.items()},
        constraints_by_edge={key: tuple(value) for key, value in constraints_by_edge.items()},
    )
