"""52周目: ベクターの円弧は、正解のあるページで何件拾っているのか。

基準は `docs/d_arc_overcount_criteria.md`(測る前にコミット済み)。

**製品コードは 1 行も変えない。参照データは採点にだけ使い、抽出の経路には渡さない。**

使い方::

    .venv/bin/python benchmarks/measure_arc_overcount.py <図面PDFのパス>

**出すのは件数と比と真偽だけ。図面の文字も室の名前も座標も出さない。**
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
from benchmarks.real_drawing_fixtures import (  # noqa: E402
    HINGED_DOOR_ARCS,
    PLAN_PAGE_INDEX,
)

#: 対照1 で探す文字。**この2つは図面番号と図面名で、
#: 参照データの作り方の説明にそのまま書かれているもの。**
#: **真偽だけを出し、文字そのものは出さない。**
SHEET_MARKERS = ("意-6", "改装平面図")


def page_has_markers(pdf_path: Path, index: int) -> tuple[bool, bool]:
    with pymupdf.open(pdf_path) as doc:
        text = doc[index].get_text()
    return tuple(marker in text for marker in SHEET_MARKERS)  # type: ignore[return-value]


def arcs_on(pdf_path: Path, index: int) -> tuple[int, bool]:
    """そのページで拾えた円弧の数と、縮尺が読めたかどうか。"""
    scale = extract_scale(pdf_path, index)
    if scale is None:
        return 0, False
    return len(find_door_arcs(pdf_path, index, scale)), True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"図面 PDF が見つかりません: {pdf_path}")
        return 2

    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count

    print(f"図面: {pdf_path.name} / {page_count} ページ\n", flush=True)

    print("=== 対照1 同じ紙か(真偽だけ)===", flush=True)
    found = page_has_markers(pdf_path, PLAN_PAGE_INDEX)
    print(f"   参照データが指すページ(1 始まりで {PLAN_PAGE_INDEX + 1})に")
    print(f"   図面番号の文字: {'ある' if found[0] else '無い'}")
    print(f"   図面名の文字  : {'ある' if found[1] else '無い'}")
    control1 = all(found)
    print(f"   → {'同じ紙とみなす' if control1 else '別の紙。結論を出さない'}")

    print("\n=== U1・U2・U3 正解のあるページ ===", flush=True)
    u1, scale_read = arcs_on(pdf_path, PLAN_PAGE_INDEX)
    u2 = len(HINGED_DOOR_ARCS)
    print(f"   縮尺が読めたか: {'はい' if scale_read else 'いいえ'}")
    print(f"   U1 拾った件数: {u1}")
    print(f"   U2 正解の件数: {u2}")
    u3 = (u1 / u2) if u2 else None
    print(f"   U3 正解 1 件につき人が見る数: {u3:.2f}" if u3 is not None else "   U3: —")

    print("\n=== U4 34 ページ全体 ===", flush=True)
    total = 0
    pages_with_arcs = 0
    for index in range(page_count):
        count, _ = arcs_on(pdf_path, index)
        total += count
        if count:
            pages_with_arcs += 1
    print(f"   U4 全体で拾った件数: {total} / 取れたページ数: {pages_with_arcs}")

    print("\n=== 対照 ===", flush=True)
    control2 = total == 90
    print(f"対照2 35周目と同じ 90 件か: {total} → {'はい' if control2 else 'いいえ'}")
    repeats = [arcs_on(pdf_path, PLAN_PAGE_INDEX)[0] for _ in range(3)]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(U1): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif u1 == 0:
        print("   U1 = 0 → **正解のあるページで 1 件も拾えていない。**"
              f"縮尺が読めたか: {'はい' if scale_read else 'いいえ'}。"
              "**なぜ 0 なのかを名指しする。**")
    elif u3 is not None and u3 <= 2:
        print(f"   U3 = {u3:.2f} ≦ 2 → **人がさばける量である。**"
              "51周目の「人が見る材料として成立している」はそのまま保たれる。")
    else:
        print(f"   U3 = {u3:.2f} > 2 → **人は正解 1 件につき {u3:.2f} 件を見ることになる。**"
              "**51周目の結論に但し書きが要る。**報告書とまとめの文書に足す。")

    payload = {
        "round": 52,
        "page_1_based": PLAN_PAGE_INDEX + 1,
        "U1_found": u1,
        "U2_reference": u2,
        "U3_ratio": u3,
        "U4_total": total,
        "U4_pages_with_arcs": pages_with_arcs,
        "scale_read_on_reference_page": scale_read,
        "controls": {
            "1_same_sheet": control1,
            "1_markers_found": list(found),
            "2_total_matches_round_35": control2,
            "3_repeat": repeats,
        },
    }
    out = ROOT / "docs" / "d_arc_overcount_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
