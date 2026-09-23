"""画像軸: **壁の網の外側をまとめて、室の形を出す。**

**この手法は 2026-09-23 に「不採用」と判定した。本番の経路から呼ばないこと。**
実図面(P011 匿名化v2)で室名の再現は **0.000**(7周目は 0.178)。
いちばん重いのは**入れ替え対照の不合格**で、
**壁の判定をほぼ消したほうが成績が良かった**(0.000 → 0.067)。
つまり**この図面では、見つけた「壁」のほとんどが壁ではなく、室を切り刻んでいる。**
判定と数字は `docs/a2_wall_network_report.md`。
同じ報告書に、**「この案件の図面からは室の面積は出ない」という結論**(キラークエスチョン)
も書いてある。残してあるのは共通の幾何を使う側の参照のためで、
**「実装済み」と読まないこと。**


**室名を種にしない。** ここが 7 周目(`room_regions.py`)との違いである。

**なぜ種を捨てたのか**

7 周目は仕上表の室名を種にして広げた。実図面で測ると、
**室名を 1 つだけ渡したときに 12 件中 4 件の面積が変わった**
(`docs/a2_room_selection_report.md` の負の対照 4)。
止まっている理由の 3 分の 1 が「たまたま隣に別の室名が書いてあったから」で、
**仕上表に載っていない室(納戸・PS・バルコニー)があると必ず飲み込む。**

ここでは室名をまったく使わずに形を決める。**室名は、できた形に名前を付けるだけ**で、
渡しても渡さなくても**面積は 1 ㎡も変わらない**(試験で縛ってある)。

**やること**

1. 図面の線を平面グラフに直す(`build_plan_graph`。**共通の土台を使う**)
2. **壁の中身**の面を見つける(狭くて長くて、**紙の上で見える厚みがある**面)
3. 壁の中身と、建具の開口と、図面の外側を**境にして**、
   残りの面を隣どうしつないでいく
4. つながった 1 かたまりを 1 つの室の候補とする

**2 の「紙の上で見える厚み」がこの周の中身である。**

実図面では同じ線が少しずれて二重に引いてある。端点を寄せる許容差では寄りきらず、
**厚みがほぼゼロの細長い面**ができる。実図面(P011 匿名化v2)では、
細長い面 3,837 個のうち **1,473 個(38%)がこれ**だった。
そのほとんどが「壁」と判定され、**室をあちこちで切り刻んでいた。**
図面の線は 0.24〜0.72pt で描かれているので、
**紙の上で 1pt 未満の面は、人が見れば面ではなく線である。**

**決めないこと**

- 1 つのかたまりに室名が 2 つ以上入ったとき、**どちらかを選ばない。**
  面積を出さずに「決められない」として返す。つないだ面積は正解より大きく出る。
  **黙って大きい面積を出すのは、出さないより悪い。**
- 室名が 1 つも入らなかったかたまりも**捨てない。**
  仕上表に載っていない室はここに出る。名前が無いだけで、室かもしれない。

**この実装が原理的に落とすもの**

- **壁を 1 本線で描いた図面。** 壁の中身の面がそもそも無いので、
  室どうしが直に接し、**全部が 1 つのかたまりに溶ける。**
  溶けた結果は面積の窓から外れるので出ない(負の対照 2 で確かめる)。
- スキャンされたページ(線が図形として入っていない)。
- 壁の網に切れ目がある図面。切れ目の向こうとつながって大きく出る。
  **出た面積が窓の外なら出さない。**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axes.image_axis.pdf_room_outlines import (
    MAX_GAP_MM,
    ROOM_MAX_SQM,
    ROOM_MIN_SQM,
    ROOM_MIN_WIDTH_MM,
    SNAP_MM,
    WALL_MIN_VISIBLE_PT,
    AreaBasis,
    PlanGraph,
    _apply_area_basis,
    _boundary_cycle,
    _has_island,
    _inside,
    _perimeter,
    bracket_to_square_centimetre,
    build_plan_graph,
    face_metrics,
    is_wall_cavity,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
METHOD_WALL_NETWORK = "pdf_vector_wall_network"


@dataclass(frozen=True)
class WallRegion:
    """壁に囲まれた 1 かたまり。**室と決まってはいない。**"""

    room_name: str
    """中にあった室名。**渡された一覧に載っていたものだけ。**
    空文字は「名前が付かなかった」であって「室でない」ではない。"""

    face_count: int
    area_sqm: float
    area_range_sqm: tuple[float, float]
    perimeter_mm: float
    min_width_mm: float

    area_basis: AreaBasis
    area_basis_note: str

    polygon_pt: tuple[tuple[float, float], ...]
    virtual_edges: int
    page_index: int
    method_id: str = METHOD_WALL_NETWORK
    limitation: str = (
        "壁を 1 本線で描いた図面では全部が 1 つに溶けるので 0 件になる。"
        "0 件は「室が無い」ではない。"
        "スキャンされたページでは常に 0 件"
    )


@dataclass(frozen=True)
class AmbiguousRegion:
    """室名が 2 つ以上入ったかたまり。**面積を出さない。**"""

    room_names: tuple[str, ...]
    face_count: int
    area_sqm: float
    reason: str


@dataclass(frozen=True)
class WallNetworkResult:
    regions: tuple[WallRegion, ...] = ()
    ambiguous: tuple[AmbiguousRegion, ...] = ()
    wall_faces: int = 0
    """壁の中身と判定した面の数。**紙の上で見えない面を数えていないかの見張り。**"""
    missing_names: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def find_regions_between_walls(
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
    min_visible_pt: float = WALL_MIN_VISIBLE_PT,
    exclude_tables: bool = True,
    area_basis: AreaBasis = "不明",
) -> WallNetworkResult:
    """壁の中身を境にして、そのまわりをまとめる。**室名は形を決めない。**"""
    if area_basis not in ("内法", "壁芯", "不明"):
        raise ValueError(f"知らない面積の数え方です: {area_basis}")
    if min_sqm <= 0 or max_sqm <= min_sqm:
        raise ValueError("面積の窓が不正です")
    if min_width_mm <= 0:
        raise ValueError("幅は正でなければなりません")
    if min_visible_pt < 0:
        raise ValueError("紙の上の厚みは 0 以上でなければなりません")

    wanted = tuple(dict.fromkeys(name.strip() for name in room_names if name.strip()))

    graph = build_plan_graph(
        pdf_path, page_index, scale, snap_mm, max_gap_mm, exclude_tables=exclude_tables
    )
    if graph is None:
        return WallNetworkResult(
            missing_names=wanted,
            notes=("線が 1 本も無いページ。0 件は「室が無い」ではない",),
        )

    metrics = [face_metrics(cycle, graph.coords, graph.mm_per_pt) for cycle in graph.cycles]
    positive = [index for index, (area, _, _) in enumerate(metrics) if area > 0]
    cavities = {
        index
        for index in positive
        if is_wall_cavity(metrics[index], min_width_mm, graph.mm_per_pt, min_visible_pt)
    }

    components = _merge_between_walls(positive, cavities, graph, metrics)

    names_in_face = _names_in_face(graph, positive, metrics, wanted)
    found: set[str] = set()

    regions: list[WallRegion] = []
    ambiguous: list[AmbiguousRegion] = []
    notes: list[str] = []
    for component in components:
        area = sum(metrics[face][0] for face in component)
        names = set()
        for face in component:
            names |= names_in_face.get(face, set())
        found |= names
        if len(names) > 1:
            ambiguous.append(
                AmbiguousRegion(
                    room_names=tuple(sorted(names)),
                    face_count=len(component),
                    area_sqm=area,
                    reason=(
                        "1 つのかたまりに室名が "
                        + "・".join(sorted(names))
                        + " と入っているので、どの室の面積か決められない"
                    ),
                )
            )
            continue
        if not min_sqm <= area <= max_sqm:
            continue  # 窓の外。**溶けた塊も、破片も、ここで落ちる**
        if _has_island(component, graph, metrics, area):
            notes.append("かたまりの中に、かたまりに入っていない面がある(独立柱など)ので出さない")
            continue
        built = _build_region(
            component, names, graph, metrics, cavities, area, page_index, area_basis
        )
        if built is None:
            notes.append("輪郭を 1 本につなげなかったので出さない")
            continue
        regions.append(built)

    regions.sort(key=lambda region: (-region.area_sqm, region.room_name))
    return WallNetworkResult(
        regions=tuple(regions),
        ambiguous=tuple(ambiguous),
        wall_faces=len(cavities),
        missing_names=tuple(name for name in wanted if name not in found),
        notes=tuple(dict.fromkeys(notes)),
    )


# ---------------------------------------------------------------------------
# まとめる
# ---------------------------------------------------------------------------


def _merge_between_walls(
    positive: list[int],
    cavities: set[int],
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
) -> list[set[int]]:
    """壁・開口・外側を境にして、残りの面を隣どうしつなぐ。

    **室名も面積の窓も見ない。** 形だけで決める。
    """
    parent = {face: face for face in positive if face not in cavities}

    def root(face: int) -> int:
        while parent[face] != face:
            parent[face] = parent[parent[face]]
            face = parent[face]
        return face

    for face in parent:
        cycle = graph.cycles[face]
        for position in range(len(cycle)):
            a = cycle[position]
            b = cycle[(position + 1) % len(cycle)]
            if (a, b) in graph.virtual_edges:
                continue  # 建具の開口。越えたら隣の室である
            neighbour = graph.owner.get((b, a))
            if neighbour is None or neighbour not in parent:
                continue  # 図面の外側、または壁の中身
            left, right = root(face), root(neighbour)
            if left != right:
                parent[right] = left

    grouped: dict[int, set[int]] = {}
    for face in parent:
        grouped.setdefault(root(face), set()).add(face)
    return [grouped[key] for key in sorted(grouped)]


def _names_in_face(
    graph: PlanGraph,
    positive: list[int],
    metrics: list[tuple[float, float, float]],
    wanted: tuple[str, ...],
) -> dict[int, set[str]]:
    """面ごとに、その中にある室名。**いちばん小さい面がその文字の入れ物である。**"""
    out: dict[int, set[str]] = {}
    if not wanted:
        return out
    allowed = set(wanted)
    for text, centre in graph.spans:
        name = text.strip()
        if name not in allowed:
            continue
        best: int | None = None
        for index in positive:
            polygon = [graph.coords[point] for point in graph.cycles[index]]
            if not _inside(centre, polygon):
                continue
            if best is None or metrics[index][0] < metrics[best][0]:
                best = index
        if best is not None:
            out.setdefault(best, set()).add(name)
    return out


def _build_region(
    component: set[int],
    names: set[str],
    graph: PlanGraph,
    metrics: list[tuple[float, float, float]],
    cavities: set[int],
    area_sqm: float,
    page_index: int,
    area_basis: AreaBasis,
) -> WallRegion | None:
    cycle = _boundary_cycle(component, graph)
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
    return WallRegion(
        room_name=next(iter(names)) if names else "",
        face_count=len(component),
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
