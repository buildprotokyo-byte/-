"""段階A ステップ1の完了分: Grounding DINO の実検出精度と、古典的画像処理との比較。

何を測るのか
------------
積算で効くのは「記号がどこにあるか」よりも **「何個あるか」** です(建具の個数が
そのまま拾い数量になる)。そこで主指標を **個数誤差**、副指標を位置合わせの
精度(適合率・再現率)としています。

比較する 3 つの読み取り方
-------------------------
1. ``template``  古典的画像処理。記号のテンプレートを **正解の図形そのものから作り**、
   正規化相互相関(``cv2.matchTemplate``)で走査する。向き違いのテンプレートを全て
   用意し、判定しきい値も振って **各劣化レベルで最良の F1 を古典側の成績とする**。
   つまり古典側には「探すべき図形の正確な形・寸法・向き」を事前に与えており、
   ベースラインとしてはかなり有利な条件。
2. ``gdino``     Grounding DINO(IDEA-Research/grounding-dino-tiny)にテキスト
   プロンプトだけを与えたゼロショット検出。図形の形は一切教えていない。
3. ``gdino@既定``  上と同じ検出結果に、アダプタの既定しきい値
   (box=0.30 / text=0.25 / margin=0.15)をそのまま適用したもの。
   **しきい値の既定値が実測上も妥当かを確かめるための行。**

「汎用AI単体」の位置づけ
------------------------
v8 設計でいう「汎用AI単体」= 図面の読み方を教えていない汎用モデルに、言葉だけで
記号を数えさせた場合、に相当するのが 2 と 3 です。Grounding DINO は図面で学習して
いないので、この比較はそのまま「汎用モデルをそのまま持ってきたらどこまで行くか」の
実測になります。

照合の規則
----------
検出ボックスと正解ボックスを IoU で最大重みマッチングし、IoU が下限以上の組を
真陽性とします。下限は 0.50(厳しめ)と 0.10(位置は大まかで良い)の 2 通りで
報告します。積算では「壁のどこにあるか」より「1 個として数えられたか」が効くため、
0.10 の方が実務に近い指標です。

実行: ``python -m benchmarks.run_grounding_dino_eval``
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Sequence

import cv2
import numpy as np

from axes.image_axis.grounding_dino_adapter import (
    DEFAULT_BOX_THRESHOLD,
    DEFAULT_CONFIDENT_MARGIN,
    DEFAULT_TEXT_THRESHOLD,
    CategoryConfig,
    GroundingDinoAdapter,
    RawDetection,
)
from benchmarks.synthetic_plans import (
    _OPENINGS,
    _WALLS,
    ALL_LEVELS,
    DegradationLevel,
    Symbol,
    SyntheticPlan,
    _draw_door,
    _draw_window,
    make_plan,
)

Box = tuple[float, float, float, float]

#: 真陽性とみなす IoU の下限。厳しめ / 実務寄りの 2 通り。
IOU_STRICT = 0.50
IOU_LOOSE = 0.10


# ---------------------------------------------------------------------------
# 照合
# ---------------------------------------------------------------------------


def iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class MatchResult:
    """1 枚 1 カテゴリ分の照合結果。"""

    n_truth: int
    n_pred: int
    true_positive: int

    @property
    def false_positive(self) -> int:
        return self.n_pred - self.true_positive

    @property
    def false_negative(self) -> int:
        return self.n_truth - self.true_positive

    @property
    def precision(self) -> float:
        return self.true_positive / self.n_pred if self.n_pred else 0.0

    @property
    def recall(self) -> float:
        return self.true_positive / self.n_truth if self.n_truth else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def count_error(self) -> int:
        """個数の誤差(予測 - 正解)。積算でいちばん効く指標。"""
        return self.n_pred - self.n_truth


def match_greedy(preds: Sequence[Box], truths: Sequence[Box], min_iou: float) -> MatchResult:
    """IoU の大きい順に貪欲に 1 対 1 で対応づける。

    予測はスコア順に並んでいる前提を置かず、IoU だけで対応づけます(個数を測るのが
    目的なので、スコア順の影響を入れない方が読みやすい)。
    """
    pairs: list[tuple[float, int, int]] = []
    for pi, p in enumerate(preds):
        for ti, t in enumerate(truths):
            value = iou(p, t)
            if value >= min_iou:
                pairs.append((value, pi, ti))
    pairs.sort(reverse=True)

    used_pred: set[int] = set()
    used_truth: set[int] = set()
    matched = 0
    for _value, pi, ti in pairs:
        if pi in used_pred or ti in used_truth:
            continue
        used_pred.add(pi)
        used_truth.add(ti)
        matched += 1
    return MatchResult(n_truth=len(truths), n_pred=len(preds), true_positive=matched)


def truth_boxes(plan: SyntheticPlan, kind: str) -> list[Box]:
    return [
        (float(s.box[0]), float(s.box[1]), float(s.box[2]), float(s.box[3]))
        for s in plan.symbols
        if s.kind == kind
    ]


# ---------------------------------------------------------------------------
# 1. 古典的画像処理: テンプレートマッチング
# ---------------------------------------------------------------------------


def _symbol_templates(kind: str) -> list[tuple[str, np.ndarray, tuple[int, int]]]:
    """記号のテンプレート画像を、正解の作図関数そのものから作る。

    戻り値は (名前, テンプレート画像(uint8), 正解ボックスに対するテンプレート左上の
    オフセット)。図面に現れ得る向きを網羅します。**探すべき形と寸法を正確に与えて
    いる**ので、古典側にとって最良に近い条件です。
    """
    from benchmarks.synthetic_plans import _CANVAS_H, _CANVAS_W, _PAPER, WallSegment

    templates: list[tuple[str, np.ndarray, tuple[int, int]]] = []
    seen: set[str] = set()

    for wall_idx, sym_kind, start, end, side, room in _OPENINGS:
        if sym_kind != kind:
            continue
        wall = _WALLS[wall_idx]
        key = f"{wall.orientation}{side}{end - start}"
        if key in seen:
            continue
        seen.add(key)

        canvas = np.full((_CANVAS_H, _CANVAS_W), _PAPER, np.uint8)
        if kind == "door":
            symbol = _draw_door(canvas, wall, start, end, side, room)
        else:
            symbol = _draw_window(canvas, wall, start, end, room)
        x1, y1, x2, y2 = symbol.box
        # テンプレートは正解ボックスそのもので切り出す(= 照合時の位置合わせが素直)。
        x1, y1 = max(0, x1), max(0, y1)
        patch = canvas[y1:y2, x1:x2]
        if patch.size == 0:
            continue
        templates.append((key, patch.copy(), (0, 0)))

    # 壁が水平か垂直かで記号の向きが変わるため、90 度回転版も足しておく。
    for key, patch, offset in list(templates):
        templates.append((f"{key}-rot90", cv2.rotate(patch, cv2.ROTATE_90_CLOCKWISE), offset))
        templates.append(
            (f"{key}-rot270", cv2.rotate(patch, cv2.ROTATE_90_COUNTERCLOCKWISE), offset)
        )
    return templates


#: NMS に渡す候補の上限(スコア上位から)。
#: しきい値を下げるとテンプレート照合は 10 万件規模の候補を出すため、上限が無いと
#: NMS が候補数の二乗で効いて終わらない。1 枚の正解記号は 4 個なので、上位 3000 件
#: まで見れば取りこぼしは起きない。
_NMS_CANDIDATE_CAP = 3000

#: NMS の後に残す最大件数。これを超える時点で検出として成立していないため、
#: 打ち切って偽陽性としてそのまま数える。
_NMS_KEEP_CAP = 200


def _nms(boxes: list[Box], scores: list[float], iou_threshold: float = 0.3) -> list[Box]:
    """スコアの高い順に、重なるボックスを潰す。"""
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    order = order[:_NMS_CANDIDATE_CAP]
    kept: list[Box] = []
    for i in order:
        if all(iou(boxes[i], k) <= iou_threshold for k in kept):
            kept.append(boxes[i])
            if len(kept) >= _NMS_KEEP_CAP:
                break
    return kept


#: 古典側で振るメディアンフィルタのカーネル。
#: **1 を必ず含めること。**3x3 のメディアンは窓記号の 1px の細線を消してしまい、
#: 固定してしまうと古典側が不当に弱くなる(実測: clean でも窓の相関が 0.26 止まり)。
MEDIAN_KSIZES: tuple[int, ...] = (1, 3, 5)


def _template_responses(
    image: np.ndarray, kind: str, median_ksize: int
) -> list[tuple[np.ndarray, int, int]]:
    """テンプレートごとの相関マップ。しきい値を振る前に 1 度だけ計算する。"""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if median_ksize > 1:
        gray = cv2.medianBlur(gray, median_ksize)

    maps: list[tuple[np.ndarray, int, int]] = []
    for _name, template, _offset in _symbol_templates(kind):
        th, tw = template.shape[:2]
        if th >= gray.shape[0] or tw >= gray.shape[1]:
            continue
        maps.append((cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED), tw, th))
    return maps


def _boxes_from_responses(
    maps: list[tuple[np.ndarray, int, int]], threshold: float
) -> list[Box]:
    boxes: list[Box] = []
    scores: list[float] = []
    for response, tw, th in maps:
        ys, xs = np.where(response >= threshold)
        for y, x in zip(ys, xs):
            boxes.append((float(x), float(y), float(x + tw), float(y + th)))
            scores.append(float(response[y, x]))
    return _nms(boxes, scores)


def template_detect(
    image: np.ndarray, kind: str, threshold: float, median_ksize: int = 1
) -> list[Box]:
    """正規化相互相関で記号を探す古典的手法。"""
    return _boxes_from_responses(_template_responses(image, kind, median_ksize), threshold)


def template_best(
    plan: SyntheticPlan, kind: str, min_iou: float, thresholds: Iterable[float]
) -> tuple[int, float, MatchResult]:
    """前処理と判定しきい値を振り、最良 F1 を古典側の成績とする。

    戻り値は (メディアンのカーネル, 判定しきい値, 照合結果)。
    """
    truths = truth_boxes(plan, kind)
    thresholds = list(thresholds)
    best: tuple[int, float, MatchResult] | None = None
    for ksize in MEDIAN_KSIZES:
        maps = _template_responses(plan.image, kind, ksize)
        for threshold in thresholds:
            preds = _boxes_from_responses(maps, threshold)
            result = match_greedy(preds, truths, min_iou)
            key = (result.f1, -abs(result.count_error))
            if best is None or key > (best[2].f1, -abs(best[2].count_error)):
                best = (ksize, threshold, result)
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# 2. Grounding DINO
# ---------------------------------------------------------------------------

#: カテゴリごとに試すプロンプト。表現で結果が変わるため複数用意し、
#: **古典側と同様に最良のものを Grounding DINO の成績とする**(条件を揃える)。
PROMPT_VARIANTS: dict[str, tuple[str, ...]] = {
    "door": (
        "a door symbol in a floor plan",
        "door",
        "a door. a doorway. a swinging door.",
        "architectural door symbol with swing arc",
    ),
    "window": (
        "a window symbol in a floor plan",
        "window",
        "a window. a window opening.",
        "architectural window symbol in a wall",
    ),
}


@dataclass
class GdinoRun:
    """1 枚 1 カテゴリ 1 プロンプト分の生の検出結果(しきい値を掛ける前)。"""

    level: str
    category: str
    prompt: str
    detections: list[RawDetection] = field(default_factory=list)
    seconds: float = 0.0


def collect_gdino(
    backend,
    levels: Sequence[DegradationLevel],
    categories: Sequence[str],
    seed: int = 0,
) -> list[GdinoRun]:
    """全条件で生スコアを取り切る。しきい値の検討は後段で何度でもやり直せる。"""
    runs: list[GdinoRun] = []
    for level in levels:
        plan = make_plan(level, seed=seed)
        for category in categories:
            for prompt in PROMPT_VARIANTS[category]:
                started = time.monotonic()
                detections = list(backend.detect(plan.image, prompt))
                elapsed = time.monotonic() - started
                runs.append(
                    GdinoRun(
                        level=str(level),
                        category=category,
                        prompt=prompt,
                        detections=detections,
                        seconds=elapsed,
                    )
                )
                print(
                    f"  [gdino] {level:8s} {category:6s} "
                    f"raw={len(detections):3d} {elapsed:5.1f}s  «{prompt}»",
                    flush=True,
                )
    return runs


def gdino_at_threshold(
    run: GdinoRun, box_threshold: float, min_iou: float, plan: SyntheticPlan
) -> MatchResult:
    accepted = [
        d
        for d in run.detections
        if d.box_score >= box_threshold and d.text_score >= box_threshold
    ]
    kept = _nms([d.box for d in accepted], [d.box_score for d in accepted])
    return match_greedy(kept, truth_boxes(plan, run.category), min_iou)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="IDEA-Research/grounding-dino-tiny")
    parser.add_argument("--levels", nargs="*", default=list(ALL_LEVELS))
    parser.add_argument("--out", default="benchmarks/grounding_dino_results.json")
    parser.add_argument(
        "--skip-gdino",
        action="store_true",
        help="古典側だけ回す(モデルが取れない環境での動作確認用)",
    )
    args = parser.parse_args()

    levels: list[DegradationLevel] = [lv for lv in args.levels]  # type: ignore[assignment]
    categories = ("door", "window")
    # 0.20〜0.98。劣化が強いと窓記号の相関は 0.25 程度まで落ちるため、
    # 下限を 0.50 にすると古典側が「1 つも見つけられなかった」ことになってしまう。
    thresholds = [round(0.20 + 0.02 * i, 2) for i in range(40)]

    payload: dict[str, object] = {"levels": levels, "model": args.model}

    # --- 古典 -------------------------------------------------------------
    print("== 古典的画像処理(テンプレートマッチング)==", flush=True)
    classical: list[dict[str, object]] = []
    for level in levels:
        plan = make_plan(level, seed=0)
        for category in categories:
            for name, min_iou in (("strict", IOU_STRICT), ("loose", IOU_LOOSE)):
                ksize, threshold, result = template_best(plan, category, min_iou, thresholds)
                classical.append(
                    {
                        "level": str(level),
                        "category": category,
                        "iou": name,
                        "best_median_ksize": ksize,
                        "best_threshold": threshold,
                        "n_truth": result.n_truth,
                        "n_pred": result.n_pred,
                        "tp": result.true_positive,
                        "fp": result.false_positive,
                        "fn": result.false_negative,
                        "precision": round(result.precision, 3),
                        "recall": round(result.recall, 3),
                        "f1": round(result.f1, 3),
                        "count_error": result.count_error,
                    }
                )
                print(
                    f"  {level:8s} {category:6s} IoU>={min_iou:.2f} "
                    f"median={ksize} best_th={threshold:.2f} "
                    f"P={result.precision:.2f} R={result.recall:.2f} "
                    f"F1={result.f1:.2f} 個数={result.n_pred}/{result.n_truth}",
                    flush=True,
                )
    payload["classical"] = classical

    if args.skip_gdino:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"\n古典側のみ書き出しました: {args.out}")
        return

    # --- Grounding DINO ---------------------------------------------------
    from axes.image_axis.grounding_dino_adapter import HuggingFaceGroundingDinoBackend

    print("\n== Grounding DINO(ゼロショット)==", flush=True)
    backend = HuggingFaceGroundingDinoBackend(model_id=args.model, raw_score_floor=0.02)
    runs = collect_gdino(backend, levels, categories)

    plans = {str(lv): make_plan(lv, seed=0) for lv in levels}

    # (a) しきい値を振って最良 — 古典側と同じ条件
    gdino_best: list[dict[str, object]] = []
    gdino_sweep: list[dict[str, object]] = []
    for name, min_iou in (("strict", IOU_STRICT), ("loose", IOU_LOOSE)):
        for level in levels:
            for category in categories:
                best: tuple[str, float, MatchResult] | None = None
                for run in runs:
                    if run.level != str(level) or run.category != category:
                        continue
                    for box_threshold in [round(0.05 * i, 2) for i in range(1, 17)]:
                        result = gdino_at_threshold(
                            run, box_threshold, min_iou, plans[str(level)]
                        )
                        gdino_sweep.append(
                            {
                                "level": str(level),
                                "category": category,
                                "iou": name,
                                "prompt": run.prompt,
                                "box_threshold": box_threshold,
                                "tp": result.true_positive,
                                "n_pred": result.n_pred,
                                "f1": round(result.f1, 3),
                                "count_error": result.count_error,
                            }
                        )
                        key = (result.f1, -abs(result.count_error))
                        if best is None or key > (best[2].f1, -abs(best[2].count_error)):
                            best = (run.prompt, box_threshold, result)
                assert best is not None
                prompt, box_threshold, result = best
                gdino_best.append(
                    {
                        "level": str(level),
                        "category": category,
                        "iou": name,
                        "best_prompt": prompt,
                        "best_box_threshold": box_threshold,
                        "n_truth": result.n_truth,
                        "n_pred": result.n_pred,
                        "tp": result.true_positive,
                        "fp": result.false_positive,
                        "fn": result.false_negative,
                        "precision": round(result.precision, 3),
                        "recall": round(result.recall, 3),
                        "f1": round(result.f1, 3),
                        "count_error": result.count_error,
                    }
                )
                print(
                    f"  {level:8s} {category:6s} IoU>={min_iou:.2f} "
                    f"best_th={box_threshold:.2f} "
                    f"P={result.precision:.2f} R={result.recall:.2f} "
                    f"F1={result.f1:.2f} 個数={result.n_pred}/{result.n_truth} "
                    f"«{prompt}»",
                    flush=True,
                )
    payload["gdino_best"] = gdino_best
    payload["gdino_sweep"] = gdino_sweep

    # (b) アダプタの既定しきい値をそのまま適用したとき ----------------------
    print("\n== アダプタ既定しきい値 "
          f"(box={DEFAULT_BOX_THRESHOLD} text={DEFAULT_TEXT_THRESHOLD} "
          f"margin={DEFAULT_CONFIDENT_MARGIN}) ==", flush=True)
    defaults: list[dict[str, object]] = []
    for level in levels:
        plan = plans[str(level)]
        for category in categories:
            run = next(
                r
                for r in runs
                if r.level == str(level)
                and r.category == category
                and r.prompt == PROMPT_VARIANTS[category][0]
            )

            class _Replay:
                def __init__(self, detections: Sequence[RawDetection]) -> None:
                    self._detections = list(detections)

                def detect(self, image, prompt):  # noqa: ANN001
                    return self._detections

            adapter = GroundingDinoAdapter(
                _Replay(run.detections),
                categories=[CategoryConfig(category, run.prompt)],
            )
            reading = adapter.detect_category(plan.image, category)
            truth = len(truth_boxes(plan, category))
            covers = reading.count_range[0] <= truth <= reading.count_range[1]
            defaults.append(
                {
                    "level": str(level),
                    "category": category,
                    "prompt": run.prompt,
                    "count_range": list(reading.count_range),
                    "truth": truth,
                    "range_covers_truth": covers,
                    "status": reading.status,
                    "raw_detection_count": reading.evidence["raw_detection_count"],
                    "rejected_count": reading.evidence["rejected_count"],
                }
            )
            print(
                f"  {level:8s} {category:6s} range={reading.count_range} "
                f"正解={truth} 包含={'○' if covers else '×'} "
                f"status={reading.status}",
                flush=True,
            )
    payload["gdino_defaults"] = defaults

    # (c) 生スコアの分布(しきい値の妥当性を見るため) ----------------------
    score_stats: list[dict[str, object]] = []
    for run in runs:
        if run.prompt != PROMPT_VARIANTS[run.category][0]:
            continue
        truths = truth_boxes(plans[run.level], run.category)
        hit_scores: list[float] = []
        miss_scores: list[float] = []
        for d in run.detections:
            best_iou = max((iou(d.box, t) for t in truths), default=0.0)
            (hit_scores if best_iou >= IOU_LOOSE else miss_scores).append(d.box_score)
        score_stats.append(
            {
                "level": run.level,
                "category": run.category,
                "n_raw": len(run.detections),
                "n_overlapping_truth": len(hit_scores),
                "max_score_overlapping": round(max(hit_scores), 3) if hit_scores else None,
                "max_score_not_overlapping": round(max(miss_scores), 3) if miss_scores else None,
                "seconds": round(run.seconds, 1),
            }
        )
    payload["gdino_score_stats"] = score_stats

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"\n書き出しました: {args.out}")


if __name__ == "__main__":
    main()
