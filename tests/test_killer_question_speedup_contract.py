"""質問選定を速くする実装が、採用される前に満たさなければならない条件。

``docs/killer_question_speedup_design.md`` の設計案が、**正しさの根拠として
寄りかかっている性質**をテストにしたもの。**このファイルは高速化そのものを
実装しない。** 今のエンジンに対して走らせて、設計案の前提が実際に成り立って
いることを確かめる。前提が1つでも崩れたら、その時点で設計案は使えない。

なぜこの形なのか
----------------
高速化は「速いが違う答えを出す」形で簡単に壊れる。おーちゃんが挙げた条件は
2つだけで、どちらも**出力の同一性**の話である。

1. 選ばれる質問の順番が、今のエンジンと完全に同じであること
2. 決定性を壊さないこと

そこで、将来の候補実装を今のエンジンと突き合わせる仕組み
(:func:`assert_same_question_sequence`)を先に用意し、設計案が前提にしている
性質を個別に固定した。候補実装ができたら、``ENGINE_UNDER_TEST`` を差し替える
だけで同じ突き合わせが走る。

**このファイルが緑になっても「速くなった」ことの証明にはならない。**
速さは別途計測する(重い計測は Codex 側の固定ベンチマーク。
``docs/killer_question_speedup_design.md`` 7節「Codex 側で測るべき項目」)。
"""

from __future__ import annotations

import random

import pytest

from arbitration.consistency_solver import ConsistencySolver
from killer_question.dependency_graph import build_dependency_graph
from killer_question.engine import CandidateScore, KillerQuestionEngine

#: 突き合わせの対象。候補実装ができたらここを差し替える。今は今のエンジン同士を
#: 突き合わせるので、**この仕組み自体が正しく動くこと**の確認になっている。
ENGINE_UNDER_TEST = KillerQuestionEngine


