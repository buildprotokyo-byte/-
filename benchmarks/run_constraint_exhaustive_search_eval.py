"""制約全探索の規模別性能・組合せ爆発安全性ベンチマーク。"""

from __future__ import annotations

import argparse
import json
import statistics

from arbitration.constraint_exhaustive_search import (
    ConstraintExhaustiveSearch,
    ExhaustiveSearchRequest,
    LinearExpression,
    SearchRelation,
)


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(probability * len(ordered) + 0.999999) - 1))
    return ordered[index]


def make_request(name: str, run: int) -> ExhaustiveSearchRequest:
    configurations = {
        "small": (3, 3, 1000, 100_000, 1000),
        "medium": (6, 5, 200, 100_000, 1000),
        "large": (10, 10, 200, 100_000, 1000),
        "exceeded": (12, 10, 200, 10_000, 1000),
    }
    variable_count, candidates, max_solutions, max_combinations, timeout_ms = configurations[name]
    names = tuple(f"v{index}" for index in range(variable_count))
    values = {variable: tuple(range(candidates)) for variable in names}
    # 枝刈りを測るため、small/mediumだけ合計制約を入れる。大規模は事前上限で止める。
    relations = ()
    if name in {"small", "medium"}:
        target = variable_count * (candidates - 1) // 2
        relations = (
            SearchRelation(
                "balanced_sum",
                LinearExpression(tuple((variable, 1) for variable in names)),
                "==",
                LinearExpression(constant=target),
            ),
        )
    return ExhaustiveSearchRequest(
        trace_id=f"benchmark-{name}-{run}",
        event_id=f"event-{name}-{run}",
        element_ids=names,
        candidate_values=values,
        relations=relations,
        max_solutions=max_solutions,
        max_combinations=max_combinations,
        timeout_ms=timeout_ms,
    )


def run(repetitions: int) -> dict:
    output = {}
    for scale in ("small", "medium", "large", "exceeded"):
        times = []
        solutions = []
        eliminated = []
        timeouts = 0
        limits = 0
        theoretical = 0
        statuses: dict[str, int] = {}
        for index in range(repetitions):
            result = ConstraintExhaustiveSearch().search(make_request(scale, index))
            times.append(result.elapsed_ms)
            solutions.append(result.solution_count)
            eliminated.append(sum(len(items) for items in result.eliminated_candidates.values()))
            timeouts += int(result.timed_out)
            limits += int(result.limit_exceeded)
            theoretical = result.theoretical_combinations
            statuses[result.status] = statuses.get(result.status, 0) + 1
        output[scale] = {
            "theoretical_combinations": theoretical,
            "enumerated_solutions_median": statistics.median(solutions),
            "eliminated_candidates_median": statistics.median(eliminated),
            "elapsed_median_ms": statistics.median(times),
            "elapsed_p95_ms": percentile(times, 0.95),
            "elapsed_p99_ms": percentile(times, 0.99),
            "timeouts": timeouts,
            "limit_exceeded": limits,
            "statuses": statuses,
        }
    return {"repetitions_per_scale": repetitions, "scales": output}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    args = parser.parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be positive")
    print(json.dumps(run(args.repetitions), indent=2))


if __name__ == "__main__":
    main()
