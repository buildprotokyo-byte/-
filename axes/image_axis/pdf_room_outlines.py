"""画像軸: CAD 由来の PDF から、**室の輪郭**を閉じた領域として取り出す。

**なぜこれが要るのか**

面積を要する見積の行(置床解体・内壁解体・天井解体・間仕切りの軸組・
石膏ボード・天井野縁組)は、これまで 1 行も数量の判定に乗っていなかった。
理由は縮尺でも読み方でもなく、**室の輪郭を取る実装が無い**ことである
(`docs/real_drawing_eval_report.md`、`docs/golden_eval_result.json`)。
縮尺が決まっても数量は決まらない。要るのは輪郭である。

**どうやって取るのか**

図面の線を平面グラフとして組み直し、**線で囲まれた最小の領域(面)**を
すべて数え上げる。テンプレートも学習も使わない。手順は 5 つ。

1. 直線・矩形・四辺形の線分を集める(**円弧は壁ではない**ので使わない)
2. 端点を許容差で寄せ、T 字に突き当たる所と交差する所で線分を切る
3. **建具の開口**(線の切れ目)を、決めた幅までなら仮の線でつなぐ
4. 半辺をたどって面を数え上げる
5. 面積の窓と**最小の幅**で絞る

**壁の中身を室として数えないための決め手は「最小の幅」である**

壁を 2 本線で描いた図面では、線の交差から「室の内法」のほかに
「壁の中身」が細長い面として生まれる。面積だけで絞ると、長い壁の中身は
小さな室と同じ面積になって残ってしまう。代わりに **2×面積÷周長**
(平均の半幅に当たる)を見る。壁の中身は 150mm 程度、室や廊下は 600mm 以上で、
ここははっきり分かれる。

**この実装が原理的に落とすもの(0 件を「室が無い」と読まないこと)**

- **スキャンされたページ**。線が図形データとして入っていないので常に 0 件。
- **開口が広すぎて閉じられない室**。3m の開口を閉じてしまうと
  「そこに壁がある」と嘘をつくことになるので、**閉じずに漏れさせる。**
  漏れた面は面積の窓や幅の条件で落ちるので、**黙って大きな面積を返さない。**
- **線が交差していない所でつながっている図面**(線幅で見かけ上つながっている等)。

**罫線の表の升目は室ではない**

建具表・仕上表の升目も「線で囲まれた閉じた領域」なので、そのままでは室として
出てしまう(全件テストで実際に、建具表の升目が「洋室1」という室として
出た)。だから罫線の表として読めた範囲の中に入る領域は室としない。

**面積が内法か壁芯かは、図形からは決まらない**

線が壁の仕上げ面なのか芯なのかは、線そのものからは分からない。
だから `area_basis` は既定で ``"不明"`` を返す。**どちらかに決めない。**
どちらとして数えるかは積算の決まりであって、図面の読み取りではない。

**この手法は独立した 2 つ目の軸ではない**

輪郭も室名も同じ 1 つの PDF から来る。`arbitration/method_policies.py` に
``calibrated=False`` / 上限 ``weak`` で登録する。
"""


from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
METHOD_ROOM_OUTLINE = "pdf_vector_room_outline"

#: 室とみなす面積の窓(平方メートル)。**暫定値で、実図面で校正していない。**
#: 下限は押入・物入を残す大きさ、上限は図面の外枠や敷地の枠を落とす大きさ。
ROOM_MIN_SQM = 0.5
ROOM_MAX_SQM = 200.0

#: 室とみなす**最小の幅**(ミリメートル)。``2 × 面積 ÷ 周長`` で測る。
#: 壁の中身(150mm 前後)と室・廊下(600mm 以上)を分けるための条件。
ROOM_MIN_WIDTH_MM = 400.0

#: 端点を同じ点として寄せる許容差(ミリメートル・実寸)。
SNAP_MM = 20.0

#: 建具の開口として仮の線でつないでよい最大の幅(ミリメートル)。
#: 住宅の建具は両開きでも 1800mm 程度まで。既定はその手前に置いてある。
#: **これを超える切れ目は閉じない。** 閉じると「壁がある」と嘘をつくため。
MAX_GAP_MM = 1200.0

