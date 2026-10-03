"""K-67 1 節: **読了率と未読マップ。**最初の画面に出すもの。

おーちゃんの K-67:「どこが読めていないか分からないと、人は全部読み直す。信頼が一気に 0 になる。」
だから**最初に読了率を出し、読めていない図形は 1 つずつページと位置で指せるようにする。**

**数え方は新しく作らない。**K-51 の「消して確かめる」(`benchmarks/erase_check.py`)の裏返しで、

    読了率 = 拾えた ÷ 数える図形

`数える図形` は図枠・表題欄を除いたもの。位置の許容は要素の四角を 3 画素広げたもので、
**面積の上限 1%**(これを超える要素には印を付けない。付けると中身を読まずに全部に印が付く)。

**「読めた」は「台帳に載った」であって「理解した」ではない。**画面にもこの言葉を出す。

種類の内訳は 2 通り出す:

- **重なりなしの 6 種類**(合計が `数える図形`): 文字 / 記号 / 線 / 点・小さい図形 / 塗り / 画像
- **重なる別の切り口**: `表`(罫線の表の中にある図形)と `数字だけの語(寸法の見込み)`。
  いまの機械の分け方には「寸法」も「表」も無いので(`erase_check.py` の説明文が
  「記号かどうかを意味で当てたものではない」と断っている)、**別の切り口として足した。**
  だから「寸法」と言い切らず「数字だけの語(寸法の見込み)」と書く。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 面積の上限(ページ比)。K-51 の既定。**変えると数字が動く**ので画面に出す。
AREA_CAPS = (0.01, 0.05, 0.20)
DEFAULT_CAP = 0.01

#: ページの信号(K-67 1 節 c、おーちゃんの案)。
GREEN, YELLOW, RED, GREY = "緑", "黄", "赤", "灰"
SIGNAL_GREEN = 0.98
SIGNAL_YELLOW = 0.90

#: 読了率の合否の線(K-68 1 番、おーちゃんの決定)。**種類ごとに言う。**全体の 1 つの数では言わない。
#: 文字・数字は台帳の「文字」(数字だけの語も含む)、表は重なる切り口の「表」、線は長さで見た読了率。
PASS_LINES = {"文字・数字": 0.98, "記号": 0.95, "表": 0.95, "線(長さ)": 0.90}
#: 参考値として表示するだけで、合否に入れないもの(K-68 1 番)。
REFERENCE_ONLY = ("点・小さい図形",)
PASS, FAIL, NOT_MEASURED = "通過", "不通過", "測れない"

#: 案件全体の警告を出す、赤のページの割合(**こちらが決めた。仮の判断**)。
CASE_WARNING_SHARE = 1 / 3
CASE_WARNING = "この案件は読めていない頁が多い。下書きは参考。人が図面を確認する前提"

#: 定義の説明。**画面に常に出す。**
DEFINITION = (
    "読了率 = 台帳に位置付きで載った図形 ÷ 数える図形(図枠・表題欄は除く)。"
    "位置の許容 1%。変えると数字が動く。"
    "**読めた=台帳に載った。理解した、ではない。**"
)

#: 「測れない」の理由。**高い数字も低い数字も出さない。**
CANNOT_MEASURE = "測れない"

#: 読みの答えが無いページの理由(K-68 C 周 1)。**0 と数えない。**
UNOBTAINED = f"{CANNOT_MEASURE}(読みが未取得。AI の答えが無い)"

#: 数字だけの語とみなす文字(寸法の見込み)。
_NUMERIC = re.compile(r"^[0-9０-９\s\.,，、\-ー―~〜×xX*/:+()（）φΦRr@＠°%‰mｍ]+$")

#: 罫線の表とみなす升目の埋まり(`axes/image_axis/pdf_room_outlines.py` と同じ値)。
TABLE_MIN_FILL = 0.6

#: 1 枚の画像がページを覆っているとみなす割合。
IMAGE_COVER = 0.8


def _is_numeric_word(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and bool(_NUMERIC.match(text)) and any(c.isdigit() for c in text)


#: 図を表と誤認したとみなす、升目 1 つあたりの「升目の縁に乗らない線」の本数(K-72 作業 B。測る前に決めた。
#: `docs/k72_readrate_guard_criteria.md`)。本物の表の升目には文字か小さい見本の図 1 つが入る。図は升目 1 つに何百本も入る。
DRAWING_LINES_PER_CELL = 100
#: 罫線か図の線かを数える線の仲間(`erase_check` の元の種類)。
_LINE_KINDS = ("直線", "曲線", "矩形", "四角形", "ハッチング")


def _drawing_lines_per_cell(table: Mapping[str, Any], prims: Sequence[Any], scale: float) -> float:
    """表の中(中心が四角に入る、除外されていない)の線の仲間のうち、升目の縁に乗らない線の本数 ÷ 升目の数。"""
    from draft import table_grid

    cells = table["升目"]
    if not cells:
        return 0.0
    horizontal, vertical = table_grid._cell_edges(cells, scale)
    inside = [p for p in prims if not p.excluded and p.kind in _LINE_KINDS and _inside(p.bbox, [table["四角"]], scale)]
    off = sum(1 for p in inside if not table_grid.is_ruling(p, horizontal, vertical))
    return off / len(cells)


def _ruled_tables(page: Any, drawing_guard: bool = True) -> tuple[list[dict[str, Any]], str]:
    """罫線の表(四角と升目の四角)。**平面図を表と誤認する欠陥(周21)を避ける守りを通す。**

    守りに落ちた(升目の埋まりが足りない)ものは表として数えない。
    **図を表と誤認したもの(升目 1 つあたり、升目の縁に乗らない線が ``DRAWING_LINES_PER_CELL`` 本以上)も表として数えない**
    (K-72 作業 B。図形の層だけで決め、AI の読みに依らない。``drawing_guard=False`` で前の守りに戻す)。
    表が 1 つも残らなかったページは「表は測れない」と出す(**0 と書かない**)。
    座標はページの表示の向き(pt)。
    """
    try:
        found = page.find_tables()
    except Exception as error:  # pragma: no cover - pymupdf の版で例外が違う
        return [], f"{CANNOT_MEASURE}(表を探せなかった: {type(error).__name__})"
    tables: list[dict[str, Any]] = []
    dropped = 0
    drawings = 0
    prims: list[Any] | None = None
    scale = 0.0
    for table in found.tables:
        cells = [cell for row in table.extract() for cell in row]
        if len(cells) < 4:
            dropped += 1
            continue
        filled = sum(1 for cell in cells if (cell or "").strip())
        if filled / len(cells) < TABLE_MIN_FILL:
            dropped += 1
            continue
        entry = {"四角": tuple(float(v) for v in table.bbox),
                 "升目": [tuple(float(v) for v in c) for c in table.cells if c]}
        if drawing_guard:
            if prims is None:
                from benchmarks import erase_check as ec

                scale = ec.WIDTH_PX / page.rect.width
                prims = ec.extract_primitives(page, 0)
                ec.mark_exclusions(prims, page.rect.width * scale, page.rect.height * scale)
            if _drawing_lines_per_cell(entry, prims, scale) >= DRAWING_LINES_PER_CELL:
                drawings += 1
                continue
        tables.append(entry)
    counts = f"守りに落ちた表 {dropped} 個" + (f"、図と見た表 {drawings} 個" if drawing_guard else "")
    if not tables:
        return [], f"{CANNOT_MEASURE}(罫線の表が無い。{counts})"
    return tables, f"罫線の表 {len(tables)} 個({counts})"


def _table_rects(page: Any, drawing_guard: bool = True) -> tuple[list[tuple[float, float, float, float]], str]:
    """罫線の表の四角(`_ruled_tables` の四角だけ)。"""
    tables, note = _ruled_tables(page, drawing_guard)
    return [t["四角"] for t in tables], note


def _inside(bbox: Sequence[float], rects: Sequence[Sequence[float]], scale: float) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2 / scale, (bbox[1] + bbox[3]) / 2 / scale
    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)


def line_ink_length(prim: Any, rect_perimeter: bool = True) -> float:
    """「線(長さ)」に入れる墨の長さ。

    直線・曲線・ハッチングは ``erase_check`` の長さ。**矩形・四角形は ``erase_check`` では長さ 0(面積だけ)なので、
    周長で数える**(K-71 作業 3 周 2。長さ 0 のままだと墨があるのに分母にも分子にも入らない。測り方の誤りの直し)。
    """
    if rect_perimeter and prim.kind == "矩形":
        x0, y0, x1, y1 = prim.bbox
        return 2.0 * ((x1 - x0) + (y1 - y0))
    if rect_perimeter and prim.kind == "四角形":
        ul, ur, ll, lr = (prim.points[i] for i in range(4))
        ring = (ul, ur, lr, ll, ul)
        return float(sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(ring, ring[1:])))
    return float(prim.length)


def why_cannot_measure(page: Any, counted: int, words: int) -> str | None:
    """このページは測れないか。測れるなら `None`。"""
    if counted == 0:
        return f"{CANNOT_MEASURE}(数える図形が 0 個)"
    if words == 0 and counted <= 3:
        return f"{CANNOT_MEASURE}(文字の層が無く、数える図形が {counted} 個)"
    try:
        area = page.rect.width * page.rect.height
        covered = max(
            (abs(b[2] - b[0]) * abs(b[3] - b[1]) for b in (i["bbox"] for i in page.get_image_info())),
            default=0.0,
        )
        if area and covered / area >= IMAGE_COVER:
            return f"{CANNOT_MEASURE}(1 枚の画像がページの {covered / area:.0%} を覆っている)"
    except Exception:  # pragma: no cover
        pass
    return None


def means_of(page: Any, words: int) -> str:
    """読むのに使える手段。**手段が無ければそう書く。**"""
    if words:
        return "文字の層"
    try:
        if page.get_image_info():
            return "画像の墨(K-64 の 2)または OCR(どちらも未接続)"
    except Exception:  # pragma: no cover
        pass
    return "手段なし"


def _kind_rates(result: Mapping[str, Any]) -> dict[str, float | None]:
    """ページ・案件のどちらの出力からも、合否に使う 4 つの読了率を取り出す。"""
    kinds = result.get("種類ごと(重なりなし)") or result.get("種類ごと") or {}
    slices = result.get("別の切り口(重なる)") or result.get("別の切り口") or {}
    ink = result.get("墨の量で見た読了率") or {}
    return {
        "文字・数字": (kinds.get("文字") or {}).get("読了率"),
        "記号": (kinds.get("記号") or {}).get("読了率"),
        "表": (slices.get("表") or {}).get("読了率"),
        "線(長さ)": (ink.get("線(長さ)") or {}).get("読了率"),
    }


def pass_fail(result: Mapping[str, Any]) -> dict[str, Any]:
    """読了率の合否(K-68 1 番)。**種類ごとに線と比べ、1 つでも割れば不通過。**

    その種類がページに無い(数える図形が 0)・表が無いときは「測れない」で、合否に入れない。
    4 つとも測れなければ全体も「測れない」(**通過にしない**)。点・小さい図形は参考値として並べるだけ。
    """
    rates = _kind_rates(result)
    rows = {}
    for name, line in PASS_LINES.items():
        rate = rates[name]
        rows[name] = {"読了率": rate, "線": line,
                      "合否": NOT_MEASURED if rate is None else (PASS if rate >= line else FAIL)}
    verdicts = [r["合否"] for r in rows.values() if r["合否"] != NOT_MEASURED]
    overall = NOT_MEASURED if not verdicts else (FAIL if FAIL in verdicts else PASS)
    kinds = result.get("種類ごと(重なりなし)") or result.get("種類ごと") or {}
    ink = result.get("墨の量で見た読了率") or {}
    reference = {
        "点・小さい図形(数)": (kinds.get("点・小さい図形") or {}).get("読了率"),
        "点・小さい図形(面積)": (ink.get("点・小さい図形(面積)") or {}).get("読了率"),
        "全体の 1 つの数": result.get("読了率"),
    }
    return {"合否": overall, "種類ごと": rows, "参考(合否に入れない)": reference,
            "不通過の種類": [n for n, r in rows.items() if r["合否"] == FAIL]}


def signal_of(verdict: str) -> str:
    """ページの信号は合否から決める(K-68 1 番。**点が多いと全体の数が高く見えるので、全体の数では決めない**)。"""
    return {PASS: GREEN, FAIL: RED}.get(verdict, GREY)


def signal(rate: float | None) -> str:
    """全体の 1 つの数の色(**参考**。ページの信号には使わない)。"""
    if rate is None:
        return GREY
    if rate >= SIGNAL_GREEN:
        return GREEN
    if rate >= SIGNAL_YELLOW:
        return YELLOW
    return RED


def page_readthrough(
    page: Any,
    number: int,
    elements: Sequence[Mapping[str, Any]],
    *,
    cap: float = DEFAULT_CAP,
    with_unread: bool = True,
    machine_grid: bool = True,
    rect_perimeter: bool = True,
    drawing_guard: bool = True,
) -> dict[str, Any]:
    """1 ページの読了率・内訳・未読の一覧。

    台帳は AI の要素に、**AI が中身を読んだ罫線の表の罫線を機械が図形の層から読んだもの**を足したもの(K-71 作業 3 周 1、
    `draft/table_grid.py`)。物差し(面積の上限・余白・見本の点)は変えない。``machine_grid=False`` で AI の要素だけで数える。
    """
    from benchmarks import erase_check as ec
    from draft import table_grid

    words = len(page.get_text("words"))
    grid_record: dict[str, Any] = {"機械が足した罫線": 0, "表ごと": []}
    if machine_grid:
        elements, grid_record = table_grid.ledger(page, number, list(elements), cap, drawing_guard=drawing_guard)
    prims, summary = ec.check_page(page, number, list(elements), cap)
    counted = summary["数える図形"]
    cannot = why_cannot_measure(page, counted, words)
    scale = ec.WIDTH_PX / page.rect.width

    out: dict[str, Any] = {
        "ページ": number,
        "手段": means_of(page, words),
        "文字の層の語数": words,
        "数える図形": counted,
        "除外": summary["除外"],
        "面積の上限": cap,
        "機械が読んだ罫線(表)": grid_record,
    }
    if cannot:
        out.update({"読了率": None, "信号": GREY, "測れない理由": cannot,
                    "種類ごと": {}, "別の切り口": {}, "未読": [], "未読の所在が指せた": None})
        return out

    live = [p for p in prims if not p.excluded]
    got = summary["拾えた"]
    out["読了率"] = round(got / counted, 4)
    out["拾えた"] = got
    out["落ちた"] = summary["落ちた"]
    out["種類ごと"] = {
        kind: {"数える": total, "拾えた": total - missed,
               "読了率": round((total - missed) / total, 4) if total else None}
        for kind, (total, missed) in summary["種類ごと(数える/落ちた)"].items()
    }
    out["線の長さで見た読了率"] = (
        None if summary["線の長さで見た落ちた率"] is None else round(1 - summary["線の長さで見た落ちた率"], 4)
    )
    # おーちゃんの段階 1 の線「線・点は面積で 90% 以上」用。**線は長さ、点は面積で測る**
    # (長さと面積は足せないので 1 つに丸めない)。どちらも「墨の量」の近似である。
    ink: dict[str, dict[str, float]] = {}
    for p_ in live:
        if p_.category == "線":
            bucket = ink.setdefault("線(長さ)", {"全部": 0.0, "拾えた": 0.0})
            amount = line_ink_length(p_, rect_perimeter)
        elif p_.category == "点・小さい図形":
            bucket = ink.setdefault("点・小さい図形(面積)", {"全部": 0.0, "拾えた": 0.0})
            x0, y0, x1, y1 = p_.bbox
            amount = max((x1 - x0) * (y1 - y0), 1.0)
        else:
            continue
        bucket["全部"] += amount
        if p_.marked:
            bucket["拾えた"] += amount
    out["墨の量で見た読了率"] = {
        name: {"全部": round(v["全部"], 1), "拾えた": round(v["拾えた"], 1),
               "読了率": round(v["拾えた"] / v["全部"], 4) if v["全部"] else None}
        for name, v in ink.items()
    }

    # 別の切り口(重なる): 表 と 数字だけの語
    rects, table_note = _table_rects(page, drawing_guard)
    slices: dict[str, Any] = {"表の見つかり方": table_note}
    if rects:
        in_table = [p for p in live if _inside(p.bbox, rects, scale)]
        slices["表"] = {"数える": len(in_table), "拾えた": sum(1 for p in in_table if p.marked),
                        "読了率": round(sum(1 for p in in_table if p.marked) / len(in_table), 4) if in_table else None}
    else:
        slices["表"] = {"読了率": None, "測れない理由": table_note}
    numeric = [p for p in live if p.kind == "文字" and _is_numeric_word(p.text)]
    slices["数字だけの語(寸法の見込み)"] = {
        "数える": len(numeric), "拾えた": sum(1 for p in numeric if p.marked),
        "読了率": round(sum(1 for p in numeric if p.marked) / len(numeric), 4) if numeric else None,
        "但し書き": "寸法線に当たったかは見ていない。だから「寸法」と言い切らない",
    }
    out["別の切り口"] = slices

    out["合否"] = pass_fail(out)
    out["信号"] = signal_of(out["合否"]["合否"])
    out["全体の 1 つの数の色(参考)"] = signal(out["読了率"])

    if with_unread:
        unread = []
        for p in live:
            if p.marked:
                continue
            unread.append({
                "ページ": number, "図形の番号": p.id, "種類": p.category, "元の種類": p.kind,
                "位置": [round(v, 1) for v in p.bbox],
                "大きさ": round(p.size, 1),
                "文字": p.text if p.kind == "文字" else "",
            })
        out["未読"] = unread
        # 所在が指せる = ページと位置の両方がある(位置が壊れていない)
        locatable = sum(1 for u in unread if u["位置"] and len(u["位置"]) == 4
                        and u["位置"][2] > u["位置"][0] - 1e-9 and u["位置"][3] > u["位置"][1] - 1e-9)
        out["未読の所在が指せた"] = locatable
        out["未読の所在が指せた割合"] = round(locatable / len(unread), 4) if unread else 1.0
    return out


def readthrough(
    pdf: Path,
    reading: Mapping[int, Mapping[str, Any]],
    pages: Sequence[int],
    *,
    cap: float = DEFAULT_CAP,
    with_unread: bool = True,
    unobtained: Sequence[int] = (),
    machine_grid: bool = True,
    rect_perimeter: bool = True,
    drawing_guard: bool = True,
) -> dict[str, Any]:
    """案件全体の読了率。**ページごとの信号と、案件全体の警告も出す。**

    ``unobtained`` は読み(AI の答え)が未取得のページ(K-68 C 周 1)。**読了率を 0 と数えず「未取得」(灰)と出す。**
    案件全体の読了率・種類ごとの数には入れないが、案件全体の警告では「読めていないページ」(赤と同じ側)に数える。
    """
    import pymupdf

    missing = sorted(set(int(n) for n in unobtained))
    per_page: list[dict[str, Any]] = []
    with pymupdf.open(pdf) as doc:
        for number in sorted(set(pages) | set(missing)):
            if number in missing:
                per_page.append({"ページ": number, "数える図形": None, "読了率": None, "信号": GREY,
                                 "測れない理由": UNOBTAINED, "読み": "未取得"})
                continue
            page = doc.load_page(number - 1)
            elements = [e for e in (reading.get(number, {}) or {}).get("要素", []) if e.get("位置")]
            per_page.append(page_readthrough(page, number, elements, cap=cap, with_unread=with_unread,
                                             machine_grid=machine_grid, rect_perimeter=rect_perimeter,
                                             drawing_guard=drawing_guard))

    measured = [p for p in per_page if p["読了率"] is not None]
    page_count = len(per_page)
    counted = sum(p["数える図形"] for p in measured)
    got = sum(p.get("拾えた", 0) for p in measured)
    colours = {GREEN: 0, YELLOW: 0, RED: 0, GREY: 0}
    for p in per_page:
        colours[p["信号"]] += 1

    kinds: dict[str, dict[str, int]] = {}
    for p in measured:
        for kind, row in p["種類ごと"].items():
            bucket = kinds.setdefault(kind, {"数える": 0, "拾えた": 0})
            bucket["数える"] += row["数える"]
            bucket["拾えた"] += row["拾えた"]
    for row in kinds.values():
        row["読了率"] = round(row["拾えた"] / row["数える"], 4) if row["数える"] else None

    slices: dict[str, dict[str, Any]] = {}
    for name in ("表", "数字だけの語(寸法の見込み)"):
        bucket = {"数える": 0, "拾えた": 0, "測れないページ": 0}
        for p in measured:
            row = (p.get("別の切り口") or {}).get(name) or {}
            if row.get("読了率") is None:
                bucket["測れないページ"] += 1
                continue
            bucket["数える"] += row.get("数える", 0)
            bucket["拾えた"] += row.get("拾えた", 0)
        bucket["読了率"] = round(bucket["拾えた"] / bucket["数える"], 4) if bucket["数える"] else None
        slices[name] = bucket

    ink_total: dict[str, dict[str, float]] = {}
    for p in measured:
        for name, row in (p.get("墨の量で見た読了率") or {}).items():
            bucket = ink_total.setdefault(name, {"全部": 0.0, "拾えた": 0.0})
            bucket["全部"] += row["全部"]
            bucket["拾えた"] += row["拾えた"]
    ink_summary = {
        name: {"全部": round(v["全部"], 1), "拾えた": round(v["拾えた"], 1),
               "読了率": round(v["拾えた"] / v["全部"], 4) if v["全部"] else None}
        for name, v in ink_total.items()
    }

    case = {"読了率": round(got / counted, 4) if counted else None, "種類ごと(重なりなし)": kinds,
            "別の切り口(重なる)": slices, "墨の量で見た読了率": ink_summary}
    verdict = pass_fail(case)

    unread = [u for p in per_page for u in p.get("未読", [])]
    locatable = sum(p.get("未読の所在が指せた") or 0 for p in per_page)
    # 読みが未取得のページも「読めていない」側に数える(灰にしたことで警告が消えないように。K-68 C 周 1)。
    red_share = (colours[RED] + len(missing)) / page_count if page_count else 0.0

    return {
        "定義": DEFINITION,
        "面積の上限": cap,
        "読了率": round(got / counted, 4) if counted else None,
        "合否": verdict,
        "数える図形": counted,
        "拾えた": got,
        "測れたページ": len(measured),
        "測れないページ": len(per_page) - len(measured),
        "読みが未取得のページ": missing,
        "信号の分布": colours,
        "赤の割合": round(red_share, 4),
        "案件全体の警告": CASE_WARNING if red_share >= CASE_WARNING_SHARE else "",
        "種類ごと(重なりなし)": kinds,
        "墨の量で見た読了率": ink_summary,
        "別の切り口(重なる)": slices,
        "機械が読んだ罫線(表)": sum((p.get("機械が読んだ罫線(表)") or {}).get("機械が足した罫線", 0) for p in per_page),
        "未読の数": len(unread),
        "未読の所在が指せた": locatable,
        "未読の所在が指せた割合": round(locatable / len(unread), 4) if unread else 1.0,
        "ページごと": [{k: v for k, v in p.items() if k != "未読"} for p in per_page],
        "未読マップ": unread,
    }
