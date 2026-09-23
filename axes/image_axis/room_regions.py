"""画像軸: **割れた区画を、仕上表の室名を種にしてまとめ直す。**

**この手法は 2026-09-23 に「不採用」と判定した。本番の経路から呼ばないこと。**
実図面(P011 匿名化v2)で測ると、室名の再現は 45 件中 8 件(0.178)、
「決められない」が 13 件、**負の対照 4 つのうち 2 つが不合格**だった。
いちばん重いのは、**室名を 1 つだけ渡すと 12 件中 4 件の面積が変わった**ことである。
止まっている理由の 3 分の 1 が「隣に別の室名が書いてあったから」なので、
**仕上表に載っていない室(納戸・PS・バルコニー)があると必ず飲み込む。**
判定と数字は `docs/a2_room_selection_report.md`。
残してあるのは次の周(線の太さで壁を見分ける)の土台に使うためで、
**「実装済み」と読まないこと。**


**なぜこれが要るのか**

実図面(P011 匿名化v2)で測ったところ、仕上表から読めた室名がのべ 45 回
図面ページに出てくるうち、**75.6% は 0.5㎡ 未満の小さすぎる面の中**にあった
(`docs/a2_real_drawing_wall_report.md`)。
**室は閉じていないのではない。閉じすぎている。**
実図面では室の中に床の目地・造作の輪郭・器具の線が描かれていて、
それが室の面を細かく割っている。合成図面には無かったものである。

**前に不採用にした寄せ直しとの違い**

`docs/a2_fragment_merge_rejected.md` の案は「室名を 1 つだけ含むまとまり」を
作ろうとして失敗した。**輪郭の中にある文字なら何でも室名として数えていた**ので、
浴室がトイレの断片を吸った。
今回は**仕上表から読んだ室名の一覧だけ**を止め札に使う。
室名という**図面の線とは別の出どころ**を使うところが違う。

**どこで止まるか(ここがこの実装の中身のほとんど)**

種の面から隣へ広げていき、次のどれかに当たったら**そこで止まる。**

1. **室になりうる大きさの面**(下限 0.5㎡ 以上)。**ここがいちばん効く。**
   飲み込んでよいのは**室より小さい破片だけ**である。
   実図面で測ると、室名の 75.6% は 0.5㎡ 未満の面の中にあった。
   破片は室より小さい。**室と同じ大きさのものは、隣の室かもしれない。**
2. **壁の中身**(細長すぎる面)。壁は室の境目である
3. **こちらが仮に閉じた辺**(建具の開口)。**開口を越えたら隣の室である**
4. **別の室名が入っている面**
5. 図面の外側
6. 広げた面積が上限を超えた(閉じきれていない図面)

**1 が本体である。** 4 だけに頼ると、
**仕上表に載っていない室(納戸・PS・バルコニー)は必ず飲み込まれる。**
壁が 1 本線で描かれた図面では、壁の中身が無いので 2 も効かない。
そこで残るのは 1 だけになる。

**その代わり、1 本の線で 2 つに割られただけの室(カウンターの線など)は
まとまらない。** 破片が室の大きさを超えるためである。
**まとまらないのは安全側の外れ方である**(面積を大きく出すより出さないほうがよい)。

**この実装が原理的に落とすもの**

- **仕上表に載っていない室。** 種が無いので絶対に出ない。
  **出なかった室を「無い」と読まない。**
- 室名が図面に書かれていない室。
- スキャンされたページ(線が図形として入っていない)。

**決めないこと**

- 1 つの領域に室名が 2 つ以上入ったとき、**どちらかを選ばない。**
  面積を出さずに「決められない」として返す。つないだ面積は正解より大きく出る。
  **黙って大きい面積を出すのは、出さないより悪い。**
- 2 つの種の領域が重なったとき、**どちらのものとも決めない**(同じ扱い)。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from axes.image_axis.pdf_room_outlines import (
    MAX_GAP_MM,
    ROOM_MAX_SQM,
    ROOM_MIN_SQM,
    ROOM_MIN_WIDTH_MM,
    SNAP_MM,
    AreaBasis,
    PlanGraph,
    _apply_area_basis,
    _inside,
    bracket_to_square_centimetre,
    build_plan_graph,
    face_metrics,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
METHOD_ROOM_REGION = "pdf_vector_room_region"

#: 壁の中身とみなすのに要る「細長さ」(長さ ÷ 幅)。
#: **狭いだけでは壁ではない。** 床の目地で割れた小さな区画も
#: ``2 × 面積 ÷ 周長`` は小さくなる(600 x 300mm の区画で 200mm)。
#: 壁の中身は狭いうえに**長い**(3800 x 200mm の壁で細長さ 20)。
#: **暫定値で、実図面で校正していない。**
WALL_MIN_ASPECT = 6.0


@dataclass(frozen=True)
class RoomRegion:
    """室名 1 つにまとまった領域。**室と決まってはいない。**"""

    room_name: str
    """**仕上表から渡された室名そのまま。** ここで名前を作らない。"""

    face_count: int
    """まとめた区画の数。1 なら割れていなかったということ。"""

    area_sqm: float
    area_range_sqm: tuple[float, float]
    perimeter_mm: float
    min_width_mm: float

    area_basis: AreaBasis
    area_basis_note: str

    polygon_pt: tuple[tuple[float, float], ...]
    virtual_edges: int
    """輪郭のうち、**こちらが仮に閉じた**辺の本数。"""

    page_index: int
    method_id: str = METHOD_ROOM_REGION
    limitation: str = (
        "仕上表に載っていない室は種が無いので絶対に出ない。"
        "出なかった室は「無い」ではない。"
        "スキャンされたページでは常に 0 件"
    )


@dataclass(frozen=True)
class AmbiguousRegion:
    """室名が 2 つ以上入った領域。**面積を出さない。**"""

    room_names: tuple[str, ...]
    face_count: int
    area_sqm: float
    reason: str


@dataclass(frozen=True)
class RoomRegionResult:
    regions: tuple[RoomRegion, ...] = ()
    ambiguous: tuple[AmbiguousRegion, ...] = ()
    missing_names: tuple[str, ...] = ()
    """図面の上に見つからなかった室名。**0 件は「その室が無い」ではない。**"""
    notes: tuple[str, ...] = ()


def find_room_regions(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    room_names: tuple[str, ...] | list[str] = (),
    *,
    min_sqm: float = ROOM_MIN_SQM,
    max_sqm: float = ROOM_MAX_SQM,
    min_width_mm: float = ROOM_MIN_WIDTH_MM,
    snap_mm: float = SNAP_MM,
    max_gap_mm: float = MAX_GAP_MM,
    exclude_tables: bool = True,
    area_basis: AreaBasis = "不明",
) -> RoomRegionResult:
    """仕上表の室名を種にして、割れた区画をまとめ直す。

    ``room_names`` を渡さなければ**何も返さない。**
    この手法は室名という別の出どころが無いと動かない。
    """
    if area_basis not in ("内法", "壁芯", "不明"):
        raise ValueError(f"知らない面積の数え方です: {area_basis}")
    if min_sqm <= 0 or max_sqm <= min_sqm:
        raise ValueError("面積の窓が不正です")
    if min_width_mm <= 0:
        raise ValueError("幅は正でなければなりません")

    wanted = tuple(dict.fromkeys(name.strip() for name in room_names if name.strip()))
    if not wanted:
        return RoomRegionResult(notes=("室名が渡されていないので何も探さない",))

    graph = build_plan_graph(
        pdf_path, page_index, scale, snap_mm, max_gap_mm, exclude_tables=exclude_tables
    )
    if graph is None:
        return RoomRegionResult(
            missing_names=wanted,
            notes=("線が 1 本も無いページ。0 件は「室が無い」ではない",),
        )

    metrics = [face_metrics(cycle, graph.coords, graph.mm_per_pt) for cycle in graph.cycles]
    positive = [index for index, (area, _, _) in enumerate(metrics) if area > 0]
    cavities = {
        index for index in positive if _looks_like_a_wall(metrics[index], min_width_mm)
    }

    #: 面ごとに、その中にある室名。**渡された室名だけを見る。**
    names_in_face: dict[int, set[str]] = {}
    for text, centre in graph.spans:
        if text.strip() not in wanted:
            continue
        holder = _smallest_face_containing(centre, positive, graph, metrics)
        if holder is None:
            continue
        names_in_face.setdefault(holder, set()).add(text.strip())

    seeds: dict[int, set[str]] = dict(names_in_face)
    found_names = {name for names in seeds.values() for name in names}
    missing = tuple(name for name in wanted if name not in found_names)

    grown: list[tuple[set[int], set[str]]] = []
    for seed in sorted(seeds):
        region, names, note = _grow(
            seed, graph, metrics, cavities, names_in_face, min_sqm, max_sqm
        )
        grown.append((region, names))
        if note:
            pass  # 面積の上限で止まった件は下で「決められない」に回す

    # 重なった領域はどちらのものとも決めない。
    claimed: dict[int, list[int]] = {}
    for order, (region, _) in enumerate(grown):
        for face in region:
            claimed.setdefault(face, []).append(order)
    overlapping = {
        order for owners in claimed.values() if len(owners) > 1 for order in owners
    }

    regions: list[RoomRegion] = []
    ambiguous: list[AmbiguousRegion] = []
    notes: list[str] = []
    for order, (region, names) in enumerate(grown):
        area = sum(metrics[face][0] for face in region)
        if len(names) > 1:
            ambiguous.append(
                AmbiguousRegion(
                    room_names=tuple(sorted(names)),
                    face_count=len(region),
                    area_sqm=area,
                    reason=(
                        "1 つの領域に室名が "
                        + "・".join(sorted(names))
                        + " と入っているので、どの室の面積か決められない"
                    ),
                )
            )
            continue
        if order in overlapping:
            ambiguous.append(
                AmbiguousRegion(
                    room_names=tuple(sorted(names)),
                    face_count=len(region),
                    area_sqm=area,
                    reason=(
                        "ほかの室の領域と重なっているので、どの室の面積か決められない"
                        "(室のあいだに壁が引かれていない)"
                    ),
                )
            )
            continue
        if not min_sqm <= area <= max_sqm:
            notes.append(
                f"{'・'.join(sorted(names))}: まとめた面積が窓の外なので出さない"
                f"(区画が広がりすぎたか、種の区画しか見つからなかった)"
            )
            continue
        if _has_island(region, graph, metrics, area):
            notes.append(
                f"{'・'.join(sorted(names))}: 領域の中に、領域に入っていない面がある"
                "(独立柱など)。外周だけで数えると面積が大きく出るので出さない"
            )
            continue
        built = _build_region(
            region, names, graph, metrics, cavities, area, page_index, area_basis
        )
        if built is None:
            notes.append(f"{'・'.join(sorted(names))}: 輪郭を 1 本につなげなかったので出さない")
            continue
        regions.append(built)

    regions.sort(key=lambda region: (-region.area_sqm, region.room_name))
    return RoomRegionResult(
        regions=tuple(regions),
        ambiguous=tuple(ambiguous),
        missing_names=missing,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 広げる
# ---------------------------------------------------------------------------


def _looks_like_a_wall(
    metric: tuple[float, float, float], min_width_mm: float
) -> bool:
    """その面が壁の中身か。**狭いだけでは壁ではない。狭くて長いのが壁である。**

    床の目地で割れた小さな区画も ``2 × 面積 ÷ 周長`` は小さくなるので、
    幅だけで見ると**区画が全部壁になってしまう**(合成図面で 133 面すべてが
    壁と判定された)。長さと幅の比も一緒に見る。
    """
    area_sqm, perimeter_mm, width_mm = metric
    if width_mm <= 0 or width_mm >= min_width_mm:
        return False
    length_mm, short_mm = _sides(area_sqm * 1_000_000.0, perimeter_mm)
    if short_mm <= 0:
        return False
    return length_mm / short_mm >= WALL_MIN_ASPECT


def _sides(area_mm2: float, perimeter_mm: float) -> tuple[float, float]:
    """面積と周長から、同じ面積・周長を持つ長方形の (長辺, 短辺) を出す。

    ``2 × 面積 ÷ 周長`` は長方形の辺そのものではない
    (1200 x 300mm の区画で 240mm になる)。長さと幅の比を見るには辺が要る。
    長方形にならない面(解が無い)では、短辺を ``2 × 面積 ÷ 周長`` で代用する。
    """
    half = perimeter_mm / 2.0
    discriminant = half * half - 4.0 * area_mm2
    if discriminant < 0:
        width = 2.0 * area_mm2 / perimeter_mm if perimeter_mm > 0 else 0.0
        return (half - width, width)
    root = discriminant ** 0.5
    short = (half - root) / 2.0
    return (half - short, short)


def _smallest_face_containing(
    point: tuple[float, float],
    positive: list[int],
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
) -> int | None:
    """その点を含むいちばん小さい面。**いちばん小さい面がその文字の入れ物である。**"""
    best: int | None = None
    for index in positive:
        polygon = [graph.coords[point_index] for point_index in graph.cycles[index]]
        if not _inside(point, polygon):
            continue
        if best is None or metrics[index][0] < metrics[best][0]:
            best = index
    return best


def _grow(
    seed: int,
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
    cavities: set[int],
    names_in_face: dict[int, set[str]],
    min_sqm: float,
    max_sqm: float,
) -> tuple[set[int], set[str], str]:
    """種の面から、**室になりうる大きさの面**・壁・開口・別の室名に当たるまで広げる。"""
    region = {seed}
    names = set(names_in_face.get(seed, set()))
    area = metrics[seed][0]
    queue: deque[int] = deque([seed])
    note = ""
    while queue:
        face = queue.popleft()
        cycle = graph.cycles[face]
        for position in range(len(cycle)):
            a = cycle[position]
            b = cycle[(position + 1) % len(cycle)]
            if (a, b) in graph.virtual_edges:
                continue  # 建具の開口。越えたら隣の室である
            neighbour = graph.owner.get((b, a))
            if neighbour is None or neighbour in region:
                continue
            if metrics[neighbour][0] <= 0:
                continue  # 図面の外側
            if neighbour in cavities:
                continue  # 壁の中身。室の境目である
            if metrics[neighbour][0] >= min_sqm:
                continue  # 室になりうる大きさ。**隣の室かもしれないので飲み込まない**
            other = names_in_face.get(neighbour)
            if other and other - names:
                continue  # 別の室名が入っている面
            if area + metrics[neighbour][0] > max_sqm:
                note = "面積の上限に当たって止まった"
                continue
            region.add(neighbour)
            area += metrics[neighbour][0]
            if other:
                names |= other
            queue.append(neighbour)
    return region, names, note


# ---------------------------------------------------------------------------
# 輪郭をつなぐ
# ---------------------------------------------------------------------------


def _boundary_cycle(region: set[int], graph: PlanGraph) -> list[int] | None:
    """領域の外周を 1 本の輪にする。穴があるときや枝分かれするときは None。"""
    outgoing: dict[int, int] = {}
    virtual = 0
    count = 0
    for face in region:
        cycle = graph.cycles[face]
        for position in range(len(cycle)):
            a = cycle[position]
            b = cycle[(position + 1) % len(cycle)]
            twin = graph.owner.get((b, a))
            if twin is not None and twin in region:
                continue
            if a in outgoing:
                return None  # 枝分かれ(8 の字など)
            outgoing[a] = b
            count += 1
            if (a, b) in graph.virtual_edges:
                virtual += 1
    if not outgoing:
        return None
    start = next(iter(outgoing))
    chain = [start]
    current = outgoing[start]
    while current != start:
        if current not in outgoing or len(chain) > count:
            return None
        chain.append(current)
        current = outgoing[current]
    if len(chain) != count:
        return None  # 外周のほかに穴がある
    return chain


def _build_region(
    region: set[int],
    names: set[str],
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
    cavities: set[int],
    area_sqm: float,
    page_index: int,
    area_basis: AreaBasis,
) -> RoomRegion | None:
    cycle = _boundary_cycle(region, graph)
    if cycle is None:
        return None
    polygon = [graph.coords[index] for index in cycle]
    perimeter_mm = _perimeter(polygon) * graph.mm_per_pt
    if perimeter_mm <= 0:
        return None
    virtual = sum(
        1
        for position in range(len(cycle))
        if (cycle[position], cycle[(position + 1) % len(cycle)]) in graph.virtual_edges
    )

    kind, note, polygon, area_sqm, perimeter_mm = _apply_area_basis(
        area_basis,
        cycle,
        -1,
        polygon,
        area_sqm,
        perimeter_mm,
        graph.coords,
        graph.owner,
        cavities,
        graph.cycles,
        graph.mm_per_pt,
    )
    return RoomRegion(
        room_name=next(iter(names)),
        face_count=len(region),
        area_sqm=area_sqm,
        area_range_sqm=bracket_to_square_centimetre(area_sqm),
        perimeter_mm=perimeter_mm,
        min_width_mm=2.0 * (area_sqm * 1_000_000.0) / perimeter_mm,
        area_basis=kind,
        area_basis_note=note,
        polygon_pt=tuple(polygon),
        virtual_edges=virtual,
        page_index=page_index,
    )


def _has_island(
    region: set[int],
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
    region_area_sqm: float,
) -> bool:
    """領域の外周の内側に、領域に入っていない面があるか。

    独立柱のように**どの壁にも触れていない**図形は、面をたどっても領域に入らない。
    外周だけで面積を数えると、その柱のぶんだけ大きく出る。**大きく出すくらいなら出さない。**
    """
    cycle = _boundary_cycle(region, graph)
    if cycle is None:
        return False  # つながらない時点で出さないので、ここでは判定しない
    polygon = [graph.coords[index] for index in cycle]
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    box = (min(xs), min(ys), max(xs), max(ys))
    for index, (area, _, _) in enumerate(metrics):
        if area <= 0 or index in region:
            continue
        if area >= region_area_sqm:
            continue  # **中にあるものは外より小さい。** 図面枠の面を島と数えない
        xs = [graph.coords[k][0] for k in graph.cycles[index]]
        ys = [graph.coords[k][1] for k in graph.cycles[index]]
        if not (box[0] <= min(xs) and max(xs) <= box[2] and box[1] <= min(ys) and max(ys) <= box[3]):
            continue
        if _inside(_representative_point(graph.cycles[index], graph.coords), polygon):
            return True
    return False


def _representative_point(
    cycle: list[int], coords: list[tuple[float, float]]
) -> tuple[float, float]:
    """面の中にあるとみなせる点。**重心を使う**(凹んだ面では外へ出ることがある)。"""
    xs = [coords[index][0] for index in cycle]
    ys = [coords[index][1] for index in cycle]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _perimeter(polygon: list[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(len(polygon)):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % len(polygon)]
        total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return total
