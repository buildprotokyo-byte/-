"""53周目: **紙を中身で特定してから**、円弧の数を正解と比べる。

基準は `docs/d_arc_overcount_v2_criteria.md`(測る前にコミット済み `d64437f`)。

52 周目は**参照データが紙を「8 ページ目」としか書いておらず、そのページ番号が
匿名化 v2 では通用しなかった**ので、対照が働いて結論を止めた。
**今度はページ番号を使わない。参照データに入っている室名で紙を決める。**

**製品コードは 1 行も変えない。参照データは採点にだけ使い、抽出の経路には渡さない**
(円弧を探す処理は 34 ページ全部に同じように当て、**あとから**紙を選ぶ)。

使い方::

    .venv/bin/python benchmarks/measure_arc_overcount_v2.py <図面PDFのパス>

**出すのはページ番号・件数・比・真偽だけ。室の名前も図面の文字も座標も出さない。**
"""

from __future__ import annotations

import json
import re
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
    P011_ROOMS,
)

#: 対照2 で探す図面名の文字。**真偽だけを出し、文字そのものは出さない。**
SHEET_NAME_MARKER = "改装平面図"

#: 目次のページ(0 始まり)。対照4 で「1 位にならない」ことを確かめる。
INDEX_PAGE_INDEX = 0

_PARENS = re.compile(r"[(（][^)）]*[)）]")


def _strip_parens(name: str) -> str:
    """`物入(ホール)` → `物入`。図面では括弧の書き方が違うことがある。"""
    return _PARENS.sub("", name).strip()


def room_hits(text: str) -> int:
    """**数え方は 2 通りで、多いほうを採る**(基準に先に書いたとおり)。"""
    exact = sum(1 for name in P011_ROOMS if name in text)
    stripped_names = {s for s in (_strip_parens(n) for n in P011_ROOMS) if s}
    stripped = sum(1 for name in stripped_names if name in text)
    return max(exact, stripped)


def page_texts(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as doc:
        return [page.get_text() for page in doc]


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

    texts = page_texts(pdf_path)
    page_count = len(texts)
    print(f"図面: {page_count} ページ / 参照データの室名: {len(P011_ROOMS)} 件\n", flush=True)

    print("=== 紙の決め方(室名の一致数)===", flush=True)
    hits = [room_hits(text) for text in texts]
    ranked = sorted(range(page_count), key=lambda i: (-hits[i], i))
    top, runner_up = ranked[0], ranked[1]
    top_hits, runner_hits = hits[top], hits[runner_up]
    print(f"   1 位: {top + 1} ページ目 / 一致 {top_hits} 件")
    print(f"   2 位: {runner_up + 1} ページ目 / 一致 {runner_hits} 件")

    twice_as_many = top_hits >= 2 * runner_hits and top_hits > 0
    has_sheet_name = SHEET_NAME_MARKER in texts[top]
    print(f"   1 位が 2 位の 2 倍以上か: {'はい' if twice_as_many else 'いいえ'}")
    print(f"   そのページに図面名の文字があるか: {'ある' if has_sheet_name else '無い'}")
    identified = twice_as_many and has_sheet_name
    print(f"   → {'紙と認める' if identified else '**特定できない。結論を出さない。**'}")

    print("\n=== V1・V2・V3 紙と決めたページ ===", flush=True)
    v1, scale_read = arcs_on(pdf_path, top)
    v2 = len(HINGED_DOOR_ARCS)
    v3 = (v1 / v2) if v2 else None
    print(f"   縮尺が読めたか: {'はい' if scale_read else 'いいえ'}")
    print(f"   V1 拾った件数: {v1}")
    print(f"   V2 正解の件数: {v2}")
    print(f"   V3 正解 1 件につき人が見る数: {v3:.2f}" if v3 is not None else "   V3: —")

    print("\n=== V4 34 ページ全体 ===", flush=True)
    total = 0
    pages_with_arcs = 0
    for index in range(page_count):
        count, _ = arcs_on(pdf_path, index)
        total += count
        if count:
            pages_with_arcs += 1
    print(f"   V4 全体で拾った件数: {total} / 取れたページ数: {pages_with_arcs}")

    print("\n=== 対照 ===", flush=True)
    control1 = total == 90
    print(f"対照1 35周目と同じ 90 件か: {total} → {'はい' if control1 else 'いいえ'}")
    control2 = has_sheet_name
    print(f"対照2 紙と決めたページに図面名の文字があるか: {'はい' if control2 else 'いいえ'}")
    repeats = [arcs_on(pdf_path, top)[0] for _ in range(3)]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(V1): {repeats} → {'はい' if control3 else 'いいえ'}")
    control4 = top != INDEX_PAGE_INDEX
    print(f"対照4 目次のページ({INDEX_PAGE_INDEX + 1} ページ目、一致 {hits[INDEX_PAGE_INDEX]} 件)が"
          f"1 位でないか: {'はい' if control4 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    controls_ok = control1 and control2 and control4
    if not controls_ok:
        verdict = "controls_failed"
        print("   **対照1・2・4 のどれかが通らなかった。結論を出さない。**")
    elif not identified:
        verdict = "sheet_not_identified"
        print("   **紙が特定できない。結論を出さない。おーちゃんに「どのページか」を聞く。**")
    elif v1 == 0:
        verdict = "v1_zero"
        print("   V1 = 0 → **正解のある紙で 1 件も拾えていない。**"
              f"縮尺が読めたか: {'はい' if scale_read else 'いいえ'}。")
    elif v3 is not None and v3 <= 2:
        verdict = "manageable"
        print(f"   V3 = {v3:.2f} ≦ 2 → **人がさばける量である。**"
              "51周目の「人が見る材料として成立している」はそのまま保たれる。")
    else:
        verdict = "needs_caveat"
        print(f"   V3 = {v3:.2f} > 2 → **人は正解 1 件につき {v3:.2f} 件を見ることになる。**"
              "**51周目の結論に但し書きが要る。**報告書とまとめの文書に足す"
              "(設計の記録。判定のしかたは変えない)。")
    print("   ※ V3 は「精度」ではない。1 件ずつ突き合わせたのではなく**数を比べただけ**。")

    payload = {
        "round": 53,
        "criteria_commit": "d64437f",
        "criteria_file": "docs/d_arc_overcount_v2_criteria.md",
        "benchmark": "benchmarks/measure_arc_overcount_v2.py",
        "page_count": page_count,
        "V0_sheet": {
            "page_1_based": top + 1,
            "hits": top_hits,
            "runner_up_page_1_based": runner_up + 1,
            "runner_up_hits": runner_hits,
            "twice_as_many": twice_as_many,
            "has_sheet_name": has_sheet_name,
            "identified": identified,
        },
        "V1_found": v1,
        "V2_reference": v2,
        "V3_ratio": v3,
        "V4_total": total,
        "V4_pages_with_arcs": pages_with_arcs,
        "scale_read_on_sheet": scale_read,
        "controls": {
            "1_total_matches_round_35": control1,
            "2_sheet_name_present": control2,
            "3_repeat": repeats,
            "4_index_page_not_top": control4,
            "4_index_page_hits": hits[INDEX_PAGE_INDEX],
        },
        "verdict": verdict,
        "implementation_changed": False,
    }
    out = ROOT / "docs" / "d_arc_overcount_v2_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
