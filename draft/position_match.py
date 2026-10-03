"""位置の重なりで、3 回の読みの行どうしを対応づける(K-71 作業 2)。**AI は呼ばない。名前は見ない。**

基準は `docs/k71_position_matching_criteria.md`(測る前にコミットした)。

- 同じページの行どうしだけを比べる。重なり率は面積の重なり率(IoU)。**しきい 0.50 以上**で「重なる」。
- 1 対 1 は重なり率の大きい順に取る(貪欲)。同点は `要素` の重なりで分け、それでも同じなら**対応づけない**。
- 鎖は基準の回(1 回目)を中心にした星形。基準の回に対応の無かった 2 回目と 3 回目の行は、その 2 回の間で対応づける。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: 面積の重なり率のしきい(測る前に固定)。
THRESHOLD = 0.50
#: 同点とみなす差。
TIE_EPS = 1e-9

TIED = "同点で決まらない"
NO_PARTNER = "位置の重なる行がほかの回に無い"


def box_of(item: Mapping[str, Any]) -> tuple[int, list[float]] | None:
    """(ページ, 直した箱)。箱かページが無ければ None。"""
    box = item.get("囲み")
    page = item.get("ページ")
    if not box or len(box) != 4 or page in (None, ""):
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
        return int(page), [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    except (TypeError, ValueError):
        return None


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """面積の重なり率(重なった面積 ÷ 合わせた面積)。ひっくり返った四角は直してから比べる。"""
    ax0, ax1, ay0, ay1 = min(a[0], a[2]), max(a[0], a[2]), min(a[1], a[3]), max(a[1], a[3])
    bx0, bx1, by0, by1 = min(b[0], b[2]), max(b[0], b[2]), min(b[1], b[3]), max(b[1], b[3])
    w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    h = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = w * h
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """2 行の重なり率。ページが違う・箱が無ければ 0。"""
    pa, pb = box_of(a), box_of(b)
    if pa is None or pb is None or pa[0] != pb[0]:
        return 0.0
    return iou(pa[1], pb[1])


def element_overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    ea, eb = set(a.get("要素") or ()), set(b.get("要素") or ())
    return len(ea & eb) / len(ea | eb) if ea | eb else 0.0


def pair(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]], *,
         threshold: float = THRESHOLD) -> dict[str, Any]:
    """2 つの回の行を 1 対 1 に対応づける。

    返すもの: ``対``(左の位置, 右の位置, 重なり率)・``同点``(同点で対応づけなかった左右の位置の組)。
    """
    cands = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            v = overlap(a, b)
            if v >= threshold:
                cands.append((v, element_overlap(a, b), i, j))
    cands.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    used_l: set[int] = set()
    used_r: set[int] = set()
    blocked_l: set[int] = set()
    blocked_r: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    k = 0
    while k < len(cands):
        v, e, i, j = cands[k]
        if i in used_l or j in used_r or i in blocked_l or j in blocked_r:
            k += 1
            continue
        # 同じ重なり率・同じ要素の重なりで、この対と行を共有する、まだ使える候補。
        rivals = [c for c in cands
                  if c is not cands[k] and abs(c[0] - v) <= TIE_EPS and abs(c[1] - e) <= TIE_EPS
                  and (c[2] == i or c[3] == j)
                  and c[2] not in used_l and c[3] not in used_r
                  and c[2] not in blocked_l and c[3] not in blocked_r]
        if rivals:
            for c in [cands[k], *rivals]:
                blocked_l.add(c[2])
                blocked_r.add(c[3])
            k += 1
            continue
        used_l.add(i)
        used_r.add(j)
        pairs.append((i, j, v))
        k += 1
    return {"対": pairs, "同点": {"左": sorted(blocked_l - used_l), "右": sorted(blocked_r - used_r)}}


def chains(rows_by_run: Sequence[Sequence[Mapping[str, Any]]], *,
           threshold: float = THRESHOLD) -> dict[str, Any]:
    """回ごとの行の並び(1 つ目が基準の回)から、鎖(回ごとに 1 行まで)を作る。

    返すもの:
    - ``鎖``: 回ごとの行の位置(無ければ None)の並び。2 回以上にまたがるものだけ。
    - ``行の行き先``: (回の位置, 行の位置) → ``鎖`` / ``同点で決まらない`` / ``位置の重なる行がほかの回に無い``。
    """
    n = len(rows_by_run)
    status: dict[tuple[int, int], str] = {}
    tied: set[tuple[int, int]] = set()
    out: list[list[int | None]] = []
    if n == 0:
        return {"鎖": out, "行の行き先": status}
    base = rows_by_run[0]
    star: dict[int, list[int | None]] = {i: [i] + [None] * (n - 1) for i in range(len(base))}
    taken: list[set[int]] = [set() for _ in range(n)]
    for r in range(1, n):
        got = pair(base, rows_by_run[r], threshold=threshold)
        for i, j, _ in got["対"]:
            star[i][r] = j
            taken[r].add(j)
        tied |= {(0, i) for i in got["同点"]["左"]} | {(r, j) for j in got["同点"]["右"]}
    for chain in star.values():
        if sum(1 for x in chain if x is not None) >= 2:
            out.append(chain)
            taken[0].add(chain[0])
    # 基準の回に対応の無かった、ほかの回どうし(3 回なら 2 回目と 3 回目)。
    for r in range(1, n):
        for s in range(r + 1, n):
            left_idx = [j for j in range(len(rows_by_run[r])) if j not in taken[r]]
            right_idx = [j for j in range(len(rows_by_run[s])) if j not in taken[s]]
            got = pair([rows_by_run[r][j] for j in left_idx], [rows_by_run[s][j] for j in right_idx],
                       threshold=threshold)
            for a, b, _ in got["対"]:
                chain: list[int | None] = [None] * n
                chain[r] = left_idx[a]
                chain[s] = right_idx[b]
                taken[r].add(left_idx[a])
                taken[s].add(right_idx[b])
                out.append(chain)
            tied |= {(r, left_idx[a]) for a in got["同点"]["左"]} | {(s, right_idx[b]) for b in got["同点"]["右"]}
    for r in range(n):
        for j in range(len(rows_by_run[r])):
            if j in taken[r]:
                status[(r, j)] = "鎖"
            elif (r, j) in tied:
                status[(r, j)] = TIED
            else:
                status[(r, j)] = NO_PARTNER
    return {"鎖": out, "行の行き先": status}


def check_chains(rows_by_run: Sequence[Sequence[Any]], chain_list: Sequence[Sequence[int | None]]) -> dict[str, int]:
    """鎖の線: 1 つの回の行を 2 行以上持つ鎖・2 つ以上の鎖に入った行の数。"""
    seen: dict[tuple[int, int], int] = {}
    over = 0
    for chain in chain_list:
        if len(chain) != len(rows_by_run):
            over += 1
        for r, j in enumerate(chain):
            if j is not None:
                seen[(r, j)] = seen.get((r, j), 0) + 1
    return {"回の行を 2 行以上持つ鎖": over, "2 つ以上の鎖に入った行": sum(1 for v in seen.values() if v > 1)}
