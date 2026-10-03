"""K-73 作業 5: 目盛りを当てる直し(K-38 の口)を、囮で確かめる(つながない。旗オフのまま)。

基準は `docs/k73_scale_decoy_criteria.md`(測る前にコミットした)。**本番のコードは変えない。AI は呼ばない。
正解は開かない。数量の値は書き出さない**(合否と件数だけ)。

使い方::

    PYTHONPATH=. python -m benchmarks.k73_scale_decoy \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --k37-rooms <reports/K-37/p011_rooms.json> \\
      --sides <reports/K-72/作業C/k37_室の辺の写し.json> \\
      --out docs/k73_scale_decoy_result.json \\
      --detail <reports/K-73/作業5/k73_scale_decoy_detail.json>

室名は図面の上で室名の文字を探す鍵としてだけメモリで使い、外に書かない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks import k72_area_ceiling as k72

VALUE_TOL_MM = 0.5
SCALE_TOL = 0.01
PAGE_AGREE_LINE = 0.5
LINE_TOL_PT = 2.0
END_TOL_PT = 2.0
CHAIN_SUM_TOL_MM = 1.0

REAL = "本物"
DECOY_SCALES = {"囮1 縮尺×0.95": 0.95, "囮2 縮尺×1.05": 1.05}
REFERENCE_SCALES = {"参考 縮尺×0.98": 0.98, "参考 縮尺×1.02": 1.02}
DECOY_MOVE = "囮3 別の室へ移す"
CHECKS = ("K1 辺が1つに決まる", "K2 辺の目盛り", "K3 ページの検算", "K4 鎖の和", "K5 室名が長方形の中",
          "K6 ほかの室名が中に無い")


# --- 読みの形(合成の試験でも同じ形を使う) --------------------------------------------
# 読み = {"id", "値", "向き", "s": (x, y), "e": (x, y), "紙": 紙の上の長さ pt, "目盛りで拾った": bool}


def offset(r: Mapping[str, Any]) -> float:
    """寸法線の位置(横の線なら y、縦の線なら x)。"""
    axis = 1 if r["向き"] == "横" else 0
    return (r["s"][axis] + r["e"][axis]) / 2.0


def extent(r: Mapping[str, Any]) -> tuple[float, float]:
    """寸法線の区間(横なら x の範囲、縦なら y の範囲)。"""
    axis = 0 if r["向き"] == "横" else 1
    a, b = r["s"][axis], r["e"][axis]
    return (min(a, b), max(a, b))


def unique(readings: Sequence[Mapping[str, Any]], value: float, orientation: str) -> Mapping[str, Any] | None:
    hits = [r for r in readings if abs(float(r["値"]) - float(value)) <= VALUE_TOL_MM and r["向き"] == orientation]
    return hits[0] if len(hits) == 1 else None


def side_readings(readings: Sequence[Mapping[str, Any]], values: Sequence[float], orientation: str) -> list | None:
    """K1: 辺の値のどれにも、値と向きが同じ読みがちょうど 1 つ。決まらなければ None。"""
    out = []
    for v in values:
        r = unique(readings, v, orientation)
        if r is None:
            return None
        out.append(r)
    return out or None


def scale_ok(r: Mapping[str, Any], mm_per_point: float, tol: float = SCALE_TOL) -> bool:
    measured = float(r["紙"]) * mm_per_point
    return measured > 0 and abs(float(r["値"]) / measured - 1.0) <= tol


def page_check(readings: Sequence[Mapping[str, Any]], mm_per_point: float, reference_id: str | None) -> dict[str, Any]:
    """K3: 目盛りで拾った読みでも基準でもない、横・縦の読みの半分以上が ±1% に入る。"""
    pool = [r for r in readings if not r["目盛りで拾った"] and r["id"] != reference_id and r["向き"] in ("横", "縦")]
    agree = sum(1 for r in pool if scale_ok(r, mm_per_point))
    rate = agree / len(pool) if pool else None
    return {"見た読み": len(pool), "合う読み": agree, "割合": None if rate is None else round(rate, 4),
            "成り立つ": rate is not None and rate >= PAGE_AGREE_LINE}


def _tile(target: Mapping[str, Any], line: Sequence[Mapping[str, Any]]) -> list | None:
    """target の区間を、line の読みで端から端まで隙間なく埋める。埋まらない・決まらないなら None。"""
    lo, hi = extent(target)
    parts = [r for r in line if r is not target and r["id"] != target["id"]
             and extent(r)[0] >= lo - END_TOL_PT and extent(r)[1] <= hi + END_TOL_PT
             and (extent(r)[1] - extent(r)[0]) < (hi - lo) - END_TOL_PT]
    cur, used = lo, []
    while abs(cur - hi) > END_TOL_PT:
        nxt = [r for r in parts if abs(extent(r)[0] - cur) <= END_TOL_PT]
        if not nxt:
            return None
        ends = {round(extent(r)[1], 0) for r in nxt}
        if len(ends) != 1 or len(nxt) != 1:
            return None
        r = nxt[0]
        if extent(r)[1] <= cur:
            return None
        used.append(r)
        cur = extent(r)[1]
        if cur > hi + END_TOL_PT:
            return None
    return used if len(used) >= 2 else None


def _lines(readings: Sequence[Mapping[str, Any]], orientation: str) -> list[list]:
    """同じ向きの読みを、線の位置(2pt 以内)でまとめる。"""
    same = sorted((r for r in readings if r["向き"] == orientation), key=offset)
    groups: list[list] = []
    for r in same:
        if groups and abs(offset(r) - offset(groups[-1][0])) <= LINE_TOL_PT:
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


def chain_check(readings: Sequence[Mapping[str, Any]], targets: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """K4: targets の各読みを「全体」としても「部分」としても鎖と比べる。食い違い・合う・確かめられないの件数。"""
    out = {"食い違い": 0, "鎖で合う": 0, "確かめられない": 0}
    for t in targets:
        lines = _lines(readings, t["向き"])
        seen = 0
        # 全体として: どの線でもよい。t の区間を埋める。
        for line in lines:
            tile = _tile(t, line)
            if tile is not None:
                seen += 1
                ok = abs(sum(float(r["値"]) for r in tile) - float(t["値"])) <= CHAIN_SUM_TOL_MM
                out["鎖で合う" if ok else "食い違い"] += 1
        # 部分として: t の線で、t を含む「全体」の区間を埋める。
        own = next((ln for ln in lines if any(r is t or r["id"] == t["id"] for r in ln)), [])
        lo, hi = extent(t)
        for o in readings:
            if o["向き"] != t["向き"] or o["id"] == t["id"]:
                continue
            olo, ohi = extent(o)
            if not (olo <= lo + END_TOL_PT and ohi >= hi - END_TOL_PT and (ohi - olo) > (hi - lo) + END_TOL_PT):
                continue
            tile = _tile(o, own)
            if tile is None or not any(r["id"] == t["id"] for r in tile):
                continue
            seen += 1
            ok = abs(sum(float(r["値"]) for r in tile) - float(o["値"])) <= CHAIN_SUM_TOL_MM
            out["鎖で合う" if ok else "食い違い"] += 1
        if not seen:
            out["確かめられない"] += 1
    return out


def box(width: Sequence[Mapping[str, Any]], length: Sequence[Mapping[str, Any]]) -> tuple[tuple, tuple]:
    """横に使った読みの左右の端と、縦に使った読みの上下の端(`other_room_names_inside` と同じ)。"""
    xs = [p[0] for r in width for p in (r["s"], r["e"])]
    ys = [p[1] for r in length for p in (r["s"], r["e"])]
    return (min(xs), max(xs)), (min(ys), max(ys))


def inside(b: tuple[tuple, tuple], points: Sequence[tuple[float, float]]) -> bool:
    (x0, x1), (y0, y1) = b
    return any(x0 < x < x1 and y0 < y < y1 for x, y in points)


def evaluate(readings: Sequence[Mapping[str, Any]], mm_per_point: float, reference_id: str | None,
             sides: Mapping[str, Sequence[float]], labels: Mapping[Any, Sequence[tuple[float, float]]],
             room: Any, fixed: Mapping[str, Sequence[Mapping[str, Any]] | None] | None = None) -> dict[str, Any]:
    """1 つの場合(目盛り・室)の K1〜K6。`labels` は室の鍵 → 室名の文字の中心(同じページ)。

    ``fixed`` は参考の計算だけで使う(辺の読みを外から渡す。合否の場合では使わない)。
    """
    res: dict[str, Any] = {c: False for c in CHECKS}
    if fixed is not None:
        w, ln = fixed["横"], fixed["縦"]
    else:
        w = side_readings(readings, sides["横"], "横")
        ln = side_readings(readings, sides["縦"], "縦")
    detail: dict[str, Any] = {"横が決まった": w is not None, "縦が決まった": ln is not None}
    pc = page_check(readings, mm_per_point, reference_id)
    detail["K3"] = pc
    if w is None or ln is None:
        res["合う"] = False
        res["詳しく"] = detail
        return res
    res["K1 辺が1つに決まる"] = True
    res["K2 辺の目盛り"] = all(scale_ok(r, mm_per_point) for r in w + ln)
    res["K3 ページの検算"] = pc["成り立つ"]
    ch = chain_check(readings, w + ln)
    detail["K4"] = ch
    res["K4 鎖の和"] = ch["食い違い"] == 0
    b = box(w, ln)
    res["K5 室名が長方形の中"] = inside(b, labels.get(room, ()))
    res["K6 ほかの室名が中に無い"] = not any(inside(b, pts) for k, pts in labels.items() if k != room)
    detail["辺の読みは目盛りで拾った"] = sum(1 for r in w + ln if r["目盛りで拾った"])
    detail["室名が見つかった"] = bool(labels.get(room))
    res["合う"] = all(res[c] for c in CHECKS)
    res["詳しく"] = detail
    return res


def move_decoys(base: Mapping[str, Any], labels: Mapping[Any, Sequence[tuple[float, float]]], target: Any) -> dict:
    """囮 3: 本物の辺で作った数量を、ほかの室の数量として出す。K1〜K4 は本物と同じ、K5・K6 を移した先で見る。"""
    out = {"件数": 0, "合う": 0, "弱い囮(移した先の室名が見つからない)": 0, "合う(弱い囮を除く)": 0,
           "K5 で落ちた": 0, "K6 で落ちた": 0, "ごと": []}
    b = base.get("_box")
    for room in labels:
        if room == target:
            continue
        out["件数"] += 1
        weak = not labels.get(room)
        k5 = b is not None and inside(b, labels.get(room, ()))
        k6 = b is not None and not any(inside(b, pts) for k, pts in labels.items() if k != room)
        ok = all(base[c] for c in CHECKS[:4]) and k5 and k6
        out["合う"] += ok
        out["弱い囮(移した先の室名が見つからない)"] += weak
        out["合う(弱い囮を除く)"] += ok and not weak
        out["K5 で落ちた"] += not k5
        out["K6 で落ちた"] += not k6
        out["ごと"].append({"室の番号": room, "弱い囮": weak, "K5": k5, "K6": k6, "合う": ok})
    return out


# --- 実データ ---------------------------------------------------------------------------


def _as_dicts(page: Any) -> list[dict[str, Any]]:
    from axes.image_axis.pdf_dimensions import UNIT_FROM_RULER
    from intake.drawing_room_dimensions import dimension_ids

    return [{"id": i, "値": r.value_mm, "向き": r.orientation, "s": tuple(r.start_pt), "e": tuple(r.end_pt),
             "紙": r.paper_distance_pt, "目盛りで拾った": r.unit_source == UNIT_FROM_RULER, "_rect": r.text_rect_pt}
            for i, r in dimension_ids([page]).items()]


def _same_pick(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (all(abs(x - y) <= 0.5 for x, y in zip(a["_rect"], b["_rect"]))
            and all(abs(x - y) <= 0.5 for x, y in zip(a["s"] + a["e"], b["s"] + b["e"])))


def auto_confirm_check(width_mm: float, length_mm: float, ceiling_mm: float | None) -> dict[str, Any]:
    """つないだら作られる数量(床・周長・内壁)に確定の印が無いことと、手法の登録が校正なし・weak であること。"""
    from arbitration.method_policies import DEFAULT_METHOD_POLICIES
    from estimating.from_room_dimensions import ORIGIN_DRAWING, quantities_from_room_dimensions
    from intake.room_dimensions import RoomDimension

    res = quantities_from_room_dimensions(
        [RoomDimension(room_name="対象の室", length_mm=length_mm, width_mm=width_mm, ceiling_height_mm=ceiling_mm)],
        origin=ORIGIN_DRAWING)
    marked = sum(1 for q in res.quantities
                 if getattr(q, "action", None) is not None or getattr(q, "confirmed_range", None) is not None)
    pol = DEFAULT_METHOD_POLICIES[ORIGIN_DRAWING.method_id]
    return {"作られる数量": len(res.quantities), "確定の印がある数量": marked,
            "手法の登録": {"calibrated": pol.calibrated, "上限": pol.max_strength},
            "自動確定": marked + (1 if pol.calibrated else 0)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-73 作業 5 目盛りの直しを囮で確かめる(つながない)")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--k37-rooms", type=Path, required=True)
    p.add_argument("--sides", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--detail", type=Path, default=None)
    a = p.parse_args(argv)

    from app import _room_label_positions
    from axes.image_axis.pdf_dimensions import read_dimensions
    from draft.stages import room_key

    k37 = json.loads(a.k37_rooms.read_text(encoding="utf-8"))
    copy = json.loads(a.sides.read_text(encoding="utf-8"))
    base = copy["基準の寸法"]
    page_no = int(base["ページ"])
    by_no = {int(r["番号"]): r for r in copy["室"]}
    targets = [n for n, r in by_no.items() if r["横"]["状態"] == k72.COPY_MAPPED and r["縦"]["状態"] == k72.COPY_MAPPED]
    if len(targets) != 1:
        raise SystemExit(f"横・縦とも対応づけ済みの室が 1 つでない: {len(targets)}")
    target = targets[0]
    sides = {o: [float(v) for v in by_no[target][o]["値"]] for o in ("横", "縦")}

    plain_page = read_dimensions(a.pdf, page_no - 1)
    plain = _as_dicts(plain_page)
    ref = unique(plain, float(base["値_mm"]), base["向き"])
    if ref is None:
        raise SystemExit("基準の寸法が素の読みで 1 つに決まらない")
    mpp = float(ref["値"]) / float(ref["紙"])

    names = [r["室名"] for r in k37["室"]]
    pos = _room_label_positions(a.pdf, [page_no], names)
    labels = {i: [(x, y) for pg, x, y in pos.get(nm, []) if pg == page_no] for i, nm in enumerate(names)}

    cases: dict[str, Any] = {}
    reads: dict[str, list] = {}
    for name, factor in [(REAL, 1.0), *DECOY_SCALES.items(), *REFERENCE_SCALES.items()]:
        m = mpp * factor
        rd = _as_dicts(read_dimensions(a.pdf, page_no - 1, ruler_mm_per_point=m, ruler_tolerance=SCALE_TOL))
        reads[name] = rd
        ev = evaluate(rd, m, ref["id"], sides, labels, target)
        picks = [r for r in rd if r["目盛りで拾った"]]
        ev["詳しく"]["目盛りで拾った読み"] = len(picks)
        ev["詳しく"]["読みの数"] = len(rd)
        ev["詳しく"]["ページ全体の鎖(目盛りで拾った読み)"] = chain_check(rd, picks)
        cases[name] = ev
    real_picks = [r for r in reads[REAL] if r["目盛りで拾った"]]
    for name in cases:
        if name == REAL:
            continue
        picks = [r for r in reads[name] if r["目盛りで拾った"]]
        cases[name]["詳しく"]["本物と同じ区間を拾った"] = sum(1 for r in picks if any(_same_pick(r, q) for q in real_picks))
    real = cases[REAL]
    w = side_readings(reads[REAL], sides["横"], "横")
    ln = side_readings(reads[REAL], sides["縦"], "縦")
    real["_box"] = box(w, ln) if (w and ln) else None
    moved = move_decoys(real, labels, target)
    real.pop("_box")

    # --- 参考(測った後に足した。合否・線には入れない) ---
    # 本物の K1 が落ちた理由を数える: 目盛りで読み直した後、辺の値に当たる読みの数と、そのうち目盛りで拾った数。
    k1_diag = {}
    for o in ("横", "縦"):
        hits = [[r for r in reads[REAL] if abs(float(r["値"]) - v) <= VALUE_TOL_MM and r["向き"] == o] for v in sides[o]]
        k1_diag[o] = {"当たる読み": [len(h) for h in hits],
                      "うち目盛りで拾った": [sum(1 for r in h if r["目盛りで拾った"]) for h in hits],
                      "素の読みで当たる読み": [sum(1 for r in plain if abs(float(r["値"]) - v) <= VALUE_TOL_MM
                                             and r["向き"] == o) for v in sides[o]]}
    # K-72 と同じ決め方(素の読みで 1 つに決まる辺は素の読み、決まらない辺だけ目盛りの読み)で辺を決めた場合。
    ref_eval = None
    fixed: dict[str, Any] = {}
    for o in ("横", "縦"):
        got = []
        for v in sides[o]:
            pr = unique(plain, v, o)
            if pr is not None:
                twin = [r for r in reads[REAL] if _same_pick(r, pr)]
                got.append(twin[0] if len(twin) == 1 else None)
            else:
                got.append(unique(reads[REAL], v, o))
        fixed[o] = got if got and all(g is not None for g in got) else None
    ref_eval = evaluate(reads[REAL], mpp, ref["id"], sides, labels, target, fixed=fixed)
    if fixed["横"] and fixed["縦"]:
        ref_eval["_box"] = box(fixed["横"], fixed["縦"])
    ref_moved = move_decoys(ref_eval, labels, target)
    ref_eval.pop("_box", None)

    # 辺の値に当たる読みが 2 つ以上ある向きは、候補ごとに辺を決めて K1〜K6 と 囮3 を見る(どちらを選ぶかは決めない)。
    per_candidate = []
    for o in ("横", "縦"):
        other = "縦" if o == "横" else "横"
        rest = side_readings(reads[REAL], sides[other], other)
        if len(sides[o]) != 1 or rest is None:
            continue
        cands = [r for r in reads[REAL] if abs(float(r["値"]) - sides[o][0]) <= VALUE_TOL_MM and r["向き"] == o]
        if len(cands) < 2:
            continue
        for c in cands:
            ev = evaluate(reads[REAL], mpp, ref["id"], sides, labels, target, fixed={o: [c], other: rest})
            ev["_box"] = box([c] if o == "横" else rest, rest if o == "横" else [c])
            mv = move_decoys(ev, labels, target)
            per_candidate.append({"向き": o, "目盛りで拾った読み": c["目盛りで拾った"],
                                  "紙の長さ×目盛りと値のずれ": round(float(c["値"]) / (float(c["紙"]) * mpp) - 1.0, 4),
                                  **{ch: ev[ch] for ch in CHECKS}, "合う": ev["合う"],
                                  "囮3 合う": f"{mv['合う']}/{mv['件数']}"})

    lines = {
        REAL: real["合う"],
        **{n: not cases[n]["合う"] for n in DECOY_SCALES},
        DECOY_MOVE: moved["合う"] == 0 and moved["合う(弱い囮を除く)"] == 0,
    }

    # --- つないだ場合の変化(出力の側。K-72 の分け方を、目盛りで決まった辺を「読めている」とみなして数え直す) ---
    reads72 = k72.v4_readings(a.pdf, page_no, float(base["値_mm"]), base["向き"])
    ch_pages = {int(r["天井高のページ"]) for r in k37["室"] if r.get("天井高のページ") is not None}
    ceilings = {n: k72.printed_ceilings(a.pdf, n) for n in ch_pages}
    rooms_before, _ = k72.room_table(k37, copy, reads72, ceilings)
    rooms_after = {k: (dict(v, 分け先=k72.A) if v["分け先"] == k72.B1 else v) for k, v in rooms_before.items()}
    connect: dict[str, Any] = {}
    for d in a.runs:
        draft = json.loads((d / "下書き.json").read_text(encoding="utf-8"))
        kinds = {int(n): v["種類"] for n, v in draft["整理"]["ページ"].items()}
        items = draft["理解"]["項目"]
        before = k72.count_rows(items, rooms_before, kinds)
        after = k72.count_rows(items, rooms_after, kinds)
        got = before["分け先"][k72.B1] - after["分け先"][k72.B1]
        gated = got if real["合う"] else 0
        connect[d.name] = {
            "B1 の行": before["分け先"][k72.B1],
            "数量が付く行(門なし)": got,
            "数量が付く行(門あり = 本物が合うときだけ)": gated,
            "うち 開口を引かない(壁・幅木)": before["印"].get(f"{k72.B1} / 開口を引かない", 0),
            "数量が無い行": {"前": before["数量が無い項目"], "後(門あり)": before["数量が無い項目"] - gated},
            "面積・長さの数量が無い行": {"前": before["面積・長さの行"], "後(門あり)": before["面積・長さの行"] - gated},
            "A": {"前": before["分け先"][k72.A], "後(門なし)": after["分け先"][k72.A]},
            "C(図面に室の寸法が無い)": {"前": before["分け先"][k72.C], "後": after["分け先"][k72.C]},
        }
    room = k37["室"][target]
    ch = room.get("天井高_mm")
    chp = room.get("天井高のページ")
    ch_ok = ch is not None and chp is not None and int(ch) in ceilings.get(int(chp), set())
    auto = auto_confirm_check(sum(sides["横"]), sum(sides["縦"]), float(ch) if ch_ok else None)
    if auto["自動確定"]:
        raise SystemExit("自動確定が出た。止めて報告する")

    def public(ev: Mapping[str, Any]) -> dict[str, Any]:
        d = ev["詳しく"]
        return {**{c: ev[c] for c in CHECKS}, "合う": ev["合う"],
                "横・縦が決まった": [d["横が決まった"], d["縦が決まった"]],
                "K3 合う読み/見た読み": f"{d['K3']['合う読み']}/{d['K3']['見た読み']}",
                "K4 鎖": d.get("K4"), "室名が見つかった": d.get("室名が見つかった"),
                "辺のうち目盛りで拾った読み": d.get("辺の読みは目盛りで拾った"),
                "読みの数": d.get("読みの数"), "目盛りで拾った読み": d.get("目盛りで拾った読み"),
                "本物と同じ区間を拾った": d.get("本物と同じ区間を拾った"),
                "ページ全体の鎖(目盛りで拾った読み)": d.get("ページ全体の鎖(目盛りで拾った読み)")}

    result = {
        "材料": "匿名化 v4 の PDF・K-72 作業 C の辺の写し(K-37 の対応づけ)・K-61 の全部あり版 3 回。AI 0 回・正解は開いていない",
        "直しの場所": "axes/image_axis/pdf_dimensions.read_dimensions(ruler_mm_per_point=, ruler_tolerance=) / "
                    "呼ぶのは app._drawing_room_result(--drawing-rooms のときだけ)。下書きの道は通らない(つないでいない)",
        "対象": {"室の番号": target, "ページ": page_no, "素の読み": len(plain), "基準が1つに決まった": True},
        "場合": {n: public(ev) for n, ev in cases.items()},
        DECOY_MOVE: {k: v for k, v in moved.items() if k != "ごと"},
        "線": lines,
        "全部の線を満たした": all(lines.values()),
        "参考(測った後に足した。合否に入れない)": {
            "本物の K1 の内訳(辺の値に当たる読みの数)": k1_diag,
            "K-72 と同じ決め方で辺を決めた場合": public(ref_eval) if ref_eval.get("詳しく") else None,
            "K-72 と同じ決め方で辺を決めた場合の 囮3": {k: v for k, v in ref_moved.items() if k != "ごと"},
            "値が同じ読みが 2 つ以上ある辺の、候補ごとの判定": per_candidate,
        },
        "つないだ場合の変化": connect,
        "自動確定の確かめ": auto,
        "天井高の印字がある": ch_ok,
        "書き出した数量の値": 0,
    }
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.detail is not None:
        a.detail.parent.mkdir(parents=True, exist_ok=True)
        det = dict(result)
        det[DECOY_MOVE] = moved
        a.detail.write_text(json.dumps(det, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
