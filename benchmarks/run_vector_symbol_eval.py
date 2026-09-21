"""v2(CAD 由来)の PDF で、記号検出の 2 経路を同じ図面・同じ正解で比べる。

**なぜこれを測るのか**

v1(全ページを画像化した匿名化版)に対する評価では、記号検出の的中が
全条件で 0 件だった(報告書 4 節)。そのとき縮尺も読めなかったので、
「縮尺が決まらないせいでテンプレートの倍率が合わず、当たらなかったのでは
ないか」という読み方が残っていた。

v2 では表題欄の `1/50` が文字として取れる。**倍率を正解に合わせた状態で
ラスターのテンプレート照合がどうなるか**を測れば、その読み方が正しいかが
決まる。あわせて、ベクター図形から直接拾う経路(`pdf_vector_symbols`)を
同じ正解で測る。

**正解の作り方**

正解の箱は `pdf_vector_symbols.find_door_arcs()` の出力そのものである。
これは**目視で全件を確認して作った**(既存平面図 15 件・改装平面図 7 件、
過検出 0 件・見落とし 0 件)。したがってベクター経路の成績が 1.00 になるのは
当たり前で、**その数字に意味は無い**。意味があるのは、同じ正解に対して
ラスター照合がどうなるかの方である。

実行::

    python -m benchmarks.run_vector_symbol_eval --pdf <設計図面_匿名化済み_v2.pdf>
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from axes.image_axis.pdf_pages import rasterize
from axes.image_axis.pdf_vector_symbols import extract_scale, find_door_arcs
from benchmarks.run_grounding_dino_eval import IOU_LOOSE, _symbol_templates
from benchmarks.run_real_drawing_eval import (
    THRESHOLDS,
    _detect_all_thresholds,
    _scaled,
    _score,
    score_separation,
)

#: 評価するページ。6 = 既存平面図、7 = 改装平面図。
PLAN_PAGES: tuple[int, ...] = (6, 7)

#: ラスター化の解像度。
DPI = 200

#: 試すテンプレートの倍率。
#:
#: **1.67 は当てずっぽうではない。** 段階Aのテンプレートは 58px。
#: この図面の本物の円弧は半径 616mm(実寸)で、1/50・200dpi では
#: 616 / 50 / 25.4 * 200 = 約 97px になる。97 / 58 = 1.67。
#: つまり**縮尺が読めたことで、正しい倍率が計算で出せるようになった**。
#: その前後も挟んで振ってある。
TEMPLATE_SCALES: tuple[float, ...] = (1.0, 1.25, 1.5, 1.67, 2.0, 2.5, 3.0)


def _truth_boxes(pdf_path: Path, page_index: int, dpi: int) -> list[tuple[float, float, float, float]]:
    scale = extract_scale(pdf_path, page_index)
    if scale is None:
        return []
    return [tuple(float(v) for v in arc.rect_px(dpi)) for arc in find_door_arcs(pdf_path, page_index, scale)]  # type: ignore[misc]


def evaluate_page(pdf_path: Path, page_index: int, dpi: int) -> dict[str, object]:
    scale = extract_scale(pdf_path, page_index)
    arcs = find_door_arcs(pdf_path, page_index, scale) if scale else []
    truths = _truth_boxes(pdf_path, page_index, dpi)

    page = rasterize(pdf_path, dpi=dpi, pages=range(page_index, page_index + 1))[0]
    image = page.image

    base = [t[1] for t in _symbol_templates("door")]
    scores = []
    for factor in TEMPLATE_SCALES:
        templates = _scaled(base, factor)
        for threshold, boxes in _detect_all_thresholds(image, templates, THRESHOLDS).items():
            scores.append(_score("raster_template", boxes, truths, threshold, factor))

    best = max(scores, key=lambda s: (s.f1, -abs(s.count_error)))
    separation = score_separation(image, _scaled(base, 1.67), truths)

    return {
        "page_index": page_index,
        "scale_denominator": scale.denominator if scale else None,
        "scale_source_text": scale.source_text if scale else None,
        "vector_arc_count": len(arcs),
        "vector_arc_radius_mm": [round(a.width_mm, 1) for a in arcs],
        "vector_arc_swept_degrees": [round(a.swept_degrees, 1) for a in arcs],
        "raster_best": {
            **asdict(best),
            "precision": round(best.precision, 3),
            "recall": round(best.recall, 3),
            "f1": round(best.f1, 3),
            "count_error": best.count_error,
        },
        "raster_conditions_tried": len(scores),
        "raster_hits_in_any_condition": int(sum(s.true_positive for s in scores)),
        "raster_score_separation_at_1_67": separation,
        "iou_criterion": IOU_LOOSE,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=DPI)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--pages", type=int, nargs="*", default=list(PLAN_PAGES))
    args = parser.parse_args()

    result = {"pdf": args.pdf.name, "dpi": args.dpi, "pages": []}
    for page_index in args.pages:
        measured = evaluate_page(args.pdf, page_index, args.dpi)
        result["pages"].append(measured)  # type: ignore[attr-defined]
        print(json.dumps(measured, ensure_ascii=False, indent=2))
        if args.out:
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
