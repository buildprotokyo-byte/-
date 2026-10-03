"""K-72 作業 B: 「図を表と誤認する守り」を直す周の診断と、前 → 後・囮の測定。

基準は `docs/k72_readrate_guard_criteria.md`(診断と線を、直しを測る前にコミット済み)。

- ``diagnose``: 直す前に、何をどう誤認しているかを件数で出す。
  罫線の表(今の守りを通ったもの)ごとに、升目の数・面積・中の図形の数(種類ごと)・升目の縁に乗る罫線の数・乗らない線の数、
  表の落ちの数と、その落ちを囲んでいた AI の大きい箱の種類。あわせて、AI の大きい箱(面積 1% 超え)を種類ごとに、
  中に入る図形の数・中の読めていない線の長さ・ページの種類で数える。
- ``measure``: 前(今の守り)→ 後(図を表と誤認する守りを足した)を、本物と囮 5 つで測る。

**新しく AI を呼ばない。出すのは件数と割合とページの番号・種類だけ**(図面の文字・室名は出さない)。

    PYTHONPATH=. python -m benchmarks.k72_readrate_guard diagnose \
        --pdf /mnt/project-files/anonymized/P011_匿名化v4.pdf \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R1 --out 診断_R1.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

#: 線の仲間(罫線か図の線かを数える対象)。
LINE_KINDS = ("直線", "曲線", "矩形", "四角形", "ハッチング")
#: 囮 5(でたらめに置いた箱)の種。
DECOY_SEED = 72


def table_profile(page: Any, table: Mapping[str, Any], prims: Sequence[Any], scale: float) -> dict[str, Any]:
    """罫線の表 1 つの中身(読みに依らない。図形の層だけで数える)。"""
    from draft import table_grid
    from draft.readthrough import _inside

    rect = table["四角"]
    horizontal, vertical = table_grid._cell_edges(table["升目"], scale)
    inside = [p for p in prims if not p.excluded and _inside(p.bbox, [rect], scale)]
    lines = [p for p in inside if p.kind in LINE_KINDS]
    ruling = sum(1 for p in lines if table_grid.is_ruling(p, horizontal, vertical))
    area = (rect[2] - rect[0]) * (rect[3] - rect[1]) / (page.rect.width * page.rect.height)
    return {
        "升目": len(table["升目"]),
        "面積(ページ比)": round(area, 4),
        "中の図形": len(inside),
        "中の図形(種類ごと)": dict(Counter(p.kind for p in inside).most_common()),
        "線の仲間": len(lines),
        "升目の縁に乗る罫線": ruling,
        "升目の縁に乗らない線": len(lines) - ruling,
        "罫線の割合": round(ruling / len(lines), 4) if lines else None,
    }


def _contained(prims: Sequence[Any], box: Sequence[float], margin: float, need: float) -> list[Any]:
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    out = []
    for p in prims:
        pts = p.points
        inside = (pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)
        if inside.mean() >= need:
            out.append(p)
    return out


def _band(n: int) -> str:
    for hi, label in ((5, "0〜5"), (20, "6〜20"), (100, "21〜100"), (500, "101〜500")):
        if n <= hi:
            return label
    return "501 以上"


def _group(sel: Sequence[Mapping[str, Any]], total: int) -> dict[str, Any]:
    misses = sum(t["表の落ち"] for t in sel)
    counted = sum(t["表の数える図形"] for t in sel)
    kinds: Counter = Counter()
    for t in sel:
        kinds.update(t["落ちを囲んでいた大きい箱の種類(P1 以外は原因)"])
    return {
        "表の数": len(sel),
        "表の数える図形(表ごとの和)": counted,
        "表の落ち": misses,
        "表の落ちの割合": round(misses / total, 4) if total else None,
        "この中だけの表の読了率(表ごとの和)": round(1 - misses / counted, 4) if counted else None,
        "ページの種類": dict(Counter(t["ページの種類"] for t in sel).most_common()),
        "中の図形の数": [t["中の図形"] for t in sel],
        "升目 / 縁に乗る罫線 / 乗らない線": [[t["升目"], t["升目の縁に乗る罫線"], t["升目の縁に乗らない線"]] for t in sel],
        "落ちを囲んでいた大きい箱の種類": dict(kinds.most_common()),
    }


def diagnose_page(page: Any, number: int, elements: Sequence[Mapping[str, Any]], page_kind: str,
                  cap: float = 0.01) -> dict[str, Any]:
    """1 ページの診断。今の台帳(K-71 の後: 機械の罫線あり)で数える。"""
    from benchmarks import erase_check as ec
    from benchmarks.k71_readrate_causes import _boxes, cause_of
    from draft import table_grid
    from draft.readthrough import _inside, _ruled_tables

    ledger, _ = table_grid.ledger(page, number, list(elements), cap)
    prims, _ = ec.check_page(page, number, list(ledger), cap)
    scale = ec.WIDTH_PX / page.rect.width
    w, h = page.rect.width * scale, page.rect.height * scale
    margin = ec.SETTINGS["印の余白(画素)"]
    need = ec.SETTINGS["印に要る見本の点の割合"]
    boxes = _boxes(ledger, margin)
    small = np.array([b for b, a, _ in boxes if a <= cap * w * h]).reshape(-1, 4)
    big_list = [(b, k) for b, a, k in boxes if a > cap * w * h]
    big = np.array([b for b, _ in big_list]).reshape(-1, 4)
    big_kinds = [k for _, k in big_list]
    live = [p for p in prims if not p.excluded]

    tables, note = _ruled_tables(page)
    rows = []
    for index, table in enumerate(tables, 1):
        row = {"ページ": number, "ページの種類": page_kind, "表": index, **table_profile(page, table, prims, scale)}
        inside = [p for p in live if _inside(p.bbox, [table["四角"]], scale)]
        missed = [p for p in inside if not p.marked]
        kinds = Counter()
        for p in missed:
            cause, kind = cause_of(p, small, big, big_kinds, need)
            kinds[(kind or "種類なし") if cause.startswith("P1") else cause[:2]] += 1
        row["表の数える図形"] = len(inside)
        row["表の落ち"] = len(missed)
        row["落ちを囲んでいた大きい箱の種類(P1 以外は原因)"] = dict(kinds.most_common())
        rows.append(row)

    # AI の大きい箱(面積 1% 超え)。中に入る図形・読めていない線の長さ
    from draft.readthrough import line_ink_length

    big_rows = []
    for e in ledger:
        pos = e.get("位置")
        if not pos or len(pos) != 4:
            continue
        x0, y0, x1, y1 = min(pos[0], pos[2]), min(pos[1], pos[3]), max(pos[0], pos[2]), max(pos[1], pos[3])
        if (x1 - x0) * (y1 - y0) <= cap * w * h:
            continue
        held = _contained(live, (x0, y0, x1, y1), margin, need)
        unread_lines = [p for p in held if p.category == "線" and not p.marked]
        big_rows.append({"種類": str(e.get("種類") or "種類なし"), "ページの種類": page_kind,
                         "中の図形": len(held),
                         "中の線(種類「線」)": sum(1 for p in held if p.category == "線"),
                         "中の読めていない線の長さ": float(sum(line_ink_length(p) for p in unread_lines)),
                         "面積(ページ比)": (x1 - x0) * (y1 - y0) / (w * h)})
    line_missed = float(sum(line_ink_length(p) for p in live if p.category == "線" and not p.marked))
    return {"表": rows, "大きい箱": big_rows, "線の落ちた長さ": line_missed, "表の見つかり方": note}


#: 診断の分け方 2 つ(どちらも読みに依らない。図形の層だけ)。
#: 案 1(直す案): 升目 1 つあたりの、升目の縁に乗らない線が ``PER_CELL`` 本以上。
#: 案 2(参考): 線の仲間のうち升目の縁に乗る罫線が半分未満(かつ縁に乗らない線が 20 本以上)。
PER_CELL = 100


def is_drawing_per_cell(t: Mapping[str, Any]) -> bool:
    return t["升目"] > 0 and t["升目の縁に乗らない線"] / t["升目"] >= PER_CELL


def is_drawing_majority(t: Mapping[str, Any]) -> bool:
    share = t["罫線の割合"]
    return t["升目の縁に乗らない線"] >= 20 and share is not None and share < 0.5


def summarize(tables: Sequence[Mapping[str, Any]], bigs: Sequence[Mapping[str, Any]], line_missed: float) -> dict[str, Any]:
    """診断のまとめ。表を 2 つの分け方で「図の見込み」とそれ以外に分ける。"""
    total = sum(t["表の落ち"] for t in tables)
    groups: dict[str, dict[str, Any]] = {}
    for label, rule in (("案1 升目あたり縁に乗らない線 100 本以上", is_drawing_per_cell),
                        ("案2 罫線が線の仲間の半分未満", is_drawing_majority)):
        groups[label] = {}
        for side, sel in (("図の見込み", [t for t in tables if rule(t)]),
                          ("それ以外", [t for t in tables if not rule(t)])):
            groups[label][side] = _group(sel, total)
    by_page_kind: Counter = Counter()
    for t in tables:
        by_page_kind[t["ページの種類"]] += t["表の落ち"]

    big_by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({b["種類"] for b in bigs}):
        sel = [b for b in bigs if b["種類"] == kind]
        held = [b["中の図形"] for b in sel]
        unread = sum(b["中の読めていない線の長さ"] for b in sel)
        big_by_kind[kind] = {
            "箱の数": len(sel),
            "中の図形の合計": sum(held),
            "中の図形の中央値": statistics.median(held) if held else 0,
            "中の図形の数の分布": dict(Counter(_band(n) for n in held)),
            "中の読めていない線の長さ": round(unread, 1),
            "線の落ちた長さに対する割合(箱が重なると重複あり)": round(unread / line_missed, 4) if line_missed else None,
            "ページの種類": dict(Counter(b["ページの種類"] for b in sel).most_common()),
        }
    return {"表の落ち": total, "表の落ち(ページの種類ごと)": dict(by_page_kind.most_common()),
            "表の分け方": groups, "大きい箱(種類ごと)": big_by_kind, "線の落ちた長さ": round(line_missed, 1)}


def diagnose(pdf: Path, run_dir: Path) -> dict[str, Any]:
    import pymupdf

    from benchmarks.k71_readrate_causes import load_run

    reading, kinds = load_run(run_dir)
    tables: list[dict[str, Any]] = []
    bigs: list[dict[str, Any]] = []
    line_missed = 0.0
    with pymupdf.open(pdf) as doc:
        for number in sorted(reading):
            elements = [e for e in (reading[number] or {}).get("要素", []) if e.get("位置")]
            got = diagnose_page(doc.load_page(number - 1), number, elements, kinds.get(number, "不明"))
            tables.extend(got["表"])
            bigs.extend(got["大きい箱"])
            line_missed += got["線の落ちた長さ"]
    return {"まとめ": summarize(tables, bigs, line_missed), "表ごと": tables}


# --- 前 → 後と囮 ---

def random_boxes(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """囮 5: でたらめに置いた箱。ページごとに AI の要素と同じ数・同じ種類の箱を、
    でたらめな位置・でたらめな大きさ(面積はページの 0.01%〜1% を対数で一様、縦横比 1/5〜5)で置く(種 72)。"""
    import pymupdf

    from benchmarks import erase_check as ec

    rng = random.Random(DECOY_SEED)
    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for number, entry in sorted(reading.items()):
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            w, h = page.rect.width * scale, page.rect.height * scale
            boxes = []
            for element in entry.get("要素", []):
                if not element.get("位置"):
                    continue
                area = w * h * 10 ** rng.uniform(-4, -2)
                aspect = 5 ** rng.uniform(-1, 1)
                bw, bh = min((area * aspect) ** 0.5, w), min((area / aspect) ** 0.5, h)
                x, y = rng.uniform(0, w - bw), rng.uniform(0, h - bh)
                boxes.append({"種類": element.get("種類"), "位置": [x, y, x + bw, y + bh]})
            out[number] = {"要素": boxes}
    return out


def candidate_table_boxes(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """囮 4: 守りの前の候補の表(図を表と誤認したものも含む)の四角を、種類「表」の箱で囲んだだけの版(中身は読まない)。"""
    import pymupdf

    from benchmarks import erase_check as ec
    from draft.readthrough import _ruled_tables

    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for number in reading:
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            tables, _ = _ruled_tables(page, drawing_guard=False)
            out[number] = {"要素": [{"種類": "表", "内容": "表", "位置": [v * scale for v in t["四角"]]} for t in tables]}
    return out


SETTINGS = {"前": {"drawing_guard": False}, "後": {"drawing_guard": True}}


def measure(pdf: Path, reading: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    from benchmarks.k71_readrate_round import rates, table_boxes
    from benchmarks.measure_readthrough import big_boxes, scatter
    from draft.readthrough import readthrough

    pages = sorted(reading)
    out: dict[str, Any] = {}

    def both(rd: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
        got = {}
        for label, kw in SETTINGS.items():
            result = readthrough(pdf, rd, pages, with_unread=False, **kw)
            got[label] = rates(result)
            got[label]["表の測れないページ"] = (result["別の切り口(重なる)"].get("表") or {}).get("測れないページ")
            got[label]["表の数える図形"] = (result["別の切り口(重なる)"].get("表") or {}).get("数える")
        return got

    out["本物"] = both(reading)
    decoys = {"囮1 でたらめに置き直した版": scatter(reading, pdf),
              "囮2 ページを覆う大きい箱で囲んだ版": big_boxes(reading, pdf),
              "囮3 守りを通った表の四角を表の箱で囲んだだけの版": table_boxes(reading, pdf),
              "囮4 守りの前の候補の表(図も含む)を表の箱で囲んだだけの版": candidate_table_boxes(reading, pdf),
              "囮5 でたらめに置いた箱": random_boxes(reading, pdf)}
    for name, fake in decoys.items():
        out[name] = both(fake)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-72 作業 B 図を表と誤認する守りの診断と測定")
    parser.add_argument("mode", choices=("diagnose", "measure"))
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    a = parser.parse_args(argv)
    if a.mode == "diagnose":
        result = {"回": a.run.name, **diagnose(a.pdf, a.run)}
    else:
        from benchmarks.measure_readthrough import load_reading

        result = {"回": a.run.name, **measure(a.pdf, load_reading(a.run))}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
