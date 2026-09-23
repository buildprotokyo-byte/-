"""48周目: 人の入力は、いまの実装でどの対象に 2 つ目のデータ源を作れるのか。

基準は `docs/d_human_input_reach_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。校正も仮定しない。**

使い方::

    .venv/bin/python benchmarks/measure_human_input_reach.py <図面PDFのパス>

**出すのは件数と対象名の頭だけ。図面に印字された文字は出さない。**
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import intake.drawing_intake as di  # noqa: E402
from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402
from intake.start_kit import PageDeclaration, ReferencePoint, StartKit  # noqa: E402

MM_PER_PT = 25.4 / 72.0

#: 基準寸法ではない対象のうち、**数量**にあたる名前の頭。
QUANTITY_HEADS = ("施工対象床面積", "専有延床面積", "施工床面積", "開き戸", "建具数量", "仕上")


def head(target: str) -> str:
    return target.split("::")[0] or "(名前なし)"


@dataclass
class Capture:
    requests: list[dict[str, Any]] = field(default_factory=list)

    def wrap(self, original: Callable[..., dict[str, Any]]):
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            request = original(*args, **kwargs)
            self.requests.append(request)
            return request

        return wrapper


@dataclass
class Outcome:
    label: str
    v1_start_kit_evidence: int = 0
    v2_start_kit_heads: set[str] = field(default_factory=set)
    v3_two_source_targets: int = 0
    v3_heads: list[str] = field(default_factory=list)
    v4_not_reference: int = 0
    v5_quantity: int = 0
    v6_targets: int = 0


def run_once(pdf_path: Path, start_kit: StartKit | None, label: str) -> Outcome:
    capture = Capture()
    original = di.to_orchestrator_request
    try:
        di.to_orchestrator_request = capture.wrap(original)
        result = read_drawing(
            IntakeConfig(pdf_path=pdf_path, case_id="MEASURE-48", start_kit=start_kit)
        )
    finally:
        di.to_orchestrator_request = original

    outcome = Outcome(label=label)
    outcome.v6_targets = len(result.decisions)
    kit_source = "MEASURE-48::start_kit"
    for request in capture.requests:
        per_target: set[str] = set()
        for entry in request["evidence"]:
            per_target.add(entry.get("source_fingerprint") or entry["source_id"])
            if entry["source_id"] == kit_source:
                outcome.v1_start_kit_evidence += 1
                outcome.v2_start_kit_heads.add(head(entry["target"]))
        if len(per_target) >= 2:
            name = head(request["element_id"])
            outcome.v3_two_source_targets += 1
            outcome.v3_heads.append(name)
            if name != "基準寸法":
                outcome.v4_not_reference += 1
                if any(name.startswith(q) for q in QUANTITY_HEADS):
                    outcome.v5_quantity += 1
    return outcome


def scale_pages(pdf_path: Path) -> list[tuple[int, float]]:
    """縮尺が読めたページを(ページ番号, 分母)で返す。"""
    probe = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="PROBE-48"))
    return [
        (page.page_number, page.scale.denominator)
        for page in probe.pages
        if page.scale is not None
    ]


def point_on(page_number: int, denominator: float, axis: str) -> ReferencePoint:
    """**印字された縮尺から逆算した仮の基準点。本物の人の入力ではない。**"""
    span = 400.0
    if axis == "horizontal":
        a, b = (100.0, 400.0), (100.0 + span, 400.0)
    else:
        a, b = (100.0, 200.0), (100.0, 200.0 + span)
    return ReferencePoint(
        page_number=page_number,
        axis=axis,
        point_a_pt=a,
        point_b_pt=b,
        actual_length_mm=span * MM_PER_PT * denominator,
        entered_by="測定用の仮の入力",
    )


def build_conditions(pages: list[tuple[int, float]]) -> list[tuple[str, StartKit | None]]:
    points = [point_on(number, denom, "horizontal") for number, denom in pages]
    declarations = tuple(
        PageDeclaration(page_number=number, kind="平面図") for number, _ in pages
    )
    return [
        ("H0 無し", None),
        ("H1 基準点1つ", StartKit(reference_points=tuple(points[:1]))),
        ("H2 基準点2つ", StartKit(reference_points=tuple(points[:2]))),
        ("H3 基準点4つ", StartKit(reference_points=tuple(points[:4]))),
        (
            "H4 基準点1つ+ページ宣言",
            StartKit(reference_points=tuple(points[:1]), page_declarations=declarations),
        ),
        (
            "H5 いま入れられる上限",
            StartKit(reference_points=tuple(points[:4]), page_declarations=declarations),
        ),
    ]


def show(outcome: Outcome) -> None:
    print(f"   V1 start_kit を名乗った証拠: {outcome.v1_start_kit_evidence}")
    print(f"   V2 その証拠が主張した対象の頭: {sorted(outcome.v2_start_kit_heads) or 'なし'}")
    print(f"   V3 2つ以上のデータ源が主張している対象: {outcome.v3_two_source_targets}")
    if outcome.v3_heads:
        print(f"      その頭: {sorted(set(outcome.v3_heads))}")
    print(f"   V4 そのうち基準寸法ではないもの: {outcome.v4_not_reference}")
    print(f"   **V5 数量に2つ目のデータ源が付いた数: {outcome.v5_quantity}**")
    print(f"   V6 対象の総数: {outcome.v6_targets}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2

    pages = scale_pages(pdf_path)
    print(f"図面: {pdf_path.name} / 縮尺が読めたページ: {len(pages)}\n", flush=True)
    if len(pages) < 4:
        print("縮尺が読めるページが 4 つ未満なので H3・H5 は作れない。")

    outcomes: list[Outcome] = []
    for label, kit in build_conditions(pages):
        print(f"=== {label} ===", flush=True)
        outcome = run_once(pdf_path, kit, label)
        show(outcome)
        outcomes.append(outcome)
        print(flush=True)

    by_label = {o.label: o for o in outcomes}
    h0, h1, h5 = by_label["H0 無し"], by_label["H1 基準点1つ"], by_label["H5 いま入れられる上限"]

    print("=== 対照 ===")
    control1 = h0.v3_two_source_targets == 0
    print(f"対照1 H0 の V3 が 0 か(47周目 G0 と一致): {'はい' if control1 else 'いいえ'}")
    control2 = h1.v3_two_source_targets == 1
    print(f"対照2 H1 の V3 が 1 か(47周目 G1 と一致): {'はい' if control2 else 'いいえ'}")
    repeats = [
        run_once(pdf_path, build_conditions(pages)[-1][1], "H5反復").v3_two_source_targets
        for _ in range(3)
    ]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(H5 の V3): {repeats} → {'はい' if control3 else 'いいえ'}")

    observed_heads = set()
    for outcome in outcomes:
        observed_heads |= outcome.v2_start_kit_heads
    control4 = observed_heads <= {"基準寸法"}
    print(
        "対照4 静的に読んだ「start_kit が名乗る対象は基準寸法だけ」と実際が合うか: "
        f"{sorted(observed_heads) or 'なし'} → {'はい' if control4 else 'いいえ'}"
    )

    print("\n=== 採否(基準に先に書いた線) ===")
    v5_max = max(o.v5_quantity for o in outcomes)
    v4_max = max(o.v4_not_reference for o in outcomes)
    if not (control1 and control2 and control4):
        print("   **対照1・2・4 のどれかが通らなかった。結論を出さない。**")
    elif v5_max == 0 and v4_max == 0:
        print("   V5 = 0 → **いまの実装では、人がどれだけ入れても数量には"
              "2つ目のデータ源が付かない。**47周目に削った「言えないこと」が、"
              "**この実装については言えるようになった。**"
              "必要なのは直しではなく**新しい入口**なので、"
              "**おーちゃんへ設計の判断として出す(勝手に作らない)。**")
    elif v5_max == 0:
        print(f"   V4 = {v4_max} ≧ 1 かつ V5 = 0 → "
              "**基準寸法以外にも付くが数量ではない。**何に付いたかを名指しする。")
    else:
        print(f"   V5 = {v5_max} ≧ 1 → **どの入力がどの数量に効いたかを名指しし、"
              "そこを増やす周へ。**")

    payload = {
        "round": 48,
        "scale_pages": len(pages),
        "conditions": {
            o.label: {
                "V1_start_kit_evidence": o.v1_start_kit_evidence,
                "V2_start_kit_heads": sorted(o.v2_start_kit_heads),
                "V3_two_source_targets": o.v3_two_source_targets,
                "V3_heads": sorted(set(o.v3_heads)),
                "V4_not_reference": o.v4_not_reference,
                "V5_quantity": o.v5_quantity,
                "V6_targets": o.v6_targets,
            }
            for o in outcomes
        },
        "controls": {
            "1_h0_zero": control1,
            "2_h1_one": control2,
            "3_repeat": repeats,
            "4_static_matches_runtime": control4,
            "4_observed_heads": sorted(observed_heads),
        },
    }
    out = Path(__file__).resolve().parent.parent / "docs" / "d_human_input_reach_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
