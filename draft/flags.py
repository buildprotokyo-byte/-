"""つなげていない 7 部品を、旗(``--with-<部品>``)の裏で一本道につなぐ(K-63 4 節)。

**旗は既定でオフ。オフのときは何もしない**(``下書き.json`` に欄も足さない。``run.py`` が旗を見て呼ぶ)。

旗オンのときに守ること
----------------------
- 部品が足すものは ``下書き.json`` の ``"旗の部品"`` の欄にだけ書く。**「理解」「組み立て」の数量を上書きしない。**
- 数量が増える所(理解・組み立てでは数量が未取得だった所に、部品が数を出した所)には、状態「推論」か「仮説」と、
  根拠(どの部品・どのページ・どの要素)を付ける。確度は「中」か「低」だけ。
- **何も確定させない。** 部品が足した行は、組み立ての行と一緒に本番の入口(機械の検算)へもう一度通し、
  自動確定が 0 件であることを確かめる(``run.py``)。
- AI を呼ぶ部品(分かれ道・線引き)は ``draft.ai`` の呼び口を使う。鍵が無ければ指示を書き出すだけで、答えは置かない。
  **機械の答え(機械が数えた数など)を AI への指示に入れない。**
- 正解のファイルは使わない。対照表・知識の表はパスで受け取る(リポジトリには置かない)。

部品(旗の名前)
----------------
- 記号を室ごとに数える(``--with-symbol-count``)… `intake/positioned_symbol_count.py`(K-55)
- 縮尺で長さを測る(``--with-scale-length``)… `axes/image_axis/pdf_dimensions.py`・`pdf_room_outlines.py`・
  `intake/drawing_intake.py` の縮尺の決め方(食い違えば使わない)
- 凡例の対照表(``--with-legend-lookup``、表は ``--legend-lookup``)… `axes/image_axis/legend_lookup.py`(K-20)
- 分かれ道を AI に選択肢で聞く(``--with-branch-questions``)… 一本道の中の分かれ道(ページで数量が違う行・
  記号の数え直し)。**K-57 周 2 の寸法線の分かれ道は、候補の線を外に出す口が `pdf_dimensions` に無いので入れていない**
- 線引き(``--with-line-judge``)… `estimating/line_judge.py`(K-46)の AI の判定のファイルを読む役
- 知識の表(``--with-knowledge``、表は ``--knowledge``)… `knowledge/table.py`。**候補は候補のまま**(採否を書き換えない)

つながなかった部品は `NOT_FLAGGED`(キラークエスチョン: 本番のコードから結合 solver を作る口が無い)。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft.ai import AIRequest
from draft.stages import (
    CONFIDENCE,
    STATES,
    UNDECIDED,
    UNKNOWN,
    Context,
    nfkc,
    pages_of_kind,
    prompt,
    room_key,
)

#: 部品 → 旗の名前(argparse の dest は "with_" + 下線)。並びは報告の順。
FLAGS: dict[str, str] = {
    "記号を室ごとに数える": "--with-symbol-count",
    "縮尺で長さを測る": "--with-scale-length",
    "凡例の対照表": "--with-legend-lookup",
    "分かれ道を AI に選択肢で聞く": "--with-branch-questions",
    "線引き": "--with-line-judge",
    "知識の表": "--with-knowledge",
}

#: 旗の裏でもつながなかった部品と理由(K-63)。
NOT_FLAGGED = {
    "キラークエスチョン(killer_question)": (
        "部品(KillerQuestionEngine)は値の候補を持つ結合 solver を要るが、本番のコードが ConsistencySolver を直接作るのは "
        "tests/test_inference_orchestrator.py::test_static_production_code_has_no_solver_bypass で禁じられている"
        "(作ってよいのは arbitration の 3 ファイルだけ)。一本道から結合 solver を作る口が arbitration に無いので、"
        "つながない(口を足すのは安全の境目を動かすことになるので、おーちゃんの判断に回す)。"
        "なお試しに直接作って動かした回では、一本道の変数(ページで数量が違う行・記号の数と食い違う項目、P011 で 47)の間に"
        "制約が無いため効きは全部 0 で、概算・通常は問い 0、精密は名前の順に並べるだけだった"),
}

#: 部品が足したものに使ってよい状態と確度(**観測・高は使わない**)。
ADDED_STATES = ("推論", "仮説", "問い")
ADDED_CONFIDENCE = ("中", "低")
assert set(ADDED_STATES) <= set(STATES) and set(ADDED_CONFIDENCE) <= set(CONFIDENCE)

#: 記号を数えてよい単位(数えて出す単位だけ。式・m2 などは数えない)。
COUNTABLE_UNITS = ("個", "箇所", "か所", "ヶ所", "台", "本", "組", "枚", "")
_ID_IN_FORMULA = re.compile(r"p\d+-[0-9A-Za-z#x]+")
_SPLIT_PLACE = re.compile(r"[\s・/、,,]+")


def dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def place_tokens(place: Any) -> set[str]:
    """場所の文字を室ごとに分けて、揃えた鍵の集まりにする(「キッチン ダイニング リビング」→ 3 室)。"""
    text = nfkc(place)
    if not text or text == UNDECIDED:
        return set()
    keys = {room_key(text)}
    keys |= {room_key(t) for t in _SPLIT_PLACE.split(text) if t}
    return {k for k in keys if k}


def _added(kind: str, *, work: str, place: str, quantity: float | None, unit: str, state: str, confidence: str,
           basis: Mapping[str, Any], related: Sequence[str] = (), understood: Any = None, note: str = "",
           index: int = 0) -> dict[str, Any]:
    if state not in ADDED_STATES or confidence not in ADDED_CONFIDENCE:
        raise ValueError(f"旗の部品が足すものの状態・確度は {ADDED_STATES}・{ADDED_CONFIDENCE} だけ: {state}・{confidence}")
    return {
        "id": f"f-{kind}-{index:03d}",
        "工事": work,
        "場所": place,
        "数量": quantity,
        "単位": unit,
        "状態": state,
        "確度": confidence,
        "根拠": dict(basis),
        "理解の項目": list(related),
        "理解の数量": understood,
        "メモ": note,
    }


def _summary(added: Sequence[Mapping[str, Any]], increases: Sequence[Mapping[str, Any]],
             questions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "足したもの": len(added),
        "数量が増えた所": len(increases),
        "問い": len(questions),
        "状態ごと": {s: sum(1 for a in added if a["状態"] == s) for s in ADDED_STATES},
        "確度ごと": {c: sum(1 for a in added if a["確度"] == c) for c in ADDED_CONFIDENCE},
    }


def _part(flag: str, ran: str, added: list[dict[str, Any]], questions: list[dict[str, Any]],
          **extra: Any) -> dict[str, Any]:
    increases = [a for a in added if a.get("数量が増える")]
    return {"旗": flag, "動いたか": ran, "要約": _summary(added, increases, questions), "足したもの": added,
            "問い": questions, **extra}


def increase_rows(parts: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """数量が増えた所を、組み立ての行と同じ形にする(機械の検算に通して自動確定 0 を確かめるため)。"""
    rows = []
    for name, part in parts.items():
        for a in part.get("足したもの", []):
            if a.get("数量が増える") and a["数量"] is not None:
                rows.append({"科目": "", "区分": "", "工事項目": a["工事"], "摘要": "", "場所": a["場所"],
                             "数量": a["数量"], "単位": a["単位"], "項目": [a["id"], *a["理解の項目"]],
                             "メモ": f"旗の部品「{name}」が足した({a['状態']}・確度{a['確度']})"})
    return rows


def _elements_by_page(reading: Mapping[str, Any]) -> dict[int, dict[str, dict[str, Any]]]:
    return {int(n): {e["id"]: e for e in entry.get("要素", [])} for n, entry in reading.get("読み", {}).items()}


def known_rooms(understanding: Mapping[str, Any], finish: Mapping[str, Any]) -> dict[str, str]:
    """理解の場所と仕上表の原本に出てくる室名(揃えた鍵 → 書き方)。**室名はここからしか取らない。**"""
    known: dict[str, str] = {}
    for it in understanding.get("項目", []):
        for tok in _SPLIT_PLACE.split(nfkc(it["場所"])):
            if tok and tok != UNDECIDED:
                known.setdefault(room_key(tok), tok)
    for r in finish.get("原本の行", []):
        known.setdefault(room_key(r["室"]), nfkc(r["室"]))
    return known


def room_label(element: Mapping[str, Any], known: Mapping[str, str]) -> str | None:
    """「文字」の要素の先頭の語が、知っている室名と(揃えた鍵で)同じなら、その室名。"""
    if element.get("種類") != "文字" or not element.get("位置"):
        return None
    head = nfkc(re.split(r"\s*/\s*|[((\n]", element.get("内容", ""))[0])
    return known.get(room_key(head)) if head else None


def _inside(point: tuple[float, float], polygon: Sequence[Sequence[float]]) -> bool:
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(polygon, list(polygon[1:]) + [polygon[0]]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


# ---------------------------------------------------------------------------
# 1. 記号を室ごとに数える(K-55)
# ---------------------------------------------------------------------------


def symbol_count(reading: Mapping[str, Any], understanding: Mapping[str, Any],
                 finish: Mapping[str, Any]) -> dict[str, Any]:
    """**名前を付けるのは AI(理解の段)、数えるのは機械。** 理解の項目から記号の要素に名前を付け、
    機械が座標で室に置いて数える。理解の数量と照らし合わせ、理解で数量が未取得だった所だけを「数量が増える」とする。

    名前づけ(仮の判断):
    - 項目の「式」に要素の id が書かれていれば、その id の記号だけに項目の名前を付ける(AI が数えたと書いた要素)。
    - 書かれていなければ、**理解の数量が未取得で、数えて出す単位の項目だけ**、項目の記号の要素を全部名付ける
      (区分の印の記号も混ざりうるので「仮説」・確度「低」)。
    - 同じ記号に 2 つの名前が付いたら、どちらも採らない(凡例の名前が決まらない、として数えるだけ)。
    室名の要素: 「文字」の要素の先頭の語が、理解の場所または仕上表の原本の室名と(揃えた鍵で)同じもの。
    """
    from intake.positioned_symbol_count import NO_ROOM, NOT_IN_LEGEND, count_symbols

    flag = FLAGS["記号を室ごとに数える"]
    items = understanding.get("項目", [])
    by_page = _elements_by_page(reading)
    known = known_rooms(understanding, finish)

    def gid(n: int, eid: str) -> str:
        return f"{n}:{eid}"

    labels: dict[str, dict[str, Any]] = {}
    split: set[str] = set()
    how = Counter()
    for it in items:
        n = it["ページ"]
        els = by_page.get(n, {})
        symbols = [i for i in it["要素"] if "記号" in els.get(i, {}).get("種類", "")]
        named = [i for i in _ID_IN_FORMULA.findall(it["式"]) if i in symbols]
        basis = "式に書かれた要素"
        if not named and it["数量"] is None and nfkc(it["単位"]) in COUNTABLE_UNITS and symbols:
            named, basis = symbols, "項目の記号を全部(区分の印を含むかもしれない)"
        name = nfkc(it["工事"]) or nfkc(it["何"])
        if not named or not name:
            continue
        how[basis] += 1
        kind = it["区分"] if it["区分"] != UNDECIDED else "不明"
        for i in named:
            key = gid(n, i)
            prev = labels.get(key)
            if prev is not None and (prev["凡例の名前"], prev["区分"]) != (name, kind):
                split.add(key)
                continue
            labels.setdefault(key, {"id": key, "凡例の名前": name, "区分": kind, "項目": [], "名づけ方": basis})
            labels[key]["項目"].append(it["id"])
    for key in split:
        labels[key]["凡例の名前"] = NOT_IN_LEGEND

    rooms = []
    pages = []
    for n, els in sorted(by_page.items()):
        page_els = []
        for eid, e in els.items():
            if not e.get("位置"):
                continue
            page_els.append({"id": gid(n, eid), "種類": "記号" if "記号" in e["種類"] else e["種類"], "位置": e["位置"]})
            name = room_label(e, known)
            if name:
                rooms.append({"id": gid(n, eid), "室名": name})
        pages.append({"ページ": n, "要素": page_els})
    result = count_symbols({"ページ": pages}, {"記号": list(labels.values()), "室名": rooms})

    by_id = {it["id"]: it for it in items}
    added: list[dict[str, Any]] = []
    compare = Counter()
    for i, row in enumerate(result.rows, 1):
        related = sorted({x for e in row.element_ids for x in labels.get(e, {}).get("項目", [])})
        bases = {labels.get(e, {}).get("名づけ方") for e in row.element_ids}
        rel_items = [by_id[x] for x in related if x in by_id]
        units = {nfkc(it["単位"]) for it in rel_items}
        unit = units.pop() if len(units) == 1 and "" not in units else "個"
        understood = [it["数量"] for it in rel_items]
        same_room = [it for it in rel_items if room_key(row.room) in place_tokens(it["場所"])]
        increase = False
        if all(q is None for q in understood):
            status, increase = "理解に数量が無い(数量が増える)", True
        elif len(same_room) == 1 and same_room[0]["数量"] == row.quantity and len(rel_items) == 1:
            status = "一致"
        elif not same_room:
            status = "理解の場所と室が違う"
        else:
            status = "数が違う"
        compare[status] += 1
        hypothesis = row.room == NO_ROOM or any(b and b.startswith("項目の記号を全部") for b in bases)
        state = "仮説" if hypothesis else "推論"
        conf = "低" if hypothesis or status != "一致" else "中"
        pages_of = [row.page] + [p for p, _ in row.other_pages]
        a = _added("sym", work=row.name, place=row.room, quantity=float(row.quantity), unit=unit, state=state,
                   confidence=conf, index=i,
                   basis={"部品": "記号を室ごとに数える(K-55、機械が座標で数えた)", "ページ": pages_of,
                          "要素": [e.split(":", 1)[1] for e in row.element_ids],
                          "名づけ方": sorted(b for b in bases if b), "区分": row.kind,
                          "式": row.as_answer_row()["式"]},
                   related=related, understood=understood, note=status)
        a["照らし合わせ"] = status
        a["数量が増える"] = increase
        added.append(a)
    summary = result.summary()
    return _part(flag, "動いた(機械だけ。AI は呼ばない)", added, [],
                 数え上げ=summary,
                 名づけ={**dict(how), "名前が割れた記号": len(split), "名付けた記号": len(labels)},
                 室名の要素=len(rooms),
                 照らし合わせ=dict(compare))


# ---------------------------------------------------------------------------
# 2. 縮尺で長さを測る
# ---------------------------------------------------------------------------

#: 測る部位と、出す量。壁は天井高が要るので測らない(**高さを仮に置かない**)。
SCALE_PARTS = {"床": "面積", "天井": "面積", "幅木": "周長"}
AREA_UNITS = ("m2", "㎡", "m²")
LENGTH_UNITS = ("m",)
#: 別のページで測った同じ室の値がこれ以上違えば、どちらも採らない(仮の判断)。
PAGE_AGREEMENT = 0.01


def page_scale(pdf: Path, number: int) -> tuple[Any, dict[str, Any]]:
    """そのページの縮尺を、本番の入口(`intake/drawing_intake.py`)と同じ決め方で決める。**食い違えば使わない。**"""
    from axes.image_axis.pdf_dimensions import page_scale_from_dimensions, read_dimensions
    from axes.image_axis.pdf_vector_symbols import extract_scale
    from intake.drawing_intake import SCALE_AGREEMENT_TOLERANCE, _resolve_scale

    index = number - 1
    printed = extract_scale(pdf, index)
    dims = read_dimensions(pdf, index)
    dim_scale = page_scale_from_dimensions(dims, tolerance=float(SCALE_AGREEMENT_TOLERANCE))
    scale, readings, disagreement = _resolve_scale(page_number=number, printed=printed, reference_points=(),
                                                   tolerance=SCALE_AGREEMENT_TOLERANCE, dimensions=dim_scale)
    info = {"ページ": number, "読み": [{"分母": round(r.denominator, 3), "出どころ": r.origin} for r in readings],
            "記入された寸法": len(dims.readings)}
    if disagreement is not None:
        info["縮尺"] = UNKNOWN
        info["理由"] = "縮尺の読みが食い違った(どちらも採らない)"
    elif scale is None:
        info["縮尺"] = UNKNOWN
        info["理由"] = "縮尺が読めない(仮に置かない)"
    else:
        info["縮尺"] = f"1/{scale.denominator:g}"
        info["出どころ"] = scale.source_text
    return scale, info


def scale_length(pdf: Path, org: Mapping[str, Any], reading: Mapping[str, Any], understanding: Mapping[str, Any],
                 finish: Mapping[str, Any]) -> dict[str, Any]:
    """平面図のページで縮尺を決め、線で閉じた室の輪郭(室名は図面の文字から)の面積・周長を測って、
    理解の床・天井(面積)・幅木(周長)の項目に室名で結ぶ。**数量は上書きしない。**

    - 床・天井: 面積。輪郭が図面の線だけで閉じていれば「推論」・確度「中」、仮に閉じた辺があれば確度「低」。
      面積が内法か壁芯かは図形から決まらない(「不明」のまま)。
    - 幅木: 周長。開口(戸)を引いていないので「仮説」・確度「低」。
    - 壁: 天井高が要るので測らない。
    - 同じ室がいくつかのページで測れて、値が 1% より違えば、どちらも採らない。
    - 輪郭の室名: 輪郭の中の図面の文字(`find_room_outlines`)が知っている室名ならそれ。無ければ、読みの室名の要素
      (理解の場所・仕上表の室名と同じ文字)の中心が輪郭の中に 1 つだけあれば、その室名(2 つ以上なら付けない)。
    """
    import pymupdf

    from axes.image_axis.pdf_room_outlines import find_room_outlines
    from draft.pages import WIDTH_PX

    flag = FLAGS["縮尺で長さを測る"]
    known = known_rooms(understanding, finish)
    by_page = _elements_by_page(reading)
    plan_pages = sorted(pages_of_kind(org, "平面図"))
    scales, measured = [], {}
    with pymupdf.open(pdf) as doc:
        widths = {n: doc.load_page(n - 1).rect.width for n in plan_pages}
    for n in plan_pages:
        scale, info = page_scale(pdf, n)
        if scale is None:
            scales.append(info)
            continue
        outlines = find_room_outlines(pdf, n - 1, scale)
        info["室の輪郭"] = len(outlines)
        zoom = WIDTH_PX / widths[n]
        labels = []
        for e in by_page.get(n, {}).values():
            name = room_label(e, known)
            if name:
                b = e["位置"]
                labels.append((name, ((b[0] + b[2]) / 2 / zoom, (b[1] + b[3]) / 2 / zoom)))
        per_name: dict[str, list[Any]] = {}
        how = Counter()
        for o in outlines:
            name, src = None, ""
            if o.name and room_key(o.name) in known:
                name, src = known[room_key(o.name)], "輪郭の中の図面の文字"
            else:
                inside = {lab for lab, c in labels if _inside(c, o.polygon_pt)}
                if len(inside) == 1:
                    name, src = inside.pop(), "読みの室名の要素が輪郭の中に 1 つ"
                elif len(inside) > 1:
                    how["室名の要素が 2 つ以上(付けない)"] += 1
            if name:
                how[src] += 1
                per_name.setdefault(room_key(name), []).append((o, name))
        info["室名を付けた輪郭"] = dict(how)
        scales.append(info)
        for key, os_ in per_name.items():
            if len(os_) > 1:
                measured.setdefault(key, []).append({"ページ": n, "食い違い": f"同じ室名の輪郭が {len(os_)} つ"})
                continue
            o, name = os_[0]
            xs = [p[0] * zoom for p in o.polygon_pt]
            ys = [p[1] * zoom for p in o.polygon_pt]
            measured.setdefault(key, []).append({
                "ページ": n, "室名": name, "面積": round(o.area_sqm, 2), "周長": round(o.perimeter_mm / 1000, 2),
                "仮に閉じた辺": o.virtual_edges, "面積の数え方": o.area_basis,
                "囲み": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                "縮尺": info["縮尺"],
            })
    added: list[dict[str, Any]] = []
    compare = Counter()
    index = 0
    for it in understanding.get("項目", []):
        what = SCALE_PARTS.get(it["部位"])
        unit = nfkc(it["単位"])
        if what is None or (what == "面積" and unit not in AREA_UNITS) or (what == "周長" and unit not in LENGTH_UNITS):
            continue
        hits = [m for k in place_tokens(it["場所"]) for m in measured.get(k, [])]
        if not hits:
            compare["測れた輪郭が無い"] += 1
            continue
        index += 1
        good = [m for m in hits if "食い違い" not in m]
        values = sorted({m["面積" if what == "面積" else "周長"] for m in good})
        quantity = None
        note = ""
        if not good:
            note = "; ".join(f"{m['ページ']}ページ: {m['食い違い']}" for m in hits)
        elif values[0] > 0 and (values[-1] - values[0]) / values[0] > PAGE_AGREEMENT:
            note = "ページで測った値が違う: " + " / ".join(f"{m['ページ']}ページ {m['面積' if what == '面積' else '周長']}"
                                                   for m in good)
        else:
            quantity = values[0]
        if what == "面積":
            state = "推論"
            conf = "中" if good and all(m["仮に閉じた辺"] == 0 for m in good) else "低"
        else:
            state, conf = "仮説", "低"
            note = (note + "; " if note else "") + "周長から開口(戸)を引いていない"
        increase = it["数量"] is None and quantity is not None
        if quantity is None:
            status = "測った値を 1 つに決められない"
        elif it["数量"] is None:
            status = "理解に数量が無い(数量が増える)"
        else:
            diff = abs(quantity - it["数量"]) / it["数量"] if it["数量"] else None
            status = "1% 以内で一致" if diff is not None and diff <= 0.01 else "数が違う"
        compare[status] += 1
        a = _added("len", work=it["工事"] or it["何"], place=it["場所"], quantity=quantity, unit=unit, state=state,
                   confidence=conf, index=index,
                   basis={"部品": "縮尺で長さを測る(機械。線で閉じた室の輪郭)", "ページ": sorted({m["ページ"] for m in hits}),
                          "要素": [], "測った量": what, "輪郭": hits},
                   related=[it["id"]], understood=it["数量"], note=note or status)
        a["照らし合わせ"] = status
        a["数量が増える"] = increase
        added.append(a)
    return _part(flag, "動いた(機械だけ。AI は呼ばない)", added, [], 縮尺=scales,
                 測れた室=sorted({m["室名"] for v in measured.values() for m in v if "室名" in m}),
                 照らし合わせ=dict(compare), 測らなかった部位="壁(天井高が要る。仮に置かない)")


# ---------------------------------------------------------------------------
# 3. 凡例の対照表(K-20)
# ---------------------------------------------------------------------------


def _norm(text: Any) -> str:
    return "".join(nfkc(text).split())


#: 凡例の印を引き当てるページの種類(仮の判断)。P011 の凡例は「平面図凡例」と設備の記号の凡例で、仕上表・仕様書の
#: 「既存」などは凡例の印ではないので引かない。
LEGEND_TARGET_KINDS = ("平面図", "設備図")


def legend_lookup(pdf: Path, org: Mapping[str, Any], reading: Mapping[str, Any], understanding: Mapping[str, Any],
                  table_path: str | None) -> dict[str, Any]:
    """文字の層の語を、凡例から写した対照表に**完全一致で**引き当て(名前を作らない)、その語がある読みの要素を
    含む理解の項目と照らし合わせる。合わなければ問いにする。**数量は足さない。**"""
    from axes.image_axis.legend_lookup import KIND_WORK, LegendTable, match_marks, summarize
    from draft.pages import positioned_words

    flag = FLAGS["凡例の対照表"]
    if not table_path:
        return _part(flag, f"{UNKNOWN}(対照表が渡されていない。--legend-lookup で渡す)", [], [])
    table = LegendTable.load(table_path)
    target = {n for kind in LEGEND_TARGET_KINDS for n in pages_of_kind(org, kind)}
    by_page = _elements_by_page(reading)
    items_of: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for it in understanding.get("項目", []):
        for e in it["要素"]:
            items_of.setdefault((it["ページ"], e), []).append(it)
    all_matches = []
    hits = []
    for n in sorted(by_page):
        if n not in target:
            continue
        words = positioned_words(pdf, n)
        matches = match_marks([w[0] for w in words], table)
        all_matches.extend(matches)
        for w, m in zip(words, matches):
            if not (m.matched or m.to_question):
                continue
            cx, cy = (w[1] + w[3]) / 2, (w[2] + w[4]) / 2
            inside = [e for e in by_page[n].values() if e.get("位置") and e["位置"][0] <= cx <= e["位置"][2]
                      and e["位置"][1] <= cy <= e["位置"][3]]
            inside.sort(key=lambda e: (e["位置"][2] - e["位置"][0]) * (e["位置"][3] - e["位置"][1]))
            hits.append((n, w, m, inside[0]["id"] if inside else None))
    same_meaning: dict[str, set[str]] = {}
    for wm in table.work_marks:
        same_meaning.setdefault(str(wm["meaning"]), set()).add(_norm(wm["code"]))
    compare = Counter()
    added: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    asked: set[tuple[str, Any]] = set()
    for n, w, m, eid in hits:
        if eid is None:
            compare["読みの要素に結べない"] += 1
            continue
        its = items_of.get((n, eid), [])
        if not its:
            compare["理解の項目に入っていない要素"] += 1
            continue
        for it in its:
            if m.to_question:
                status = "対照表で 1 つに決まらない"
            elif m.kind == KIND_WORK:
                codes = same_meaning.get(str(m.meaning), set())
                kind = _norm(it["区分"])
                status = "合う" if any(c and (c in kind or kind in c) for c in codes) and kind else "違う"
            else:
                base = _norm(re.split(r"[((]", m.name or "")[0])
                text = _norm(it["何"]) + _norm(it["工事"]) + _norm(it["読み取った値"])
                status = "合う" if base and base in text else "違う"
            compare[status] += 1
            legend = m.meaning if m.kind == KIND_WORK else m.name
            if status == "合う" or (it["id"], legend) in asked:
                continue
            asked.add((it["id"], legend))
            q = {"部品": "凡例の対照表", "種類": "凡例との違い", "状態": "問い", "確度": "低",
                 "問い": f"{n}ページの「{w[0]}」は、凡例では「{legend or '1 つに決まらない'}」です。"
                         f"項目「{it['工事']}」({it['場所']}、区分 {it['区分']})はどちらですか",
                 "選択肢": [f"凡例のとおり: {legend}" if legend else "凡例の名前のどれか(人が選ぶ)",
                          f"項目のとおり: {it['工事']}", "分からない(現地・設計者に確認する)"],
                 "見る所": [n] + list(m.source_pages), "関係する項目": [it["id"]],
                 "鍵": f"凡例:{n}:{eid}:{it['id']}", "位置": [{"ページ": n, "位置": list(w[1:5])}]}
            questions.append(q)
    s = summarize(all_matches)
    return _part(flag, "動いた(機械だけ。AI は呼ばない)", added, questions,
                 引き当て={"語": s.total, "名前が付いた": s.named, "不明": s.unknown, "不明の理由": s.by_reason,
                        "質疑へ回す": s.questions, "要素に結べた": sum(1 for h in hits if h[3])},
                 照らし合わせ=dict(compare),
                 注="線の色の対照(match_line_colors)はこの旗では引かない(線の図形を一本道の読みに持っていない)")


# ---------------------------------------------------------------------------
# 4. 分かれ道を AI に選択肢で聞く(機械の答えは見せない)
# ---------------------------------------------------------------------------

BRANCH_SHAPE = '{"答え": [{"鍵": "...", "選んだ": "選択肢の文字そのまま", "数": 数 または null, "根拠": "1 行"}]}'
NONE_OF_THEM = "どれでもない"
CANNOT_DECIDE = "決められない"
_COUNT_NOTE = re.compile(r"数えた記号の要素は \d+ 個、数量は ")


def find_branches(understanding: Mapping[str, Any], assembly: Mapping[str, Any]) -> list[dict[str, Any]]:
    """一本道の中の分かれ道。**選択肢には機械の答え(機械が数えた数)を入れない。**

    1. ページで数量が違う行: 選択肢は各ページの読みの数量(どちらも AI の読み)。
    2. 機械の検算で記号の数と食い違った項目: 選択肢は「この数でよい」「この数ではない(数を書く)」。
       機械が数えた数は渡さない。
    """
    by_id = {it["id"]: it for it in understanding.get("項目", [])}
    out = []
    for c in assembly.get("ページで数量が違う", []):
        members = [by_id[i] for i in c["項目"] if i in by_id]
        unit = members[0]["単位"] if members else ""
        values = sorted({q for q in c["数量"] if q is not None})
        out.append({
            "鍵": "数量:" + ":".join(c["項目"]), "種類": "ページで数量が違う",
            "問い": f"{c['工事']}({c['場所']})の数量は、どれですか",
            "選択肢": [f"{v:g} {unit}" for v in values] + [NONE_OF_THEM, CANNOT_DECIDE],
            "見る所": [{"ページ": m["ページ"], "位置": m["囲み"]} for m in members],
            "関係する項目": c["項目"], "値": {f"{v:g} {unit}": v for v in values}, "単位": unit,
            "工事": c["工事"], "場所": c["場所"],
        })
    for it in understanding.get("項目", []):
        if it["数量"] is None or not any(_COUNT_NOTE.match(n) for n in it["検算"]):
            continue
        q = f"{it['数量']:g} {it['単位']}"
        out.append({
            "鍵": f"数え直し:{it['id']}", "種類": "記号の数え直し",
            "問い": f"{it['ページ']}ページ: {it['工事']}({it['場所']})の記号の数は {q} でよいですか",
            "選択肢": [f"{q}(この数でよい)", "この数ではない(正しい数を「数」に書く)", CANNOT_DECIDE],
            "見る所": [{"ページ": it["ページ"], "位置": it["囲み"]}], "関係する項目": [it["id"]],
            "値": {}, "単位": it["単位"], "工事": it["工事"], "場所": it["場所"],
        })
    return out


def branch_questions(ctx: Context, understanding: Mapping[str, Any], assembly: Mapping[str, Any]) -> dict[str, Any]:
    flag = FLAGS["分かれ道を AI に選択肢で聞く"]
    branches = find_branches(understanding, assembly)
    groups: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    for b in branches:
        groups.setdefault(tuple(sorted({s["ページ"] for s in b["見る所"]})), []).append(b)
    keys = sorted(groups)
    reqs = [AIRequest(
        stage="分かれ道", key="p" + "-".join(map(str, pages)),
        instructions=prompt("分かれ道", pages="・".join(map(str, pages))),
        images=[ctx.page(n).image for n in pages],
        data={"分かれ道": [{k: b[k] for k in ("鍵", "問い", "選択肢", "見る所")} for b in groups[pages]]},
        answer_shape=BRANCH_SHAPE,
    ) for pages in keys]
    answers = ctx.caller.map(reqs, ctx.parallel)
    got: dict[str, Mapping[str, Any]] = {}
    missing = 0
    for ans in answers:
        if not isinstance(ans.payload, Mapping):
            missing += 1
            continue
        for a in ans.payload.get("答え", []) or []:
            if isinstance(a, Mapping) and a.get("鍵"):
                got[str(a["鍵"])] = a
    added: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    result = Counter()
    statuses: dict[str, str] = {}
    for i, b in enumerate(branches, 1):
        a = got.get(b["鍵"])
        choice = str(a.get("選んだ") or "") if a else ""
        if a is None:
            status = UNKNOWN
        elif choice not in b["選択肢"]:
            status = "選択肢の外の答え(使わない)"
        else:
            status = choice
        result["未取得" if status == UNKNOWN else ("決めた" if status in b["値"] else status)] += 1
        statuses[b["鍵"]] = status
        entry = {k: b[k] for k in ("鍵", "種類", "問い", "選択肢", "関係する項目")}
        entry["AI の答え"] = status
        entry["根拠"] = str(a.get("根拠") or "") if a else ""
        if a and a.get("数") is not None:
            entry["AI が書いた数"] = a.get("数")
        if status in b["値"]:
            pages = sorted({s["ページ"] for s in b["見る所"]})
            item = _added("br", work=b["工事"], place=b["場所"], quantity=float(b["値"][status]), unit=b["単位"],
                          state="推論", confidence="中", index=i,
                          basis={"部品": "分かれ道を AI に選択肢で聞いた(機械の答えは見せていない)", "ページ": pages,
                                 "要素": [], "AI の根拠": entry["根拠"]},
                          related=b["関係する項目"], understood=None, note="ページで数量が違う行を、AI が選択肢から選んだ")
            item["数量が増える"] = True
            added.append(item)
        else:
            questions.append({"部品": "分かれ道", "種類": b["種類"], "状態": "問い", "確度": "低", "問い": b["問い"],
                              "選択肢": b["選択肢"], "見る所": sorted({s["ページ"] for s in b["見る所"]}),
                              "関係する項目": b["関係する項目"], "鍵": f"分かれ道:{b['鍵']}", "AI の答え": status})
    ran = (f"動いた(AI を {len(reqs)} 回呼ぶはず。答えが未取得 {missing} 回)" if missing
           else f"動いた(AI を {len(reqs)} 回)")
    return _part(flag, ran, added, questions, 分かれ道=[{"鍵": b["鍵"], "種類": b["種類"], "答え": statuses[b["鍵"]]}
                                                       for b in branches],
                 結果=dict(result), AI_の呼び出し=len(reqs), AI_の答えが未取得=missing,
                 注="機械の答え(機械が数えた数・機械の推す候補)は AI への指示に入れていない")


# ---------------------------------------------------------------------------
# 5. 線引き(K-46)
# ---------------------------------------------------------------------------

LINE_SHAPE = '{"判定": [{"番号": n, "判定": "工事の行|工事の行ではない", "理由": "短く"}]}'


def line_judge(ctx: Context, assembly: Mapping[str, Any]) -> dict[str, Any]:
    """組み立ての行を、AI の判定(`estimating/line_judge.AIJudgmentFileJudge`)で線引きする。
    **判定が無い行は落とさず要確認。「工事の行ではない」行も消さない**(見積から外す候補として並べるだけ)。"""
    from estimating.line_judge import (
        VERDICT_NEEDS_CHECK,
        VERDICT_NOT_WORK,
        VERDICT_WORK,
        AIJudgmentFileJudge,
        LineJudgeError,
        LineText,
    )

    flag = FLAGS["線引き"]
    rows = assembly.get("内訳の行", [])
    texts = [LineText(i, r["工事項目"], r["場所"]) for i, r in enumerate(rows, 1)]
    if not texts:
        return _part(flag, "動いた(行が無い)", [], [])
    req = AIRequest(stage="線引き", key="内訳の行", instructions=prompt("線引き"),
                    data={"行": [{"番号": t.number, "工事": t.work, "場所": t.place} for t in texts]},
                    answer_shape=LINE_SHAPE)
    ans = ctx.caller.call(req)
    note = ""
    if isinstance(ans.payload, Mapping) and isinstance(ans.payload.get("判定"), list):
        try:
            judge = AIJudgmentFileJudge(ans.payload["判定"], source="AI(線引きの段)")
        except LineJudgeError as exc:
            judge, note = AIJudgmentFileJudge(None, source=f"AI の判定の形が違う: {exc}"), str(exc)
    else:
        judge = AIJudgmentFileJudge(None, source="AI の答えが未取得")
    verdicts = judge.judge(texts)
    out = [{"番号": t.number, "工事項目": t.work, "場所": t.place, "数量": rows[t.number - 1]["数量"],
            **v.as_evidence()} for t, v in zip(texts, verdicts)]
    counts = {k: sum(1 for v in verdicts if v.verdict == k) for k in (VERDICT_WORK, VERDICT_NOT_WORK, VERDICT_NEEDS_CHECK)}
    ran = "動いた(AI の答えが未取得。全部の行を要確認にした)" if ans.payload is None else "動いた(AI を 1 回)"
    return _part(flag, ran, [], [], 判定=out, 判定ごと=counts,
                 見積から外す候補=[o for o in out if o["判定"] == VERDICT_NOT_WORK],
                 注="行は消さない・数量は変えない。「工事の行ではない」は外す候補として並べるだけ" + (f"({note})" if note else ""))


# ---------------------------------------------------------------------------
# 6. 知識の表(候補は候補のまま)
# ---------------------------------------------------------------------------


def _hits(words: Sequence[str], texts: Sequence[str]) -> bool:
    """知識の語が、項目の文字の中にそのまま入っているか(**逆向きは見ない**: 「天井」は「天井飾り」に掛からない)。"""
    body = [_norm(t) for t in texts if _norm(t) and _norm(t) != UNDECIDED]
    return any(_norm(w) and any(_norm(w) in b for b in body) for w in words)


def knowledge(understanding: Mapping[str, Any], table_path: str | None) -> dict[str, Any]:
    """知識の表の項目を、理解の項目に「掛かるかもしれない」として結ぶ。**数量は作らない。採否は書き換えない。**

    - 数え方: 項目に数え方の注記を付ける(仮説・確度 低)。
    - 波及: 波及先を「波及の候補」として足す(数量は未取得のまま。仮説・確度 低)。
    - 問い: 問いにする(選択肢は表のとおり)。
    「不採用」の知識は使わない(数だけ数える)。工事・部位のどちらにも掛からない知識(軸だけ)は結ばない。
    """
    from knowledge.table import KIND_COUNTING, KIND_PROPAGATION, KIND_QUESTION, load_knowledge, usage_mark

    flag = FLAGS["知識の表"]
    if not table_path:
        return _part(flag, f"{UNKNOWN}(知識の表が渡されていない。--knowledge で渡す)", [], [])
    table = load_knowledge(table_path)
    items = understanding.get("項目", [])
    added: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    used, skipped, unbound = Counter(), 0, 0
    for entry in table.entries:
        if entry.is_rejected:
            skipped += 1
            continue
        kinds, parts = entry.applies_to.work_kinds, entry.applies_to.parts
        if not kinds and not parts:
            unbound += 1
            continue
        matched = [it for it in items
                   if (not kinds or _hits(kinds, [it["科目"], it["工事"], it["区分"]]))
                   and (not parts or _hits(parts, [it["部位"], it["何"], it["工事"]]))]
        if not matched:
            continue
        used[entry.kind] += 1
        mark = usage_mark(entry)
        ids = [it["id"] for it in matched]
        pages = sorted({it["ページ"] for it in matched})
        basis = {"部品": "知識の表", "ページ": pages, "要素": [], "知識": entry.entry_id, "印": mark,
                 "採否": entry.adoption, "拘束力": entry.source.binding, "合成の見本": table.synthetic}
        if entry.kind == KIND_COUNTING:
            a = _added("kn", work=f"数え方の注記: {entry.statement}", place="", quantity=None,
                       unit=str(entry.detail.get("unit", "")), state="仮説", confidence="低", index=len(added) + 1,
                       basis={**basis, "数え方": entry.detail.get("method")}, related=ids,
                       note="数え方を項目に掛けるかは人が決める(数量は変えていない)")
            added.append(a)
        elif entry.kind == KIND_PROPAGATION:
            a = _added("kn", work=f"波及の候補: {entry.detail.get('trigger', '')} → {'・'.join(entry.detail.get('affected', []))}",
                       place="", quantity=None, unit="", state="仮説", confidence="低", index=len(added) + 1,
                       basis={**basis, "波及": dict(entry.detail)}, related=ids,
                       note="数量は未取得のまま(知識から数を作らない)")
            added.append(a)
        elif entry.kind == KIND_QUESTION:
            questions.append({"部品": "知識の表", "種類": "知識の問い", "状態": "問い", "確度": "低",
                              "問い": str(entry.detail.get("question", entry.statement)),
                              "選択肢": list(entry.detail.get("answer_options", [])), "見る所": pages,
                              "関係する項目": ids, "鍵": f"知識:{entry.entry_id}",
                              "答えで変わること": entry.detail.get("what_changes", ""), "印": mark})
    return _part(flag, "動いた(機械だけ。AI は呼ばない)", added, questions,
                 表={"表": table.table_id, "合成の見本": table.synthetic, "知識": len(table.entries),
                    "採否ごと": dict(Counter(e.adoption for e in table.entries))},
                 掛かった知識=dict(used), 使わなかった不採用=skipped, 項目に結ばない知識=unbound,
                 注="採否(候補・採用・不採用)は読むだけで書き換えない。候補は候補のまま")


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------


def overview(parts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """旗オンで変わる出力の要約(比較表の材料)。"""
    added = [a for p in parts.values() for a in p.get("足したもの", [])]
    qs = [q for p in parts.values() for q in p.get("問い", [])]
    return {
        "オンの旗": [p["旗"] for p in parts.values()],
        "足したもの": len(added),
        "数量が増えた所": sum(1 for a in added if a.get("数量が増える")),
        "数量が増えた所(数量のあるもの)": sum(1 for a in added if a.get("数量が増える") and a["数量"] is not None),
        "問い": len(qs),
        "状態ごと": {s: sum(1 for a in added if a["状態"] == s) + (len(qs) if s == "問い" else 0) for s in ADDED_STATES},
        "確度ごと": {c: sum(1 for a in added if a["確度"] == c) + sum(1 for q in qs if q.get("確度") == c)
                  for c in ADDED_CONFIDENCE},
        "観測・確度高を付けたもの": sum(1 for a in added if a["状態"] == "観測" or a["確度"] == "高"),
    }
