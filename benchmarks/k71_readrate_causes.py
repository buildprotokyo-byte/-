"""K-71 作業 3 周 0: 表と線の落ちの原因を件数で分ける(直さない。数えるだけ)。

基準は `docs/k71_readrate_criteria.md`(測る前にコミット済み)。切り口は 3 つ:
ページの種類(整理の段の種類)/ 図形の種類(`erase_check` の元の種類と線の種類)/ 位置の取り方(P1〜P4)。

**新しく AI を呼ばない。出すのは件数と割合だけ**(図面の文字・室名は出さない)。

    PYTHONPATH=. python -m benchmarks.k71_readrate_causes \
        --pdf /mnt/project-files/anonymized/P011_匿名化v4.pdf \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R1 --out 周0_R1.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

P1, P2, P3, P4 = "P1 大きい箱にだけ入っていた", "P2 一部だけ重なる", "P3 近いが外れ", "P4 覆う要素が無い"
CAUSES = (P1, P2, P3, P4)
NEAR_PX = 20.0


def load_run(run: Path) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    draft = json.loads((run / "下書き.json").read_text(encoding="utf-8"))
    reading = {int(k): v for k, v in (draft["読む"].get("読み") or {}).items()}
    kinds = {int(k): (v or {}).get("種類") or "不明" for k, v in ((draft.get("整理") or {}).get("ページ") or {}).items()}
    return reading, kinds


def _boxes(elements: Sequence[Mapping[str, Any]], margin: float) -> list[tuple[tuple[float, float, float, float], float, str]]:
    out = []
    for e in elements:
        pos = e.get("位置")
        if not pos or len(pos) != 4:
            continue
        x0, y0, x1, y1 = min(pos[0], pos[2]), min(pos[1], pos[3]), max(pos[0], pos[2]), max(pos[1], pos[3])
        out.append(((x0 - margin, y0 - margin, x1 + margin, y1 + margin), (x1 - x0) * (y1 - y0), str(e.get("種類") or "")))
    return out


def _inside_share(points: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    """各箱について、見本の点が入る割合。"""
    if not len(boxes):
        return np.zeros(0)
    inside = ((points[:, None, 0] >= boxes[None, :, 0]) & (points[:, None, 0] <= boxes[None, :, 2])
              & (points[:, None, 1] >= boxes[None, :, 1]) & (points[:, None, 1] <= boxes[None, :, 3]))
    return inside.mean(axis=0)


def _gap(bbox: Sequence[float], boxes: np.ndarray) -> float:
    if not len(boxes):
        return float("inf")
    dx = np.maximum(np.maximum(boxes[:, 0] - bbox[2], 0), bbox[0] - boxes[:, 2])
    dy = np.maximum(np.maximum(boxes[:, 1] - bbox[3], 0), bbox[1] - boxes[:, 3])
    return float(np.hypot(dx, dy).min())


def cause_of(prim: Any, small: np.ndarray, big: np.ndarray, big_kinds: Sequence[str], need: float) -> tuple[str, str]:
    """印の付かなかった図形 1 つの「位置の取り方」の原因。返り値は (原因, 大きい箱の要素の種類)。"""
    share_big = _inside_share(prim.points, big)
    if len(share_big) and (share_big >= need).any():
        return P1, big_kinds[int(np.argmax(share_big >= need))] or "種類なし"
    share_small = _inside_share(prim.points, small)
    if len(share_small) and (share_small > 0).any():
        return P2, ""
    if _gap(prim.bbox, small) <= NEAR_PX:
        return P3, ""
    return P4, ""


def page_causes(page: Any, number: int, elements: Sequence[Mapping[str, Any]], page_kind: str,
                cap: float = 0.01, line_types: bool = True) -> dict[str, Any]:
    from benchmarks import erase_check as ec
    from draft.readthrough import _inside, _table_rects

    prims, _ = ec.check_page(page, number, list(elements), cap)
    scale = ec.WIDTH_PX / page.rect.width
    w, h = page.rect.width * scale, page.rect.height * scale
    margin = ec.SETTINGS["印の余白(画素)"]
    need = ec.SETTINGS["印に要る見本の点の割合"]
    boxes = _boxes(elements, margin)
    small = np.array([b for b, a, _ in boxes if a <= cap * w * h]).reshape(-1, 4)
    big_list = [(b, k) for b, a, k in boxes if a > cap * w * h]
    big = np.array([b for b, _ in big_list]).reshape(-1, 4)
    big_kinds = [k for _, k in big_list]
    rects, _ = _table_rects(page)
    live = [p for p in prims if not p.excluded]
    kinds_of_line = ec.classify_lines(prims) if line_types else {}

    rows = []
    for p in live:
        in_table = bool(rects) and _inside(p.bbox, rects, scale)
        is_line = p.category == "線"
        if not (in_table or is_line) or p.marked:
            continue
        cause, big_kind = cause_of(p, small, big, big_kinds, need)
        rows.append({"表": in_table, "線": is_line, "元の種類": p.kind, "種類": p.category,
                     "線の種類": kinds_of_line.get(p.id, "") if is_line else "",
                     "長さ": float(p.length) if is_line else 0.0, "原因": cause, "大きい箱の種類": big_kind,
                     "ページの種類": page_kind, "ページ": number})
    outside = sum(1 for e in elements if e.get("位置") and len(e["位置"]) == 4
                  and (min(e["位置"][0], e["位置"][2]) < -1 or min(e["位置"][1], e["位置"][3]) < -1
                       or max(e["位置"][0], e["位置"][2]) > w + 1 or max(e["位置"][1], e["位置"][3]) > h + 1))
    table_total = sum(1 for p in live if rects and _inside(p.bbox, rects, scale))
    line_total = [p for p in live if p.category == "線"]
    return {"落ち": rows, "画像の外にはみ出す要素": outside,
            "表の数える図形": table_total, "線の数える図形": len(line_total),
            "線の長さの全部": float(sum(p.length for p in line_total if p.kind in ("直線", "曲線", "ハッチング"))),
            "線の長さの全部(readthrough と同じ)": float(sum(p.length for p in line_total))}


def tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """表の落ち(件数)と線の落ち(件数・長さ)を、3 つの切り口で数える。"""
    out: dict[str, Any] = {}
    for target in ("表", "線"):
        sel = [r for r in rows if r[target]]
        block: dict[str, Any] = {"落ちた数": len(sel)}
        if target == "線":
            block["落ちた長さ"] = round(sum(r["長さ"] for r in sel), 1)
        axes = {"ページの種類": "ページの種類", "図形の種類": "元の種類", "位置の取り方": "原因"}
        if target == "線":
            axes["線の種類"] = "線の種類"
        for name, key in axes.items():
            counts: Counter = Counter(r[key] for r in sel)
            lengths: defaultdict = defaultdict(float)
            for r in sel:
                lengths[r[key]] += r["長さ"]
            table = {}
            for k, n in counts.most_common():
                cell = {"数": n, "割合": round(n / len(sel), 4) if sel else None}
                if target == "線":
                    total = block["落ちた長さ"]
                    cell["長さ"] = round(lengths[k], 1)
                    cell["長さの割合"] = round(lengths[k] / total, 4) if total else None
                table[k] = cell
            block[name] = table
        big = Counter(r["大きい箱の種類"] for r in sel if r["原因"] == P1)
        block["P1 の大きい箱の要素の種類"] = dict(big.most_common())
        out[target] = block
    return out


def pick_cause(per_run: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """0(c): 表の落ち(件数)の割合と線の落ち(長さ)の割合の平均が、3 回の平均でいちばん大きい原因。"""
    score = {}
    for cause in CAUSES:
        vals = []
        for t in per_run:
            table = (t["表"]["位置の取り方"].get(cause) or {}).get("割合") or 0.0
            line = (t["線"]["位置の取り方"].get(cause) or {}).get("長さの割合") or 0.0
            vals.append((table + line) / 2)
        score[cause] = round(sum(vals) / len(vals), 4) if vals else 0.0
    best = max(score, key=score.get)
    return {"点": score, "いちばん大きい原因": best}


def run(pdf: Path, run_dir: Path, cap: float = 0.01, line_types: bool = True) -> dict[str, Any]:
    import pymupdf

    reading, kinds = load_run(run_dir)
    rows: list[dict[str, Any]] = []
    outside = 0
    no_pos = 0
    with pymupdf.open(pdf) as doc:
        for number in sorted(reading):
            elements = [e for e in (reading[number] or {}).get("要素", []) if e.get("位置")]
            no_pos += sum(1 for e in (reading[number] or {}).get("要素", []) if not e.get("位置"))
            page = doc.load_page(number - 1)
            got = page_causes(page, number, elements, kinds.get(number, "不明"), cap, line_types)
            rows.extend(got["落ち"])
            outside += got["画像の外にはみ出す要素"]
    result = tally(rows)
    result["見張り"] = {"画像の外にはみ出す要素": outside, "位置の無い要素": no_pos}
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-71 周 0 表と線の落ちの原因")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-line-types", action="store_true")
    a = parser.parse_args(argv)
    result = run(a.pdf, a.run, line_types=not a.no_line_types)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
