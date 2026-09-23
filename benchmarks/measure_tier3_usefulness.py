"""51周目: 全部が階層3になるとき、人はそれを使えるのか。

基準は `docs/d_tier3_usefulness_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。反実仮想も使わない。**

使い方::

    .venv/bin/python benchmarks/measure_tier3_usefulness.py <図面PDFのパス>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intake.drawing_intake import IntakeConfig, read_drawing  # noqa: E402

#: 紙の上の位置が分かる鍵。
POSITION_KEYS = ("rect_pt", "point_a_pt", "center_pt", "cell_rect_pt")
#: 元の文字列が分かる鍵。
TEXT_KEYS = ("source_text", "printed_scale_source_text", "cell_text", "raw_text")


def has_position_or_text(provenance: dict) -> tuple[bool, bool]:
    position = any(provenance.get(key) for key in POSITION_KEYS)
    text = any(provenance.get(key) for key in TEXT_KEYS)
    return position, text


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2

    result = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="MEASURE-51"))

    tiers = Counter(item.tier for item in result.decisions)
    print(f"図面: {pdf_path.name}\n")
    print("=== T1 階層ごとの件数 ===")
    for tier in (1, 2, 3):
        print(f"   階層{tier}: {tiers.get(tier, 0)} 件")
    print(f"   対象の数: {len(result.decisions)}")

    print("\n=== T2 階層2 が 0 件なら、その理由 ===")
    reasons = Counter(
        reason for item in result.decisions for reason in item.reasons
    )
    for reason, count in reasons.most_common():
        print(f"   {count} 件: {reason}")

    print("\n=== T3・T4・T5 人が確かめられる材料 ===")
    missing = Counter()
    complete = 0
    by_method = Counter()
    for finding in result.findings:
        provenance = dict(finding.provenance or {})
        position, text = has_position_or_text(provenance)
        checks = {
            "数値の範囲": finding.value_range is not None,
            "ページ番号": provenance.get("page_number") is not None,
            "位置または元の文字": position or text,
            "手法の名前": bool(finding.method_id),
        }
        by_method[finding.method_id] += 1
        if all(checks.values()):
            complete += 1
        else:
            for name, ok in checks.items():
                if not ok:
                    missing[name] += 1

    total = len(result.findings)
    print(f"   T3 読めた数量: {total} 件")
    print(f"   T4 4つそろっているもの: {complete} 件")
    print(f"   T5 欠けている項目: {dict(missing) or 'なし'}")
    print(f"   手法ごとの件数: {dict(by_method)}")

    print("\n=== 対照 ===")
    control1 = tiers.get(1, 0) == 0
    print(f"対照1 階層1 が 0 件か(47周目 G0 と一致): {'はい' if control1 else 'いいえ'}")
    control2 = len(result.decisions) == 7
    print(f"対照2 対象の数が 7 か(47周目 G0 と一致): "
          f"{len(result.decisions)} → {'はい' if control2 else 'いいえ'}")
    repeats = []
    for _ in range(3):
        again = read_drawing(IntakeConfig(pdf_path=pdf_path, case_id="MEASURE-51"))
        count = 0
        for finding in again.findings:
            provenance = dict(finding.provenance or {})
            position, text = has_position_or_text(provenance)
            if (
                finding.value_range is not None
                and provenance.get("page_number") is not None
                and (position or text)
                and finding.method_id
            ):
                count += 1
        repeats.append(count)
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(T4): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif complete == total:
        print("   T4 = T3 → **人が見る材料としては成立している。**"
              "**自動確定が0件であることと、使えないことは別である。**")
    else:
        print(f"   T4 = {complete} < T3 = {total} → **足りない。**"
              f"欠けている項目を名指しする: {dict(missing)}。"
              "足す変更は判定に触らないのでテストつきで出せる。")

    payload = {
        "round": 51,
        "T1_tiers": {str(k): v for k, v in sorted(tiers.items())},
        "T1_targets": len(result.decisions),
        "T2_reasons": dict(reasons),
        "T3_findings": total,
        "T4_complete": complete,
        "T5_missing": dict(missing),
        "by_method": dict(by_method),
        "controls": {"1_tier1_zero": control1, "2_targets_7": control2,
                      "3_repeat": repeats},
    }
    out = ROOT / "docs" / "d_tier3_usefulness_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
