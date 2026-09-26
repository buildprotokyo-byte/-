"""改装平面の**色つきの注記**を 1 行ずつ読む(K-42)。

なぜ読むのか
------------
K-41 周 6(`docs/k41_loop_round6_plan_notes_criteria.md`)で、改装平面の赤・青の
文字の行(「床見切新設」「給気口交換」など)を注記の行にすると、機械の一本道が
当てていない正解を 19 件当てた。黒い文字の囮は 1 件だった。**色で工事を
読み分けていることが効いている。**この読み方を本番の入口の道の 1 つにする。

測ったときの試作(リポジトリの外)は、図面 1 枚の座標(図枠・早見表の x)と
室名の位置を書き込んでいた。ここでは**どの図面でも同じ規則で**線を引く:

- 読むページ … 表題欄(`benchmarks/page_geometry`)の語に「改装平面」と「図」が
  入っているページ。呼ぶ側がページを直接渡してもよい。
- 図枠 … ページの幅(高さ)の 6 割より長い線で囲まれた範囲。無ければページ全体。
  表題欄は `page_geometry.in_drawing` で外す。
- 室ごとの仕上の早見表 … 「天井:」「床:」のような**部位と区切りの文字の行**が
  縦に並んだ所を早見表とみなし、その右の値(赤い「貼替」など)と、上の見出し
  (室名)を外す。内装仕上表と同じ中身なので、ここで読むと二重になる。

この道がしないこと
------------------
- **数を作らない。**数量は、注記の文字そのものに数が刷られているときだけ
  (「可動棚6枚」→ 6 枚)。刷られていなければ ``None``。**0 にしない。**
- **同じ注記が何回出たかを数量にしない。**「同じ注記の数」は採点する側が
  見るための知らせで、数量の欄には入れない。
- **何も確定させない。**色の意味(赤=新設/交換、青=移設/脱着)は凡例が
  名乗るもので、**1 案件の凡例でしか確かめていない候補**である。
- **少し外れたものを黙って捨てない**(2026-09-26 おーちゃんの指示)。色が赤・青に
  近いが許容の外の行は「候補(近いが外れ)」に理由つきで残す。見積の行にはしない。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pymupdf

# ---------------------------------------------------------------------------
# 色の許容
# ---------------------------------------------------------------------------

#: 赤・青とみなす許容。**主の成分がこれ以上、ほかの成分がこれ以下。**
#: 0xff0000 ちょうどだけを見ると、刷り方の違い(0xee1111 など)で落ちる。
COLOUR_MAIN_MIN = 0.75
COLOUR_OTHER_MAX = 0.25

#: 近いが許容の外とみなす範囲。**ここに入ったものは捨てずに「候補(近いが外れ)」へ。**
COLOUR_NEAR_MAIN_MIN = 0.5
COLOUR_NEAR_OTHER_MAX = 0.5
COLOUR_NEAR_MARGIN = 0.3
"""主の成分が、ほかの成分より少なくともこれだけ大きいこと(桃色は赤に近い、紫は近くない)。"""

RED = "赤"
BLUE = "青"

#: 色 → 工事の別。**凡例 n(黒=既存、青=移設・脱着、赤=交換・新設)が名乗る意味で、
#: 1 案件の凡例でしか確かめていない候補。**凡例の文をここで確かめてはいない。
COLOUR_KIND: dict[str, str] = {RED: "新設/交換", BLUE: "移設/脱着"}

NEAR_MISS_LABEL = "候補(近いが外れ)"


def colour_hex(rgb: Sequence[float]) -> str:
    r, g, b = (max(0, min(255, round(float(v) * 255))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_of_packed(packed: int) -> tuple[float, float, float]:
    return (((packed >> 16) & 0xFF) / 255.0, ((packed >> 8) & 0xFF) / 255.0, (packed & 0xFF) / 255.0)


def classify_colour(rgb: Sequence[float] | None) -> tuple[str | None, str | None]:
    """``(許容に入った色, 近いが外れた色)``。どちらかは必ず ``None``。

    黒・灰・緑などは両方 ``None``(このモジュールの対象ではない)。
    """
    if rgb is None:
        return None, None
    r, g, b = (float(v) for v in rgb[:3])
    for name, main, others in ((RED, r, (g, b)), (BLUE, b, (r, g))):
        if main >= COLOUR_MAIN_MIN and max(others) <= COLOUR_OTHER_MAX:
            return name, None
    for name, main, others in ((RED, r, (g, b)), (BLUE, b, (r, g))):
        if (
            main >= COLOUR_NEAR_MAIN_MIN
            and max(others) <= COLOUR_NEAR_OTHER_MAX
            and main - max(others) >= COLOUR_NEAR_MARGIN
        ):
            return None, name
    return None, None


# ---------------------------------------------------------------------------
# ページを選ぶ・図枠
# ---------------------------------------------------------------------------


def nfkc(text: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def title_words(page: pymupdf.Page) -> list[str]:
    """表題欄(表示の向きで下端)の語。"""
    from benchmarks import page_geometry

    return [nfkc(w[4]) for w in page.get_text("words") if not page_geometry.in_drawing(page, w[:4])]


def pages_with_title(pdf_path: str | Path, keyword: str) -> list[int]:
    """表題欄の語に ``keyword`` と「図」が入っているページ(1 始まり)。"""
    out: list[int] = []
    with pymupdf.open(pdf_path) as doc:
        for number in range(1, doc.page_count + 1):
            words = title_words(doc.load_page(number - 1))
            if any(keyword in w and "図" in w for w in words):
                out.append(number)
    return out


#: 図枠とみなす線の長さ(ページの幅・高さに対する割合)。
FRAME_LINE_RATIO = 0.6


def drawing_frame(page: pymupdf.Page) -> pymupdf.Rect:
    """図枠(回転前の座標)。**長い線が見つからなければページ全体。**"""
    width, height = page.rect.width, page.rect.height
    xs: list[float] = []
    ys: list[float] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1 and abs(a.x - b.x) >= FRAME_LINE_RATIO * width:
                    ys.append(a.y)
                elif abs(a.x - b.x) < 1 and abs(a.y - b.y) >= FRAME_LINE_RATIO * height:
                    xs.append(a.x)
            elif item[0] == "re":
                rect = item[1]
                if rect.width >= FRAME_LINE_RATIO * width and rect.height >= FRAME_LINE_RATIO * height:
                    xs.extend((rect.x0, rect.x1))
                    ys.extend((rect.y0, rect.y1))
    x0, x1 = (min(xs), max(xs)) if len(xs) >= 2 and max(xs) - min(xs) > 1 else (0.0, width)
    y0, y1 = (min(ys), max(ys)) if len(ys) >= 2 and max(ys) - min(ys) > 1 else (0.0, height)
    return pymupdf.Rect(x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# 室ごとの仕上の早見表
# ---------------------------------------------------------------------------

#: 早見表の行の頭(部位と区切り)。NFKC で全角のコロンは ":" になる。
QUICK_TABLE_ROW = re.compile(r"^(天井|壁|床|巾木|幅木|廻り縁|回り縁|腰壁)[:]")


@dataclass(frozen=True)
class TextLine:
    text: str
    bbox: tuple[float, float, float, float]
    rgb: tuple[float, float, float] | None

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    @property
    def height(self) -> float:
        return max(1.0, self.bbox[3] - self.bbox[1])


def text_lines(page: pymupdf.Page) -> list[TextLine]:
    """文字の層の行を、**同じ色の span が続く所ごと**に切ったもの。

    「天井:」(黒)と「貼替」(赤)が 1 行にまとまって返ることがあるので、
    行の色を 1 つに決めずに、色が変わる所で切る。
    """
    out: list[TextLine] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            runs: list[tuple[int, list[dict]]] = []
            for span in line["spans"]:
                packed = int(span["color"])
                if runs and runs[-1][0] == packed:
                    runs[-1][1].append(span)
                else:
                    runs.append((packed, [span]))
            for packed, spans in runs:
                text = "".join(span["text"] for span in spans)
                if not nfkc(text):
                    continue
                out.append(
                    TextLine(
                        text=text.strip(),
                        bbox=(
                            min(float(span["bbox"][0]) for span in spans),
                            min(float(span["bbox"][1]) for span in spans),
                            max(float(span["bbox"][2]) for span in spans),
                            max(float(span["bbox"][3]) for span in spans),
                        ),
                        rgb=rgb_of_packed(packed),
                    )
                )
    return out


def quick_table_regions(lines: Iterable[TextLine]) -> list[tuple[float, float, float, float]]:
    """「天井:」のような行が縦に 2 つ以上並んだ所の外形(見出しと値の欄を含む)。

    行の高さを h として、右へ 8h(値の欄)、上へ 3h(室名の見出し)広げる。
    **座標の定数は持たない。**
    """
    heads = sorted(
        (line for line in lines if QUICK_TABLE_ROW.match(nfkc(line.text))),
        key=lambda line: (round(line.bbox[0]), line.bbox[1]),
    )
    groups: list[list[TextLine]] = []
    for line in heads:
        for group in groups:
            last = group[-1]
            if abs(last.bbox[0] - line.bbox[0]) <= 3.0 and 0 <= line.bbox[1] - last.bbox[1] <= 3.0 * line.height:
                group.append(line)
                break
        else:
            groups.append([line])
    regions: list[tuple[float, float, float, float]] = []
    for group in groups:
        if len(group) < 2:
            continue
        h = max(line.height for line in group)
        x0 = min(line.bbox[0] for line in group)
        y0 = min(line.bbox[1] for line in group)
        x1 = max(line.bbox[2] for line in group)
        y1 = max(line.bbox[3] for line in group)
        regions.append((x0 - h, y0 - 3.0 * h, x1 + 8.0 * h, y1 + h))
    return regions


def _inside(point: tuple[float, float], rect: Sequence[float]) -> bool:
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


# ---------------------------------------------------------------------------
# 注記に刷られた数
# ---------------------------------------------------------------------------

#: 数を表す助数詞。**刷られた数だけを取る。**「式」は数ではないので入れない。
_COUNT_RE = re.compile(r"(\d+)(枚|個|台|箇所|ヶ所|ケ所|カ所|か所|本|組|基|灯|セット)")
_UNIT_ALIASES = {"ヶ所": "箇所", "ケ所": "箇所", "カ所": "箇所", "か所": "箇所"}


def printed_count(text: str) -> tuple[float | None, str | None, str | None]:
    """注記の文字に刷られた数 ``(数, 単位, 読んだ文字)``。

    刷られていなければ ``(None, None, None)``。**2 つ以上刷られていたら、どれが
    数量かを選ばずに ``None``**(読んだ文字は残す)。
    """
    found = _COUNT_RE.findall(nfkc(text))
    if not found:
        return None, None, None
    if len(found) > 1:
        return None, None, "・".join(n + u for n, u in found)
    number, unit = found[0]
    return float(number), _UNIT_ALIASES.get(unit, unit), number + unit


# ---------------------------------------------------------------------------
# 読む
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColourNote:
    """色つきの注記 1 行。**見積の行の候補で、確定ではない。**"""

    page: int
    text: str
    colour: str
    colour_value: str
    kind: str
    bbox: tuple[float, float, float, float]
    place: str
    place_distance_pt: float | None
    count: float | None
    count_unit: str | None
    count_text: str | None
    same_text_count: int = 1
    """同じ注記(NFKC で空白を除いて同じ文字)がこのページに何行あるか。**数量ではない。**"""
    same_text_places: tuple[str, ...] = ()


@dataclass(frozen=True)
class NearMiss:
    """許容の少し外にあったもの。**捨てずに、理由つきで残す。見積の行にはしない。**"""

    page: int
    text: str
    colour_value: str
    reason: str
    bbox: tuple[float, float, float, float]


@dataclass
class PlanNotesResult:
    notes: list[ColourNote] = field(default_factory=list)
    near_misses: list[NearMiss] = field(default_factory=list)
    recognized: int = 0
    """図枠の中・早見表の外で、赤・青またはそれに近い色の文字の行。"""
    excluded_quick_table: int = 0
    excluded_outside_frame: int = 0
    labels_used: int = 0


_BRACKETS = str.maketrans("", "", "()[]{}")


def label_key(text: str | None) -> str:
    """室名を突き合わせる形。NFKC・空白を除き、**括弧も除く**(「洋室1」と「洋室(1)」を同じに)。

    `app._room_label_positions` は括弧を残す(K-37 の検算はそれで測ってある)ので、
    そちらは変えずに、この道だけ少し緩く突き合わせる。
    """
    return nfkc(text).translate(_BRACKETS)


def room_label_positions(
    pdf_path: str | Path, page_number: int, names: Sequence[str]
) -> dict[str, list[tuple[float, float]]]:
    """室名(仕上表から読んだもの)が図面に刷られた位置(語の中心)。

    仕上表の室名が複数行(「キッチン/ダイニング/リビング」)のときは、行ごとに探す。
    **見つからない室名は空のまま。**位置を作らない。
    """
    wanted = {
        name: {label_key(part) for part in name.split("\n") if label_key(part)} for name in names
    }
    out: dict[str, list[tuple[float, float]]] = {name: [] for name in names}
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            return out
        for x0, y0, x1, y1, word, *_ in doc.load_page(page_number - 1).get_text("words"):
            key = label_key(word)
            for name, parts in wanted.items():
                if key in parts:
                    out[name].append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return out


def nearest_place(
    point: tuple[float, float], labels: Mapping[str, Sequence[tuple[float, float]]]
) -> tuple[str, float | None]:
    best: tuple[str, float | None] = ("", None)
    for name, positions in labels.items():
        for x, y in positions:
            distance = math.hypot(x - point[0], y - point[1])
            if best[1] is None or distance < best[1]:
                best = (name, distance)
    return best


def read_colour_notes(
    pdf_path: str | Path,
    page_number: int,
    room_labels: Mapping[str, Sequence[tuple[float, float]]] | None = None,
) -> PlanNotesResult:
    """1 ページ(1 始まり)の赤・青の注記を読む。

    ``room_labels`` は室名 → 図面の上の中心の位置(このページのもの)。
    **早見表の中にある室名の位置は使わない**(見出しなので、注記の場所ではない)。
    """
    from benchmarks import page_geometry

    result = PlanNotesResult()
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            raise IndexError(f"ページ {page_number} は存在しません")
        page = doc.load_page(page_number - 1)
        frame = drawing_frame(page)
        lines = text_lines(page)
        regions = quick_table_regions(lines)
        labels = {
            name: [p for p in positions if not any(_inside(p, r) for r in regions)]
            for name, positions in (room_labels or {}).items()
        }
        labels = {name: positions for name, positions in labels.items() if positions}
        result.labels_used = sum(len(p) for p in labels.values())

        found: list[tuple[TextLine, str]] = []
        for line in lines:
            colour, near = classify_colour(line.rgb)
            if colour is None and near is None:
                continue
            centre = line.centre
            if not frame.contains(pymupdf.Point(*centre)) or not page_geometry.in_drawing(page, line.bbox):
                result.excluded_outside_frame += 1
                continue
            if any(_inside(centre, r) for r in regions):
                result.excluded_quick_table += 1
                continue
            result.recognized += 1
            if colour is None:
                result.near_misses.append(
                    NearMiss(
                        page=page_number,
                        text=line.text,
                        colour_value=colour_hex(line.rgb or (0, 0, 0)),
                        reason=f"色が{near}に近いが許容(主の成分 {COLOUR_MAIN_MIN} 以上・"
                        f"ほかの成分 {COLOUR_OTHER_MAX} 以下)の外",
                        bbox=line.bbox,
                    )
                )
                continue
            found.append((line, colour))

    placed: list[tuple[TextLine, str, str, float | None]] = []
    for line, colour in found:
        place, distance = nearest_place(line.centre, labels)
        placed.append((line, colour, place, distance))
    by_text: dict[str, list[str]] = {}
    for line, _, place, _ in placed:
        by_text.setdefault(nfkc(line.text), []).append(place)
    for line, colour, place, distance in placed:
        count, unit, count_text = printed_count(line.text)
        same = by_text[nfkc(line.text)]
        result.notes.append(
            ColourNote(
                page=page_number,
                text=line.text,
                colour=colour,
                colour_value=colour_hex(line.rgb or (0, 0, 0)),
                kind=COLOUR_KIND[colour],
                bbox=tuple(round(v, 1) for v in line.bbox),  # type: ignore[arg-type]
                place=place,
                place_distance_pt=round(distance, 1) if distance is not None else None,
                count=count,
                count_unit=unit,
                count_text=count_text,
                same_text_count=len(same),
                same_text_places=tuple(sorted({p for p in same if p})),
            )
        )
    return result
