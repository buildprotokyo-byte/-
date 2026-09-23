"""47周目(キラークエスチョン): この図面で自動確定が1件でも出る道はあるのか。

基準は `docs/d_tier1_reachability_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。** 反実仮想はこのプロセスの中だけで、
登録簿の辞書と要求の作り方を差し替えて作る。

使い方::

    .venv/bin/python benchmarks/measure_tier1_reachability.py <図面PDFのパス>

**出すのは件数と対象名の形だけ。図面に印字された文字は出さない。**
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402

import intake.drawing_intake as di  # noqa: E402
from arbitration.method_policies import (  # noqa: E402
    DEFAULT_METHOD_POLICIES,
    MethodPolicy,
)
from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402
from intake.start_kit import ReferencePoint, StartKit  # noqa: E402

MM_PER_PT = 25.4 / 72.0

#: 対象名から中身が読み取れないように、形だけ残す。
def _shape(target: str) -> str:
    head = target.split("::")[0]
    return head if head else "(名前なし)"


@dataclass
class Capture:
    """`to_orchestrator_request` が作った要求を覗いて記録する。"""

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


@dataclass
class Outcome:
    label: str
    targets: int = 0
    tier_counts: Counter = field(default_factory=Counter)
    sources_overall: int = 0
    targets_with_two_sources: int = 0
    tier1_targets: list[str] = field(default_factory=list)
    tier1_reasons: list[str] = field(default_factory=list)
    two_source_targets: list[str] = field(default_factory=list)


def run_once(
    pdf_path: Path, *, start_kit: StartKit | None, calibrated: bool, label: str
) -> Outcome:
    """1 条件ぶん回す。`calibrated=True` なら反実仮想の世界で回す。"""
    capture = Capture()
    original = di.to_orchestrator_request
    patched_policies: dict[str, MethodPolicy] | None = None
    saved: dict[str, MethodPolicy] = {}
    try:
        di.to_orchestrator_request = capture.wrap(original, calibrated=calibrated)
        if calibrated:
            saved = dict(DEFAULT_METHOD_POLICIES)
            patched_policies = {
                key: MethodPolicy(calibrated=True, max_strength=value.max_strength)
                for key, value in DEFAULT_METHOD_POLICIES.items()
            }
            DEFAULT_METHOD_POLICIES.update(patched_policies)
        config = IntakeConfig(
            pdf_path=pdf_path, case_id="MEASURE-47", start_kit=start_kit
        )
        result = read_drawing(config)
    finally:
        di.to_orchestrator_request = original
        if saved:
            DEFAULT_METHOD_POLICIES.clear()
            DEFAULT_METHOD_POLICIES.update(saved)

    outcome = Outcome(label=label)
    outcome.targets = len(result.decisions)
    for decision in result.decisions:
        outcome.tier_counts[decision.tier] += 1
        if decision.tier == 1:
            outcome.tier1_targets.append(_shape(decision.target))
            outcome.tier1_reasons.extend(decision.reasons)

    fingerprints: set[str] = set()
    for request in capture.requests:
        per_target = {
            entry.get("source_fingerprint") or entry.get("source_id")
            for entry in request["evidence"]
        }
        fingerprints |= per_target
        if len(per_target) >= 2:
            outcome.targets_with_two_sources += 1
            outcome.two_source_targets.append(_shape(request["element_id"]))
    outcome.sources_overall = len(fingerprints)
    return outcome


def build_start_kit(pdf_path: Path) -> tuple[StartKit | None, str]:
    """**仮の人の入力**を 1 つ作る。印字された縮尺とぴったり合う値にする。

    **本物の人の入力ではない。**「人が完璧に入れた場合」を測るためだけのもの。
    リポジトリには保存しない。
    """
    probe = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="PROBE-47"))
    for page in probe.pages:
        if page.scale is None:
            continue
        denominator = page.scale.denominator
        span_pt = 400.0
        return (
            StartKit(
                reference_points=(
                    ReferencePoint(
                        page_number=page.page_number,
                        axis="horizontal",
                        point_a_pt=(100.0, 400.0),
                        point_b_pt=(100.0 + span_pt, 400.0),
                        actual_length_mm=span_pt * MM_PER_PT * denominator,
                        entered_by="測定用の仮の入力",
                    ),
                )
            ),
            f"ページ {page.page_number} の印字された縮尺に合わせた基準点 1 つ",
        )
    return None, "縮尺が読めるページが無かったので仮の入力は作れない"


def synthetic_pdf(tmp: Path) -> Path:
    """対照4 用の合成図面。テストと同じ作り方で 1 ページ。"""
    from tests.test_drawing_intake import _vector_plan_page

    path = tmp / "synthetic_47.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    doc.save(path)
    doc.close()
    return path


def show(outcome: Outcome) -> None:
    print(f"   Z1 対象の数: {outcome.targets}")
    print(f"   Z2 独立したデータ源の数: {outcome.sources_overall}")
    print(f"   Z3 階層1(自動確定): {outcome.tier_counts.get(1, 0)}")
    print(
        f"   Z4 階層2: {outcome.tier_counts.get(2, 0)} / "
        f"階層3: {outcome.tier_counts.get(3, 0)}"
    )
    print(f"   Z5 2つ以上のデータ源が主張している対象: {outcome.targets_with_two_sources}")
    if outcome.two_source_targets:
        print(f"      その対象の形: {sorted(set(outcome.two_source_targets))}")
    if outcome.tier1_targets:
        print(f"   Z6 通った対象の形: {sorted(set(outcome.tier1_targets))}")
        for reason in dict.fromkeys(outcome.tier1_reasons):
            print(f"      理由: {reason}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2

    print(f"図面: {pdf_path.name}\n")

    print("=== G0 いまのまま(スタートキット無し) ===", flush=True)
    g0 = run_once(pdf_path, start_kit=None, calibrated=False, label="G0")
    show(g0)

    start_kit, kit_note = build_start_kit(pdf_path)
    print(f"\n仮の人の入力: {kit_note}", flush=True)

    print("\n=== G1 仮の人の入力を足す ===", flush=True)
    g1 = run_once(pdf_path, start_kit=start_kit, calibrated=False, label="G1")
    show(g1)

    print("\n=== G2 さらに4手法すべてを校正済みにした世界(反実仮想) ===", flush=True)
    g2 = run_once(pdf_path, start_kit=start_kit, calibrated=True, label="G2")
    show(g2)

    print("\n=== 対照 ===", flush=True)
    control1 = g0.tier_counts.get(1, 0) == 0
    print(f"対照1 G0 の階層1 が 0 件か: {'はい' if control1 else 'いいえ'}")

    again = run_once(pdf_path, start_kit=None, calibrated=False, label="G0再")
    control2 = again.tier_counts == g0.tier_counts
    print(f"対照2 反実仮想のあと G0 に戻るか: {'はい' if control2 else 'いいえ'}")

    repeats = [
        run_once(pdf_path, start_kit=start_kit, calibrated=True, label="G2反復")
        .tier_counts.get(1, 0)
        for _ in range(3)
    ]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(G2 の階層1): {repeats} → {'はい' if control3 else 'いいえ'}")

    import tempfile

    tmp = Path(tempfile.mkdtemp())
    syn_path = synthetic_pdf(tmp)
    syn_kit, _ = build_start_kit(syn_path)
    syn = run_once(syn_path, start_kit=syn_kit, calibrated=True, label="合成G2")
    control4 = syn.tier_counts.get(1, 0) >= 1
    print(
        f"対照4 合成図面に G2 を当てると階層1が出るか: "
        f"{syn.tier_counts.get(1, 0)} 件 → {'はい' if control4 else 'いいえ'}"
    )
    if syn.tier1_targets:
        print(f"      合成で通った対象の形: {sorted(set(syn.tier1_targets))}")

    print("\n=== 採否(基準に先に書いた線) ===")
    z5 = g2.targets_with_two_sources
    z3 = g2.tier_counts.get(1, 0)
    if not (control1 and control4):
        print("   **対照1 か対照4 が通らなかった。結論を出さない。**")
    elif z5 == 0:
        print("   Z5 = 0 → **校正は目標に効かない。壁は独立性である。**"
              "次に問うのは「2つ目のデータ源を実際にどう入れるか」。")
    elif z3 == 0:
        print(f"   Z5 = {z5} ≧ 1 かつ Z3(G2) = 0 → "
              "**独立性はある。だが校正以外にも壁がある。**何が止めているかを名指しする。")
    else:
        print(f"   Z3(G2) = {z3} ≧ 1 → **校正が本当の壁で、その大きさは {z3} 件。**"
              "**Z6 を見て、人の入力と印字の一致だけで通った形なら危険として報告する。**")

    payload = {
        "round": 47,
        "conditions": {
            o.label: {
                "Z1_targets": o.targets,
                "Z2_sources": o.sources_overall,
                "Z3_tier1": o.tier_counts.get(1, 0),
                "Z4_tier2": o.tier_counts.get(2, 0),
                "Z4_tier3": o.tier_counts.get(3, 0),
                "Z5_targets_with_two_sources": o.targets_with_two_sources,
                "Z5_shapes": sorted(set(o.two_source_targets)),
                "Z6_tier1_shapes": sorted(set(o.tier1_targets)),
                "Z6_reasons": list(dict.fromkeys(o.tier1_reasons)),
            }
            for o in (g0, g1, g2)
        },
        "synthetic_start_kit": kit_note,
        "controls": {
            "1_g0_tier1_is_zero": control1,
            "2_restored": control2,
            "3_repeat": repeats,
            "4_synthetic_reaches_tier1": control4,
            "4_synthetic_tier1_count": syn.tier_counts.get(1, 0),
            "4_synthetic_shapes": sorted(set(syn.tier1_targets)),
        },
    }
    out = Path(__file__).resolve().parent.parent / "docs" / "d_tier1_reachability_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
