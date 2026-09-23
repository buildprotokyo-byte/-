"""56周目: **「0 件」が「無かった」なのか「探せなかった」なのかを、本番経路は区別しているか。**

基準は `docs/d_silent_zero_criteria.md`(測る前にコミット済み `d116b4f`)。

**測る周なので製品コードは 1 行も変えない。**

使い方::

    .venv/bin/python benchmarks/measure_silent_zero.py <v1(スキャン)のPDF> <v2のPDF>

**出すのは件数と真偽だけ。図面の文字は 1 文字も出さない**
(記録の文字列は**コードに書いてある固定の文**だけを、あらかじめ決めた分類に当てて数える)。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intake.drawing_intake import (  # noqa: E402
    TARGET_DOOR_QUANTITY_PREFIX,
    IntakeConfig,
    read_drawing,
)

#: **あらかじめ決めた分類。**コードに書いてある固定の文の一部で当てる。
#: 図面から読んだ文字は 1 つも入っていない。
NOTE_KINDS: tuple[tuple[str, str], ...] = (
    ("スキャンなので読めない", "スキャン画像のページ。この経路では読めない"),
    ("描画オブジェクトが無い", "描画オブジェクトが無いページ"),
    ("縮尺が読めないので開き戸を探さない", "縮尺が読めないため、実寸に依存する抽出"),
    ("縮尺の印字が読めなかった", "縮尺の印字は読めなかった"),
    ("開き戸の円弧が0件", "開き戸の円弧は 0 件"),
)


def classify_notes(notes: tuple[str, ...]) -> Counter:
    counts: Counter = Counter()
    for note in notes:
        for label, needle in NOTE_KINDS:
            if needle in note:
                counts[label] += 1
                break
        else:
            counts["その他(分類に無い記録)"] += 1
    return counts


def run(pdf_path: Path, case_id: str) -> dict:
    result = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id=case_id))
    status_counts: Counter = Counter(outcome.status for outcome in result.pages)
    note_counts: Counter = Counter()
    for outcome in result.pages:
        note_counts.update(classify_notes(outcome.notes))
    door_findings = [
        finding
        for finding in result.findings
        if finding.target.startswith(TARGET_DOOR_QUANTITY_PREFIX)
        or finding.target.startswith("開き戸::")
    ]
    return {
        "page_count": len(result.pages),
        "statuses": dict(status_counts),
        "notes": dict(note_counts),
        "notes_total": sum(note_counts.values()),
        "findings_total": len(result.findings),
        "door_findings": len(door_findings),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    v1_path, v2_path = Path(argv[1]), Path(argv[2])
    for path in (v1_path, v2_path):
        if not path.exists():
            print(f"PDF が見つかりません: {path}")
            return 2

    print("=== B0 本番の取り込み経路に、そのまま 2 つとも通す ===", flush=True)
    v1 = run(v1_path, "p011-v1")
    print(f"   [v1(スキャン)] ページ {v1['page_count']} / 例外なし")
    v2 = run(v2_path, "p011-v2")
    print(f"   [v2]            ページ {v2['page_count']} / 例外なし")

    print("\n=== B2・B4 ページごとの扱いと記録 ===", flush=True)
    for label, data in (("v1(スキャン)", v1), ("v2", v2)):
        print(f"   [{label}]")
        print(f"     ページの扱い: {data['statuses']}")
        print(f"     記録の内訳  : {data['notes']}")
        print(f"     記録の合計  : {data['notes_total']}")

    print("\n=== B3 出力された数量 ===", flush=True)
    print(f"   v1 全体 {v1['findings_total']} 件 / うち建具 {v1['door_findings']} 件")
    print(f"   v2 全体 {v2['findings_total']} 件 / うち建具 {v2['door_findings']} 件")

    # B1: 「試せなかった」を表す記録が 1 つでも残っているか。
    unavailable_labels = (
        "スキャンなので読めない",
        "描画オブジェクトが無い",
        "縮尺が読めないので開き戸を探さない",
    )
    v1_unavailable = sum(v1["notes"].get(label, 0) for label in unavailable_labels)
    b1 = v1_unavailable > 0
    # 「探したが無かった」を表す記録と、別の文字列になっているか。
    distinguishable = ("開き戸の円弧が0件" in v1["notes"]) or ("開き戸の円弧が0件" in v2["notes"])

    print("\n=== B1 区別が残っているか ===", flush=True)
    print(f"   「試せなかった」を表す記録(v1): {v1_unavailable} 件")
    print(f"   「探したが 0 件だった」を表す記録が別にあるか: "
          f"{'ある' if distinguishable else '無い'}")

    print("\n=== 対照 ===", flush=True)
    control1 = v2["findings_total"] >= 1
    print(f"対照1 v2 で数量が 1 件以上出たか: {v2['findings_total']} → "
          f"{'はい' if control1 else 'いいえ'}")
    control2 = v1["page_count"] == 34
    print(f"対照2 v1 のページ数が 34 か: {'はい' if control2 else 'いいえ'}")
    repeats = [run(v1_path, "p011-v1")["notes_total"] for _ in range(2)] + [v1["notes_total"]]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(v1 の記録の合計): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not control1:
        verdict = "control_failed"
        print("   **対照1 が通らなかった。結論を出さない。**")
    elif b1 and distinguishable:
        verdict = "distinguished"
        print("   **区別している。**「試せなかった」と「探したが 0 件」は"
              "別の記録として残っている。")
        print("   → **次の周で、その区別が人の目に届く場所まで来ているかを測る。**")
    else:
        verdict = "not_distinguished"
        print("   **区別していない。人に「開き戸なし」と読ませる形で 0 が出ている。**")
        print("   → **再現する失敗テストを先に書いてから、記録を足す変更を出す。**")

    payload = {
        "round": 56,
        "criteria_commit": "d116b4f",
        "criteria_file": "docs/d_silent_zero_criteria.md",
        "benchmark": "benchmarks/measure_silent_zero.py",
        "v1_scanned": v1,
        "v2": v2,
        "B1_unavailable_notes_v1": v1_unavailable,
        "B1_has_separate_zero_note": distinguishable,
        "controls": {
            "1_v2_produced_findings": control1,
            "2_v1_has_34_pages": control2,
            "3_repeat": repeats,
        },
        "verdict": verdict,
        "implementation_changed": False,
    }
    out = ROOT / "docs" / "d_silent_zero_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