# ---------------------------------------------------------------------------
# 合成データ(実図面は使わない)
# ---------------------------------------------------------------------------
def _chain(n: int, width: int = 2) -> ConsistencySolver:
    """鎖状: e0 <= e1 <= ... 。1つ決めると隣が絞られる。"""
    solver = ConsistencySolver()
    for i in range(n):
        solver.add_variable(f"e{i}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(n - 1):
        solver.add_relation(f"rel{i}", f"e{i+1}", ">=", f"e{i}")
    return solver


def _two_islands(size: int = 3, width: int = 2) -> ConsistencySolver:
    """互いにつながらない2つのまとまり。まとまり限定の前提そのもの。"""
    solver = ConsistencySolver()
    for island in ("a", "b"):
        for i in range(size):
            solver.add_variable(
                f"{island}{i}", 10, 10 + width, axis="image", source_axis="image"
            )
        for i in range(size - 1):
            solver.add_relation(
                f"rel_{island}{i}", f"{island}{i+1}", ">=", f"{island}{i}"
            )
    return solver


def _star(n: int = 4, width: int = 2) -> ConsistencySolver:
    """中心1つに全部がぶら下がる形。"""
    solver = ConsistencySolver()
    for i in range(n):
        solver.add_variable(f"e{i}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(1, n):
        solver.add_relation(f"rel{i}", "e0", "<=", f"e{i}")
    return solver


def _with_total(n: int = 3, width: int = 2) -> ConsistencySolver:
    """合計の制約が1本入った形。全部が1つのまとまりになる。"""
    solver = ConsistencySolver()
    for i in range(n):
        solver.add_variable(f"e{i}", 10, 10 + width, axis="image", source_axis="image")
    solver.add_variable("total", 10 * n, 10 * n + n * width, axis="text", source_axis="text")
    solver.add_relation("sum", "total", "==", lambda v: sum(v[f"e{i}"] for i in range(n)))
    return solver


BUILDERS = {
    "鎖": lambda: _chain(4),
    "まとまり2つ": lambda: _two_islands(3),
    "星": lambda: _star(4),
    "合計あり": lambda: _with_total(3),
}


# ---------------------------------------------------------------------------
# 突き合わせの仕組み(候補実装ができたらそのまま使う)
# ---------------------------------------------------------------------------
def question_sequence(engine_cls, build_solver, *, limit: int = 8):
    """質問を順に選んで答えていき、(変数名, スコア) の列と終了理由を返す。

    回答は常に「候補のいちばん小さい値」にする。どの値を選ぶかで後続の質問は
    変わるが、**両方の実装に同じ規則を使う**ので突き合わせとしては成立する。
    """
    engine = engine_cls(build_solver())
    sequence: list[tuple[str, float]] = []
    for _ in range(limit):
        question = engine.next_question()
        if question is None:
            break
        sequence.append((question.variable, question.score))
        engine.answer(question.variable, question.candidate_values[0])
    final = engine._finish()  # noqa: SLF001 - 終了理由まで突き合わせたい
    return tuple(sequence), final.stopped_reason, tuple(sorted(final.remaining_unresolved))


def assert_same_question_sequence(reference_cls, candidate_cls, build_solver, label=""):
    """2つの実装が、質問の列・スコア・最終状態まで完全に一致することを確かめる。

    **スコアは丸めずに比べる。** 今のエンジンは同点を ``score == max_score`` と
    いう浮動小数の厳密比較で判定しているため、わずかなビットの違いが
    同点崩しの結果を変えうる。丸めて比べると、その取りこぼしを見逃す。
    """
    ref = question_sequence(reference_cls, build_solver)
    cand = question_sequence(candidate_cls, build_solver)
    assert ref == cand, (
        f"{label}: 質問の列が一致しない\n"
        f"  今のエンジン: {ref}\n"
        f"  候補の実装  : {cand}"
    )


@pytest.mark.parametrize("label", list(BUILDERS))
def test_候補実装は今のエンジンと質問の列が完全に一致する(label):
    """おーちゃんの条件1。候補実装を差し替えるまでは、仕組みの自己確認になる。"""
    assert_same_question_sequence(
        KillerQuestionEngine, ENGINE_UNDER_TEST, BUILDERS[label], label=label
    )


@pytest.mark.parametrize("label", list(BUILDERS))
def test_同じ入力からは毎回同じ質問の列が出る(label):
    """おーちゃんの条件2(決定性)。

    Z3 の共有コンテキストが原因で ``unsat_core()`` が毎回変わるという
    **本物の非決定性**が実際にあった(commit ``03b8eac``)。同じ入力を繰り返して
    列が割れないことを、実装を変える前後どちらでも確かめられるようにしておく。
    """
    runs = {question_sequence(ENGINE_UNDER_TEST, BUILDERS[label]) for _ in range(3)}
    assert len(runs) == 1, f"{label}: 同じ入力で {len(runs)} 種類の列が出た"


# ---------------------------------------------------------------------------
# 設計案が寄りかかっている性質(1つでも崩れたら設計案は使えない)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", list(BUILDERS))
def test_制約はまとまりをまたがない(label):
    """まとまり限定(段階1)の前提。

    2つ以上の変数を参照する制約は、その参照先が必ず同じ連結成分に収まる
    (辺を張るのがその制約自身だから)。**これが成り立つからこそ、まとまりの
    外の変数と制約を z3 に載せなくてよい。**
    """
    solver = BUILDERS[label]()
    graph = build_dependency_graph(solver)
    components = graph.components()
    for name in solver.constraint_names():
        referenced = solver.referenced_variables(name) & graph.nodes
        if len(referenced) < 2:
            continue
        holders = [c for c in components if referenced <= c]
        assert len(holders) == 1, (
            f"制約 '{name}' の参照先 {sorted(referenced)} が1つのまとまりに収まっていない"
        )


def test_変数を参照しない制約は今は作れないので黙って飛ばしてはいけない():
    """まとまり限定(段階1)の境界。**設計案を書く途中で実測して分かったこと。**

    「変数を1つも参照しない制約(定数だけの式)は、どのまとまりにも属さないので
    まとまり限定で落ちてしまう」と最初は考えたが、**実際には今の設計では
    そういう制約は作れない。** z3 の式は必ずコンテキストに属し、``build`` の中で
    コンテキストを手に入れる方法は渡された変数辞書しかないためである。

    - ``lambda v: 1 == 2`` … 素の Python の ``False`` になり ``Boolean expression expected``
    - ``lambda v: z3.BoolVal(False)`` … 別のコンテキストの式なので ``invalid argument``
    - ``lambda v: z3.BoolVal(False, v["e0"].ctx)`` … 通るが、その瞬間に ``e0`` を
      参照したことになり、``e0`` のまとまりに属する

    つまり**「参照先が空の制約」は現時点では必ず壊れた入力**である。
    まとまりごとに制約を仕分ける実装は、参照先が空の制約に出会ったときに
    **黙って飛ばしてはならない**(飛ばすと、今は例外になっている壊れた入力が
    静かに通ってしまう)。今と同じように失敗させること。
    """
    import z3

    def build_solver(build):
        solver = ConsistencySolver()
        solver.add_variable("e0", 10, 12, axis="image", source_axis="image")
        solver.add_constraint("定数だけ", build)
        return solver

    # 参照先が空になる2つの書き方は、どちらも solve() で例外になる。
    for build in (lambda v: 1 == 2, lambda v: z3.BoolVal(False)):
        solver = build_solver(build)
        assert solver.referenced_variables("定数だけ") == frozenset()
        with pytest.raises(z3.z3types.Z3Exception):
            solver.solve()

    # コンテキストを変数から取れば通るが、そのとき参照先はもう空ではない。
    solver = build_solver(lambda v: z3.BoolVal(False, v["e0"].ctx))
    assert solver.referenced_variables("定数だけ") == frozenset({"e0"})
    assert not solver.solve().is_consistent


def test_絞り込んだ範囲は答えが1つしかないので何度解いても同じ():
    """コンテキストや solver を使い回してよい根拠。

    候補の評価が読んでいるのは「矛盾するかしないか」と「各変数の最小値・最大値」
    だけで、すべての変数に上下限が付いているため領域は有界。有界な整数線形領域の
    最小値・最大値は**答えが1つしかない**ので、内部状態の持ち越しでは変わらない。
    ``unsat_core()`` は正しい答えが複数あるという点でこれと違い、**壊れたのは
    そちらだけ**だった(``03b8eac``)。
    """
    solver = _with_total(3)
    first = {n: s.solved_range for n, s in solver.solve().variables.items()}
    for _ in range(5):
        again = {n: s.solved_range for n, s in solver.solve().variables.items()}
        assert again == first


def test_拘束しない幅0の要素をまとまりに足してもスコアは変わらない():
    """段階3「幅0の下流は計算しない」が完全に等価であることの根拠。

    削減量は ``max(0, 元の幅 - 新しい幅)``。元の幅が0なら新しい幅が何であっても
    0以下なので、寄与は必ず0になる。したがって幅0の変数は最小値・最大値を
    求める必要がない。

    **足す要素は、他の要素を拘束しないものでなければならない。** 拘束する形
    (例: ``fixed=11`` で ``fixed <= e2``)にすると ``e2`` の範囲自体が動き、
    「幅0だから寄与しない」ではなく「問題が別物になった」ためにスコアが変わる
    (最初に書いたときこれで引っかかった: 2.0 -> 1.333)。
    """
    def build(with_fixed: bool) -> ConsistencySolver:
        solver = _chain(3)
        if with_fixed:
            # 幅0で、かつ e0 の範囲 [10,12] を一切狭めない(5 <= e0 は常に真)。
            solver.add_variable("fixed", 5, 5, axis="image", source_axis="image")
            solver.add_relation("rel_fixed", "fixed", "<=", "e0")
        return solver

    # 前提の確認: 足しても既存の要素の絞り込み結果が変わっていないこと。
    plain = {n: s.solved_range for n, s in build(False).solve().variables.items()}
    added = {n: s.solved_range for n, s in build(True).solve().variables.items()}
    assert added["fixed"] == (5, 5)
    assert {k: v for k, v in added.items() if k != "fixed"} == plain

    scores = []
    for with_fixed in (False, True):
        engine = KillerQuestionEngine(build(with_fixed))
        result = engine.solver.solve()
        graph = build_dependency_graph(engine.solver)
        scores.append(engine.score_candidate(result, graph, "e0").reduction_score)
    assert scores[0] == scores[1], (
        f"幅0の要素を足しただけでスコアが動いた: {scores[0]} -> {scores[1]}"
    )


def test_質問の選定は候補を並べた順番に依存しない():
    """段階2(まとまりをまたいだ評価結果の使い回し)の前提。

    使い回すと、候補が計算される順番は変わる。選定がその順番に依存していたら、
    使い回した瞬間に質問の順番が変わってしまう。
    """
    solver = _two_islands(3)
    engine = KillerQuestionEngine(solver)
    result = engine.solver.solve()
    graph = build_dependency_graph(engine.solver)
    scores = [
        engine.score_candidate(result, graph, name)
        for name in sorted(result.variables)
    ]
    picked = engine._pick_best_by_score(scores)  # noqa: SLF001

    rng = random.Random(20260922)
    for _ in range(10):
        shuffled = list(scores)
        rng.shuffle(shuffled)
        assert engine._pick_best_by_score(shuffled) == picked  # noqa: SLF001


def test_一方のまとまりへの回答は他方の解を変えない():
    """段階2の前提そのもの。

    まとまりをまたぐ制約が無い以上、片方のまとまりの要素を確定しても、
    もう片方の絞り込み結果は動かない。**動かないからこそ、もう片方の評価結果を
    次の質問でも使い回せる。**
    """
    engine = KillerQuestionEngine(_two_islands(3))
    before = {
        name: sol.solved_range
        for name, sol in engine.solver.solve().variables.items()
        if name.startswith("b")
    }
    engine.answer("a0", 11)
    after = {
        name: sol.solved_range
        for name, sol in engine.solver.solve().variables.items()
        if name.startswith("b")
    }
    assert before == after


def test_同点は実際に起きるのでスコアの計算の順番を変えてはいけない():
    """同点崩しが飾りではないことを固定する。

    今のエンジンは ``score == max_score`` という**浮動小数の厳密比較**で同点を
    判定している。対称な形では同点が実際に起きるので、高速化で足し算の順番が
    変わってビットがずれると、**同点崩しの結果すなわち質問の順番が変わりうる。**
    だから段階3は「計算の順番を変えない」ことを条件にする。
    """
    # 左右対称な2つのまとまり。対応する要素どうしが必ず同点になる。
    solver = _two_islands(3)
    engine = KillerQuestionEngine(solver)
    result = engine.solver.solve()
    graph = build_dependency_graph(engine.solver)
    scores = [
        engine.score_candidate(result, graph, name) for name in sorted(result.variables)
    ]
    top = max(s.score for s in scores)
    tied = [s for s in scores if s.score == top]
    assert len(tied) > 1, "この形では同点が起きるはずで、起きないなら前提が変わっている"


def test_候補が多すぎる要素は質問に選ばれないが未確定のまま残る():
    """段階1〜3のどれも、この扱いを変えてはいけない。

    列挙できないことを理由に確定済みへ回すと、**人が見ないまま数量が通る。**
    """
    solver = ConsistencySolver()
    solver.add_variable("広い", 0, 100_000, axis="image", source_axis="image")
    solver.add_variable("狭い", 10, 12, axis="image", source_axis="image")
    solver.add_relation("rel", "狭い", "<=", "広い")

    engine = KillerQuestionEngine(solver)
    result = engine.solver.solve()
    graph = build_dependency_graph(engine.solver)
    wide = engine.score_candidate(result, graph, "広い")
    assert wide.too_many_candidates
    assert not wide.is_askable
    assert "広い" in engine._unresolved_names(result)  # noqa: SLF001
