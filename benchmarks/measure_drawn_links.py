"""周1-2「作図者が書いた繋がりで切る」の測定。

基準は `docs/loop_round1b_drawn_link_criteria.md`(**測る前にコミット済み**)。

周1 は**近さ**でまとまりを切った。**この道具は、作図者が明示的に書いた繋がり
(寸法線の端の印)で切る。**それだけが違う。

**本番の経路には繋がない。図面の中身は返さない。件数だけを返す。**
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from benchmarks.measure_cluster_reading import (
    DIMENSION_TOLERANCE,
    MM_PER_POINT,
    Word,
    _NUMBER,
    collect_words,
    normalize,
)

#: 端の印とみなす塗りつぶしの大きさの上限(ポイント)。**この紙の実測から決めた値ではなく、
#: 「文字より小さい」ことを表す上限として先に決めた。**
MARK_MAX_SIZE = 6.0

#: 印が線の端に付いているとみなす距離(ポイント)。印の大きさと、この値の大きいほう。
MARK_SNAP = 3.0

#: 線と数字を組にする距離の上限。**その数字の文字の高さの倍数**(基準 3 節)。
PAIR_HEIGHTS = 3.0

Point = tuple[float, float]


@dataclass(frozen=True)
class Mark:
    centre: Point
    size: float
    round_shape: bool


@dataclass(frozen=True)
class Segment:
    a: Point
    b: Point

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def middle(self) -> Point:
        return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2)


def collect_marks_and_segments(page: pymupdf.Page) -> tuple[list[Mark], list[Segment]]:
    """塗りつぶしの小さい印と、まっすぐな線を取り出す。

    **印の形を「丸」と決めつけない**(K-30)。塗りつぶしで小さいものを印として見て、
    丸かどうかは**結果として数える。**
    """
    marks: list[Mark] = []
    segments: list[Segment] = []
    for item in page.get_drawings():
        kinds = {s[0] for s in item["items"]}
        for s in item["items"]:
            if s[0] == "l":
                segments.append(Segment((s[1].x, s[1].y), (s[2].x, s[2].y)))
        rect = item["rect"]
        size = max(rect.x1 - rect.x0, rect.y1 - rect.y0)
        if item["type"] in ("f", "fs") and 0 < size <= MARK_MAX_SIZE:
            marks.append(
                Mark(((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2), size, "c" in kinds)
            )
    return marks, segments


def _marked_end(point: Point, marks: list[Mark]) -> bool:
    return any(math.dist(point, m.centre) <= max(MARK_SNAP, m.size) for m in marks)


def dimension_lines(segments: list[Segment], marks: list[Mark]) -> list[Segment]:
    """**両端に印がある直線。**作図者が「ここからここまで」と書いたもの。"""
    return [s for s in segments if _marked_end(s.a, marks) and _marked_end(s.b, marks)]


def pair_numbers(lines: list[Segment], words: list[Word]) -> dict[int, list[Word]]:
    """線 1 本に、**中点にいちばん近い数字 1 つ**を組にする。

    返すのは線の番号ごとの数字の一覧。**2 つ以上入った線は「決まらない」に数える。**
    """
    numbers = [w for w in words if _NUMBER.match(normalize(w.text))]
    pairs: dict[int, list[Word]] = {}
    for word in numbers:
        best: tuple[float, int] | None = None
        for index, line in enumerate(lines):
            limit = word.height * PAIR_HEIGHTS
            distance = math.dist(word.centre, line.middle)
            if distance <= limit and (best is None or distance < best[0]):
                best = (distance, index)
        if best is not None:
            pairs.setdefault(best[1], []).append(word)
    return pairs


def measure_page(
    pdf_path: str | Path, page_index: int, *, denominator: float, seed: int = 20260925
) -> dict[str, Any]:
    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        words = collect_words(page)
        marks, segments = collect_marks_and_segments(page)

    lines = dimension_lines(segments, marks)
    pairs = pair_numbers(lines, words)
    mm_per_point = MM_PER_POINT * denominator
    rng = random.Random(seed)

    real_hits = 0
    fake_hits = 0
    decided = 0
    undecided = 0
    for index, matched in pairs.items():
        if len(matched) > 1:
            undecided += 1
            continue
        decided += 1
        word = matched[0]
        text = normalize(word.text)
        value = float(text)
        length_mm = lines[index].length * mm_per_point
        if value > 0 and abs(length_mm - value) / value <= DIMENSION_TOLERANCE:
            real_hits += 1
        fake = float(rng.randint(10 ** (len(text) - 1), 10 ** len(text) - 1))
        if abs(length_mm - fake) / fake <= DIMENSION_TOLERANCE:
            fake_hits += 1

    return {
        "ページ番号": page_index + 1,
        "縮尺の分母": denominator,
        "文字の断片": len(words),
        "まっすぐな線": len(segments),
        "小さい塗りつぶしの印": len(marks),
        "そのうち丸い形": sum(1 for m in marks if m.round_shape),
        "両端に印がある線": len(lines),
        "数字が組になった線": len(pairs),
        "数字が2つ以上ついた線": undecided,
        "決まった線": decided,
        "本物が当たった件数": real_hits,
        "囮でも当たった件数": fake_hits,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True, help="ページ番号:縮尺の分母 をカンマ区切りで")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    result = {"ページごと": []}
    for spec in args.pages.split(","):
        number, denominator = spec.split(":")
        result["ページごと"].append(
            measure_page(args.pdf, int(number) - 1, denominator=float(denominator))
        )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# 周1-3「引き出し線で切る」
# 基準は `docs/loop_round1c_leader_line_criteria.md`(**測る前にコミット済み**)
# ---------------------------------------------------------------------------

#: 引き出し線の、印の無い端と文字を組にする距離。**その文字の高さの倍数。**
LEADER_HEIGHTS = 1.5


def leader_lines(segments: list[Segment], marks: list[Mark]) -> list[Segment]:
    """**ちょうど片方の端にだけ印がある直線。**

    両端に印があるもの(寸法線、周1-2)と、どちらにも無いものは外す。
    返す線は、**印のあるほうを `a`、無いほうを `b`** に揃える。
    """
    out: list[Segment] = []
    for segment in segments:
        marked_a = _marked_end(segment.a, marks)
        marked_b = _marked_end(segment.b, marks)
        if marked_a == marked_b:
            continue
        out.append(segment if marked_a else Segment(segment.b, segment.a))
    return out


def pair_words(lines: list[Segment], words: list["Word"]) -> dict[int, list["Word"]]:
    """線の**印の無いほうの端**に、文字の高さの `LEADER_HEIGHTS` 倍以内の文字を組にする。"""
    from benchmarks.measure_cluster_reading import _point_to_box

    pairs: dict[int, list[Word]] = {}
    for index, line in enumerate(lines):
        for word in words:
            if _point_to_box(line.b, word.box) <= word.height * LEADER_HEIGHTS:
                pairs.setdefault(index, []).append(word)
    return pairs


def _shift(line: Segment, page: tuple[float, float], rng: random.Random) -> Segment:
    """**向きと長さを保ったまま**、紙の上のでたらめな位置へ動かす(囮)。"""
    dx = line.b[0] - line.a[0]
    dy = line.b[1] - line.a[1]
    x = rng.uniform(0.0, max(page[0] - abs(dx), 1.0))
    y = rng.uniform(0.0, max(page[1] - abs(dy), 1.0))
    a = (x, y)
    return Segment(a, (a[0] + dx, a[1] + dy))


def measure_leader_page(
    pdf_path: str | Path, page_index: int, *, seed: int = 20260925
) -> dict[str, Any]:
    from benchmarks.measure_cluster_reading import (
        MIN_TABLE_MATCH_LENGTH,
        table_cell_texts,
    )

    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        words = collect_words(page)
        marks, segments = collect_marks_and_segments(page)
        size = (page.rect.x1 - page.rect.x0, page.rect.y1 - page.rect.y0)
        cells = table_cell_texts(doc, "建具") | table_cell_texts(doc, "仕上")

    lines = leader_lines(segments, marks)
    pairs = pair_words(lines, words)

    in_table = 0
    undecided = 0
    for matched in pairs.values():
        if len(matched) > 1:
            undecided += 1
            continue
        text = normalize(matched[0].text)
        if len(text) >= MIN_TABLE_MATCH_LENGTH and text in cells:
            in_table += 1

    rng = random.Random(seed)
    shifted = [_shift(line, size, rng) for line in lines]
    fake_pairs = pair_words(shifted, words)
    fake_in_table = 0
    for matched in fake_pairs.values():
        if len(matched) > 1:
            continue
        text = normalize(matched[0].text)
        if len(text) >= MIN_TABLE_MATCH_LENGTH and text in cells:
            fake_in_table += 1

    return {
        "ページ番号": page_index + 1,
        "文字の断片": len(words),
        "小さい塗りつぶしの印": len(marks),
        "片端だけに印がある線": len(lines),
        "文字が組になった線": len(pairs),
        "文字が2つ以上ついた線": undecided,
        "表の升目にあった文字": in_table,
        "囮で文字が組になった線": len(fake_pairs),
        "囮で表の升目にあった文字": fake_in_table,
    }
