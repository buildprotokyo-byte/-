"""位置の重なりで、3 回の読みの行どうしを対応づける(K-71 作業 2)。**AI は呼ばない。名前は見ない。**

基準は `docs/k71_position_matching_criteria.md`(測る前にコミットした)。

- 同じページの行どうしだけを比べる。重なり率は面積の重なり率(IoU)。**しきい 0.50 以上**で「重なる」。
- 1 対 1 は重なり率の大きい順に取る(貪欲)。同点は `要素` の重なりで分け、それでも同じなら**対応づけない**。
- 鎖は基準の回(1 回目)を中心にした星形。基準の回に対応の無かった 2 回目と 3 回目の行は、その 2 回の間で対応づける。

K-72 作業 A(基準は `docs/k72_card_element_criteria.md`。測る前にコミットした): ``elements``(回ごとの
(ページ, 要素の id) → 要素)を渡したときだけ、候補に **要素の重なり** を足す。同じページ・同じ室(室の組では見ない)・
同じ種類・近い大きさ(幅・高さそれぞれ 2 倍まで)・要素の箱が重なる、を全部満たす要素の組が 1 つ以上ある対だけを候補にする。
渡さなければ K-71 と同じ動き。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

#: 面積の重なり率のしきい(測る前に固定)。
THRESHOLD = 0.50
#: 同点とみなす差。
TIE_EPS = 1e-9

TIED = "同点で決まらない"
NO_PARTNER = "位置の重なる行がほかの回に無い"
#: K-72: 重なり率 0.50 以上の行はあるが、どれも要素が重ならなかった。
ELEMENT_MISMATCH = "位置は重なるが要素が重ならない"
#: K-72: 要素の「近い大きさ」(幅どうし・高さどうしの 小さい方 ÷ 大きい方 の下限。2 倍まで)。測る前に固定。
SIZE_RATIO = 0.5
#: 幅・高さがこれ未満の箱は、この大きさとして比べる(線の要素は高さ 0 がある)。
MIN_SIDE = 1.0


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


def _sides(box: Sequence[float]) -> list[float]:
    """直した箱。幅・高さが 1 画素未満なら、中心を変えずに 1 画素へ広げる。"""
    x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    if x1 - x0 < MIN_SIDE:
        c = (x0 + x1) / 2
        x0, x1 = c - MIN_SIDE / 2, c + MIN_SIDE / 2
    if y1 - y0 < MIN_SIDE:
        c = (y0 + y1) / 2
        y0, y1 = c - MIN_SIDE / 2, c + MIN_SIDE / 2
    return [x0, y0, x1, y1]


def same_element(a: Mapping[str, Any], b: Mapping[str, Any], *, size_ratio: float = SIZE_RATIO) -> bool:
    """回をまたいで「同じ要素」か(同じ種類・近い大きさ・箱が重なる)。ページは呼ぶ側で揃える。"""
    if not a.get("種類") or a.get("種類") != b.get("種類"):
        return False
    pa, pb = a.get("位置"), b.get("位置")
    if not pa or not pb or len(pa) != 4 or len(pb) != 4:
        return False
    try:
        ax0, ay0, ax1, ay1 = _sides([float(v) for v in pa])
        bx0, by0, bx1, by1 = _sides([float(v) for v in pb])
    except (TypeError, ValueError):
        return False
    wa, ha, wb, hb = ax1 - ax0, ay1 - ay0, bx1 - bx0, by1 - by0
    if min(wa, wb) / max(wa, wb) < size_ratio or min(ha, hb) / max(ha, hb) < size_ratio:
        return False
    return min(ax1, bx1) - max(ax0, bx0) > 0 and min(ay1, by1) - max(ay0, by0) > 0


def _elements_of(item: Mapping[str, Any], index: Mapping[tuple[int, str], Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    try:
        page = int(item.get("ページ"))
    except (TypeError, ValueError):
        return []
    out = []
    for eid in item.get("要素") or ():
        e = index.get((page, str(eid)))
        if e is not None:
            out.append(e)
    return out


def elements_match(a: Mapping[str, Any], b: Mapping[str, Any],
                   index_a: Mapping[tuple[int, str], Mapping[str, Any]],
                   index_b: Mapping[tuple[int, str], Mapping[str, Any]], *,
                   check_room: bool = True, size_ratio: float = SIZE_RATIO) -> bool:
    """2 行の要素が重なるか(K-72 基準 1-1)。要素が 1 つも引けない行は、どの行とも重ならない。"""
    if a.get("ページ") in (None, "") or a.get("ページ") != b.get("ページ"):
        return False
    if check_room:
        from sameness.rows import row_room

        if (row_room(a) or "") != (row_room(b) or ""):
            return False
    ea, eb = _elements_of(a, index_a), _elements_of(b, index_b)
    return any(same_element(x, y, size_ratio=size_ratio) for x in ea for y in eb)


def element_index(reading: Mapping[str, Any]) -> dict[tuple[int, str], Mapping[str, Any]]:
    """下書きの ``読む.読み``(ページ → {要素: [...]})から、(ページ, 要素の id) → 要素。"""
    out: dict[tuple[int, str], Mapping[str, Any]] = {}
    for page, v in (reading or {}).items():
        for e in (v or {}).get("要素") or ():
            if e.get("id") is not None:
                out[(int(page), str(e["id"]))] = e
    return out


def pair(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]], *,
         threshold: float = THRESHOLD, accept: Callable[[int, int], bool] | None = None) -> dict[str, Any]:
    """2 つの回の行を 1 対 1 に対応づける。

    ``accept``(左の位置, 右の位置)を渡すと、重なり率がしきい以上でも、それが偽の対は候補にしない(K-72 の要素の重なり)。

    返すもの: ``対``(左の位置, 右の位置, 重なり率)・``同点``(同点で対応づけなかった左右の位置の組)・
    ``要素で落とした``(重なり率はしきい以上だが ``accept`` で落とした対のある左右の位置)。
    """
    cands = []
    rejected_l: set[int] = set()
    rejected_r: set[int] = set()
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            v = overlap(a, b)
            if v >= threshold:
                if accept is not None and not accept(i, j):
                    rejected_l.add(i)
                    rejected_r.add(j)
                    continue
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
    return {"対": pairs, "同点": {"左": sorted(blocked_l - used_l), "右": sorted(blocked_r - used_r)},
            "要素で落とした": {"左": sorted(rejected_l), "右": sorted(rejected_r)}}


def chains(rows_by_run: Sequence[Sequence[Mapping[str, Any]]], *,
           threshold: float = THRESHOLD,
           elements: Sequence[Mapping[tuple[int, str], Mapping[str, Any]]] | None = None,
           check_room: bool = True, size_ratio: float = SIZE_RATIO) -> dict[str, Any]:
    """回ごとの行の並び(1 つ目が基準の回)から、鎖(回ごとに 1 行まで)を作る。

    返すもの:
    - ``鎖``: 回ごとの行の位置(無ければ None)の並び。2 回以上にまたがるものだけ。
    - ``行の行き先``: (回の位置, 行の位置) → ``鎖`` / ``同点で決まらない`` /
      ``位置は重なるが要素が重ならない``(``elements`` を渡したときだけ) / ``位置の重なる行がほかの回に無い``。

    ``elements`` は回ごとの (ページ, 要素の id) → 要素(`element_index`)。``check_room`` は室の組で偽にする。
    """
    n = len(rows_by_run)
    status: dict[tuple[int, int], str] = {}
    tied: set[tuple[int, int]] = set()
    rejected: set[tuple[int, int]] = set()

    def acceptor(r: int, s: int, li: Sequence[int], ri: Sequence[int]) -> Callable[[int, int], bool] | None:
        if elements is None:
            return None
        return lambda i, j: elements_match(rows_by_run[r][li[i]], rows_by_run[s][ri[j]], elements[r], elements[s],
                                           check_room=check_room, size_ratio=size_ratio)

    out: list[list[int | None]] = []
    if n == 0:
        return {"鎖": out, "行の行き先": status}
    base = rows_by_run[0]
    star: dict[int, list[int | None]] = {i: [i] + [None] * (n - 1) for i in range(len(base))}
    taken: list[set[int]] = [set() for _ in range(n)]
    for r in range(1, n):
        got = pair(base, rows_by_run[r], threshold=threshold,
                   accept=acceptor(0, r, range(len(base)), range(len(rows_by_run[r]))))
        for i, j, _ in got["対"]:
            star[i][r] = j
            taken[r].add(j)
        tied |= {(0, i) for i in got["同点"]["左"]} | {(r, j) for j in got["同点"]["右"]}
        rejected |= {(0, i) for i in got["要素で落とした"]["左"]} | {(r, j) for j in got["要素で落とした"]["右"]}
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
                       threshold=threshold, accept=acceptor(r, s, left_idx, right_idx))
            for a, b, _ in got["対"]:
                chain: list[int | None] = [None] * n
                chain[r] = left_idx[a]
                chain[s] = right_idx[b]
                taken[r].add(left_idx[a])
                taken[s].add(right_idx[b])
                out.append(chain)
            tied |= {(r, left_idx[a]) for a in got["同点"]["左"]} | {(s, right_idx[b]) for b in got["同点"]["右"]}
            rejected |= ({(r, left_idx[a]) for a in got["要素で落とした"]["左"]}
                         | {(s, right_idx[b]) for b in got["要素で落とした"]["右"]})
    for r in range(n):
        for j in range(len(rows_by_run[r])):
            if j in taken[r]:
                status[(r, j)] = "鎖"
            elif (r, j) in tied:
                status[(r, j)] = TIED
            elif (r, j) in rejected:
                status[(r, j)] = ELEMENT_MISMATCH
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
