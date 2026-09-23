"""54周目: **正解データはどちらのファイルのどのページで作られたのか。**

基準は `docs/d_reference_provenance_criteria.md`(測る前にコミット済み `2787ffb`)。

参照データの箱は「切り出したあとの画像における画素」で書いてある。
**切り出し画像の大きさは紙の寸法と解像度と切り出し割合だけで決まる**ので、
**箱が収まるかどうかは、目で見ずに、形だけで決まる。**

**ページをラスター化しない。** 画素数は `page.rect` と倍率行列から計算する
(`get_pixmap` と同じ丸め方)。**図面の絵も文字も一切読まない。**

使い方::

    .venv/bin/python benchmarks/measure_reference_provenance.py <古いPDF> <v2のPDF>

**出すのはページ数・画素数・件数・比・真偽だけ。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from benchmarks.real_drawing_fixtures import (  # noqa: E402
    HINGED_DOOR_ARCS,
    PLAN_CROP,
    PLAN_DPI,
)


def crop_size(page: pymupdf.Page, dpi: int = PLAN_DPI) -> tuple[int, int]:
    """そのページを `dpi` でラスター化して切り出したときの (幅, 高さ) 画素。

    `rasterize()` → `load_plan()` と同じ計算をなぞる。
    画像は作らないので中身は読まない。
    """
    zoom = dpi / 72.0
    irect = (page.rect * pymupdf.Matrix(zoom, zoom)).irect
    w, h = irect.width, irect.height
    y0, y1, x0, x1 = PLAN_CROP
    return int(x1 * w) - int(x0 * w), int(y1 * h) - int(y0 * h)


def boxes_fit(crop_w: int, crop_h: int) -> bool:
    """5 件の箱が全部その切り出し画像に収まるか。"""
    return all(
        0 <= x1 and 0 <= y1 and x2 <= crop_w and y2 <= crop_h
        for x1, y1, x2, y2 in (symbol.box for symbol in HINGED_DOOR_ARCS)
    )


def bounding_fraction(crop_w: int, crop_h: int) -> float:
    """**Y2**: 5 件を全部含む最小の矩形の面積 ÷ 切り出し画像の面積。記録だけに使う。"""
    xs1 = [s.box[0] for s in HINGED_DOOR_ARCS]
    ys1 = [s.box[1] for s in HINGED_DOOR_ARCS]
    xs2 = [s.box[2] for s in HINGED_DOOR_ARCS]
    ys2 = [s.box[3] for s in HINGED_DOOR_ARCS]
    area = (max(xs2) - min(xs1)) * (max(ys2) - min(ys1))
    return area / (crop_w * crop_h) if crop_w and crop_h else 0.0


def survey(pdf_path: Path) -> dict:
    fits: list[int] = []
    misses = 0
    sizes: list[tuple[int, int]] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            w, h = crop_size(doc[index])
            sizes.append((w, h))
            if boxes_fit(w, h):
                fits.append(index + 1)
            else:
                misses += 1
        page_count = doc.page_count
    return {
        "page_count": page_count,
        "fits_1_based": fits,
        "Y1": len(fits),
        "misses": misses,
        "crop_sizes": sorted(set(sizes)),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    paths = {"古いほう": Path(argv[1]), "匿名化v2": Path(argv[2])}
    for label, path in paths.items():
        if not path.exists():
            print(f"{label} の PDF が見つかりません: {path}")
            return 2

    print("=== 対照4 箱そのものの健全さ ===", flush=True)
    sane = all(
        (s.box[2] - s.box[0]) > 0 and (s.box[3] - s.box[1]) > 0 for s in HINGED_DOOR_ARCS
    )
    print(f"   箱 {len(HINGED_DOOR_ARCS)} 件とも幅・高さが 0 より大きいか: "
          f"{'はい' if sane else 'いいえ'}")

    results: dict[str, dict] = {}
    print("\n=== Y0・Y1・Y2・Y3 ===", flush=True)
    for label, path in paths.items():
        info = survey(path)
        results[label] = info
        print(f"   [{label}]")
        print(f"     Y0 ページ数: {info['page_count']}")
        print(f"     切り出し画像の大きさ(画素、種類ごと): {info['crop_sizes']}")
        print(f"     Y1 5 件が全部収まるページ数: {info['Y1']}")
        print(f"     Y3 収まるページ(1 始まり): {info['fits_1_based']}")
        if info["fits_1_based"]:
            first = info["fits_1_based"][0]
            with pymupdf.open(path) as doc:
                w, h = crop_size(doc[first - 1])
            y2 = bounding_fraction(w, h)
            info["Y2_first_fitting_page"] = round(y2, 4)
            print(f"     Y2 箱の広がり ÷ 切り出し画像(記録だけ): {y2:.4f}")
        else:
            info["Y2_first_fitting_page"] = None

    old, v2 = results["古いほう"], results["匿名化v2"]

    print("\n=== 対照 ===", flush=True)
    control1 = old["page_count"] >= 8 and v2["page_count"] >= 8
    print(f"対照1 どちらも 8 ページ以上か: {'はい' if control1 else 'いいえ'}")
    control2 = (old["misses"] + v2["misses"]) >= 1
    print(f"対照2 収まらないページが 1 つ以上あるか(判定が「いいえ」を言えるか): "
          f"{old['misses'] + v2['misses']} ページ → {'はい' if control2 else 'いいえ'}")
    repeats = [survey(paths["古いほう"])["Y1"] for _ in range(3)]
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(古いほうの Y1): {repeats} → {'はい' if control3 else 'いいえ'}")
    control4 = sane
    print(f"対照4 箱の健全さ: {'はい' if control4 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2 and control4):
        verdict = "controls_failed"
        print("   **対照1・2・4 のどれかが通らなかった。結論を出さない。**")
    elif old["Y1"] >= 1 and v2["Y1"] == 0:
        if old["Y1"] == 1:
            verdict = "old_file_page_identified"
            print(f"   **参照データは古いほうのファイルの {old['fits_1_based'][0]} "
                  "ページ目のものである。ページまで特定できた。**")
        else:
            verdict = "old_file_page_ambiguous"
            print(f"   **参照データは古いほうのファイルのものである。**"
                  f"ただし収まるページが {old['Y1']} 個あるのでページは決まらない。"
                  "**どのページかをおーちゃんに聞く。**")
    elif v2["Y1"] >= 1 and old["Y1"] == 0:
        if v2["Y1"] == 1:
            verdict = "v2_page_identified"
            print(f"   **参照データは匿名化 v2 の {v2['fits_1_based'][0]} "
                  "ページ目のものである。ページまで特定できた。**")
        else:
            verdict = "v2_page_ambiguous"
            print(f"   **参照データは匿名化 v2 のものである。**"
                  f"ただし収まるページが {v2['Y1']} 個あるのでページは決まらない。"
                  "**どのページかをおーちゃんに聞く。**")
    elif old["Y1"] >= 1 and v2["Y1"] >= 1:
        verdict = "both_fit"
        print("   **両方のファイルで収まる。画素の大きさでは決まらない。**"
              "**Y2 では決めない。おーちゃんに聞く。**")
    else:
        verdict = "neither_fits"
        print("   **どちらのファイルとも形が合わない。記録はもっと壊れている。**"
              "**おーちゃんに聞く。**")

    payload = {
        "round": 54,
        "criteria_commit": "2787ffb",
        "criteria_file": "docs/d_reference_provenance_criteria.md",
        "benchmark": "benchmarks/measure_reference_provenance.py",
        "dpi": PLAN_DPI,
        "crop": list(PLAN_CROP),
        "reference_boxes": len(HINGED_DOOR_ARCS),
        "files": results,
        "controls": {
            "1_both_have_8_pages": control1,
            "2_some_page_does_not_fit": control2,
            "2_miss_count": old["misses"] + v2["misses"],
            "3_repeat": repeats,
            "4_boxes_sane": control4,
        },
        "verdict": verdict,
        "implementation_changed": False,
        "rasterized": False,
    }
    out = ROOT / "docs" / "d_reference_provenance_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
