"""同じ入力を何度解いても同じ答えが返ることの回帰テスト。

**なぜこれが要るか。** このリポジトリは「測った数値」を根拠に設計を決めている。
測定が走らせるたびに変わるなら、報告書に書いた数字は根拠にならない。

2026-09-21、難易度4の測定の最後に全件テストを回したところ
`tests/test_trial789_reproduction.py` の1件が落ちた。調べると、
**まったく同じ制約集合を解いても Z3 の unsat_core() が呼ぶたびに変わっていた**
(同一入力15回で核が5種類)。原因は z3.Int()/z3.Solver() を引数なしで呼ぶと
使われる**プロセス共有のグローバルコンテキスト**で、呼び出しをまたいで内部状態を
持ち越していた。解くたびに新しい ``z3.Context()` を作ることで解消した。

矛盾の核が変わると「人に確認してもらう要素」が変わり、監査の母集団も費用も変わる。
つまり ``docs/trial789_reproduction_report.md`` の費用・母集団の数値は、
**この修正以前は再現性が無かった**(10章25項)。

どの核も矛盾の説明としては正しいので、これは正しさの不具合ではなく
**再現性の不具合**である。だからこそ、テストが無いと誰も気づかない。
"""

from __future__ import annotations

import itertools

from arbitration.consistency_solver import ConsistencySolver

_REPEATS = 12


def _contradictory_cluster() -> ConsistencySolver:
    """積集合が空になる(= unsat になる)入力を組み立てる。

    同じ値を指すはずの要素を等式で結びながら、レンジを両立しないように置く。
    実案件でクラスタの等式と誤った読みが衝突する形をそのまま小さくしたもの。
    """
    solver = ConsistencySolver()
    names = [f"v{i}" for i in range(8)]
    for index, name in enumerate(names):
        low, high = (6, 7) if index % 2 == 0 else (2, 4)
        solver.add_variable(name, low, high, axis="image", unit="count")
    for left, right in itertools.combinations(names, 2):
        solver.add_relation(f"eq::{left}::{right}", left, "==", right)
    return solver


def _consistent_cluster() -> ConsistencySolver:
    solver = ConsistencySolver()
    for index in range(6):
        solver.add_variable(f"v{index}", 4, 9, axis="image", unit="count")
    for left, right in itertools.combinations([f"v{i}" for i in range(6)], 2):
        solver.add_relation(f"eq::{left}::{right}", left, "==", right)
    solver.add_variable("anchor", 5, 6, axis="text", unit="count")
    solver.add_relation("eq::v0::anchor", "v0", "==", "anchor")
    return solver


def test_矛盾の核は同じ入力に対して毎回同じになる() -> None:
    """**本題。** ここが割れていたせいで測定が再現しなかった。"""
    cores = {
        _contradictory_cluster().solve().conflicting_constraints
        for _ in range(_REPEATS)
    }
    assert len(cores) == 1, (
        f"同じ入力なのに矛盾の核が {len(cores)} 種類に割れた: {cores}"
    )
    assert cores.pop(), "unsat なのに核が空では、どこを確認すればよいか分からない"


def test_矛盾していない場合の収束レンジも毎回同じになる() -> None:
    results = {
        tuple(sorted(
            (name, item.solved_range)
            for name, item in _consistent_cluster().solve().variables.items()
        ))
        for _ in range(_REPEATS)
    }
    assert len(results) == 1, f"収束レンジが {len(results)} 種類に割れた"


def test_解いた状態が次の呼び出しに漏れない() -> None:
    """**共有コンテキストの実害はこれだった。**

    先に別の問題を解いてから解いても、単独で解いたときと同じ答えになること。
    グローバルコンテキストを使っていると、先に解いた内容が後の解に影響する。
    """
    alone = _contradictory_cluster().solve().conflicting_constraints
    for _ in range(_REPEATS):
        _consistent_cluster().solve()          # 別の問題を挟む
        _contradictory_cluster().solve()       # さらに同じ問題を挟む
    after = _contradictory_cluster().solve().conflicting_constraints
    assert after == alone, (
        "他の問題を解いた後だと答えが変わる。コンテキストが共有されている"
    )


def test_矛盾の核は矛盾に関与した制約だけを指す() -> None:
    """決定的にした核が、そもそも正しい核であることの確認。

    決定的でありさえすれば良いわけではない。無関係な制約まで核に入れると、
    人に無駄な確認をさせることになる。
    """
    result = _contradictory_cluster().solve()
    assert result.status == "unsat"
    for name in result.conflicting_constraints:
        assert name.startswith(("range::", "eq::")), name
