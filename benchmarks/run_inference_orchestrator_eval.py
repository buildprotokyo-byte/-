"""統合オーケストレーターの再現可能な1000回性能測定。"""

from __future__ import annotations

import argparse
import json
import statistics

from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(probability * len(ordered) + 0.999999) - 1))
    return ordered[index]


def reading(value: int, source: str, method: str, axis: str) -> dict:
    return {
        "target": "door_count",
        "count_range": [value, value],
        "source_id": source,
        "source_fingerprint": f"sha256:{source}",
        "axis_id": axis,
        "method_id": method,
        "strength": "strong",
        "status": "confident",
        "calibrated": True,
        "model_confidence": 0.9,
        "unit": "count",
    }


def run(iterations: int) -> dict:
    orchestrator = InferenceOrchestrator(
        {
            "vision": MethodPolicy(calibrated=True, max_strength="strong"),
            "table": MethodPolicy(calibrated=True, max_strength="strong"),
        },
        {"drawing-A": "sha256:drawing-A", "spec-A": "sha256:spec-A"},
    )
    samples = {"total": [], "firewall": [], "z3": [], "escalation": []}
    event_count = 0
    for index in range(iterations):
        second = 5 if index % 2 == 0 else 7
        result = orchestrator.process(
            {
                "trace_id": f"benchmark-{index}",
                "element_id": "door-1",
                "evidence": [
                    reading(5, "drawing-A", "vision", "image"),
                    reading(second, "spec-A", "table", "text"),
                ],
                "relations": [],
            }
        )
        event_count += len(result.events)
        samples["total"].append(result.timings.total_seconds * 1000)
        samples["firewall"].append(result.timings.firewall_seconds * 1000)
        samples["z3"].append(result.timings.z3_seconds * 1000)
        samples["escalation"].append(result.timings.escalation_seconds * 1000)

    summary = {}
    for name, values in samples.items():
        summary[name] = {
            "median_ms": statistics.median(values),
            "p95_ms": percentile(values, 0.95),
            "p99_ms": percentile(values, 0.99),
        }
    return {"iterations": iterations, "event_count": event_count, "timings": summary}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    print(json.dumps(run(args.iterations), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
