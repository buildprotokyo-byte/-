"""位置つきの読みから、記号を室ごとに数える(K-55)。

**読むのは AI、数えるのは機械。** AI は要素 1 つずつに名前を付けるだけで(記号には凡例の名前と
改装の区分、文字には室名の印)、数えない。同じ物かどうかも判定しない。機械は座標だけで室に置き、
(凡例の名前, 区分, 室) ごとに数える。基準は `docs/k55_count_symbols_criteria.md`(測る前にコミット)。

ここが守ること
--------------
- **数は機械が数えたものだけ。** 数を作らない。取れないものは 0 にしない。
- 室は、記号の四角の中心から、同じページの室名の要素の中心までの距離で決める(K-51 3 節と同じ
  200 画素)。**どの室名も届かなければ「室が決まらない」**として数え、室を推し量らない。
- 同じ (凡例の名前, 区分, 室) が 2 ページ以上に出るときは、数の多いページの数を採る(図をまたいだ
  数えすぎを避ける仮の判断)。ほかのページの数は内訳に「別の図にもある」として残す。
- 凡例の名前が決まらない記号(「凡例に無い」・空)は行にせず、件数だけ数える。
- 出力は `intake/ai_reading.py` の答案と同じ行の形。本番の経路はこれを AI の答案の後ろに足す。
  **確かさは上げない。**
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: 室に置く距離(画素、幅 2000 画素の画像)。K-51 3 節と同じ。仮の判断。
ROOM_DISTANCE_PX = 200.0

#: 室名が届かなかった記号の場所。
NO_ROOM = "室が決まらない"

#: 凡例の名前が決まらない印。
NOT_IN_LEGEND = "凡例に無い"

#: 数え上げの単位。
UNIT = "個"

#: 行の読み手の欄に書く名前。
READER = "K-51 周2 の位置つきの読み+記号の名前づけ(機械が室ごとに数えた)"


@dataclass(frozen=True)
class CountedRow:
    """(凡例の名前, 区分, 室) 1 つぶんの数。"""

    name: str
    kind: str
    room: str
    quantity: int
    page: int
    element_ids: tuple[str, ...]
    other_pages: tuple[tuple[int, int], ...] = ()

    def as_answer_row(self) -> dict[str, Any]:
        """`intake/ai_reading.py` の答案の行の形。"""
        formula = f"{self.page} ページの記号を機械が数えた: {self.quantity} {UNIT}"
        if self.other_pages:
            others = "、".join(f"{p} ページ {n} {UNIT}" for p, n in self.other_pages)
            formula += f"(別の図にもある: {others}。多いほうのページの数を採った)"
        return {
            "工事": f"{self.name}({self.kind})",
            "場所": self.room,
            "数量": self.quantity,
            "単位": UNIT,
            "式": formula,
            "根拠": "要素 " + "・".join(self.element_ids),
        }


@dataclass
class CountResult:
    rows: list[CountedRow] = field(default_factory=list)
    without_name: int = 0
    without_room: int = 0
    unlabeled: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "行": len(self.rows),
            "数えた記号": sum(r.quantity for r in self.rows),
            "室が決まらない記号": self.without_room,
            "凡例の名前が決まらない記号(行にしていない)": self.without_name,
            "名前づけの無い記号(行にしていない)": self.unlabeled,
        }

    def as_reading(self) -> dict[str, Any]:
        """本番の経路にそのまま渡せる答案の形。"""
        return {
            "行": [r.as_answer_row() for r in self.rows],
            "ページごとの所要": [],
            "数え上げ": self.summary(),
        }


def _center(box: Iterable[float]) -> tuple[float, float]:
    x0, y0, x1, y1 = list(box)
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def place_in_room(
    box: Iterable[float],
    rooms: list[tuple[str, tuple[float, float, float, float]]],
    max_distance: float = ROOM_DISTANCE_PX,
) -> str:
    """記号の四角を、中心がいちばん近い室名の室に置く。届かなければ ``NO_ROOM``。"""
    cx, cy = _center(box)
    best, best_d = NO_ROOM, max_distance
    for name, rbox in rooms:
        rx, ry = _center(rbox)
        d = math.hypot(cx - rx, cy - ry)
        if d <= best_d:
            best, best_d = name, d
    return best


def count_symbols(
    reading: Mapping[str, Any],
    naming: Mapping[str, Any],
    max_distance: float = ROOM_DISTANCE_PX,
) -> CountResult:
    """位置つきの読み(``{"ページ": [{"ページ", "要素"}]}``)と名前づけ
    (``{"記号": [{"id", "凡例の名前", "区分"}], "室名": [{"id", "室名"}]}``)から数える。"""
    labels = {s["id"]: s for s in naming.get("記号", []) if s.get("id")}
    room_names = {r["id"]: str(r.get("室名") or "").strip() for r in naming.get("室名", []) if r.get("id")}
    result = CountResult()
    per_page: dict[tuple[str, str, str], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for page in reading.get("ページ", []):
        pno = int(page["ページ"])
        elements = page.get("要素", [])
        rooms = [
            (room_names[e["id"]], tuple(e["位置"]))
            for e in elements
            if e.get("id") in room_names and room_names[e["id"]] and e.get("位置")
        ]
        for e in elements:
            if e.get("種類") != "記号" or not e.get("位置"):
                continue
            label = labels.get(e.get("id"))
            if label is None:
                result.unlabeled += 1
                continue
            name = str(label.get("凡例の名前") or "").strip()
            if not name or name == NOT_IN_LEGEND:
                result.without_name += 1
                continue
            kind = str(label.get("区分") or "").strip() or "不明"
            room = place_in_room(e["位置"], rooms, max_distance)
            if room == NO_ROOM:
                result.without_room += 1
            per_page[(name, kind, room)][pno].append(e["id"])
    for (name, kind, room), pages in per_page.items():
        ordered = sorted(pages.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        top_page, ids = ordered[0]
        result.rows.append(
            CountedRow(
                name=name,
                kind=kind,
                room=room,
                quantity=len(ids),
                page=top_page,
                element_ids=tuple(ids),
                other_pages=tuple((p, len(i)) for p, i in ordered[1:]),
            )
        )
    result.rows.sort(key=lambda r: (r.name, r.kind, r.room))
    return result
