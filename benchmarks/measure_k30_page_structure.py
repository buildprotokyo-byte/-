"""K-30 ①ページの構造: 図面が自分で輪郭線を引いているかを数える。

基準は `docs/k30_page_structure_criteria.md`(**測る前にコミット済み**)。
構えは `docs/reading_stance.md`、製図の決まりの原文は `docs/drafting_rules_reference.md`。

**なぜ数えるのか**

原文 1 節(JIS Z 8311)は「ページで一番大きい矩形は必ず外枠である」と書いている。
だが **2026-09-24 12:01 の決まり**により、**宣言されている書き方は、実際の図面で
使われているかを数えてから根拠にする。**だからこの道具は、実装を書く前に
「この案件は本当に輪郭線を引いているか」だけを数える。

**この道具がしないこと**

- **本番の経路を一切通らない。**`axes/` にも `intake/` にも何も足していない。
- **図面の文字を 1 文字も出さない。**図面の名前は読むが、出すのは
  「読めたか」の真偽と、振り分けた**種類の名前**だけである。

実行::

    .venv/bin/python -m benchmarks.measure_k30_page_structure --pdf <図面> [--json <出力>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from axes.image_axis.candidate_ledger import (  # noqa: E402
    NORM,
    LedgerSettings,
    _page_words,
    read_title_block,
)

#: 同じ座標とみなす幅(pt)。**割合ではなく、線の引き方のゆらぎを吸収する値。**
#: 図面の枠は太線を細い線の重ね描きで表すため、1pt 弱ずれた線が並ぶ。
SNAP = 1.5

#: 水平・垂直とみなす幅(pt)。これを超えて傾いた線は斜めの線として捨てる。
STRAIGHT = 0.6

#: 枠として採る面積の帯(基準の線 1)。**この 2 つは採否の線であって、
#: 実装のしきい値ではない。**実装では使わない。
FRAME_MIN_RATIO = 0.70
FRAME_MAX_RATIO = 0.99

#: 図面の種類。**製図の一般の呼び方だけを並べる。**当たらないものは「その他」。
KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("平面図", re.compile(r"平面図|平面$")),
    ("断面図", re.compile(r"断面図|矩計")),
    ("立面図", re.compile(r"立面図")),
    ("配置図", re.compile(r"配置図")),
    ("展開図", re.compile(r"展開図")),
    ("詳細図", re.compile(r"詳細図|詳細$")),
    ("伏図", re.compile(r"伏図")),
    ("姿図", re.compile(r"姿図")),
    ("凡例", re.compile(r"凡例")),
    ("仕上表", re.compile(r"仕上表|仕上げ表")),
    ("建具表", re.compile(r"建具表")),
    ("図面リスト", re.compile(r"図面リスト|図面目録|図面一覧")),
    # 追記 2 のあと、**この案件の図面から作り直して足した 2 つ**。
    # 先に並べた一覧に当たらなかった 7 件を見たら、3 件がこの 2 種類だった。
    ("撤去図", re.compile(r"撤去|解体")),
    ("設備図", re.compile(r"電気|設備|配線|給排水|空調|換気")),
)


@dataclass(frozen=True)
class Segment:
    pos: float
    """線が乗っている座標(横線なら y、縦線なら x)。"""
    start: float
    end: float


def segments(page: pymupdf.Page) -> tuple[list[Segment], list[Segment]]:
    """そのページの水平・垂直の線を、**表示の向きに直して**集める。

    線(`l`)だけでなく、矩形(`re`)と四辺形(`qu`)の辺も採る。
    **太い枠が塗りつぶしの四辺形で描かれている図面がある**ため。
    """
    matrix = page.rotation_matrix
    horizontal: list[Segment] = []
    vertical: list[Segment] = []

    def add_box(rect: pymupdf.Rect) -> None:
        horizontal.append(Segment(rect.y0, rect.x0, rect.x1))
        horizontal.append(Segment(rect.y1, rect.x0, rect.x1))
        vertical.append(Segment(rect.x0, rect.y0, rect.y1))
        vertical.append(Segment(rect.x1, rect.y0, rect.y1))

    for drawing in page.get_drawings():
        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":
                first, second = item[1] * matrix, item[2] * matrix
                if abs(first.y - second.y) <= STRAIGHT and abs(first.x - second.x) > 1:
                    horizontal.append(
                        Segment((first.y + second.y) / 2, min(first.x, second.x), max(first.x, second.x))
                    )
                elif abs(first.x - second.x) <= STRAIGHT and abs(first.y - second.y) > 1:
                    vertical.append(
                        Segment((first.x + second.x) / 2, min(first.y, second.y), max(first.y, second.y))
                    )
            elif kind == "re":
                add_box(pymupdf.Rect(item[1] * matrix).normalize())
            elif kind == "qu":
                add_box(pymupdf.Rect((item[1] * matrix).rect).normalize())
    return horizontal, vertical


def merge(lines: list[Segment]) -> list[Segment]:
    """同じ座標に乗る線を 1 本にまとめ、繋がっている区間を繋ぐ。

    **太線は細い線の重ね描きで表される**ので、まとめないと同じ辺が何本にも数えられる。
    """
    groups: list[tuple[float, list[Segment]]] = []
    for line in sorted(lines, key=lambda s: (s.pos, s.start)):
        if groups and abs(groups[-1][0] - line.pos) <= SNAP:
            groups[-1][1].append(line)
        else:
            groups.append((line.pos, [line]))
    out: list[Segment] = []
    for pos, members in groups:
        # **区間の順に並べ直してから繋ぐ。**座標がわずかに違う線を 1 本にまとめているので、
        # 並びは座標の順のままでは区間の順になっていない。
        members = sorted(members, key=lambda s: (s.start, s.end))
        start, end = members[0].start, members[0].end
        for line in members[1:]:
            if line.start <= end + SNAP:
                end = max(end, line.end)
            else:
                out.append(Segment(pos, start, end))
                start, end = line.start, line.end
        out.append(Segment(pos, start, end))
    return out


def spans(lines: list[Segment], pos: float, start: float, end: float) -> bool:
    """`pos` の位置に、`start`〜`end` を通しで覆う線があるか。"""
    return any(
        abs(line.pos - pos) <= SNAP and line.start <= start + SNAP and line.end >= end - SNAP
        for line in lines
    )


def largest_closed_rect(
    horizontal: list[Segment],
    vertical: list[Segment],
    paper: pymupdf.Rect,
) -> tuple[float, float, float, float] | None:
    """**四辺が全部引かれている矩形のうち、いちばん大きいもの**を返す。

    JIS Z 8311 の輪郭線は**用紙の縁の内側**に引くので、紙の縁に接する矩形は
    輪郭線ではない(紙そのもの、または背景の画像)。だから四辺とも縁から離れていること
    を条件にする。**割合は使っていない。**
    """
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for index, top in enumerate(horizontal):
        for bottom in horizontal[index + 1 :]:
            if abs(top.start - bottom.start) > SNAP or abs(top.end - bottom.end) > SNAP:
                continue
            x0 = (top.start + bottom.start) / 2
            x1 = (top.end + bottom.end) / 2
            y0, y1 = sorted((top.pos, bottom.pos))
            if not (
                x0 > SNAP
                and y0 > SNAP
                and paper.width - x1 > SNAP
                and paper.height - y1 > SNAP
            ):
                continue
            area = (x1 - x0) * (y1 - y0)
            if best is not None and area <= best[0]:
                continue
            if spans(vertical, x0, y0, y1) and spans(vertical, x1, y0, y1):
                best = (area, (x0, y0, x1, y1))
    return None if best is None else best[1]


def title_block_rect(
    horizontal: list[Segment],
    vertical: list[Segment],
    frame: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """枠の**右下の隅に接する**いちばん大きい区画を返す(JIS Z 8311: 表題欄は右下)。

    条件は 4 つだけで、**大きさのしきい値は置いていない。**

    1. 上の辺が、枠の右の辺まで届いている
    2. 枠の下の辺が、その区画の左端から右端までを通しで覆っている
    3. 左の辺と右の辺が、上の辺から枠の下の辺まで通しで引かれている
    4. **左端が枠の左端より内側にある**(「右下」なので、枠の全幅にまたがる帯は
       表題欄ではない。枠を太線で二重に引いた線がここに引っかかるのも防ぐ)
    """
    fx0, fy0, fx1, fy1 = frame
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for top in horizontal:
        if abs(top.end - fx1) > SNAP:
            continue
        if not (fy0 + SNAP < top.pos < fy1 - SNAP):
            continue
        x0 = top.start
        if x0 <= fx0 + SNAP or x0 >= fx1 - SNAP:
            continue
        area = (fx1 - x0) * (fy1 - top.pos)
        if best is not None and area <= best[0]:
            continue
        if not spans(horizontal, fy1, x0, fx1):
            continue
        if spans(vertical, x0, top.pos, fy1) and spans(vertical, fx1, top.pos, fy1):
            best = (area, (x0, top.pos, fx1, fy1))
    return None if best is None else best[1]


def classify(name: str | None) -> str | None:
    """図面の名前を種類に振り分ける。当たらなければ「その他」。読めなければ None。"""
    if not name:
        return None
    for kind, pattern in KINDS:
        if pattern.search(name):
            return kind
    return "その他"


def settings_for(rect: tuple[float, float, float, float], paper: pymupdf.Rect) -> LedgerSettings:
    """表題欄の矩形を、既存の読み手が使う 0〜1000 の帯に直す。

    **発明した帯(880 / 600)の代わりに、図面自身が引いた矩形を渡す。**
    """
    x0, y0, _x1, _y1 = rect
    return LedgerSettings(
        title_bottom_from=NORM + 1.0,  # 下端の帯は使わない。右下の区画だけで切る
        title_right_from_x=x0 / paper.width * NORM,
        title_right_from_y=y0 / paper.height * NORM,
    )


def measure(pdf_path: str | Path) -> dict:
    pages: list[dict] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            paper = page.rect
            raw_h, raw_v = segments(page)
            horizontal, vertical = merge(raw_h), merge(raw_v)
            words = _page_words(page)
            row: dict = {
                "page": index + 1,
                "rotation": page.rotation,
                "図形あり": bool(raw_h or raw_v),
                "文字あり": bool(words),
                "枠": None,
                "枠の割合": None,
                "枠の余白": None,
                "表題欄": False,
                "表題欄の割合": None,
                "名前(枠から)": False,
                "名前(いまの帯)": False,
                "種類": None,
            }
            frame = largest_closed_rect(horizontal, vertical, paper)
            if frame is not None:
                x0, y0, x1, y1 = frame
                ratio = (x1 - x0) * (y1 - y0) / (paper.width * paper.height)
                row["枠"] = True
                row["枠の割合"] = round(ratio, 4)
                row["枠の余白"] = [
                    round(x0, 1),
                    round(y0, 1),
                    round(paper.width - x1, 1),
                    round(paper.height - y1, 1),
                ]
                title = title_block_rect(horizontal, vertical, frame)
                if title is not None:
                    tx0, ty0, tx1, ty1 = title
                    row["表題欄"] = True
                    row["表題欄の割合"] = round(
                        (tx1 - tx0) * (ty1 - ty0) / ((x1 - x0) * (y1 - y0)), 4
                    )
                    read = read_title_block(words, settings_for(title, paper))
                    row["名前(枠から)"] = read.drawing_name is not None
                    row["種類"] = classify(read.drawing_name)
            else:
                row["枠"] = False
            current = read_title_block(words, LedgerSettings())
            row["名前(いまの帯)"] = current.drawing_name is not None
            # 追記 2: 名前をどこから取るかは未決のまま、**取れた名前が種類に振り分くか**
            # だけを測る。枠の道が止まっていても測れる。
            row["種類(いまの帯)"] = classify(current.drawing_name)
            pages.append(row)
    return {"pages": pages, "summary": summarize(pages)}


def summarize(pages: list[dict]) -> dict:
    vector = [p for p in pages if p["図形あり"]]
    framed = [p for p in vector if p["枠"]]
    in_band = [
        p for p in framed if FRAME_MIN_RATIO <= (p["枠の割合"] or 0) <= FRAME_MAX_RATIO
    ]
    titled = [p for p in in_band if p["表題欄"]]
    named = [p for p in in_band if p["名前(枠から)"]]
    kinds: dict[str, int] = {}
    for page in named:
        kinds[page["種類"]] = kinds.get(page["種類"], 0) + 1
    named_band = [p for p in pages if p["名前(いまの帯)"]]
    band_kinds: dict[str, int] = {}
    for page in named_band:
        band_kinds[page["種類(いまの帯)"]] = band_kinds.get(page["種類(いまの帯)"], 0) + 1
    return {
        "ページ数": len(pages),
        "図形あり": len(vector),
        "閉じた枠が取れた": len(framed),
        f"枠の割合が {FRAME_MIN_RATIO:.0%}〜{FRAME_MAX_RATIO:.0%}": len(in_band),
        "線1(図形ありのうちの割合)": round(len(in_band) / len(vector), 4) if vector else None,
        "枠の右下に表題欄": len(titled),
        "線2(枠のうちの割合)": round(len(titled) / len(in_band), 4) if in_band else None,
        "名前が読めた(枠から)": len(named),
        "線4(枠のうちの割合)": round(len(named) / len(in_band), 4) if in_band else None,
        "名前が読めた(いまの帯)": sum(1 for p in pages if p["名前(いまの帯)"]),
        "種類の内訳": kinds,
        "その他": kinds.get("その他", 0),
        "種類の内訳(いまの帯)": band_kinds,
        "線6(いまの帯で読めた名前のうち種類が付いた割合)": (
            round(1 - band_kinds.get("その他", 0) / len(named_band), 4) if named_band else None
        ),
        "線5(名前のうち種類が付いた割合)": (
            round(1 - kinds.get("その他", 0) / len(named), 4) if named else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    result = measure(args.pdf)
    for row in result["pages"]:
        print(
            f"p{row['page']:2d} 回転={row['rotation']:3d} 枠={row['枠']} "
            f"割合={row['枠の割合']} 余白={row['枠の余白']} 表題欄={row['表題欄']} "
            f"名前(枠)={row['名前(枠から)']} 名前(帯)={row['名前(いまの帯)']} 種類={row['種類']}"
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
