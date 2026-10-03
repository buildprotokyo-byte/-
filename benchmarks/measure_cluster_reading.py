"""周1「まとまりで読む」の測定。

基準は `docs/loop_round1_cluster_reading_criteria.md`(**測る前にコミット済み**)。

**この道具は本番の経路に繋がない。**読むだけで、何も確定させない。
**図面の中身(文字そのもの)は返さない。件数だけを返す。**

やること:

1. 1 ページの**文字の断片**と**描画命令**を取る
2. **文字の高さの N 倍**を単位に、近いものを 1 つの**まとまり**にする
   (紙の上の長さで持たないので、**縮尺が変わっても同じしきいで動く**)
3. まとまりごとに、4 つの意味のどれかを**この冊自身で裏を取って**当てる
4. 同じ分母で、**単独で当てにいったとき**の件数も出す
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

#: 基準の追記1。**結果を見ずに決めた値。動かさない。**
NEIGHBOUR_HEIGHTS = 2.0

#: 寸法の裏取りの許容差(基準 3 節)。
DIMENSION_TOLERANCE = 0.05

MM_PER_POINT = 25.4 / 72.0

#: 基準の追記3。**まとまりの箱の対角が、文字の高さのこの倍数を超えたら、それ以上繋げない。**
#: 連鎖(A と B が近く、B と C が近いだけで A と C が同じまとまりになる)を止めるため。
MAX_EXTENT_HEIGHTS = 30.0

#: 基準の追記4。**この文字数以下の語は、表の升目に当たっても裏が取れたとしない。**
MIN_TABLE_MATCH_LENGTH = 3

#: 通り芯の円とみなす形の条件(基準 3 節「円の内側にあり、その円が直線の端に付いている」)。
CIRCLE_ASPECT = (0.8, 1.25)
CIRCLE_HEIGHTS = (1.0, 4.0)
CIRCLE_ENDPOINT_SLACK = 1.5

_NUMBER = re.compile(r"^\d{2,6}$")
_DROP = re.compile(r"[\s・,、()（）「」【】\-ー－]")


def normalize(text: str) -> str:
    """そろえ方は K-33 と同じ。NFKC → 記号を落とす → 小文字化。"""
    return _DROP.sub("", unicodedata.normalize("NFKC", text)).lower()


Box = tuple[float, float, float, float]
Point = tuple[float, float]


@dataclass
class Word:
    box: Box
    text: str

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def centre(self) -> Point:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


@dataclass
class Cluster:
    """近くにある文字・線・円を 1 つにまとめたもの。"""

    words: list[Word] = field(default_factory=list)
    segments: list[tuple[Point, Point]] = field(default_factory=list)
    circles: list[Box] = field(default_factory=list)

    @property
    def median_height(self) -> float:
        return statistics.median([w.height for w in self.words]) if self.words else 0.0

    @property
    def box(self) -> Box:
        xs = [v for w in self.words for v in (w.box[0], w.box[2])]
        ys = [v for w in self.words for v in (w.box[1], w.box[3])]
        return (min(xs), min(ys), max(xs), max(ys))


def _box_gap(a: Box, b: Box) -> float:
    """2 つの箱のいちばん近いところの距離。重なっていれば 0。"""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _point_to_box(p: Point, b: Box) -> float:
    dx = max(b[0] - p[0], p[0] - b[2], 0.0)
    dy = max(b[1] - p[1], p[1] - b[3], 0.0)
    return math.hypot(dx, dy)


def collect_words(page: pymupdf.Page) -> list[Word]:
    return [Word((w[0], w[1], w[2], w[3]), w[4]) for w in page.get_text("words")]


def collect_shapes(page: pymupdf.Page) -> tuple[list[tuple[Point, Point]], list[Box]]:
    """まっすぐな線と、円らしい閉じた形を取り出す。

    **円は「縦横の比がほぼ 1 で、文字の高さの 1〜4 倍の大きさ」で見る。**
    位置や割合で覚えない(K-30)ので、ページごとの比で持つ。
    """
    segments: list[tuple[Point, Point]] = []
    circles: list[Box] = []
    for item in page.get_drawings():
        kinds = {seg[0] for seg in item["items"]}
        for seg in item["items"]:
            if seg[0] == "l":
                segments.append(((seg[1].x, seg[1].y), (seg[2].x, seg[2].y)))
        if "c" in kinds and "l" not in kinds:
            r = item["rect"]
            w, h = r.x1 - r.x0, r.y1 - r.y0
            if h > 0 and CIRCLE_ASPECT[0] <= w / h <= CIRCLE_ASPECT[1]:
                circles.append((r.x0, r.y0, r.x1, r.y1))
    return segments, circles


def build_clusters(
    words: list[Word], segments, circles, *, heights: float,
    max_extent: float | None = None,
) -> list[Cluster]:
    """文字の高さを単位に、近いものを 1 つのまとまりにする。

    `max_extent` を渡すと、**まとまりの箱の対角が文字の高さのその倍数を超える
    繋ぎ方を見送る**(基準の追記3。連鎖で紙の半分が 1 つになるのを止める)。
    """
    parent = list(range(len(words)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    boxes = [w.box for w in words]

    def merged_box(a: Box, b: Box) -> Box:
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    candidates: list[tuple[float, int, int]] = []
    for i, a in enumerate(words):
        for j in range(i + 1, len(words)):
            b = words[j]
            limit = (a.height + b.height) / 2 * heights
            gap = _box_gap(a.box, b.box)
            if gap <= limit:
                candidates.append((gap, i, j))
    # **近いものから順に繋ぐ。**上限に当たったら、その組だけを見送る(決まった順で動く)。
    candidates.sort()
    for _, i, j in candidates:
        a, b = find(i), find(j)
        if a == b:
            continue
        box = merged_box(boxes[a], boxes[b])
        if max_extent is not None:
            diagonal = math.hypot(box[2] - box[0], box[3] - box[1])
            height = (words[i].height + words[j].height) / 2
            if height > 0 and diagonal > height * max_extent:
                continue
        union(i, j)
        boxes[find(i)] = box

    groups: dict[int, Cluster] = {}
    for i, w in enumerate(words):
        groups.setdefault(find(i), Cluster()).words.append(w)
    clusters = list(groups.values())

    for cluster in clusters:
        limit = cluster.median_height * heights
        box = cluster.box
        for seg in segments:
            if min(_point_to_box(seg[0], box), _point_to_box(seg[1], box)) <= limit:
                cluster.segments.append(seg)
        for circle in circles:
            if _box_gap(circle, box) <= limit:
                cluster.circles.append(circle)
    return clusters


def table_cell_texts(doc: pymupdf.Document, keyword: str) -> set[str]:
    """その語を含むページの、罫線の表の升目の中身。**見出しは当てに行かない。**"""
    out: set[str] = set()
    for page in doc:
        if keyword not in page.get_text():
            continue
        try:
            tables = page.find_tables()
        except Exception:  # noqa: BLE001 - 表が取れないページは飛ばす
            continue
        for table in tables:
            for row in table.extract():
                for cell in row:
                    if cell:
                        value = normalize(str(cell))
                        if value:
                            out.add(value)
    return out


def _segment_length(seg: tuple[Point, Point]) -> float:
    return math.hypot(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1])


def meaning_of_word(
    word: Word,
    cluster: Cluster | None,
    *,
    mm_per_point: float,
    door_cells: set[str],
    finish_cells: set[str],
    segments: list[tuple[Point, Point]],
    min_table_length: int = 1,
) -> set[str]:
    """その文字に当たった意味を返す。**裏が取れたものだけ。**"""
    found: set[str] = set()
    text = normalize(word.text)
    if not text:
        return found
    # 追記4。**1〜2 文字の語は表のどこかに必ず出てくるので、証拠にしない。**
    if len(text) >= min_table_length:
        if text in door_cells:
            found.add("建具の記号")
        if text in finish_cells:
            found.add("室名")
    if cluster is None:
        return found
    if _NUMBER.match(text):
        value = float(text)
        for seg in cluster.segments:
            length_mm = _segment_length(seg) * mm_per_point
            if length_mm > 0 and abs(length_mm - value) / value <= DIMENSION_TOLERANCE:
                found.add("寸法")
                break
    cx, cy = word.centre
    for circle in cluster.circles:
        if not (circle[0] <= cx <= circle[2] and circle[1] <= cy <= circle[3]):
            continue
        radius = max(circle[2] - circle[0], circle[3] - circle[1]) / 2
        ox, oy = (circle[0] + circle[2]) / 2, (circle[1] + circle[3]) / 2
        for seg in segments:
            for end in seg:
                if math.hypot(end[0] - ox, end[1] - oy) <= radius * CIRCLE_ENDPOINT_SLACK:
                    found.add("通り芯")
                    break
            if "通り芯" in found:
                break
        if "通り芯" in found:
            break
    return found


def measure_page(
    pdf_path: str | Path,
    page_index: int,
    *,
    denominator: float,
    heights: float = NEIGHBOUR_HEIGHTS,
    max_extent: float | None = None,
    min_table_length: int = 1,
) -> dict[str, Any]:
    """1 ページを、まとまりで読んだときと単独で読んだときの両方で測る。"""
    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        words = collect_words(page)
        segments, circles = collect_shapes(page)
        door_cells = table_cell_texts(doc, "建具")
        finish_cells = table_cell_texts(doc, "仕上")

    clusters = build_clusters(
        words, segments, circles, heights=heights, max_extent=max_extent
    )
    owner: dict[int, Cluster] = {}
    for cluster in clusters:
        for w in cluster.words:
            owner[id(w)] = cluster

    mm_per_point = MM_PER_POINT * denominator
    kinds = ("寸法", "建具の記号", "室名", "通り芯")
    tally: dict[str, Any] = {
        "ページ番号": page_index + 1,
        "まとまりの上限(文字の高さの倍数)": max_extent,
        "表に当てる最短の文字数": min_table_length,
        "縮尺の分母": denominator,
        "文字の断片": len(words),
        "まっすぐな線": len(segments),
        "円らしい形": len(circles),
        "まとまり": len(clusters),
        "まとまりで": {k: 0 for k in kinds},
        "単独で": {k: 0 for k in kinds},
        "まとまりで裏が取れた件数": 0,
        "単独で裏が取れた件数": 0,
        "2つ当たったまとまり": 0,
        "決まったまとまり": 0,
    }

    per_cluster: dict[int, set[str]] = {}
    for w in words:
        cluster = owner.get(id(w))
        grouped = meaning_of_word(
            w, cluster, mm_per_point=mm_per_point, door_cells=door_cells,
            finish_cells=finish_cells, segments=segments,
            min_table_length=min_table_length,
        )
        alone = meaning_of_word(
            w, None, mm_per_point=mm_per_point, door_cells=door_cells,
            finish_cells=finish_cells, segments=segments,
            min_table_length=min_table_length,
        )
        for k in grouped:
            tally["まとまりで"][k] += 1
        for k in alone:
            tally["単独で"][k] += 1
        if grouped:
            tally["まとまりで裏が取れた件数"] += 1
        if alone:
            tally["単独で裏が取れた件数"] += 1
        if cluster is not None and grouped:
            per_cluster.setdefault(id(cluster), set()).update(grouped)

    for found in per_cluster.values():
        if len(found) > 1:
            # 基準の線3。**2 つ当たったまとまりは「決まらない」にする。**
            tally["2つ当たったまとまり"] += 1
        else:
            tally["決まったまとまり"] += 1
    return tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True, help="ページ番号:縮尺の分母 をカンマ区切りで")
    parser.add_argument("--heights", type=float, default=NEIGHBOUR_HEIGHTS)
    parser.add_argument("--max-extent", type=float, default=None)
    parser.add_argument("--min-table-length", type=int, default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    result = {"しきい(文字の高さの倍数)": args.heights, "ページごと": []}
    for spec in args.pages.split(","):
        number, denominator = spec.split(":")
        result["ページごと"].append(
            measure_page(args.pdf, int(number) - 1, denominator=float(denominator),
                         heights=args.heights, max_extent=args.max_extent,
                         min_table_length=args.min_table_length)
        )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