#: 罫線の表とみなす「升目の埋まり」の下限。
#: **暫定値で、実図面で校正していない。**
TABLE_MIN_FILL = 0.6

AreaBasis = Literal["内法", "壁芯", "不明"]


@dataclass(frozen=True)
class RoomOutline:
    """線で囲まれた閉じた領域 1 つ。**室の候補**であって、室と決まってはいない。"""

    polygon_pt: tuple[tuple[float, float], ...]
    """輪郭の頂点(ページ座標・ポイント)。根拠としてそのまま残す。"""

    area_sqm: float
    """輪郭の面積(平方メートル)。縮尺を掛けてある。**丸めていない値。**"""

    area_range_sqm: tuple[float, float]
    """面積を 1cm²(0.0001㎡)の刻みで挟んだ範囲。

    仲裁層は「刻みより細かい値」を受け取らない(黙って丸めると、丸めてよいか
    どうかの判断がここで勝手に決まってしまうため)。だから**丸めずに挟む**。
    図形から測った面積がこの範囲に入ることだけを主張する。

    **この範囲は測定の不確かさではない。** 端点を 20mm の許容差で寄せている
    ことと縮尺の誤差のぶんは、ここには入っていない。
    """

    perimeter_mm: float
    """輪郭の周長(ミリメートル・実寸)。"""

    min_width_mm: float
    """``2 × 面積 ÷ 周長``。細長さの目安。"""

    name: str | None
    """輪郭の中にあった室名。**図面に書かれている文字からしか取らない。**"""

    name_basis: str
    """名前が付いた/付かなかった理由を、人が読める言葉でそのまま残す。"""

    area_basis: AreaBasis
    """面積が内法か壁芯か。**図形からは決まらないので既定は「不明」。**"""

    virtual_edges: int
    """輪郭のうち、**こちらが仮に閉じた**辺の本数。0 なら図面の線だけ。"""

    page_index: int
    method_id: str = METHOD_ROOM_OUTLINE
    area_basis_note: str = ""
    """``area_basis`` がそう決まった理由。**決められなかった理由もここに残す。**"""
    limitation: str = (
        "開口が広すぎる室は閉じずに漏れる(閉じると「壁がある」と嘘になるため)。"
        "スキャンされたページでは常に 0 件で、0 件は「室が無い」ではない"
    )


# ---------------------------------------------------------------------------
# 線分を集める
# ---------------------------------------------------------------------------


