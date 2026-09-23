"""50周目: 「人の入力は図面と独立している」は、何を根拠に言えているのか。

基準は `docs/d_human_independence_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。** 反実仮想はこのプロセスの中だけ。

使い方::

    .venv/bin/python benchmarks/measure_human_independence.py <図面PDFのパス>
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import intake.drawing_intake as di  # noqa: E402
from arbitration.method_policies import (  # noqa: E402
    DEFAULT_METHOD_POLICIES,
    MethodPolicy,
)
from intake.drawing_intake import (  # noqa: E402
    IntakeConfig,
    file_fingerprint,
    read_drawing,
    start_kit_fingerprint,
)
from intake.start_kit import ReferencePoint, StartKit  # noqa: E402

MM_PER_PT = 25.4 / 72.0
SPAN_PT = 400.0


@dataclass
class Capture:
    requests: list[dict[str, Any]] = field(default_factory=list)

    def wrap(self, original: Callable[..., dict[str, Any]], *, calibrated: bool):
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            request = original(*args, **kwargs)
            if calibrated:
                for entry in request["evidence"]:
                    entry["calibrated"] = True
            self.requests.append(request)
            return request

        return wrapper


def run(pdf_path: Path, kit: StartKit, *, calibrated: bool):
    capture = Capture()
    original = di.to_orchestrator_request
    saved: dict[str, MethodPolicy] = {}
    try:
        di.to_orchestrator_request = capture.wrap(original, calibrated=calibrated)
        if calibrated:
            saved = dict(DEFAULT_METHOD_POLICIES)
            DEFAULT_METHOD_POLICIES.update(
                {
                    key: MethodPolicy(calibrated=True, max_strength=value.max_strength)
                    for key, value in DEFAULT_METHOD_POLICIES.items()
                }
            )
        result = read_drawing(
            IntakeConfig(pdf_path=pdf_path, case_id="MEASURE-50", start_kit=kit)
        )
    finally:
        di.to_orchestrator_request = original
        if saved:
            DEFAULT_METHOD_POLICIES.clear()
            DEFAULT_METHOD_POLICIES.update(saved)
    return result, capture


def kit_with(page_number: int, length_mm: float) -> StartKit:
    return StartKit(
        reference_points=(
            ReferencePoint(
                page_number=page_number,
                axis="horizontal",
                point_a_pt=(100.0, 400.0),
                point_b_pt=(100.0 + SPAN_PT, 400.0),
                actual_length_mm=length_mm,
                entered_by="測定用の仮の入力",
            ),
        )
    )


def keys_for_reference(capture: Capture) -> list[str]:
    for request in capture.requests:
        if request["element_id"].startswith("基準寸法"):
            return sorted(
                {
                    (entry.get("source_fingerprint") or entry["source_id"])[:8]
                    for entry in request["evidence"]
                }
            )
    return []


def tier_counts(result) -> dict[int, int]:
    out: dict[int, int] = {}
    for item in result.decisions:
        out[item.tier] = out.get(item.tier, 0) + 1
    return out


def reference_decision(result):
    for item in result.decisions:
        if item.target.startswith("基準寸法"):
            return item
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2

    probe = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="PROBE-50"))
    page = next((p for p in probe.pages if p.scale is not None), None)
    if page is None:
        print("縮尺が読めるページが無い")
        return 2
    copied = SPAN_PT * MM_PER_PT * page.scale.denominator

    print(f"図面: {pdf_path.name} / 基準点を置くページ: {page.page_number}\n", flush=True)

    print("=== Q1 鍵を決める入力(コードから)===")
    print("   図面: ファイルの中身の sha256(file_fingerprint。名前やパスは混ぜない)")
    print("   人の入力: 入れた値そのものの sha256(start_kit_fingerprint)")

    print("\n=== Q2 同じ PDF を別の source_id で渡すと鍵は分かれるか ===", flush=True)
    # `source_id` は case_id から作られるので、案件名を変えて 2 回指紋を取る。
    same_file_twice = file_fingerprint(pdf_path) == file_fingerprint(pdf_path)
    print(f"   同じファイルの指紋が一致するか: {'はい' if same_file_twice else 'いいえ'}")
    print(f"   → 鍵は {'分かれない(中身で見分けている)' if same_file_twice else '分かれる'}")

    conditions = [
        ("S0 書き写し", copied),
        ("S1 独自+3%", copied * 1.03),
        ("S2 独自+10%", copied * 1.10),
    ]

    print("\n=== Q3・Q4 人の入力の値を変えたときの鍵 ===", flush=True)
    kit_keys = {
        label: start_kit_fingerprint(kit_with(page.page_number, length))[:8]
        for label, length in conditions
    }
    for label, value in kit_keys.items():
        print(f"   {label}: {value}")
    q3 = len(set(kit_keys.values())) == len(kit_keys)
    print(f"   Q3 値を変えると鍵は変わるか: {'はい' if q3 else 'いいえ'}")
    print(f"   Q4 書き写しかどうかで鍵に違いが出るか: "
          f"{'いいえ(見分けられない)' if q3 else '判定不能'}")

    print("\n=== Q5 反実仮想での階層1 ===", flush=True)
    results: dict[str, dict[str, Any]] = {}
    for label, length in conditions:
        result, capture = run(pdf_path, kit_with(page.page_number, length), calibrated=True)
        decision = reference_decision(result)
        tiers = tier_counts(result)
        results[label] = {
            "tier1": tiers.get(1, 0),
            "reference_tier": decision.tier if decision else None,
            "reference_reasons": list(decision.reasons) if decision else [],
            "keys": keys_for_reference(capture),
        }
        print(f"   {label}: 階層1 {tiers.get(1, 0)} 件 / "
              f"基準寸法は階層{decision.tier if decision else '—'}")
        if decision:
            for reason in decision.reasons:
                print(f"      理由: {reason}")

    print("\n=== 対照 ===", flush=True)
    control1 = results["S0 書き写し"]["tier1"] == 1
    print(f"対照1 S0 の階層1 が 49周目と同じ 1 件か: {'はい' if control1 else 'いいえ'}")
    control2 = same_file_twice
    print(f"対照2 同じ PDF の鍵が分かれないか: {'はい' if control2 else 'いいえ'}")
    repeats = []
    for _ in range(3):
        row = []
        for label, length in conditions:
            result, _ = run(pdf_path, kit_with(page.page_number, length), calibrated=True)
            row.append(tier_counts(result).get(1, 0))
        repeats.append(tuple(row))
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回: {repeats} → {'はい' if control3 else 'いいえ'}")

    s0 = results["S0 書き写し"]["tier1"]
    s1 = results["S1 独自+3%"]["tier1"]
    s2 = results["S2 独自+10%"]["tier1"]

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif s0 == 0:
        print("   **S0 が落ちた。見込みが外れている。何が落としたのかを名指しする。**")
    elif s0 >= 1 and (s1 == 0 or s2 == 0):
        print("   **S0 が通り、独自の値が落ちた。**"
              "**いまの一致の見方は、独立性を確かめるどころか、書き写しを選んで通している。**"
              "「人の入力を2つ目のデータ源として数えてよいか」は**仕組みでは決められない。**"
              "48周目の札は取り下げず、この事実を添えて出し直す。")
    else:
        print("   **S0・S1・S2 が同じ。一致の見方は書き写しを優遇していない。札はそのままでよい。**")

    payload = {
        "round": 50,
        "page": page.page_number,
        "Q1": {"drawing": "file_fingerprint(中身の sha256)",
                "human": "start_kit_fingerprint(入れた値の sha256)"},
        "Q2_same_file_same_key": same_file_twice,
        "Q3_value_changes_key": q3,
        "Q3_keys": kit_keys,
        "Q4_can_detect_copying": False if q3 else None,
        "Q5": results,
        "controls": {"1_s0_matches_round_49": control1,
                      "2_same_pdf_one_key": control2,
                      "3_repeat": [list(r) for r in repeats]},
    }
    out = ROOT / "docs" / "d_human_independence_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
