"""実案件 P011 の設計図面に、このリポジトリの実装をそのまま当てて測る。

**なぜこれを測るのか**

段階Aまでの数値(古典的テンプレート照合が劣化 heavy 以外でほぼ F1 1.00 など)は
すべて `benchmarks/synthetic_plans.py` が生成する**合成図面**の上のものです。
合成図面は、格子に整列した壁・一定寸法の開き戸・一定太さの窓だけでできており、
家具も文字も寸法線もありません。しかも照合に使うテンプレートは
**その合成図面を描く関数そのもの**から作られています
(`run_grounding_dino_eval._symbol_templates`)。

本物の図面でも同じ数字が出るのかは、一度も測られていませんでした。
このベンチマークが初めてそれを測ります。

**測る 3 条件(記号検出)**

1. ``synthetic_as_is``   段階Aが持っている唯一のテンプレート(合成生成器由来、58px)
   を、そのまま実図面に当てる。**今のリポジトリで実行できる唯一の条件。**
2. ``synthetic_rescaled`` 同じテンプレートを実図面の縮尺に合わせて拡大する。
   倍率としきい値を振って**最良の F1 を採る**。テンプレート照合が倍率不変でない
   ことによる不利を取り除き、手法そのものを公平に見るための条件。
3. ``real_template``     実図面から切り出した円弧をテンプレートにする。
   **正解の位置を人が与えているので実運用では使えない**が、
   「手法の上限」を知るための条件。

実行: ``python -m benchmarks.run_real_drawing_eval --pdf <path>``
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from axes.image_axis.pdf_pages import PdfPage, describe, rasterize
from axes.image_axis.vtracer_vectorizer import binarize_classical, measure_linework, vectorize
from benchmarks.real_drawing_fixtures import (
    ARC_LIKE_NON_DOORS,
    HINGED_DOOR_ARCS,
    PLAN_CROP,
    PLAN_DPI,
    PLAN_PAGE_INDEX,
    PRINTED_DIMENSIONS_MM,
    PRINTED_SCALE_TEXT,
    SLIDING_DOOR_MARKERS,
)
from benchmarks.run_grounding_dino_eval import IOU_LOOSE, _nms, _symbol_templates, match_greedy

Box = tuple[float, float, float, float]

#: テンプレートを何倍に拡大して試すか。1.0 は「段階Aのまま」。
TEMPLATE_SCALES: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 5.0, 5.5, 6.0)

#: 相関のしきい値。段階Aと同じく振って最良を採る。
THRESHOLDS: tuple[float, ...] = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)

#: 1 つのしきい値・1 枚のテンプレートあたりで NMS に渡す候補の上限。
_CANDIDATE_CAP = 20000


@dataclass(frozen=True)
class DetectionScore:
    condition: str
    threshold: float
    template_scale: float
    detected: int
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        return self.true_positive / self.detected if self.detected else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positive + self.false_negative
        return self.true_positive / total if total else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def count_error(self) -> int:
        """個数誤差。積算で直接効くのはこちら。"""
        return self.detected - (self.true_positive + self.false_negative)


def load_plan(pdf_path: Path, dpi: int = PLAN_DPI) -> tuple[PdfPage, np.ndarray]:
    """評価対象ページを読み、図面本体を切り出して返す。"""
    page = rasterize(pdf_path, dpi=dpi, pages=range(PLAN_PAGE_INDEX, PLAN_PAGE_INDEX + 1))[0]
    h, w = page.image.shape
    y0, y1, x0, x1 = PLAN_CROP
    crop = page.image[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]
    return page, crop.copy()


# ---------------------------------------------------------------------------
# 記号検出
# ---------------------------------------------------------------------------


def _detect_all_thresholds(
    image: np.ndarray, templates: list[np.ndarray], thresholds: tuple[float, ...]
) -> dict[float, list[Box]]:
    """テンプレート群で相関を 1 回だけ取り、しきい値ごとの検出をまとめて返す。

    相関マップは 1 枚で 100MB を超えるので、テンプレートごとに 1 枚だけ持ち、
    その場で全しきい値ぶんの候補を抜き出してから捨てます。
    """
    pool: dict[float, tuple[list[Box], list[float]]] = {t: ([], []) for t in thresholds}
    lowest = min(thresholds)

    for template in templates:
        th, tw = template.shape[:2]
        if th >= image.shape[0] or tw >= image.shape[1]:
            continue
        response = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(response >= lowest)
        if len(ys):
            values = response[ys, xs]
            for threshold in thresholds:
                keep = values >= threshold
                ky, kx, kv = ys[keep], xs[keep], values[keep]
                # 候補の打ち切りは **しきい値ごとに** 行う。全しきい値で共有すると、
                # 低いしきい値の候補が高いしきい値の上位に食われて、
                # しきい値を振ったことにならない(最初の実装はこれを間違えていた)。
                if len(kv) > _CANDIDATE_CAP:
                    picked = np.argsort(kv)[-_CANDIDATE_CAP:]
                    ky, kx, kv = ky[picked], kx[picked], kv[picked]
                boxes, scores = pool[threshold]
                for y, x, v in zip(ky, kx, kv):
                    boxes.append((float(x), float(y), float(x + tw), float(y + th)))
                    scores.append(float(v))
        del response

    return {
        threshold: (_nms(boxes, scores) if boxes else [])
        for threshold, (boxes, scores) in pool.items()
    }


def _scaled(templates: list[np.ndarray], factor: float) -> list[np.ndarray]:
    if factor == 1.0:
        return templates
    out = []
    for t in templates:
        h, w = t.shape[:2]
        out.append(cv2.resize(t, (max(1, int(w * factor)), max(1, int(h * factor)))))
    return out


def _score(
    condition: str,
    boxes: list[Box],
    truths: list[Box],
    threshold: float,
    scale: float,
) -> DetectionScore:
    match = match_greedy(boxes, truths, IOU_LOOSE)
    tp = match.true_positive
    return DetectionScore(
        condition=condition,
        threshold=threshold,
        template_scale=scale,
        detected=len(boxes),
        true_positive=tp,
        false_positive=len(boxes) - tp,
        false_negative=len(truths) - tp,
    )


def score_separation(
    image: np.ndarray, templates: list[np.ndarray], truths: list[Box]
) -> dict[str, object]:
    """しきい値に依存しない診断: **正解の位置は高く出ているのか。**

    段階Aで Grounding DINO の不採用を決めたのと同じ見方をする。
    「正解の箱の中の最高スコア」と「図面全体の最高スコア」を並べ、
    前者が後者に埋もれているなら、**しきい値をどう動かしても直らない**。
    """
    per_truth = [-1.0] * len(truths)
    global_max = -1.0
    # 正解の箱の中に入る左上座標の範囲を、テンプレートごとに見る。
    for template in templates:
        th, tw = template.shape[:2]
        if th >= image.shape[0] or tw >= image.shape[1]:
            continue
        response = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        global_max = max(global_max, float(response.max()))
        for index, (x1, y1, x2, y2) in enumerate(truths):
            # 左上がこの範囲にあれば、検出箱は正解箱と十分に重なる。
            lo_x, lo_y = int(max(0, x1 - tw * 0.5)), int(max(0, y1 - th * 0.5))
            hi_x = int(min(response.shape[1] - 1, x2 - tw * 0.5))
            hi_y = int(min(response.shape[0] - 1, y2 - th * 0.5))
            if hi_x <= lo_x or hi_y <= lo_y:
                continue
            window = response[lo_y : hi_y + 1, lo_x : hi_x + 1]
            if window.size:
                per_truth[index] = max(per_truth[index], float(window.max()))
        del response

    return {
        "max_score_on_each_true_door": [round(v, 4) for v in per_truth],
        "max_score_anywhere": round(global_max, 4),
        "best_true_door_score": round(max(per_truth), 4) if per_truth else None,
        "true_door_beats_global_max": bool(per_truth and max(per_truth) >= global_max - 1e-9),
    }


def evaluate_symbols(plan: np.ndarray) -> dict[str, object]:
    """3 条件で開き戸の円弧を探し、それぞれの最良成績を返す。"""
    truths: list[Box] = [tuple(float(v) for v in s.box) for s in HINGED_DOOR_ARCS]  # type: ignore[misc]
    base = [t[1] for t in _symbol_templates("door")]

    results: dict[str, object] = {"template_source_size_px": [t.shape for t in base]}
    all_scores: list[DetectionScore] = []

    # 条件1: 段階Aのテンプレートをそのまま。倍率は 1.0 に固定。
    for threshold, boxes in _detect_all_thresholds(plan, base, THRESHOLDS).items():
        all_scores.append(_score("synthetic_as_is", boxes, truths, threshold, 1.0))

    # 条件2: 倍率を振る。
    for scale in TEMPLATE_SCALES:
        if scale == 1.0:
            continue
        templates = _scaled(base, scale)
        for threshold, boxes in _detect_all_thresholds(plan, templates, THRESHOLDS).items():
            all_scores.append(_score("synthetic_rescaled", boxes, truths, threshold, scale))

    # 条件3: 実図面から切り出した円弧をテンプレートにする。
    # 洗面室の円弧を 1 枚だけ使う(1 枚で全 5 件を説明できるかを見たいので増やさない)。
    ref = HINGED_DOOR_ARCS[4]
    x1, y1, x2, y2 = ref.box
    patch = plan[y1:y2, x1:x2].copy()
    real_templates = [patch]
    for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
        real_templates.append(cv2.rotate(patch, rot))
    real_templates.append(cv2.flip(patch, 1))
    real_templates.append(cv2.flip(patch, 0))
    for threshold, boxes in _detect_all_thresholds(plan, real_templates, THRESHOLDS).items():
        all_scores.append(_score("real_template", boxes, truths, threshold, 1.0))

    best: dict[str, dict[str, object]] = {}
    for condition in ("synthetic_as_is", "synthetic_rescaled", "real_template"):
        subset = [s for s in all_scores if s.condition == condition]
        top = max(subset, key=lambda s: (s.f1, -abs(s.count_error)))
        best[condition] = {
            **asdict(top),
            "precision": round(top.precision, 3),
            "recall": round(top.recall, 3),
            "f1": round(top.f1, 3),
            "count_error": top.count_error,
        }
    results["score_separation"] = {
        "synthetic_as_is": score_separation(plan, base, truths),
        "synthetic_rescaled_x5": score_separation(plan, _scaled(base, 5.0), truths),
        "real_template": score_separation(plan, real_templates, truths),
    }
    results["best"] = best
    results["all"] = [
        {**asdict(s), "f1": round(s.f1, 3), "count_error": s.count_error} for s in all_scores
    ]
    return results


# ---------------------------------------------------------------------------
# 線画(壁)の計測
# ---------------------------------------------------------------------------


def _linework_of(image: np.ndarray) -> dict[str, object]:
    """段階Aで決めた手順そのまま: 大津の二値化(median=1)→ インクを黒に戻す → VTracer。"""
    otsu_ink = cv2.bitwise_not(binarize_classical(image, median_ksize=1))
    out: dict[str, object] = {
        "shape": list(image.shape),
        "ink_ratio_raw": round(float((image < 200).mean()), 4),
        "ink_ratio_after_otsu": round(float((otsu_ink < 128).mean()), 4),
    }
    started = time.time()
    drawing = vectorize(otsu_ink)
    out["vtracer_seconds"] = round(time.time() - started, 1)
    out["vtracer_path_count"] = drawing.path_count
    out["vtracer_ring_count"] = drawing.ring_count

    # mm_per_pixel=1.0 を渡しているので、返る "mm" はそのままピクセル数である。
    # この図面は縮尺が未確定なので、ミリメートルに換算してはいけない(報告書 3 節)。
    measured = measure_linework(drawing.to_mask(), mm_per_pixel=1.0)
    out["skeleton_total_length_px"] = round(float(measured.total_length_mm), 1)
    out["skeleton_pixels"] = int(measured.skeleton_pixels)
    out["ink_pixels"] = int(measured.ink_pixels)
    out["component_count"] = int(measured.component_count)
    out["mean_stroke_width_px"] = round(float(measured.mean_stroke_width_px), 2)
    return out


def evaluate_linework(plan: np.ndarray) -> dict[str, object]:
    """実図面と合成図面に、同じ前処理を当てて「図形の数」を並べる。

    **狙いは壁の長さを当てることではない。** 縮尺が未確定なので長さは
    ミリメートルにできない(報告書 3 節)。ここで見たいのは、
    ベクター化したあとに残る図形が、合成図面と実図面でどれだけ違うかである。
    合成図面は壁と記号しかインクが無いので、残る図形はほぼ全部が測りたい対象。
    実図面はそうではない、という差が数で出る。
    """
    out: dict[str, object] = {"real_full_resolution": _linework_of(plan)}

    # 解像度を落とすと細線がどう失われるかも見ておく。
    factor = 2000 / max(plan.shape)
    small = cv2.resize(plan, (int(plan.shape[1] * factor), int(plan.shape[0] * factor)))
    out["real_reduced"] = {"resize_factor": round(factor, 4), **_linework_of(small)}

    # 比較対象: 合成図面(段階Aが測っていたもの)。
    from benchmarks.synthetic_plans import make_plan

    out["synthetic"] = {
        level: _linework_of(make_plan(level).image) for level in ("clean", "light", "medium")
    }
    return out


# ---------------------------------------------------------------------------
# 空間階層(絶対ルール軸)
# ---------------------------------------------------------------------------


def evaluate_spatial_hierarchy() -> dict[str, object]:
    """実在の間取り(P011)を IFC 空間階層に載せ、`audit()` が何を言うかを見る。

    **入力は人が読んだ間取りである。**画像軸が自動で出したものではないので、
    これは「自動化率」の測定ではなく、**絶対ルール軸の検査そのものが、
    実在のマンション改装の間取りに対して意味のある答えを返すか**の測定。
    """
    from axes.absolute_rule_axis.ifc_containment import ElementReading, audit, build_from_reading
    from benchmarks.real_drawing_fixtures import P011_ELEMENTS, P011_ROOMS

    elements = [ElementReading(name, kind, room) for name, kind, room in P011_ELEMENTS]  # type: ignore[arg-type]
    model = build_from_reading(P011_ROOMS, elements)

    out: dict[str, object] = {"room_count": len(P011_ROOMS), "element_count": len(P011_ELEMENTS)}
    for label, require_window in (("require_window_true", True), ("require_window_false", False)):
        report = audit(model, require_window=require_window)
        out[label] = {
            "is_consistent": report.is_consistent,
            "summary": report.summary(),
            "findings": [str(f) for f in report.findings],
        }
    return out


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=PLAN_DPI)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--skip-linework", action="store_true")
    parser.add_argument(
        "--only",
        choices=("all", "pages", "symbols", "linework", "ifc"),
        default="all",
        help="一部だけ測り直したいときに使う。記号検出は数分かかる。",
    )
    args = parser.parse_args()

    report: dict[str, object] = {}

    def save() -> None:
        """途中経過をその都度書き出す。記号検出だけで数分かかるので、
        あとの工程で落ちてもそこまでの実測が消えないようにする。"""
        if args.out:
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.only == "ifc":
        report["spatial_hierarchy"] = evaluate_spatial_hierarchy()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(text)
        return

    pages = rasterize(args.pdf, dpi=72)
    report["pages"] = [
        {
            "page": p.page_index + 1,
            "paper_mm": [round(p.paper_width_mm), round(p.paper_height_mm)],
            "content_kind": p.content_kind,
            "vector_draw_count": p.vector_draw_count,
            "embedded_image_count": p.embedded_image_count,
            "text_span_count": p.text_span_count,
        }
        for p in pages
    ]
    report["pages_table"] = describe(pages)
    report["pages_with_embedded_text"] = sum(1 for p in pages if p.text_span_count)
    report["pages_scanned_only"] = sum(1 for p in pages if p.content_kind == "raster")

    page, plan = load_plan(args.pdf, dpi=args.dpi)
    report["plan_page"] = PLAN_PAGE_INDEX + 1
    report["plan_shape"] = list(plan.shape)
    report["paper_mm_per_pixel"] = round(page.mm_per_pixel, 4)
    report["drawing_mm_per_pixel"] = page.drawing_mm_per_pixel()
    report["printed_scale_text"] = PRINTED_SCALE_TEXT
    report["printed_dimensions_mm"] = PRINTED_DIMENSIONS_MM

    report["reference_hinged_doors"] = len(HINGED_DOOR_ARCS)
    report["reference_sliding_doors"] = len(SLIDING_DOOR_MARKERS)
    report["arc_like_non_doors"] = list(ARC_LIKE_NON_DOORS)

    save()

    started = time.time()
    report["symbols"] = evaluate_symbols(plan)
    report["symbol_seconds"] = round(time.time() - started, 1)
    save()

    if not args.skip_linework:
        report["linework"] = evaluate_linework(plan)
        save()

    report["spatial_hierarchy"] = evaluate_spatial_hierarchy()
    save()

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
