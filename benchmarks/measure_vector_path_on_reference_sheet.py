"""55周目: **正解データのある紙で、ベクターの経路は動くのか。**

基準は `docs/d_vector_path_on_reference_sheet_criteria.md`(測る前にコミット済み `1b0c7e6`)。

54 周目で、正解データは**古いほうのファイル**で作られていたと分かった。
一方、35 周目からの「90 件」は **v2** のものである。
**別の紙なら、円弧の当たり外れは測れていない。**

**製品コードは 1 行も変えない。**

使い方::

    .venv/bin/python benchmarks/measure_vector_path_on_reference_sheet.py <古いPDF> <v2のPDF>

**出すのはページ数・件数・真偽だけ。図面の文字は 1 文字も出さない。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from axes.image_axis.pdf_vector_symbols import (  # noqa: E402
    extract_scale,
    find_door_arcs,
)


def survey(pdf_path: Path) -> dict:
    chars = 0
    pages_with_vectors = 0
    pages_with_scale = 0
    arcs = 0
    pages_with_arcs = 0
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
        for index in range(page_count):
            page = doc[index]
            chars += len(page.get_text())
            if len(page.get_drawings()) > 0:
                pages_with_vectors += 1
    # 縮尺と円弧は製品の関数をそのまま使う(ファイルを開き直す)。
    for index in range(page_count):
        scale = extract_scale(pdf_path, index)
        if scale is None:
            continue
        pages_with_scale += 1
        found = len(find_door_arcs(pdf_path, index, scale))
        arcs += found
        if found:
            pages_with_arcs += 1
    return {
        "page_count": page_count,
        "chars": chars,
        "pages_with_vectors": pages_with_vectors,
        "pages_with_scale": pages_with_scale,
        "arcs": arcs,
        "pages_with_arcs": pages_with_arcs,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    old_path, v2_path = Path(argv[1]), Path(argv[2])
    for path in (old_path, v2_path):
        if not path.exists():
            print(f"PDF が見つかりません: {path}")
            return 2

    print("=== 古いほう(正解データのある紙を含むほう)===", flush=True)
    old = survey(old_path)
    print(f"   Z0 ページ数: {old['page_count']} / 文字数の合計: {old['chars']}")
    print(f"   Z1 ベクターの図形があるページ数: {old['pages_with_vectors']}")
    print(f"   Z2 縮尺が読めたページ数: {old['pages_with_scale']}")
    print(f"   Z3 拾った円弧の合計: {old['arcs']} / 取れたページ数: {old['pages_with_arcs']}")

    print("\n=== 対照1 同じ経路を v2 に当てる ===", flush=True)
    v2 = survey(v2_path)
    print(f"   Z4 拾った円弧の合計: {v2['arcs']} / 取れたページ数: {v2['pages_with_arcs']}")
    control1 = v2["arcs"] == 90
    print(f"   35周目と同じ 90 件か: {'はい' if control1 else 'いいえ'}")

    print("\n=== 対照 ===", flush=True)
    control2 = old["page_count"] == 34
    print(f"対照2 古いほうのページ数が 34 か: {'はい' if control2 else 'いいえ'}")
    repeats = [survey(old_path)["arcs"] for _ in range(3)]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(Z3): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not control1:
        verdict = "control_failed"
        reason = None
        print("   **対照1 が通らなかった。経路そのものの問題か紙の問題かを切り分けられない。"
              "結論を出さない。**")
    elif old["arcs"] == 0:
        if old["pages_with_vectors"] == 0:
            reason = "no_vectors"
            why = "**ベクターの図形が 1 ページにも無い(スキャンだから)。**"
        elif old["pages_with_scale"] == 0:
            reason = "no_scale"
            why = "**図形はあるが縮尺が読めない(文字が無いから)。**"
        else:
            reason = "vectors_and_scale_but_no_arcs"
            why = "**図形も縮尺もあるのに、円弧として 1 つも通らない。**"
        verdict = "cannot_measure"
        print("   Z3 = 0 → **ベクターの経路は、正解データのある紙で 1 件も動かない。**")
        print(f"   理由: {why}")
        print("   → **「ベクターの円弧の当たり外れは、実図面で測れていない」**"
              "実図面の報告書に但し書きを足す(設計の記録。判定のしかたは変えない)。")
    else:
        verdict = "can_measure"
        reason = None
        print(f"   Z3 = {old['arcs']} ≧ 1 → **動く。**"
              "次の周で、その紙の円弧を正解 5 件と IoU 0.10 で突き合わせる。")
    print("   ※ どちらの枝でも 51 周目の結論は変えない(箱の座標を使っていないため)。")

    payload = {
        "round": 55,
        "criteria_commit": "1b0c7e6",
        "criteria_file": "docs/d_vector_path_on_reference_sheet_criteria.md",
        "benchmark": "benchmarks/measure_vector_path_on_reference_sheet.py",
        "old_file": old,
        "v2_file": v2,
        "controls": {
            "1_v2_matches_round_35": control1,
            "2_old_has_34_pages": control2,
            "3_repeat": repeats,
        },
        "verdict": verdict,
        "reason": reason,
        "implementation_changed": False,
    }
    out = ROOT / "docs" / "d_vector_path_on_reference_sheet_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
