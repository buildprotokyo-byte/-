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

#: 数字だけの語とみなす文字(寸法の見込み)。
_NUMERIC = re.compile(r"^[0-9０-９\s\.,，、\-ー―~〜×xX*/:+()（）φΦRr@＠°%‰mｍ]+$")

#: 罫線の表とみなす升目の埋まり(`axes/image_axis/pdf_room_outlines.py` と同じ値)。
TABLE_MIN_FILL = 0.6

#: 1 枚の画像がページを覆っているとみなす割合。
IMAGE_COVER = 0.8


def _is_numeric_word(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and bool(_NUMERIC.match(text)) and any(c.isdigit() for c in text)


def _table_rects(page: Any) -> tuple[list[tuple[float, float, float, float]], str]:
    """罫線の表の四角。**平面図を表と誤認する欠陥(周21)を避ける守りを通す。**

    守りに落ちた(升目の埋まりが足りない)ものは表として数えない。
    表が 1 つも残らなかったページは「表は測れない」と出す(**0 と書かない**)。
    """
    try:
        found = page.find_tables()
    except Exception as error:  # pragma: no cover - pymupdf の版で例外が違う
        return [], f"{CANNOT_MEASURE}(表を探せなかった: {type(error).__name__})"
    rects: list[tuple[float, float, float, float]] = []
    dropped = 0
    for table in found.tables:
        cells = [cell for row in table.extract() for cell in row]
        if len(cells) < 4:
            dropped += 1
            continue
        filled = sum(1 for cell in cells if (cell or "").strip())
        if filled / len(cells) < TABLE_MIN_FILL:
            dropped += 1
            continue
        rects.append(tuple(float(v) for v in table.bbox))
    if not rects:
        return [], f"{CANNOT_MEASURE}(罫線の表が無い。守りに落ちた表 {dropped} 個)"
    return rects, f"罫線の表 {len(rects)} 個(守りに落ちた表 {dropped} 個)"


def _inside(bbox: Sequence[float], rects: Sequence[Sequence[float]], scale: float) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2 / scale, (bbox[1] + bbox[3]) / 2 / scale
    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)


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


def signal(rate: float | None) -> str:
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
) -> dict[str, Any]:
    """1 ページの読了率・内訳・未読の一覧。"""
    from benchmarks import erase_check as ec

    words = len(page.get_text("words"))
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
    out["信号"] = signal(out["読了率"])
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
            amount = p_.length
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
    rects, table_note = _table_rects(page)
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
) -> dict[str, Any]:
    """案件全体の読了率。**ページごとの信号と、案件全体の警告も出す。**"""
    import pymupdf

    per_page: list[dict[str, Any]] = []
    with pymupdf.open(pdf) as doc:
        for number in pages:
            page = doc.load_page(number - 1)
            elements = [e for e in (reading.get(number, {}) or {}).get("要素", []) if e.get("位置")]
            per_page.append(page_readthrough(page, number, elements, cap=cap, with_unread=with_unread))

    measured = [p for p in per_page if p["読了率"] is not None]
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

    unread = [u for p in per_page for u in p.get("未読", [])]
    locatable = sum(p.get("未読の所在が指せた") or 0 for p in per_page)
    red_share = colours[RED] / len(per_page) if per_page else 0.0

    return {
        "定義": DEFINITION,
        "面積の上限": cap,
        "読了率": round(got / counted, 4) if counted else None,
        "数える図形": counted,
        "拾えた": got,
        "測れたページ": len(measured),
        "測れないページ": len(per_page) - len(measured),
        "信号の分布": colours,
        "赤の割合": round(red_share, 4),
        "案件全体の警告": CASE_WARNING if red_share >= CASE_WARNING_SHARE else "",
        "種類ごと(重なりなし)": kinds,
        "墨の量で見た読了率": ink_summary,
        "別の切り口(重なる)": slices,
        "未読の数": len(unread),
        "未読の所在が指せた": locatable,
        "未読の所在が指せた割合": round(locatable / len(unread), 4) if unread else 1.0,
        "ページごと": [{k: v for k, v in p.items() if k != "未読"} for p in per_page],
        "未読マップ": unread,
    }
