"""質問選定（キラークエスチョン）が z3 を何回呼び、どれだけ時間を使うかを測る。

作りかけ（2026-09-22）。キュー5「質問選定の高速化」の設計案づくりの途中で、
順番待ちのため中断した時点のもの。設計案そのものはまだ書いていない。

実行: `.venv/bin/python benchmarks/profile_killer_question.py`
"""
from __future__ import annotations
import sys, time, cProfile, pstats, io
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

import z3
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine
from killer_question.dependency_graph import build_dependency_graph

# --- z3 の呼び出し回数を数える計器 ---
COUNTS = {"check": 0, "optimize_new": 0, "opt_add": 0, "opt_check": 0, "solver_new": 0, "ctx_new": 0}
_orig_solver_check = z3.Solver.check
_orig_opt_check = z3.Optimize.check
_orig_opt_add = z3.Optimize.add
_orig_opt_init = z3.Optimize.__init__
_orig_solver_init = z3.Solver.__init__
_orig_ctx_init = z3.Context.__init__

def p_solver_check(self, *a, **k):
    COUNTS["check"] += 1
    return _orig_solver_check(self, *a, **k)
def p_opt_check(self, *a, **k):
    COUNTS["opt_check"] += 1
    return _orig_opt_check(self, *a, **k)
def p_opt_add(self, *a, **k):
    COUNTS["opt_add"] += 1
    return _orig_opt_add(self, *a, **k)
def p_opt_init(self, *a, **k):
    COUNTS["optimize_new"] += 1
    return _orig_opt_init(self, *a, **k)
def p_solver_init(self, *a, **k):
    COUNTS["solver_new"] += 1
    return _orig_solver_init(self, *a, **k)
def p_ctx_init(self, *a, **k):
    COUNTS["ctx_new"] += 1
    return _orig_ctx_init(self, *a, **k)

z3.Solver.check = p_solver_check
z3.Optimize.check = p_opt_check
z3.Optimize.add = p_opt_add
z3.Optimize.__init__ = p_opt_init
z3.Solver.__init__ = p_solver_init
z3.Context.__init__ = p_ctx_init


def build(n_vars: int, width: int = 3) -> ConsistencySolver:
    """鎖状につながった n_vars 個の要素。各要素はレンジ幅 width。"""
    s = ConsistencySolver()
    for i in range(n_vars):
        s.add_variable(f"e{i:04d}", 10, 10 + width, axis="image", source_axis="image")
    for i in range(n_vars - 1):
        s.add_relation(f"rel{i:04d}", f"e{i+1:04d}", ">=", f"e{i:04d}")
    return s


def reset():
    for k in COUNTS:
        COUNTS[k] = 0


def measure(n_vars: int, width: int = 3):
    solver = build(n_vars, width)
    eng = KillerQuestionEngine(solver)
    # solve() 単体
    reset()
    t0 = time.perf_counter()
    res = eng.solver.solve()
    t_solve = time.perf_counter() - t0
    solve_counts = dict(COUNTS)
    # next_question() 1回
    reset()
    t0 = time.perf_counter()
    q = eng.next_question()
    t_q = time.perf_counter() - t0
    q_counts = dict(COUNTS)
    return {
        "n": n_vars, "width": width,
        "solve_sec": t_solve, "solve_counts": solve_counts,
        "nq_sec": t_q, "nq_counts": q_counts,
        "picked": q.variable if q else None,
    }


if __name__ == "__main__":
    print(f"{'N':>5} {'幅':>3} | {'solve 1回':>10} {'opt_check':>9} | {'next_question 1回':>18} {'solver.check':>12} {'opt_check':>9} {'opt.add':>9}")
    for n in (4, 8, 16, 24, 32):
        r = measure(n)
        print(f"{r['n']:>5} {r['width']:>3} | {r['solve_sec']:>9.4f}s {r['solve_counts']['opt_check']:>9} | "
              f"{r['nq_sec']:>17.4f}s {r['nq_counts']['check']:>12} {r['nq_counts']['opt_check']:>9} {r['nq_counts']['opt_add']:>9}")
