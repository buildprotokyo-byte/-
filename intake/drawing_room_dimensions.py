"""図面に記入された寸法から、室の縦・横・天井高を組む(K-37 やること 1、2026-09-25)。

なぜこのモジュールが要るのか
----------------------------
おーちゃんの K-37:

> **10室の寸法は、図面に書かれているはずのものです。だから人に聞いてはいけません。**
> 読めているのに、使い道が無い。ここを繋ぐのが先です。

#139 で平面図の寸法は読めるようになった(`axes/image_axis/pdf_dimensions.py`)が、
**それを室の長さとして見積の行に渡す部品が無かった。**室の面積を出す道は
人の入力(`intake/room_dimensions.py`)しか無かった。

どう組むか
----------
- **どの寸法がどの室のどの向きの長さかは、AI が図面を見て決める**(K-36 の役割表
  「行の骨組み・区分: AI の通読」)。ここはその対応づけ(`RoomAssignment`)を受け取るだけ。
- **長さの値は、機械が読んだ寸法の値だけから作る。**対応づけが指せるのは寸法の id で、
  値そのものは書けない。1 つの向きに複数の id を並べたら**足す**。引き算・按分はしない。
- 天井高は、そのページに ``CH=<値>`` が**印字されているときだけ**使う。

決められないものの扱い(K-37 の決まり)
--------------------------------------
- どの室にも結び付かなかった寸法 → **「対応先不明」として残す。捨てない。**
- 縦横が揃わない室 → 面積を出さない。**「片方だけ判明」として残す。**
- **推測で補わない。**知らない id・向きの合わない id は使わず、理由を ``gaps`` に残す。

出どころを人の入力と混ぜない
----------------------------
作る `RoomDimension` の ``entered_by`` には「図面の寸法」と使った id を書き、
数量にするときは ``estimating.from_room_dimensions.ORIGIN_DRAWING`` を渡す。
手法 ``drawing_room_dimensions`` は `arbitration/method_policies.py` に**未校正**で登録してある。
**図面の寸法も人の入力も、同じ PDF の読みと突き合わせても独立した 2 つ目にはならない**
(寸法はその PDF の印字である)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from intake.room_dimensions import AREA_BASES, BASIS_UNKNOWN, AreaBasis, RoomDimension, RoomDimensionError

#: 手法ID。登録簿(`arbitration/method_policies.py`)には循環を避けて文字列で置く。
METHOD_DRAWING_ROOM_DIMENSIONS = "drawing_room_dimensions"

#: 室の状態。
STATE_BOTH = "両方判明"
STATE_ONE = "片方だけ判明"
STATE_NONE = "不明"

#: どの室にも結び付かなかった寸法の印。
UNRESOLVED = "対応先不明"

#: 対応づけの向きと、寸法の読みの向き。**横=図の左右、縦=図の上下。**
_WIDTH = "横"
_LENGTH = "縦"

_CEILING_PATTERN = re.compile(r"CH\s*=\s*(\d{3,5})")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def dimension_ids(pages: Iterable[Any]) -> dict[str, Any]:
    """ページごとの寸法の読みに id を振る。``P{ページ}-D{その中の順番}``(どちらも 1 始まり)。

    順番は `read_dimensions` が返す順(決定的に並べてある)。**AI に見せた id と同じ振り方である。**
    """
    ids: dict[str, Any] = {}
    for page in pages:
        for index, reading in enumerate(page.readings, 1):
            ids[f"P{page.page_number}-D{index}"] = reading
    return ids


@dataclass(frozen=True)
class RoomAssignment:
    """1 室ぶんの対応づけ。**値は持たない。寸法の id だけを持つ。**"""

    room_name: str
    width_ids: tuple[str, ...] = ()
    """横(図の左右)の長さになる寸法の id。複数なら足す。"""

    length_ids: tuple[str, ...] = ()
    """縦(図の上下)の長さになる寸法の id。"""

    assigned_by: str = ""
    """誰が対応づけたか。**根拠として残す。**"""

    area_basis: AreaBasis = BASIS_UNKNOWN
    """寸法が芯々か内法か。対応づけた側が見て決めたもの。分からなければ不明。"""

    ceiling_height_mm: float | None = None
    """天井高。**そのページに ``CH=`` として印字された値だけ。**"""

    ceiling_page: int | None = None
    """天井高が印字されているページ(1 始まり)。無ければ全ページから探す。"""

    reason: str = ""


@dataclass(frozen=True)
class DrawingRoomResult:
    rooms: tuple[RoomDimension, ...] = ()
    """寸法が 1 つでも決まった室。**縦横が揃わない室も入る**(面積は下流で出さない)。"""

    states: Mapping[str, str] = field(default_factory=dict)
    """室名ごとの ``STATE_*``。"""

    unresolved: tuple[str, ...] = ()
    """どの室にも使われなかった寸法の id(**対応先不明**)。"""

    gaps: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        by_state: dict[str, list[str]] = {STATE_BOTH: [], STATE_ONE: [], STATE_NONE: []}
        for name, state in self.states.items():
            by_state[state].append(name)
        return {
            **by_state,
            UNRESOLVED: list(self.unresolved),
            "使わなかった理由": list(self.gaps),
        }


def _side(
    room: str,
    label: str,
    ids: Sequence[str],
    readings: Mapping[str, Any],
    gaps: list[str],
) -> tuple[float | None, list[str]]:
    total = 0.0
    used: list[str] = []
    for dim_id in ids:
        reading = readings.get(dim_id)
        if reading is None:
            gaps.append(f"[機械の読みに無い id] {room} の{label}: {dim_id} は使っていない")
            return None, []
        if getattr(reading, "orientation", None) != label:
            gaps.append(
                f"[向きが合わない] {room} の{label}: {dim_id}({reading.text})は"
                f"{getattr(reading, 'orientation', '?')}向きの寸法なので使っていない"
            )
            return None, []
        total += float(reading.value_mm)
        used.append(dim_id)
    if not used:
        return None, []
    return total, used


def _printed_ceiling(
    assignment: RoomAssignment, page_texts: Mapping[int, str], gaps: list[str]
) -> float | None:
    if assignment.ceiling_height_mm is None:
        return None
    value = int(round(assignment.ceiling_height_mm))
    pages = (
        [assignment.ceiling_page] if assignment.ceiling_page is not None else list(page_texts)
    )
    for page in pages:
        found = {int(m) for m in _CEILING_PATTERN.findall(_nfkc(page_texts.get(page, "")))}
        if value in found:
            return float(value)
    gaps.append(
        f"[天井高が印字に無い] {assignment.room_name}: CH={value} がページ "
        f"{pages} の文字に見つからないので、天井高は使っていない"
    )
    return None


def rooms_from_drawing(
    assignments: Iterable[RoomAssignment],
    readings: Mapping[str, Any],
    page_texts: Mapping[int, str],
) -> DrawingRoomResult:
    """対応づけと寸法の読みから、室の寸法を組む。**値は読みからしか取らない。**"""
    rooms: list[RoomDimension] = []
    states: dict[str, str] = {}
    gaps: list[str] = []
    used_ids: set[str] = set()

    for assignment in assignments:
        name = assignment.room_name.strip()
        if assignment.area_basis not in AREA_BASES:
            gaps.append(f"[測り方が不明] {name}: {assignment.area_basis!r} は不明として扱う")
        width, width_used = _side(name, _WIDTH, assignment.width_ids, readings, gaps)
        length, length_used = _side(name, _LENGTH, assignment.length_ids, readings, gaps)
        found = [v for v in (width, length) if v is not None]
        states[name] = (STATE_BOTH, STATE_ONE, STATE_NONE)[2 - len(found)]
        if not found:
            continue
        used = width_used + length_used
        try:
            room = RoomDimension(
                room_name=name,
                length_mm=length,
                width_mm=width,
                ceiling_height_mm=_printed_ceiling(assignment, page_texts, gaps),
                area_basis=assignment.area_basis if assignment.area_basis in AREA_BASES else BASIS_UNKNOWN,
                entered_by=(
                    f"図面の寸法({'・'.join(used)})。対応づけ: {assignment.assigned_by or '不明'}"
                ),
            )
        except RoomDimensionError as error:
            gaps.append(f"[寸法として使えない] {name}: {error}")
            states[name] = STATE_NONE
            continue
        used_ids.update(used)
        rooms.append(room)

    unresolved = tuple(dim_id for dim_id in readings if dim_id not in used_ids)
    return DrawingRoomResult(
        rooms=tuple(rooms), states=states, unresolved=unresolved, gaps=tuple(gaps)
    )


def _page_of(dim_id: str) -> int | None:
    head = dim_id.split("-", 1)[0]
    return int(head[1:]) if head.startswith("P") and head[1:].isdigit() else None


def _span(ids: Sequence[str], readings: Mapping[str, Any], axis: int) -> tuple[float, float] | None:
    values: list[float] = []
    for dim_id in ids:
        reading = readings.get(dim_id)
        start = getattr(reading, "start_pt", None)
        end = getattr(reading, "end_pt", None)
        if start is None or end is None:
            return None
        values.extend((float(start[axis]), float(end[axis])))
    return (min(values), max(values)) if values else None


def other_room_names_inside(
    assignments: Iterable[RoomAssignment],
    readings: Mapping[str, Any],
    labels: Mapping[str, Sequence[tuple[int, float, float]]],
) -> tuple[str, ...]:
    """室の長方形の中に、**別の室の室名**が入っていたら知らせる(K-40 1 番の検算)。

    長方形は、横に使った寸法の左右の端と、縦に使った寸法の上下の端で作る(同じページのときだけ)。
    ``labels`` は室名ごとの ``(ページ, x, y)``(室名の文字の中心、pt)。
    実図面(K-38)では、LDK の長方形に玄関とホールの室名が入り、LDK の面積が大きすぎた。
    **知らせるだけで、面積は変えない。**
    """
    warnings: list[str] = []
    for assignment in assignments:
        pages = {_page_of(i) for i in assignment.width_ids + assignment.length_ids}
        if len(pages) != 1 or None in pages:
            continue
        page = pages.pop()
        xs = _span(assignment.width_ids, readings, 0)
        ys = _span(assignment.length_ids, readings, 1)
        if xs is None or ys is None:
            continue
        name = _nfkc(assignment.room_name)
        inside = sorted(
            other
            for other, points in labels.items()
            if _nfkc(other) != name
            and any(p == page and xs[0] < x < xs[1] and ys[0] < y < ys[1] for p, x, y in points)
        )
        if inside:
            warnings.append(
                f"[長方形の中に別の室名] {assignment.room_name.strip()} の長方形"
                f"(ページ {page}、横 {xs[0]:.0f}〜{xs[1]:.0f}pt・縦 {ys[0]:.0f}〜{ys[1]:.0f}pt)に "
                f"{'・'.join(n.strip() for n in inside)} の室名が入っている。面積が大きすぎるおそれ"
            )
    return tuple(warnings)


@dataclass(frozen=True)
class PageRuler:
    """1 ページぶんの基準(K-38)。**AI が選んだ 1 つの寸法の id と、許容差だけを持つ。**

    ``reference_id`` は**縮尺なしで読んだときの id**(K-37 で AI が基準を選んだ読み)。
    目盛りはその寸法の値 ÷ 紙の上の長さから機械が作る。AI は値を書けない。
    そのページは、この目盛りで読み直してから室を組む(候補が 2 本以上で落ちた数字を 1 本に決める)。
    室の対応づけの id は、**読み直したあとの id** を指す。
    """

    page: int
    reference_id: str
    tolerance: float


def load_rulers(payload: Mapping[str, Any]) -> tuple[PageRuler, ...]:
    """対応づけの JSON の ``"基準": [{"ページ", "基準の寸法", "許容差"}]`` を読む。無ければ空。"""
    out: list[PageRuler] = []
    for row in payload.get("基準", ()):
        tolerance = row.get("許容差")
        if tolerance is None:
            raise ValueError(f"基準 {row!r} に許容差が無い。許容差は黙って決めない")
        out.append(PageRuler(int(row["ページ"]), str(row["基準の寸法"]), float(tolerance)))
    return tuple(out)


def load_assignments(payload: Mapping[str, Any]) -> tuple[RoomAssignment, ...]:
    """対応づけの JSON(``{"対応づけた人": ..., "室": [{"室名", "横", "縦", "測り方", "天井高_mm", "天井高のページ"}]}``)を読む。"""
    by = str(payload.get("対応づけた人", ""))
    out: list[RoomAssignment] = []
    for row in payload.get("室", ()):
        out.append(
            RoomAssignment(
                room_name=str(row["室名"]),
                width_ids=tuple(row.get("横", ())),
                length_ids=tuple(row.get("縦", ())),
                assigned_by=by,
                area_basis=row.get("測り方", BASIS_UNKNOWN),
                ceiling_height_mm=row.get("天井高_mm"),
                ceiling_page=row.get("天井高のページ"),
                reason=str(row.get("理由", "")),
            )
        )
    return tuple(out)


__all__ = [
    "METHOD_DRAWING_ROOM_DIMENSIONS",
    "PageRuler",
    "STATE_BOTH",
    "STATE_NONE",
    "STATE_ONE",
    "UNRESOLVED",
    "DrawingRoomResult",
    "RoomAssignment",
    "dimension_ids",
    "load_assignments",
    "load_rulers",
    "other_room_names_inside",
    "rooms_from_drawing",
]
