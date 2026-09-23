"""【試作・採用するものではない】質問選定をまとまり(連結成分)単位に限定した場合に、
質問の列が今のエンジンと一致するかどうかだけを見るための使い捨てコード。

2026-09-22、キュー5「質問選定の高速化」の設計案づくりの途中。**これは実装案の
下書きですらなく、「一致するかどうか」を確かめるためだけのもの。** 既存のコードは
1行も変えていない。設計案が固まったら、この形をそのまま採るとは限らない。

実行:
    .venv/bin/python benchmarks/proto_killer_question_component_scoping.py equiv
    .venv/bin/python benchmarks/proto_killer_question_component_scoping.py time
"""
from __future__ import annotations
import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import z3
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import (
    KillerQuestionEngine, CandidateScore, Question,
    _HYPOTHESIS_PREFIX, _MAX_CANDIDATE_ENUMERATION,
)
from killer_question.dependency_graph import build_dependency_graph


class FastEngine(KillerQuestionEngine):
    """score_candidate をまとまり単位＋Optimize 使い回しで置き換えた試作。"""

    def _component_items(self, graph, ctx_pack, comp_key):
        return ctx_pack[comp_key]

    def _build_component_packs(self, graph):
        """連結成分ごとに (ctx, z3vars, exprs) を1回だけ作る。"""
        strong = {n for n in self._solver.variable_names()}
        packs = {}
        comp_of = {}
        for comp in graph.components():
            key = frozenset(comp)
            for n in comp:
                comp_of[n] = key
        # 変数を1つも参照しない制約は、どのまとまりにも属さない。全体で見る。
        global_constraints = []
        by_comp = {frozenset(c): [] for c in graph.components()}
        for cname in self._solver.constraint_names():
            refs = self._solver.referenced_variables(cname) & strong
            if not refs:
                global_constraints.append(cname)
                continue
            by_comp[comp_of[next(iter(refs))]].append(cname)
        return by_comp, global_constraints, comp_of

    def score_candidate(self, base_result, graph, variable):
        lower, upper = base_result.variables[variable].solved_range
        width = upper - lower + 1
        downstream = graph.connected_component(variable)
        impact = self._impact(variable, base_result)
        if width > _MAX_CANDIDATE_ENUMERATION:
            return CandidateScore(variable, 0.0, 0.0, impact, (), downstream,
                                  too_many_candidates=True)
        candidates = tuple(range(lower, upper + 1))
        base_widths = {
            name: base_result.variables[name].solved_range[1]
            - base_result.variables[name].solved_range[0]
            for name in downstream if name in base_result.variables
        }
        if not candidates or not base_widths:
            return CandidateScore(variable, 0.0, 0.0, impact, candidates, downstream)

        # ---- ここが試作の中身 ----
        # まとまりに属する変数と制約だけを z3 に載せる。
        comp_vars = set(downstream) | {variable}
        comp_vars &= set(base_result.variables)
        strong_names = set(base_result.variables)
        cnames = []
        for cname in self._solver.constraint_names():
            refs = self._solver.referenced_variables(cname) & strong_names
            if refs and refs <= comp_vars:
                cnames.append(cname)
            elif not refs:
                cnames.append(cname)  # 定数だけの制約は落とさない
        # 幅0の下流は、どう固定しても削減量に寄与しない（max(0, 0-new) <= 0）。
        targets = [n for n, w in base_widths.items() if w > 0]

        ctx = z3.Context()
        z3vars = {n: z3.Int(n, ctx) for n in strong_names}
        base_exprs = []
        for n in comp_vars:
            lo, hi = self._solver._variables[n].lower, self._solver._variables[n].upper
            base_exprs.append(z3.And(z3vars[n] >= lo, z3vars[n] <= hi))
        for cname in cnames:
            base_exprs.append(self._solver._constraint_by_name(cname).build(z3vars))

        reductions = []
        for value in candidates:
            opt = z3.Optimize(ctx=ctx)
            for e in base_exprs:
                opt.add(e)
            opt.add(z3vars[variable] == value)
            if opt.check() != z3.sat:
                reductions.append(0)
                continue
            total = 0
            for name in targets:
                bw = base_widths[name]
                opt.push(); opt.minimize(z3vars[name]); opt.check()
                lo2 = opt.model().eval(z3vars[name]).as_long(); opt.pop()
                opt.push(); opt.maximize(z3vars[name]); opt.check()
                hi2 = opt.model().eval(z3vars[name]).as_long(); opt.pop()
                total += max(0, bw - (hi2 - lo2))
            reductions.append(total)
        reduction_score = sum(reductions) / len(reductions)
        return CandidateScore(variable, reduction_score * impact, reduction_score,
                              impact, candidates, downstream)


