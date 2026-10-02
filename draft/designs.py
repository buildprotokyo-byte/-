"""読みの設計の別案(K-63)。**既定は V1(今までの一本道)。V2・V3 は ``--design`` で選んだときだけ動く。**

- V2 目的から探す型: 仕様書・仕上表・概要などの文字の層から AI が「工事概略」を 1 回で作り、図面はその各項目の根拠を
  探すためだけに読む。出すのは項目の id・位置・数・確度と「概略に無い工事」だけ。図面で見つからない項目は
  「見つからない」と出し、無いことの証拠にしない(数量は未取得のまま、状態は「問い」)。
- V3 閉じた語彙型: 読みと理解を 1 回にまとめ、出すのは閉じた語彙の id(細目・部位・区分・単位・状態・室)と位置と数だけ。
  工事でない書き込みも語彙の id と位置で挙げる(落ちを測るため)。語彙は差し替えられるファイル(``draft/vocab/``)。

どちらも、後ろの段(仕上表・質問・組み立て・機械の検算)には V1 と同じ形の「理解」を渡す。**数を作らない。**
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft import stages
from draft.ai import AIRequest
from draft.stages import (UNDECIDED, UNKNOWN, Context, _clean_box, _union, ai_pages, check_item, link_same_things,
                          measure_misses, nfkc, pages_of_kind, prompt, room_key)

DESIGNS = ("V1", "V2", "V3")
VOCAB_ENV = "DRAFT_VOCAB"
DEFAULT_VOCAB = Path(__file__).with_name("vocab") / "default.json"
#: 工事概略に文字の層を渡すページの種類(文章や表で全体を要約しているページ)。
OUTLINE_KINDS = ("表紙・図面リスト", "概要", "仕様書", "仕上表", "建具表", "製品資料")


def load_vocab(path: str | Path | None = None) -> dict[str, Any]:
    """語彙を読む。場所は引数 → 環境変数 ``DRAFT_VOCAB`` → 既定のファイル。"""
    p = Path(path or os.environ.get(VOCAB_ENV) or DEFAULT_VOCAB)
    return json.loads(p.read_text(encoding="utf-8"))


def _legend_images(ctx: Context, org: Mapping[str, Any], exclude: Sequence[int] = ()) -> list[Path]:
    return [ctx.page(m).image for m in pages_of_kind(org, stages.KIND_LEGEND) if m not in exclude][:2]


def _batches(ctx: Context, pages: Sequence[int]) -> list[list[int]]:
    size = ctx.pass1_batch if ctx.pass1_batch > 0 else len(pages)
    ordered = sorted(pages)
    return [ordered[i:i + size] for i in range(0, len(ordered), size)] if ordered else []


def _key(batch: Sequence[int], one: bool) -> str:
    return "全ページ" if one else f"p{batch[0]}-p{batch[-1]}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _boxes(value: Any) -> list[list[float]]:
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value):
        value = [value]
    out = []
    for b in value or []:
        cb = _clean_box(b)
        if cb:
            out.append(cb)
    return out


def _item(n: int, index: int, boxes: list[list[float]], **fields: Any) -> dict[str, Any]:
    """V1 の理解の項目と同じ形にする(後ろの段をそのまま使うため)。"""
    base = {
        "id": f"u-p{n}-{index:03d}", "ページ": n, "要素": [], "位置": boxes, "囲み": _union(boxes),
        "読み取った値": "", "何": "", "部位": UNDECIDED, "場所": UNDECIDED, "区分": UNDECIDED, "工事": "",
        "科目": UNDECIDED, "品番": "", "数量": None, "単位": "", "式": "", "状態": "問い", "確度": "低",
        "根拠の種類": "図面から読んだ", "理由": "", "選択肢": [], "検算": [],
    }
    base.update(fields)
    return base


def _reading_from_boxes(per_page: Mapping[int, list[tuple[str, list[float]]]]) -> dict[int, dict[str, Any]]:
    """落ちを測るための「読み」(V1 と同じ形)。要素の内容は語彙の id か項目の id だけ。"""
    out = {}
    for n, marks in per_page.items():
        out[n] = {"描かれているもの": "", "要素": [
            {"id": f"p{n}-{i:03d}", "種類": kind, "内容": "", "位置": box, "確かさ": ""}
            for i, (kind, box) in enumerate(marks, 1)], "分からなかったもの": [], "形の崩れた要素": 0}
    return out


def _reading_summary(ctx: Context, reading: dict[int, dict[str, Any]], targets: Sequence[int],
                     before: Mapping[int, Any] | None, after: Mapping[int, Any], reread: Sequence[int],
                     reread_missing: Sequence[int]) -> dict[str, Any]:
    pages_out = {}
    for n in targets:
        b, a = (before or {}).get(n), after.get(n)
        rate = a["落ちた率"] if a else None
        pages_out[n] = {"通読の落ちた率": b["落ちた率"] if b else None, "落ちた率": rate, "読み直した": n in reread,
                        "読み落としの可能性が高い": rate is None or rate > stages.HIGH_MISS_FLAG}
    return {
        "読み": reading, "ページ": pages_out,
        "通読の落ち": stages._totals(before) if before else UNKNOWN,
        "読み直した後の落ち": stages._totals(after) if after else UNKNOWN,
        "読み直したページ": list(reread), "読み直しが未取得のページ": list(reread_missing),
    }


# ---------------------------------------------------------------------------
# V2 目的から探す型
# ---------------------------------------------------------------------------

OUTLINE_SHAPE = (
    '{"項目": [{"id": "G001", "室": "...", "部位": "...", "工事": "...", "区分": "...", "科目": "...", '
    '"品番": "", "数量": null, "単位": "", "出典": [n], "探す図": [n]}]}'
)
SEARCH_SHAPE = (
    '{"ページ": [{"ページ": n, "見つけた": [{"id": "G001", "位置": [[x0, y0, x1, y1]], "数": null, "確度": "中"}], '
    '"概略に無い": [{"工事": "...", "部位": "...", "室": "...", "区分": "...", "位置": [[x0, y0, x1, y1]], "数": null}]}]}'
)
NOT_FOUND_OPTIONS = ["仕様書のとおり工事に入る", "仕様書の記載は別の工事(今回の見積に入れない)",
                     "分からない(現地・設計者に確認する)"]


def outline(ctx: Context, org: Mapping[str, Any]) -> dict[str, Any]:
    """工事概略(AI 1 回)。文字の層を渡し、文字の層の無いページだけ画像を渡す。"""
    pages = sorted(n for n, e in org["ページ"].items()
                   if e.get("種類") in OUTLINE_KINDS and not ctx.page(int(n)).blank)
    with_text = [n for n in pages if ctx.page(n).text.strip()]
    no_text = [n for n in pages if n not in with_text]
    req = AIRequest(
        stage="工事概略", key="全体", instructions=prompt("工事概略"),
        images=[ctx.page(n).image for n in no_text],
        data={"文字の層": {str(n): ctx.page(n).text for n in with_text},
              "ページの種類": {str(n): {"種類": e.get("種類"), "描かれているもの": e.get("描かれているもの")}
                          for n, e in sorted(org["ページ"].items())},
              "画像だけのページ": no_text},
        answer_shape=OUTLINE_SHAPE)
    ans = ctx.caller.call(req)
    items = []
    if isinstance(ans.payload, Mapping):
        for i, raw in enumerate(ans.payload.get("項目", []) or [], 1):
            if not isinstance(raw, Mapping):
                continue
            src = [int(x) for x in (raw.get("出典") or []) if isinstance(x, (int, float)) and 1 <= int(x) <= len(ctx.pages)]
            items.append({
                "id": nfkc(raw.get("id")) or f"G{i:03d}", "室": nfkc(raw.get("室")) or UNDECIDED,
                "部位": nfkc(raw.get("部位")) or UNDECIDED, "工事": nfkc(raw.get("工事")),
                "区分": nfkc(raw.get("区分")) or UNDECIDED, "科目": nfkc(raw.get("科目")) or UNDECIDED,
                "品番": nfkc(raw.get("品番")), "数量": _num(raw.get("数量")), "単位": nfkc(raw.get("単位")),
                "出典": src, "探す図": [int(x) for x in (raw.get("探す図") or []) if isinstance(x, (int, float))],
            })
    else:
        ctx.stop("工事概略", "工事概略の答えが未取得。図面の根拠探しは「概略に無い」だけになる")
    return {"出どころ": "AI" if items else UNKNOWN, "渡したページ": pages, "画像で渡したページ": no_text, "項目": items}


def _compact_outline(items: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    return [[it["id"], it["室"], it["部位"], it["工事"], it["区分"], it["品番"]] for it in items]


def v2_read_understand(ctx: Context, org: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """V2: 工事概略 → 根拠探し(3 ページずつ)→ 落ちを測る(読み直しは無い)→ 理解の形にする。"""
    started = time.perf_counter()
    plan = outline(ctx, org)
    targets = ai_pages(ctx, org)
    batches = _batches(ctx, targets)
    legends = _legend_images(ctx, org)
    reqs = [AIRequest(stage="根拠探し", key=_key(b, len(batches) == 1), instructions=prompt("根拠探し"),
                      images=[ctx.page(n).image for n in b] + [p for p in legends if p not in [ctx.page(n).image for n in b]],
                      data={"工事概略": {"列": ["id", "室", "部位", "工事", "区分", "品番"],
                                     "項目": _compact_outline(plan["項目"])},
                            "文字の層": {str(n): ctx.page(n).text for n in b}},
                      answer_shape=SEARCH_SHAPE) for b in batches]
    answers = ctx.caller.map(reqs, ctx.parallel)
    by_id = {it["id"]: it for it in plan["項目"]}
    marks: dict[int, list[tuple[str, list[float]]]] = {n: [] for n in targets}
    found: dict[tuple[int, str], dict[str, Any]] = {}
    extra: list[tuple[int, Mapping[str, Any], list[list[float]]]] = []
    unknown_pages: list[int] = []
    for b, ans in zip(batches, answers):
        if not isinstance(ans.payload, Mapping):
            unknown_pages += b
            continue
        seen = set()
        for entry in ans.payload.get("ページ", []) or []:
            if not isinstance(entry, Mapping):
                continue
            try:
                n = int(entry.get("ページ"))
            except (TypeError, ValueError):
                continue
            if n not in b:
                continue
            seen.add(n)
            for f in entry.get("見つけた", []) or []:
                if not isinstance(f, Mapping):
                    continue
                gid = nfkc(f.get("id"))
                boxes = _boxes(f.get("位置"))
                marks[n] += [(f"概略 {gid}", bx) for bx in boxes]
                slot = found.setdefault((n, gid), {"boxes": [], "nums": [], "nulls": 0, "conf": []})
                slot["boxes"] += boxes
                q = _num(f.get("数"))
                if q is None:
                    slot["nulls"] += 1
                else:
                    slot["nums"].append(q)
                slot["conf"].append(nfkc(f.get("確度")))
            for x in entry.get("概略に無い", []) or []:
                if isinstance(x, Mapping):
                    boxes = _boxes(x.get("位置"))
                    marks[n] += [("概略に無い", bx) for bx in boxes]
                    extra.append((n, x, boxes))
        unknown_pages += [n for n in b if n not in seen]
    if unknown_pages:
        ctx.stop("根拠探し", f"根拠探しの答えが未取得のページ {sorted(unknown_pages)}(そのページは見つけた 0 のまま)")
    reading = _reading_from_boxes(marks)
    for n in unknown_pages:
        reading[n]["注"] = "根拠探しの答えにこのページが無い(未取得)"
    t = time.perf_counter()
    measured = [n for n in targets if n not in unknown_pages]
    after = measure_misses(ctx.pdf, reading, measured) if measured else {}
    ctx.timings["読む: 落ちを測る(機械)"] = round(time.perf_counter() - t, 1)
    read_out = _reading_summary(ctx, reading, targets, None, after, [], [])
    read_out["通読の落ち"] = read_out["読み直した後の落ち"]

    items: list[dict[str, Any]] = []
    counters: dict[int, int] = {}

    def nxt(n: int) -> int:
        counters[n] = counters.get(n, 0) + 1
        return counters[n]

    order = {"高": 0, "中": 1, "低": 2}
    found_ids = {gid for (_, gid) in found}
    for g in plan["項目"]:
        src = g["出典"][0] if g["出典"] else (plan["渡したページ"][0] if plan["渡したページ"] else 1)
        common = dict(部位=g["部位"], 場所=g["室"], 区分=g["区分"], 工事=g["工事"], 科目=g["科目"], 品番=g["品番"],
                      単位=g["単位"], 何=g["工事"], 根拠の種類="図面から読んだ")
        if g["id"] in found_ids:
            items.append(_item(src, nxt(src), [], **common, 数量=g["数量"], 状態="観測", 確度="中",
                               理由=f"仕様書など(概略 {g['id']}、出典 {g['出典']})"))
        else:
            items.append(_item(src, nxt(src), [], **common, 数量=g["数量"], 状態="問い", 確度="低",
                               選択肢=list(NOT_FOUND_OPTIONS),
                               理由=f"概略 {g['id']} の根拠が図面で見つからない(無いことの証拠にしない)"))
    for (n, gid), slot in sorted(found.items()):
        g = by_id.get(gid)
        if g is None:
            continue
        qty = sum(slot["nums"]) if slot["nums"] else None
        conf = max(slot["conf"] or ["低"], key=lambda c: order.get(c, 2))
        conf = conf if conf in order else "低"
        note = f"見つけた {len(slot['nums']) + slot['nulls']} か所の数の和" if slot["nums"] else ""
        if slot["nums"] and slot["nulls"]:
            note += f"(数が書かれていない {slot['nulls']} か所は入れていない)"
        items.append(_item(n, nxt(n), slot["boxes"], 部位=g["部位"], 場所=g["室"], 区分=g["区分"], 工事=g["工事"],
                           科目=g["科目"], 品番=g["品番"], 数量=qty, 単位=g["単位"], 式=note, 何=g["工事"],
                           状態="観測", 確度=conf, 理由=f"概略 {gid} の根拠"))
    for n, x, boxes in extra:
        items.append(_item(n, nxt(n), boxes, 部位=nfkc(x.get("部位")) or UNDECIDED, 場所=nfkc(x.get("室")) or UNDECIDED,
                           区分=nfkc(x.get("区分")) or UNDECIDED, 工事=nfkc(x.get("工事")), 何=nfkc(x.get("工事")),
                           数量=_num(x.get("数")), 状態="推論", 確度="低",
                           理由="概略(仕様書など)に無いが図面に描かれていた"))
    link_same_things(items)
    understanding = {"項目": items, "決められなかった要素": [], "未取得のページ": sorted(unknown_pages)}
    plan["見つからなかった項目"] = sorted(set(by_id) - found_ids)
    ctx.timings["V2(合計の壁時計)"] = round(time.perf_counter() - started, 1)
    return plan, read_out, understanding


# ---------------------------------------------------------------------------
# V3 閉じた語彙型
# ---------------------------------------------------------------------------

VOCAB_SHAPE = (
    '{"ページ": [{"ページ": n, "項目": [{"細目": "N04", "部位": "壁", "区分": "張替", "室": "R01", "単位": "m2", '
    '"数量": null, "式": "", "品番": "", "状態": "観測", "確度": "中", "位置": [[x0, y0, x1, y1]], "選択肢": []}], '
    '"書き込み": [["N1", x0, y0, x1, y1]], "分からない": [[x0, y0, x1, y1, "理由"]]}]}'
)


def room_table(original: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """仕上表の原本の室から室の表を作る(R01 から)。揃えた室名で重ねない。"""
    out, seen = [], set()
    for r in original:
        k = room_key(r["室"])
        if k and k not in seen:
            seen.add(k)
            out.append([f"R{len(out) + 1:02d}", r["室"]])
    return out


def _vocab_data(vocab: Mapping[str, Any], rooms: Sequence[Sequence[str]]) -> dict[str, Any]:
    return {
        "細目": [[s["id"], s["科目"], s["名前"], s["既定の単位"]] for s in vocab["細目"]],
        "部位": vocab["部位"], "区分": vocab["区分"], "単位": vocab["単位"], "状態": vocab["状態"], "確度": vocab["確度"],
        "工事でない書き込み": [[x["id"], x["名前"]] for x in vocab["工事でない書き込み"]],
        "室の表": [list(r) for r in rooms] + [["全体", "住戸全体"], ["未確定", "決められない"]],
    }


def v3_request(ctx: Context, batch: Sequence[int], key: str, vdata: Mapping[str, Any],
               legends: Sequence[Path]) -> AIRequest:
    imgs = [ctx.page(n).image for n in batch]
    return AIRequest(stage="語彙で読む", key=key, instructions=prompt("語彙で読む"),
                     images=imgs + [p for p in legends if p not in imgs],
                     data={"語彙": dict(vdata), "文字の層": {str(n): ctx.page(n).text for n in batch}},
                     answer_shape=VOCAB_SHAPE)


def _v3_pages(payload: Any, batch: Sequence[int]) -> dict[int, Mapping[str, Any]]:
    out = {}
    if isinstance(payload, Mapping):
        for e in payload.get("ページ", []) or []:
            if isinstance(e, Mapping):
                try:
                    n = int(e.get("ページ"))
                except (TypeError, ValueError):
                    continue
                if n in batch:
                    out[n] = e
    return out


def _v3_marks(entry: Mapping[str, Any]) -> list[tuple[str, list[float]]]:
    marks = []
    for it in entry.get("項目", []) or []:
        if isinstance(it, Mapping):
            marks += [(f"項目 {nfkc(it.get('細目'))}", b) for b in _boxes(it.get("位置"))]
    for w in entry.get("書き込み", []) or []:
        if isinstance(w, (list, tuple)) and len(w) >= 5:
            b = _clean_box(list(w[1:5]))
            if b:
                marks.append((f"書き込み {w[0]}", b))
    for u in entry.get("分からない", []) or []:
        if isinstance(u, (list, tuple)) and len(u) >= 4:
            b = _clean_box(list(u[:4]))
            if b:
                marks.append(("分からない", b))
    return marks


def _v3_items(n: int, entry: Mapping[str, Any], vocab: Mapping[str, Any], rooms: Mapping[str, str],
              start: int) -> list[dict[str, Any]]:
    by = {s["id"]: s for s in vocab["細目"]}
    items = []
    for i, it in enumerate(entry.get("項目", []) or [], start):
        if not isinstance(it, Mapping):
            continue
        sid = nfkc(it.get("細目"))
        s = by.get(sid)
        known = s is not None
        notes = [] if s else [f"細目 {sid or '空'} は語彙に無い id だった(X99 として扱う)"]
        s = s or by.get("X99") or {"科目": UNDECIDED, "名前": "その他(語彙に無い)"}
        room_id = nfkc(it.get("室"))
        place = rooms.get(room_id, room_id) or UNDECIDED
        if place == "決められない":
            place = UNDECIDED
        raw = {
            "要素": [], "読み取った値": "", "何": s["名前"], "部位": it.get("部位"), "場所": place,
            "区分": it.get("区分"), "工事": s["名前"], "科目": s["科目"], "品番": it.get("品番"),
            "数量": it.get("数量"), "単位": it.get("単位"), "式": it.get("式") or "", "状態": it.get("状態"),
            "確度": it.get("確度"), "根拠の種類": "図面から読んだ", "理由": f"細目 {sid}", "選択肢": it.get("選択肢") or [],
        }
        item = check_item(raw, n, {}, i)
        boxes = _boxes(it.get("位置"))
        item["位置"], item["囲み"] = boxes, _union(boxes)
        item["検算"] = notes + [x for x in item["検算"] if "読みに無い id" not in x]
        if item["状態"] == "仮説" and len(boxes) >= 2:
            item["検算"] = [x for x in item["検算"] if "仮説" not in x]
        item["細目"] = sid if known else ("X99" if "X99" in by else None)
        items.append(item)
    return items


def v3_read_understand(ctx: Context, org: Mapping[str, Any], transcribed: Mapping[str, Any],
                       vocab: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """V3: 室の表(仕上表の原本から)→ 語彙で読む(3 ページずつ)→ 落ちを測る → 33% を超えるページを 1 ページずつ読み直す。"""
    started = time.perf_counter()
    rooms = room_table(transcribed.get("original", []))
    vdata = _vocab_data(vocab, rooms)
    room_names = {r[0]: r[1] for r in rooms} | {"全体": "全体", "未確定": UNDECIDED}
    targets = ai_pages(ctx, org)
    legends = _legend_images(ctx, org)
    batches = _batches(ctx, targets)
    answers = ctx.caller.map([v3_request(ctx, b, _key(b, len(batches) == 1), vdata, legends) for b in batches],
                             ctx.parallel)
    entries: dict[int, Mapping[str, Any]] = {}
    for b, ans in zip(batches, answers):
        entries.update(_v3_pages(ans.payload, b))
    missing = [n for n in targets if n not in entries]
    if missing:
        ctx.stop("語彙で読む", f"語彙で読むの答えが未取得のページ {missing}(要素 0 のまま進めた)")
    reading = _reading_from_boxes({n: _v3_marks(entries[n]) for n in targets if n in entries})
    for n in missing:
        reading[n] = {"描かれているもの": "", "要素": [], "分からなかったもの": [], "形の崩れた要素": 0,
                      "注": "語彙で読むの答えにこのページが無い(未取得)"}
    t = time.perf_counter()
    measured = [n for n in targets if n in entries]
    before = measure_misses(ctx.pdf, reading, measured) if measured else {}
    ctx.timings["読む: 落ちを測る(機械、1回目)"] = round(time.perf_counter() - t, 1)
    chosen = [n for n in measured if before.get(n, {}).get("落ちた率", 0) > stages.REREAD_THRESHOLD]
    re_answers = ctx.caller.map([v3_request(ctx, [n], f"読み直し p{n}", vdata, legends) for n in chosen], ctx.parallel)
    reread, reread_missing = [], []
    for n, ans in zip(chosen, re_answers):
        e = _v3_pages(ans.payload, [n]).get(n)
        if e is None:
            reread_missing.append(n)
            continue
        entries[n] = e
        reading[n] = _reading_from_boxes({n: _v3_marks(e)})[n]
        reading[n]["読み直した"] = True
        reread.append(n)
    if reread_missing:
        ctx.stop("語彙で読む", f"読み直しの答えが未取得のページ {reread_missing}(1 回目の読みのまま進めた)")
    t = time.perf_counter()
    after = measure_misses(ctx.pdf, reading, measured) if measured else {}
    ctx.timings["読む: 落ちを測る(機械、2回目)"] = round(time.perf_counter() - t, 1)
    read_out = _reading_summary(ctx, reading, targets, before, after, reread, reread_missing)
    for n in measured:
        unk = [u for u in entries[n].get("分からない", []) or [] if isinstance(u, (list, tuple)) and len(u) >= 4]
        reading[n]["分からなかったもの"] = [{"位置": _clean_box(list(u[:4])), "理由": str(u[4]) if len(u) > 4 else ""}
                                     for u in unk]
    items: list[dict[str, Any]] = []
    for n in sorted(entries):
        items += _v3_items(n, entries[n], vocab, room_names, 1)
    link_same_things(items)
    understanding = {"項目": items, "決められなかった要素": [], "未取得のページ": missing}
    info = {"室の表": rooms, "語彙の細目の数": len(vocab["細目"]),
            "語彙に無い細目": sum(1 for it in items if any("語彙に無い id" in x for x in it["検算"]))}
    ctx.timings["V3(合計の壁時計)"] = round(time.perf_counter() - started, 1)
    return info, read_out, understanding
