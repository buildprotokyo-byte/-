"""今のエンジンと、まとまり限定の試作の実時間を突き合わせる（使い捨ての計測）。

2026-09-22 実測。結果は docs/killer_question_speedup_design.md 4-3節。
**まとまりが分かれた形では最大20.8倍、全体が1つのまとまりになる形では1.2〜1.3倍**
という、構造による極端な違いがここで出た。

実行: `.venv/bin/python benchmarks/compare_killer_question_prototype.py`
注意: 今のエンジンは N=32 で1問に約38秒かかる。最後の「大きい規模」は鎖 N=100 で
数分かかるので、時間が無いときは範囲を狭めること。
"""
import sys, time
import pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "benchmarks"))
from proto_killer_question_component_scoping import FastEngine, chain, clusters
from killer_question.engine import KillerQuestionEngine
print(f"{'構造':>10} {'N':>4} {'今':>9} {'試作':>9} {'倍率':>7}", flush=True)
for label, b in (("鎖", chain), ("まとまり複数", clusters)):
    for n in (16, 24, 32):
        t = time.perf_counter(); KillerQuestionEngine(b(n)).next_question()
        old = time.perf_counter() - t
        t = time.perf_counter(); FastEngine(b(n)).next_question()
        new = time.perf_counter() - t
        print(f"{label:>10} {n:>4} {old:>8.2f}s {new:>8.2f}s {old/new:>6.1f}x", flush=True)
print("\n=== 試作だけ、大きい規模（1問目）===", flush=True)
for label, b in (("まとまり複数", clusters), ("鎖", chain)):
    for n in (50, 100):
        t = time.perf_counter(); FastEngine(b(n)).next_question()
        print(f"{label} N={n}: {time.perf_counter()-t:.2f}s", flush=True)