# ---------------- 構造いろいろ ----------------
def chain(n, width=3):
    s = ConsistencySolver()
    for i in range(n):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(n - 1):
        s.add_relation(f"rel{i:04d}", f"e{i+1:04d}", ">=", f"e{i:04d}")
    return s

def independent(n, width=3):
    s = ConsistencySolver()
    for i in range(n):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    return s

def star(n, width=3):
    s = ConsistencySolver()
    for i in range(n):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(1, n):
        s.add_relation(f"rel{i:04d}", "e0000", "<=", f"e{i:04d}")
    return s

def clusters(n, size=5, width=3):
    """独立したまとまりが複数（まとまり限定がいちばん効く形）。"""
    s = ConsistencySolver()
    for i in range(n):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(n - 1):
        if (i + 1) % size != 0:
            s.add_relation(f"rel{i:04d}", f"e{i+1:04d}", ">=", f"e{i:04d}")
    return s

def with_total(n, width=3):
    """合計の制約が1本入った形（まとまりが1つになる）。"""
    s = ConsistencySolver()
    for i in range(n):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    s.add_variable("total", 10 * n, 10 * n + n * width, axis="text", source_axis="text")
    s.add_relation("sum", "total", "==",
                   lambda v: sum(v[f"e{i:04d}"] for i in range(n)))
    return s

STRUCTS = {"独立": independent, "鎖": chain, "星": star,
           "まとまり複数": clusters, "合計あり": with_total}


def question_sequence(engine_cls, builder, n, limit=6):
    eng = engine_cls(builder(n))
    seq = []
    for _ in range(limit):
        q = eng.next_question()
        if q is None:
            seq.append(("STOP", None))
            break
        seq.append((q.variable, round(q.score, 12)))
        eng.answer(q.variable, q.candidate_values[0])
    return seq


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "equiv"
    if mode == "equiv":
        print("=== 質問の列とスコアの一致（今のエンジン 対 試作） ===")
        for label, b in STRUCTS.items():
            for n in (6, 10):
                a = question_sequence(KillerQuestionEngine, b, n)
                c = question_sequence(FastEngine, b, n)
                print(f"{label:>6} N={n:>3}: {'一致' if a == c else '★不一致★'}  ({len(a)}手)")
                if a != c:
                    print("   今:", a); print("   案:", c)
    else:
        print("=== next_question() 1回の実時間（秒） ===")
        print(f"{'構造':>8} {'N':>5} {'今':>9} {'試作':>9} {'倍率':>7}")
        for label, b in (("鎖", chain), ("まとまり複数", clusters), ("合計あり", with_total)):
            for n in (16, 32, 50):
                s1 = b(n); t = time.perf_counter(); KillerQuestionEngine(s1).next_question()
                t_old = time.perf_counter() - t
                s2 = b(n); t = time.perf_counter(); FastEngine(s2).next_question()
                t_new = time.perf_counter() - t
                print(f"{label:>8} {n:>5} {t_old:>8.3f}s {t_new:>8.3f}s {t_old/t_new:>6.1f}x")
