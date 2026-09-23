"""49周目: `基準寸法` の「独立していない」という但し書きは、判定に効いているのか。

基準は `docs/d_independence_note_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。壊しはその場で戻す。**

使い方::

    .venv/bin/python benchmarks/measure_independence_note.py <図面PDFのパス>
"""

from __future__ import annotations

import json
import re
import subprocess
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
from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402
from intake.start_kit import ReferencePoint, StartKit  # noqa: E402

MM_PER_PT = 25.4 / 72.0
NOTE_KEY = "independence_note"
#: 但し書きの言葉。判定理由の中にこれが現れるかを見る。
NOTE_WORDS = ("座標を共有", "座標の取り違え", "独立なのは縮尺")


@dataclass
class Capture:
    requests: list[dict[str, Any]] = field(default_factory=list)

    def wrap(self, original: Callable[..., dict[str, Any]], *, calibrated: bool,
             drop_note: bool):
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            request = original(*args, **kwargs)
            for entry in request["evidence"]:
                if calibrated:
                    entry["calibrated"] = True
                if drop_note:
                    provenance = entry.get("provenance")
                    if isinstance(provenance, dict):
                        provenance.pop(NOTE_KEY, None)
            self.requests.append(request)
            return request

        return wrapper


def run(pdf_path: Path, kit: StartKit, *, calibrated: bool, drop_note: bool):
    capture = Capture()
    original = di.to_orchestrator_request
    saved: dict[str, MethodPolicy] = {}
    try:
        di.to_orchestrator_request = capture.wrap(
            original, calibrated=calibrated, drop_note=drop_note
        )
        if calibrated:
            saved = dict(DEFAULT_METHOD_POLICIES)
            DEFAULT_METHOD_POLICIES.update(
                {
                    key: MethodPolicy(calibrated=True, max_strength=value.max_strength)
                    for key, value in DEFAULT_METHOD_POLICIES.items()
                }
            )
        result = read_drawing(
            IntakeConfig(pdf_path=pdf_path, case_id="MEASURE-49", start_kit=kit)
        )
    finally:
        di.to_orchestrator_request = original
        if saved:
            DEFAULT_METHOD_POLICIES.clear()
            DEFAULT_METHOD_POLICIES.update(saved)
    return result, capture


def summarise(result) -> list[tuple[str, int, tuple[int, int] | None]]:
    """判定を比べられる形に畳む。対象名は頭だけ。"""
    return sorted(
        (item.target.split("::")[0], item.tier, item.confirmed_range)
        for item in result.decisions
    )


def build_kit(pdf_path: Path) -> StartKit:
    probe = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="PROBE-49"))
    for page in probe.pages:
        if page.scale is None:
            continue
        span = 400.0
        return StartKit(
            reference_points=(
                ReferencePoint(
                    page_number=page.page_number,
                    axis="horizontal",
                    point_a_pt=(100.0, 400.0),
                    point_b_pt=(100.0 + span, 400.0),
                    actual_length_mm=span * MM_PER_PT * page.scale.denominator,
                    entered_by="測定用の仮の入力",
                ),
            )
        )
    raise SystemExit("縮尺が読めるページが無い")


def readers_of_note() -> list[str]:
    """`independence_note` を**読む**製品コードを探す。"""
    out = subprocess.run(
        ["/usr/bin/grep", "-rn", NOTE_KEY, "--include=*.py",
         "intake", "axes", "arbitration", "estimating", "killer_question"],
        cwd=ROOT, capture_output=True, text=True,
    )
    readers: list[str] = []
    for line in out.stdout.splitlines():
        # 書き込み側は辞書の鍵として現れる。読む側は添字か `get` で現れる。
        if re.search(rf"\[[\"']{NOTE_KEY}[\"']\]|get\(\s*[\"']{NOTE_KEY}[\"']", line):
            readers.append(line.split(":")[0] + ":" + line.split(":")[1])
    return readers