def _segments(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """直線・矩形・四辺形から線分を集める。**円弧(建具の振り)は壁ではない。**"""
    out: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                out.append((p1.x, p1.y, p2.x, p2.y))
            elif kind == "re":
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for index in range(4):
                    a, b = corners[index], corners[(index + 1) % 4]
                    out.append((a[0], a[1], b[0], b[1]))
            elif kind == "qu":
                q = item[1]
                corners = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                for index in range(4):
                    a, b = corners[index], corners[(index + 1) % 4]
                    out.append((a[0], a[1], b[0], b[1]))
    return out


# ---------------------------------------------------------------------------
# 平面グラフに直す
# ---------------------------------------------------------------------------


class _Points:
    """許容差で同じ点に寄せながら、点に番号を振る。"""

    def __init__(self, tolerance: float) -> None:
        self._tolerance = tolerance
        self._cell = max(tolerance, 1e-9) * 2.0
        self._buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.coords: list[tuple[float, float]] = []

    def add(self, x: float, y: float) -> int:
        cx, cy = int(math.floor(x / self._cell)), int(math.floor(y / self._cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in self._buckets.get((cx + dx, cy + dy), ()):
                    px, py = self.coords[index]
                    if math.hypot(px - x, py - y) <= self._tolerance:
                        return index
        index = len(self.coords)
        self.coords.append((x, y))
        self._buckets[(cx, cy)].append(index)
        return index


def _split_segments(
    segments: list[tuple[float, float, float, float]], tolerance: float
) -> tuple[list[tuple[int, int, bool]], "_Points"]:
    """線分を、**その上に乗っている点すべて**で切り、点の番号の組にして返す。

    切る点は 2 通りある。どちらも入れないと面が閉じない。

    1. **交点** … 2 本の線分が交差する所(平行でない組)
    2. **線分の上に乗っている端点** … T 字に突き当たる所と、
       **同じ直線上で重なっている線分の端**

    2 を落としていたせいで、平面図の測定で 7 室中 0 室しか出なかった。
    壁は隣り合う室で共有されるので、同じ直線上に重なる線分が必ず現れる
    (居室の下端と廊下の上端など)。交点だけを見ていると、そこで線が
    切れないまま面をたどることになり、室が 1 つの大きな領域に溶ける。

    返すのは ``(点1, 点2, 仮の線か)``。ここで作る辺はすべて図面の線なので
    ``仮の線か`` は常に False。
    """
    points = _Points(tolerance)
    raw: list[tuple[int, int]] = []
    for x1, y1, x2, y2 in segments:
        if math.hypot(x2 - x1, y2 - y1) <= tolerance:
            continue
        raw.append((points.add(x1, y1), points.add(x2, y2)))

    # 1. 交点を足す。格子で候補を絞ってから総当たりする。
    cell = max(tolerance * 20.0, 1.0)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, (a, b) in enumerate(raw):
        ax, ay = points.coords[a]
        bx, by = points.coords[b]
        for gx in range(int(min(ax, bx) // cell), int(max(ax, bx) // cell) + 1):
            for gy in range(int(min(ay, by) // cell), int(max(ay, by) // cell) + 1):
                grid[(gx, gy)].append(index)

    seen_pairs: set[tuple[int, int]] = set()
    for bucket in grid.values():
        for i, first in enumerate(bucket):
            for second in bucket[i + 1 :]:
                pair = (first, second) if first < second else (second, first)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                hit = _intersection(points, raw[first], raw[second], tolerance)
                if hit is not None:
                    points.add(*hit)

    # 2. すべての点を、その点が上に乗っている線分に割り込ませる。
    #    点の格子を作り、線分に沿って歩きながら近くの点だけを見る。
    vertex_cell = max(tolerance * 2.0, 1e-6)
    vertex_grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, (x, y) in enumerate(points.coords):
        vertex_grid[(int(x // vertex_cell), int(y // vertex_cell))].append(index)

    edges: list[tuple[int, int, bool]] = []
    for a, b in raw:
        ax, ay = points.coords[a]
        bx, by = points.coords[b]
        length = math.hypot(bx - ax, by - ay)
        if length <= 0:
            continue
        candidates: set[int] = set()
        steps = max(int(length / vertex_cell) + 1, 1)
        for step in range(steps + 1):
            t = step / steps
            px, py = ax + t * (bx - ax), ay + t * (by - ay)
            gx, gy = int(px // vertex_cell), int(py // vertex_cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    candidates.update(vertex_grid.get((gx + dx, gy + dy), ()))
        on_line: list[tuple[float, int]] = [(0.0, a), (length, b)]
        for candidate in candidates:
            if candidate in (a, b):
                continue
            cx, cy = points.coords[candidate]
            t = ((cx - ax) * (bx - ax) + (cy - ay) * (by - ay)) / (length * length)
            if not 0.0 < t < 1.0:
                continue
            # 線分からの距離
            distance = abs((bx - ax) * (ay - cy) - (ax - cx) * (by - ay)) / length
            if distance <= tolerance:
                on_line.append((t * length, candidate))
        on_line.sort()
        for step in range(len(on_line) - 1):
            u, v = on_line[step][1], on_line[step + 1][1]
            if u != v:
                edges.append((u, v, False))
    return _dedupe(edges), points


def _intersection(points, first, second, tolerance) -> tuple[float, float] | None:
    """2 本の線分の交点。端点を共有していれば None(切る必要が無い)。

    平行な組は None を返す。**同じ直線上で重なっている線分**はここでは
    扱わず、`_split_segments()` の 2 段目(点を線分に割り込ませる)が拾う。
    """
    a, b = first
    c, d = second
    if {a, b} & {c, d}:
        return None
    ax, ay = points.coords[a]
    bx, by = points.coords[b]
    cx, cy = points.coords[c]
    dx, dy = points.coords[d]
    r = (bx - ax, by - ay)
    s_vec = (dx - cx, dy - cy)
    denominator = r[0] * s_vec[1] - r[1] * s_vec[0]
    if abs(denominator) < 1e-12:
        return None
    t = ((cx - ax) * s_vec[1] - (cy - ay) * s_vec[0]) / denominator
    u = ((cx - ax) * r[1] - (cy - ay) * r[0]) / denominator
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    return (ax + t * r[0], ay + t * r[1])


def _dedupe(edges: list[tuple[int, int, bool]]) -> list[tuple[int, int, bool]]:
    seen: dict[tuple[int, int], bool] = {}
    for u, v, virtual in edges:
        key = (u, v) if u < v else (v, u)
        # 図面の線が 1 本でもあれば、仮の線として記録しない。
        seen[key] = seen.get(key, True) and virtual
    return [(u, v, virtual) for (u, v), virtual in seen.items()]


def _close_gaps(
    edges: list[tuple[int, int, bool]],
    coords: list[tuple[float, float]],
    max_gap_pt: float,
    tolerance_pt: float,
) -> list[tuple[int, int, bool]]:
    """行き止まりの端点どうしを、決めた幅までなら仮の線でつなぐ。

    **開口は壁の続きにしかできない。** だから、つないでよいのは
    次の 3 つを満たす組だけにする。

    1. どちらも行き止まり(そこで線が終わっている)
    2. 距離が ``max_gap_pt`` 以内
    3. **相手の端点が、こちらの壁をまっすぐ延ばした線の上に乗っている**
       (両側から見て許容差以内)

    3 を入れていなかったとき、**平行な線を 30 本並べただけの図面から
    15 室が出た**(測定の負の対照で判明)。行き止まりどうしが近ければ
    向きを問わずつないでいたので、壁でも何でもない線から室が作られていた。

    つないだ辺は「仮の線」として記録する。``max_gap_pt`` を超える切れ目は
    つながない(つなぐと「そこに壁がある」と嘘をつくことになる)。
    """
    degree: dict[int, int] = defaultdict(int)
    neighbor_of: dict[int, list[int]] = defaultdict(list)
    for u, v, _ in edges:
        degree[u] += 1
        degree[v] += 1
        neighbor_of[u].append(v)
        neighbor_of[v].append(u)
    dangling = sorted(index for index, count in degree.items() if count == 1)
    if len(dangling) < 2:
        return edges

    def on_extension(origin: int, target: int) -> bool:
        """``target`` が ``origin`` の壁をまっすぐ延ばした線の上にあるか。"""
        back = neighbor_of[origin][0]
        ox, oy = coords[origin]
        bx, by = coords[back]
        tx, ty = coords[target]
        length = math.hypot(ox - bx, oy - by)
        if length <= 0:
            return False
        # 壁の向きの単位ベクトル(back → origin)
        ux, uy = (ox - bx) / length, (oy - by) / length
        dx, dy = tx - ox, ty - oy
        along = dx * ux + dy * uy
        if along <= 0:
            return False  # 壁の反対側。開口ではない
        across = abs(dx * -uy + dy * ux)
        return across <= tolerance_pt

    pairs: list[tuple[float, int, int]] = []
    for i, a in enumerate(dangling):
        for b in dangling[i + 1 :]:
            ax, ay = coords[a]
            bx, by = coords[b]
            distance = math.hypot(bx - ax, by - ay)
            if not 0.0 < distance <= max_gap_pt:
                continue
            if not (on_extension(a, b) and on_extension(b, a)):
                continue
            pairs.append((distance, a, b))
    pairs.sort()
    used: set[int] = set()
    out = list(edges)
    for _, a, b in pairs:
        if a in used or b in used:
            continue
        used.add(a)
        used.add(b)
        out.append((a, b, True))
    return out


# ---------------------------------------------------------------------------
# 面を数え上げる
# ---------------------------------------------------------------------------


def _faces(
    edges: list[tuple[int, int, bool]], coords: list[tuple[float, float]]
) -> list[tuple[list[int], int]]:
    """半辺をたどって、線で囲まれた最小の領域(面)を数え上げる。

    **座標は y を上向きに直してから角度を測る。** PDF の座標は y が下向きなので、
    そのまま測ると内側の面と外側の面の向きが入れ替わり、どちらが内側か
    決められなくなる。上向きに直せば、内側の面が反時計回り(面積が正)に揃う。

    返すのは ``(頂点の並び, 仮の線の本数)``。外側の面は除いてある。
    """
    neighbors: dict[int, list[int]] = defaultdict(list)
    virtual_of: dict[tuple[int, int], bool] = {}
    for u, v, virtual in edges:
        neighbors[u].append(v)
        neighbors[v].append(u)
        virtual_of[(u, v)] = virtual
        virtual_of[(v, u)] = virtual

    def angle(origin: int, target: int) -> float:
        ox, oy = coords[origin]
        tx, ty = coords[target]
        return math.atan2(-(ty - oy), tx - ox)  # y を上向きに

    order: dict[int, list[int]] = {}
    position: dict[tuple[int, int], int] = {}
    for vertex, adjacent in neighbors.items():
        ordered = sorted(set(adjacent), key=lambda other: angle(vertex, other))
        order[vertex] = ordered
        for index, other in enumerate(ordered):
            position[(vertex, other)] = index

    visited: set[tuple[int, int]] = set()
    faces: list[tuple[list[int], int]] = []
    for u, v, _ in edges:
        for start in ((u, v), (v, u)):
            if start in visited:
                continue
            cycle: list[int] = []
            virtual_count = 0
            current = start
            while current not in visited:
                visited.add(current)
                cycle.append(current[0])
                if virtual_of.get(current, False):
                    virtual_count += 1
                a, b = current
                ring = order[b]
                index = position[(b, a)]
                # a から見て **1 つ時計回り側**の辺へ進む。
                current = (b, ring[(index - 1) % len(ring)])
            if len(cycle) >= 3:
                faces.append((cycle, virtual_count))
    return faces


def _signed_area_pt(cycle: list[int], coords: list[tuple[float, float]]) -> float:
    """y を上向きに直したうえでの符号つき面積(ポイント²)。内側の面は正。"""
    total = 0.0
    for index in range(len(cycle)):
        x1, y1 = coords[cycle[index]]
        x2, y2 = coords[cycle[(index + 1) % len(cycle)]]
        total += x1 * (-y2) - x2 * (-y1)
    return total / 2.0


def _perimeter_pt(cycle: list[int], coords: list[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(len(cycle)):
        x1, y1 = coords[cycle[index]]
        x2, y2 = coords[cycle[(index + 1) % len(cycle)]]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _looks_like_a_ruled_table(region) -> bool:
    """その「表」が本当に罫線の表か、平面図を表と取り違えたものかを分ける。

    見るのは**升目の埋まり**(罫線で区切られた升目のうち何割に文字が入っているか)
    ひとつだけ。建具表・仕上表は升目を埋めるために引かれているので、
    ほとんどの升目に文字が入る。平面図の壁が作る格子には、室名がいくつか
    入っているだけである。

    この条件は測定で 2 度決め直した。**どちらも実際に壊れてから直している。**

    1. 条件を入れていなかったとき … 建具表の升目が「洋室1」という室として
       仲裁層まで届いた。
    2. 升目の大きさの揃い方で見たとき … **壁を 2 本線で描いた平面図が
       「12 行 12 列の表」と判定され、7 室中 0 室になった。**
       壁の厚みが一定なので、升目の高さが揃ってしまう。

    **これは目安であって、校正された判定ではない。** 空欄の多い表は表と
    みなされず、その升目が室の候補として出る。出た候補は
    ``calibrated=False`` の弱い証拠なので、それだけで数量が決まることはない。
    """
    cells = [cell for row in region.rows for cell in row]
    if len(cells) < 4:
        return False
    filled = sum(1 for cell in cells if cell.text.strip())
    return filled / len(cells) >= TABLE_MIN_FILL


def _within(polygon: list[tuple[float, float]], rect: tuple[float, float, float, float]) -> bool:
    """輪郭がまるごと矩形の中に入っているか(罫線の表の升目を落とすため)。"""
    x0, y0, x1, y1 = rect
    return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in polygon)


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if cross > x:
                inside = not inside
    return inside


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 壁芯(芯々)の面積
#
# 2026-09-23 におーちゃんが「面積は芯々で数える」と決めた
# (`docs/decision_area_basis.md`)。**図面からは導けない会社のルールである。**
#
# 壁が 2 本線で描いてある図面では、2 本は壁の両側の面なので、囲まれた面は内法で、
# 壁の中身が別の細長い面として残っている。**その厚みの半分だけ外へ押し出せば
# 壁芯になる。仮定は 1 つも要らない。**
#
# 壁が 1 本線で描いてある図面には厚みがどこにも無い。
# **そこで厚みを仮定して数字を出すことはしない。** 「不明」のまま返す。
# ---------------------------------------------------------------------------


def _edge_owner(faces: list[list[int]]) -> dict[tuple[int, int], int]:
    """向きつきの辺 → その辺を持つ面。半辺は 1 つの面にしか属さない。"""
    owner: dict[tuple[int, int], int] = {}
    for index, cycle in enumerate(faces):
        for position in range(len(cycle)):
            owner[(cycle[position], cycle[(position + 1) % len(cycle)])] = index
    return owner


def _distance_to_line(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """点から直線 ab までの距離。ab が退化しているときは点 a までの距離。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-12:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    return abs(dx * (a[1] - point[1]) - dy * (a[0] - point[0])) / length


def _wall_thickness_pt(
    cavity_cycle: list[int],
    coords: list[tuple[float, float]],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """辺 ab の向こうにある壁の中身の厚み。

    **中身の頂点から辺の直線までのいちばん遠い距離**で測る。
    面積÷長さ で測ると、隅が斜めに落としてある壁で厚みが太く出る。
    """
    return max(_distance_to_line(coords[index], a, b) for index in cavity_cycle)


def _offset_polygon(
    polygon: list[tuple[float, float]], distances: list[float]
) -> list[tuple[float, float]] | None:
    """各辺をそれぞれの距離だけ外へ押し出した多角形。

    ``polygon`` は **y を上向きに直した座標で反時計回り**であること。
    押し出した直線どうしを交わらせて頂点を作る。平行で交わらない場合は
    押し出した点をそのまま使う。**丸めない。**
    """
    count = len(polygon)
    if count < 3 or len(distances) != count:
        return None
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index in range(count):
        (x1, y1), (x2, y2) = polygon[index], polygon[(index + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return None
        # 反時計回りの多角形では、進む向きの右側が外側。
        nx, ny = dy / length, -dx / length
        shift = distances[index]
        lines.append(((x1 + nx * shift, y1 + ny * shift), (x2 + nx * shift, y2 + ny * shift)))

    out: list[tuple[float, float]] = []
    for index in range(count):
        previous = lines[(index - 1) % count]
        current = lines[index]
        point = _line_intersection(previous, current)
        out.append(point if point is not None else current[0])
    return out


def _line_intersection(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float] | None:
    (x1, y1), (x2, y2) = first
    (x3, y3), (x4, y4) = second
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    denominator = d1x * d2y - d1y * d2x
    if abs(denominator) <= 1e-12:
        return None
    t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / denominator
    return (x1 + t * d1x, y1 + t * d1y)


def _apply_area_basis(
    wanted: AreaBasis,
    cycle: list[int],
    face_index: int,
    polygon: list[tuple[float, float]],
    area_sqm: float,
    perimeter_mm: float,
    coords: list[tuple[float, float]],
    owner: dict[tuple[int, int], int],
    cavities: set[int],
    cycles: list[list[int]],
    mm_per_pt: float,
) -> tuple[AreaBasis, str, list[tuple[float, float]], float, float]:
    """求められた数え方で面積を出し直す。**出せないときは「不明」のまま返す。**

    返すのは ``(数え方, 理由, 輪郭, 面積㎡, 周長mm)``。
    ``wanted`` が「不明」のときは**何も計算しない**(既定の振る舞いを変えない)。
    """
    if wanted == "不明":
        return "不明", "", polygon, area_sqm, perimeter_mm

    # 辺ごとに、向こう側にある壁の中身の厚みを測る。
    count = len(cycle)
    thickness_pt: list[float | None] = []
    for position in range(count):
        a_index = cycle[position]
        b_index = cycle[(position + 1) % count]
        neighbour = owner.get((b_index, a_index))
        if neighbour is None or neighbour not in cavities:
            thickness_pt.append(None)
            continue
        thickness_pt.append(
            _wall_thickness_pt(cycles[neighbour], coords, coords[a_index], coords[b_index])
        )

    # 建具の開口の辺にも壁の中身は接している。開口は壁の**両方の面**に開くので、
    # こちらが仮に閉じた辺の向こう側は、やはり壁の中身の面になる。
    # **だから「同じ直線に乗っている別の辺から厚みを借りる」処理は要らない。**
    # 一度書いたが、試験でも合成の間取りでも一度も通らなかったので消した
    # (`docs/a2_center_line_area_report.md`)。借りる処理は、借り先を間違えると
    # 黙って違う面積を出すので、通らないまま残しておくほうが危ない。

    unknown = [position for position, value in enumerate(thickness_pt) if value is None]
    if unknown:
        return (
            "不明",
            f"{len(unknown)}/{count} の辺で壁の厚みが図面から読めないため、"
            "内法か壁芯かを決められない(壁が 1 本線で描かれているか、"
            "隣の室との境に壁の中身が無い)",
            polygon,
            area_sqm,
            perimeter_mm,
        )

    if wanted == "内法":
        return (
            "内法",
            "すべての辺の向こうに壁の中身があるので、この面は壁の内側である",
            polygon,
            area_sqm,
            perimeter_mm,
        )

    # 壁芯: 各辺を、その辺の壁の厚みの半分だけ外へ押し出す。
    flipped = [(x, -y) for x, y in polygon]
    offsets = [value / 2.0 for value in thickness_pt if value is not None]
    moved = _offset_polygon(flipped, offsets)
    if moved is None:
        return (
            "不明",
            "輪郭を外へ押し出せなかった(辺の長さが 0 の頂点がある)",
            polygon,
            area_sqm,
            perimeter_mm,
        )
    new_polygon = [(x, -y) for x, y in moved]
    new_cycle = list(range(len(new_polygon)))
    signed = _signed_area_pt(new_cycle, new_polygon)
    if signed <= 0:
        return (
            "不明",
            "押し出した輪郭が裏返った(壁が厚すぎるか、輪郭がねじれている)",
            polygon,
            area_sqm,
            perimeter_mm,
        )
    thicknesses = sorted({round(value * mm_per_pt) for value in thickness_pt if value is not None})
    return (
        "壁芯",
        "辺ごとに壁の中身の厚みを測り、その半分だけ外へ押し出した"
        f"(厚み {'・'.join(str(value) for value in thicknesses)} mm)",
        new_polygon,
        signed * mm_per_pt * mm_per_pt / 1_000_000.0,
        _perimeter_pt(new_cycle, new_polygon) * mm_per_pt,
    )


def find_room_outlines(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    min_sqm: float = ROOM_MIN_SQM,
    max_sqm: float = ROOM_MAX_SQM,
    min_width_mm: float = ROOM_MIN_WIDTH_MM,
    snap_mm: float = SNAP_MM,
    max_gap_mm: float = MAX_GAP_MM,
    exclude_tables: bool = True,
    area_basis: AreaBasis = "不明",
) -> list[RoomOutline]:
    """線で囲まれた閉じた領域を、室の候補として返す。

    **面積が内法か壁芯かは決めない**(``area_basis`` は既定で ``"不明"``)。
    **室名は図面に書かれている文字からしか取らない**(無ければ名前なし、
    2 つ入っていたら食い違いとして名前を付けない)。

    スキャンされたページには線が図形として入っていないので常に空を返す。
    **空は「室が無い」ではなく「この手法では読めていない」である。**
    """
    if min_sqm <= 0 or max_sqm <= min_sqm:
        raise ValueError("面積の窓が不正です")
    if min_width_mm <= 0 or snap_mm <= 0 or max_gap_mm <= 0:
        raise ValueError("幅・許容差・開口の値は正でなければなりません")
    if area_basis not in ("内法", "壁芯", "不明"):
        raise ValueError(f"知らない面積の数え方です: {area_basis}")

    mm_per_pt = scale.mm_per_point
    tolerance_pt = snap_mm / mm_per_pt
    max_gap_pt = max_gap_mm / mm_per_pt

    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        page = doc.load_page(page_index)
        segments = _segments(page)
        spans: list[tuple[str, tuple[float, float]]] = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if text:
                        bbox = span["bbox"]
                        spans.append((text, ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)))

    if not segments:
        return []

    # 罫線の表の升目は室ではない。表として読めた範囲を先に除いておく。
    table_rects: list[tuple[float, float, float, float]] = []
    if exclude_tables:
        table_rects = [
            region.rect_pt
            for region in find_tables(pdf_path, page_index)
            if _looks_like_a_ruled_table(region)
        ]

    edges, points = _split_segments(segments, tolerance_pt)
    edges = _close_gaps(edges, points.coords, max_gap_pt, tolerance_pt)

    all_faces = _faces(edges, points.coords)
    cycles = [cycle for cycle, _ in all_faces]
    owner = _edge_owner(cycles)

    #: 細長すぎて室ではない面 = 壁の中身。**面積の窓では絞らない**
    #: (壁の中身は室の下限 0.5 ㎡ より小さいことがある)。
    cavities: set[int] = set()
    for index, cycle in enumerate(cycles):
        signed = _signed_area_pt(cycle, points.coords)
        if signed <= 0:
            continue
        perimeter_pt = _perimeter_pt(cycle, points.coords)
        if perimeter_pt <= 0:
            continue
        width = 2.0 * signed / perimeter_pt * mm_per_pt
        if width < min_width_mm:
            cavities.add(index)

    out: list[RoomOutline] = []
    for face_index, (cycle, virtual_count) in enumerate(all_faces):
        signed = _signed_area_pt(cycle, points.coords)
        if signed <= 0:
            continue  # 外側の面
        area_sqm = signed * mm_per_pt * mm_per_pt / 1_000_000.0
        if not min_sqm <= area_sqm <= max_sqm:
            continue
        perimeter_mm = _perimeter_pt(cycle, points.coords) * mm_per_pt
        if perimeter_mm <= 0:
            continue
        width_mm = 2.0 * (area_sqm * 1_000_000.0) / perimeter_mm
        if width_mm < min_width_mm:
            continue  # 細長すぎる。壁の中身とみなす

        polygon = [points.coords[index] for index in cycle]
        area_kind, area_kind_note, polygon, area_sqm, perimeter_mm = _apply_area_basis(
            area_basis,
            cycle,
            face_index,
            polygon,
            area_sqm,
            perimeter_mm,
            points.coords,
            owner,
            cavities,
            cycles,
            mm_per_pt,
        )
        if any(_within(polygon, rect) for rect in table_rects):
            continue  # 罫線の表の升目
        hits = [text for text, center in spans if _inside(center, polygon)]
        if len(hits) == 1:
            name, basis = hits[0], f"輪郭の中にあった文字「{hits[0]}」"
        elif not hits:
            name, basis = None, "輪郭の中に文字が無いため名前を付けない"
        else:
            name = None
            basis = "輪郭の中に文字が複数あるため名前を決めない(" + "・".join(hits) + ")"

        step = 0.0001  # 1cm²
        lower = math.floor(area_sqm / step) * step
        upper = math.ceil(area_sqm / step) * step
        out.append(
            RoomOutline(
                polygon_pt=tuple(polygon),
                area_sqm=area_sqm,
                area_range_sqm=(round(lower, 4), round(upper, 4)),
                perimeter_mm=perimeter_mm,
                min_width_mm=width_mm,
                name=name,
                name_basis=basis,
                area_basis=area_kind,
                virtual_edges=virtual_count,
                page_index=page_index,
                area_basis_note=area_kind_note,
            )
        )
    out.sort(key=lambda r: (-r.area_sqm, round(r.polygon_pt[0][1], 1), round(r.polygon_pt[0][0], 1)))
    return out