def break_test() -> tuple[bool, str]:
    """**対照2**: 但し書きを書く行を消して全件テストを流す。"""
    path = ROOT / "intake" / "drawing_intake.py"
    original = path.read_text(encoding="utf-8")
    old = f'            "{NOTE_KEY}": shared_note,\n'
    assert original.count(old) == 1, "置き換え先が1つではない"
    try:
        path.write_text(original.replace(old, ""), encoding="utf-8")
        for cache in ROOT.rglob("__pycache__"):
            if ".venv" in cache.parts:
                continue
            subprocess.run(["/bin/rm", "-rf", str(cache)])
        proc = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-x", "-q",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
                 "HOME": "/root", "PYTHONPATH": str(ROOT)},
            timeout=1800,
        )
    finally:
        path.write_text(original, encoding="utf-8")
    failed = [l for l in proc.stdout.splitlines() if l.startswith("FAILED")]
    tail = [l for l in proc.stdout.splitlines() if l.strip()][-1:]
    return proc.returncode != 0, " / ".join(failed[:1] + tail)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2
    kit = build_kit(pdf_path)

    print("=== X1 but し書きを読む製品コード ===", flush=True)
    readers = readers_of_note()
    print(f"   読む側: {readers or 'なし'} → {len(readers)} か所")

    print("\n=== X3・X5 反実仮想(47周目の G2)===", flush=True)
    kept, capture_kept = run(pdf_path, kit, calibrated=True, drop_note=False)
    tier1 = [d for d in kept.decisions if d.tier == 1]
    print(f"   階層1 の件数: {len(tier1)}")
    note_in_reasons = any(
        word in reason for d in tier1 for reason in d.reasons for word in NOTE_WORDS
    )
    print(f"   X3 判定理由に但し書きの言葉が現れるか: {'はい' if note_in_reasons else 'いいえ'}")
    for d in tier1:
        print(f"      対象の頭: {d.target.split('::')[0]} / 理由: {list(d.reasons)}")

    keys: list[str] = []
    for request in capture_kept.requests:
        if request["element_id"].startswith("基準寸法"):
            keys = sorted(
                {
                    f"{e['method_id']} → {(e.get('source_fingerprint') or e['source_id'])[:8]}"
                    for e in request["evidence"]
                }
            )
            break
    print(f"   X5 2つの読みのデータ源の鍵: {keys} → "
          f"{'異なる' if len(keys) >= 2 else '同じ'}")

    print("\n=== X4 但し書きを消しても判定が変わるか ===", flush=True)
    dropped, _ = run(pdf_path, kit, calibrated=True, drop_note=True)
    same = summarise(kept) == summarise(dropped)
    print(f"   判定が同じか: {'同じ(変わらない)' if same else '違う(変わった)'}")

    print("\n=== 対照 ===", flush=True)
    control1 = len(tier1) == 1 and all(
        d.target.startswith("基準寸法") for d in tier1
    )
    print(f"対照1 47周目と同じ 基準寸法 1 件か: {'はい' if control1 else 'いいえ'}")

    print("対照2 但し書きを書く行を消して全件テスト(3分半ほど)...", flush=True)
    control2, detail = break_test()
    print(f"   落ちたか: {'はい' if control2 else 'いいえ'} / {detail}")

    repeats = []
    for _ in range(3):
        again, _ = run(pdf_path, kit, calibrated=True, drop_note=True)
        repeats.append(summarise(again) == summarise(kept))
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(X4 の判定): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif len(keys) < 2:
        print("   X5 = 同じ → **別のデータ源として数えられていない。危険は無い。**"
              "47周目の1件は別の理由で通ったことになるので、そちらを測り直す。")
    elif len(readers) == 0 and same:
        print("   X1 = 0 かつ X4 = 変わらない → **但し書きは判定に効いていない。**"
              "**記録もされ、固定もされているのに、読む側がいない。"
              "「書いてあるだけの安全」である。**"
              "判定に効かせる変更は判定のしかたを変えるので PR のまま待つ。")
    else:
        print("   X1 ≧ 1 または X4 = 変わる → **効いている。どこでどう効いているかを名指しする。**")

    payload = {
        "round": 49,
        "X1_readers": readers,
        "X1_count": len(readers),
        "X3_note_in_reasons": note_in_reasons,
        "X3_tier1_count": len(tier1),
        "X3_tier1_heads": sorted({d.target.split("::")[0] for d in tier1}),
        "X3_tier1_reasons": sorted({r for d in tier1 for r in d.reasons}),
        "X4_decision_unchanged_without_note": same,
        "X5_source_keys": keys,
        "X5_distinct": len(keys) >= 2,
        "controls": {
            "1_matches_round_47": control1,
            "2_break_fails_suite": control2,
            "2_detail": detail,
            "3_repeat": repeats,
        },
    }
    out = ROOT / "docs" / "d_independence_note_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
