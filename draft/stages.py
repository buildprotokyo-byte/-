"""一本道の段(K-61)。**どの段も数を作らない。読めなかったものは null / 「未取得」のまま渡す。**

段:
1. 整理(AI)… ページの種類・担当・読む順
2. 読む(AI が通読 → 機械が落ちを測る → 落ちが 33% を超えるページを AI が 1 ページずつ読み直す)
3. 理解(AI がページごとに、要素が何でどこのものかを決めて項目にする → 機械が検算する)
4. 仕上表(工事概略書)… 標準ひな型を必ず出す。原本があれば並べて違いを光らせる
5. 質問(機械)… 原本との違いと、読めなかった所と、AI が決められなかった所だけ
6. 組み立て(機械)… 公共書式・材料表・時間
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft.ai import AICaller, AIRequest
from draft.pages import TEXT_HEAD_CHARS, PageInfo

PROMPTS = Path(__file__).with_name("prompts")

#: 読み直すページの線(K-59 の既定): 通読の落ちがこれを超えるページ。
REREAD_THRESHOLD = 0.33
#: 読み直した後もこれを超えるページには「読み落としの可能性が高い」と付ける(仮の判断)。
HIGH_MISS_FLAG = 0.15
#: 消して確かめるの面積の上限(K-51 以降の既定)。
ERASE_AREA_CAP = 0.01

UNKNOWN = "未取得"
UNDECIDED = "未確定"
NO_ORIGINAL = "原本なし"

KIND_FINISH = "仕上表"
KIND_LEGEND = "凡例"
KIND_BLANK = "白紙"
FINISH_PARTS = ("床", "幅木", "壁", "天井")

STATES = ("観測", "推論", "仮説", "問い")
CONFIDENCE = ("高", "中", "低")
BASIS_KINDS = ("図面から読んだ", "凡例から", "公開基準から推論", "仮に置いた")

#: 原価表が無いときに先に聞く科目(K-60: P011 では木工・内装・電気で総額の約 75%)。**仮の判断。**
PRIORITY_KAMOKU = ("木工事", "内装", "電気設備")
#: 段階ごとの問いの上限と、拾う範囲(K-60 の表)。
MODES = {
    "概算": {"問いの上限": 3, "許容誤差": "±15%", "人の作業時間": "5分", "拾う範囲": "金額の大きい科目だけ"},
    "通常": {"問いの上限": 5, "許容誤差": "±10%", "人の作業時間": "10分", "拾う範囲": "その下の科目まで"},
    "精密": {"問いの上限": 10, "許容誤差": "±5%", "人の作業時間": "15分", "拾う範囲": "細目まで"},
}


def nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", "" if text is None else str(text)).strip()


def prompt(name: str, **values: Any) -> str:
    text = (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


@dataclass
class Context:
    """1 回の通しの持ち物。段は ``stops`` に失敗を書き、``state`` に出力を置く。"""

    pdf: Path
    pages: list[PageInfo]
    caller: AICaller
    parallel: int = 8
    state: dict[str, Any] = field(default_factory=dict)
    stops: list[dict[str, str]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    provisional: list[dict[str, str]] = field(default_factory=list)
    #: 通読の 1 回に渡すページ数。0 は全ページを 1 回で(K-59 の既定)。小さくすると並べて呼べる(時間を下げる案)。
    pass1_batch: int = 0
    #: 画像を送らず、文字の層(位置つき)だけで読ませるページ(K-62 の手段 a)。既定は空(今までどおり画像も送る)。
    text_only: set[int] = field(default_factory=set)
    #: K-64: OCR の語を文字の層の代わりに使ったページの語(幅 2000 画素の座標)。既定は空(今までどおり)。
    ocr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    def page(self, number: int) -> PageInfo:
        return self.pages[number - 1]

    def stop(self, stage: str, what: str) -> None:
        self.stops.append({"段": stage, "止まった所": what})


# ---------------------------------------------------------------------------
# 1. 整理
# ---------------------------------------------------------------------------

ORGANIZE_SHAPE = (
    '{"ページ": [{"ページ": n, "種類": "...", "描かれているもの": "...", "担当": "AI|機械|読まない", '
    '"理由": "..."}], "読む順": [n, ...]}'
)


def organize(ctx: Context) -> dict[str, Any]:
    """AI にページの種類と読む順を決めさせる。**答えが無ければ機械の見立て(白紙だけ)で進む。**"""
    heads = {str(p.number): p.text[:TEXT_HEAD_CHARS] for p in ctx.pages}
    req = AIRequest(
        stage="整理",
        key="全ページ",
        instructions=prompt("整理"),
        images=[p.thumb for p in ctx.pages],
        data={"文字の層の先頭": heads},
        answer_shape=ORGANIZE_SHAPE,
    )
    answer = ctx.caller.call(req)
    kinds: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    source = UNKNOWN
    if isinstance(answer.payload, Mapping):
        source = "AI"
        for row in answer.payload.get("ページ", []) or []:
            try:
                n = int(row.get("ページ"))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(ctx.pages):
                kinds[n] = {
                    "種類": nfkc(row.get("種類")) or UNKNOWN,
                    "描かれているもの": nfkc(row.get("描かれているもの")),
                    "担当": nfkc(row.get("担当")) or "AI",
                    "理由": nfkc(row.get("理由")),
                }
        for n in answer.payload.get("読む順", []) or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(ctx.pages) and n not in order:
                order.append(n)
    else:
        ctx.stop("整理", "AI の整理の答えが未取得。ページの種類は機械の見立て(白紙だけ)で進めた")
    for p in ctx.pages:
        entry = kinds.setdefault(p.number, {"種類": UNKNOWN, "描かれているもの": "", "担当": "AI", "理由": ""})
        if p.blank:
            # 白紙は機械が確かめられる(文字も線も画像も無い)。AI の見立てより優先する。
            entry.update({"種類": KIND_BLANK, "担当": "読まない", "理由": "文字・線・画像が無い(機械)"})
    order += [n for n in range(1, len(ctx.pages) + 1) if n not in order]
    return {"出どころ": source, "ページ": kinds, "読む順": order}


def pages_of_kind(org: Mapping[str, Any], kind: str) -> list[int]:
    return [n for n, v in org["ページ"].items() if v["種類"] == kind]


def ai_pages(ctx: Context, org: Mapping[str, Any]) -> list[int]:
    """AI が読むページ。**白紙だけを外す**(担当「機械」も今は AI が読む。K-59 の既定は全ページを読む)。"""
    return [n for n in org["読む順"] if not ctx.page(n).blank]


# ---------------------------------------------------------------------------
# 2. 読む
# ---------------------------------------------------------------------------

READ_SHAPE = (
    '{"ページ": [{"ページ": n, "描かれているもの": "...", "要素": [{"id": "pN-001", "種類": "...", '
    '"内容": "...", "位置": [x0, y0, x1, y1], "確かさ": "..."}], "分からなかったもの": '
    '[{"位置": [...], "理由": "..."}]}]}'
)


#: 手段 a で画像を送らない候補の種類(表・仕様書から。図の多いページは今までどおり画像を送る)。
TEXT_ONLY_KINDS = ("表紙・図面リスト", "概要", "仕様書", "仕上表", "建具表")
TEXT_ONLY_NOTE = ("このページの画像は渡していない。文字の層の語と、その位置(幅 2000 画素の画像の座標)だけで読む。"
                  "位置は語の位置を写してよい。線・罫・記号は見えないので推し量って挙げず、分からなかったものに書く。")


def text_only_pages(ctx: Context, org: Mapping[str, Any]) -> set[int]:
    """文字の層があり、表・仕様書の種類のページ(K-62 の手段 a・追記 3)。"""
    return {n for n, e in org.get("ページ", {}).items()
            if e.get("種類") in TEXT_ONLY_KINDS and ctx.page(int(n)).text.strip()}


def _images(ctx: Context, numbers: Sequence[int]) -> list[Path]:
    return [ctx.page(n).image for n in numbers if n not in ctx.text_only]


def _text_data(ctx: Context, numbers: Sequence[int]) -> dict[str, Any]:
    from draft.pages import positioned_words

    data: dict[str, Any] = {"文字の層": {str(n): ctx.page(n).text for n in numbers}}
    only = [n for n in numbers if n in ctx.text_only]
    if only:
        data["画像を渡していないページ"] = {"ページ": only, "読み方": TEXT_ONLY_NOTE}
        data["文字の層(位置つき)"] = {str(n): positioned_words(ctx.pdf, n) for n in only}
    ocr_pages = [n for n in numbers if n in ctx.ocr]
    if ocr_pages:
        from draft.ocr import positioned

        data["OCR で読んだ文字のページ"] = {
            "ページ": ocr_pages,
            "読み方": "このページには文字の層が無く、上の文字の層は OCR が読んだもの(読み違いがありうる)。"
                    "位置は幅 2000 画素の画像の座標。画像と食い違えば画像を信じ、食い違いは分からなかったものに書く。",
            "位置つき": {str(n): positioned(ctx.ocr, n) for n in ocr_pages},
        }
    return data


def pass1_request(ctx: Context, pages: Sequence[int], key: str = "全ページ") -> AIRequest:
    ordered = sorted(pages)
    return AIRequest(
        stage="通読",
        key=key,
        instructions=prompt("通読"),
        images=_images(ctx, ordered),
        data=_text_data(ctx, ordered),
        answer_shape=READ_SHAPE,
    )


def reread_request(ctx: Context, n: int) -> AIRequest:
    return AIRequest(
        stage="読み直し",
        key=f"p{n}",
        instructions=prompt("読み直し", page=n),
        images=_images(ctx, [n]),
        data=_text_data(ctx, [n]),
        answer_shape=READ_SHAPE,
    )


def _clean_box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def page_entries(payload: Any) -> dict[int, dict[str, Any]]:
    """読みの答えをページ → {描かれているもの, 要素, 分からなかったもの} にする。形の崩れた要素は落として数える。"""
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(payload, Mapping):
        return out
    for entry in payload.get("ページ", []) or []:
        if not isinstance(entry, Mapping):
            continue
        try:
            n = int(entry.get("ページ"))
        except (TypeError, ValueError):
            continue
        elements, dropped = [], 0
        seen: set[str] = set()
        for i, el in enumerate(entry.get("要素", []) or [], 1):
            if not isinstance(el, Mapping):
                dropped += 1
                continue
            box = _clean_box(el.get("位置"))
            eid = nfkc(el.get("id")) or f"p{n}-x{i:03d}"
            if eid in seen:
                eid = f"{eid}#{i}"
            seen.add(eid)
            elements.append({
                "id": eid,
                "種類": nfkc(el.get("種類")),
                "内容": str(el.get("内容") or ""),
                "位置": box,
                "確かさ": nfkc(el.get("確かさ")),
            })
        unknown = [
            {"位置": _clean_box(u.get("位置")), "理由": str(u.get("理由") or "")}
            for u in entry.get("分からなかったもの", []) or []
            if isinstance(u, Mapping)
        ]
        out[n] = {
            "描かれているもの": str(entry.get("描かれているもの") or ""),
            "要素": elements,
            "分からなかったもの": unknown,
            "形の崩れた要素": dropped,
        }
    return out


def measure_misses(pdf: Path, reading: Mapping[int, Mapping[str, Any]], pages: Sequence[int]) -> dict[int, dict[str, Any]]:
    """機械が、読みの四角で図形がどれだけ拾えたかを数える(K-51 の消して確かめる)。囮も同じ数え方で数える。"""
    import pymupdf

    from benchmarks import erase_check as ec
    from benchmarks.run_erase_check import make_decoys

    real = {n: [e for e in reading.get(n, {}).get("要素", []) if e.get("位置")] for n in pages}
    decoy = make_decoys({"読み": real}, pymupdf.open(pdf))["囮_読み"]
    out: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf) as doc:
        for n in pages:
            page = doc.load_page(n - 1)
            if is_image_only(page):
                # K-64: 図形も文字の層も無い画像だけのページ(スキャン)は、ページ全体の画像 1 枚を 1 つの図形と数えてしまい、
                # どう読んでも落ちが 100% になる。測れないので未取得とする(読み直しにも回さない。確度の旗では高を出さない)。
                out[n] = {"数える図形": 0, "落ちた": 0, "拾えた": 0, "落ちた率": None, "囮が拾えた": 0,
                          "注": "画像だけのページ(スキャン)。図形で数える落ちは測れない"}
                continue
            _, s = ec.check_page(page, n, real.get(n, []), ERASE_AREA_CAP)
            _, d = ec.check_page(page, n, decoy.get(n, []), ERASE_AREA_CAP)
            out[n] = {
                "数える図形": s["数える図形"],
                "落ちた": s["落ちた"],
                "拾えた": s["拾えた"],
                "落ちた率": round(s["落ちた"] / s["数える図形"], 4) if s["数える図形"] else 0.0,
                "囮が拾えた": d["拾えた"],
            }
    return out


def is_image_only(page: Any) -> bool:
    """図形も文字の層も無く、画像だけがあるページか(pymupdf のページ)。"""
    return bool(page.get_images()) and not page.get_drawings() and not page.get_text("words")


def _totals(m: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    count = sum(v["数える図形"] for v in m.values())
    miss = sum(v["落ちた"] for v in m.values())
    got = sum(v["拾えた"] for v in m.values())
    decoy = sum(v["囮が拾えた"] for v in m.values())
    unmeasured = sorted(n for n, v in m.items() if v["落ちた率"] is None)
    return {
        "数える図形": count,
        "落ちた": miss,
        "落ちた率": round(miss / count, 4) if count else None,
        "拾えた": got,
        "囮が拾えた": decoy,
        "拾えた − 囮": got - decoy,
        **({"測れなかったページ": unmeasured} if unmeasured else {}),
    }


def read(ctx: Context, org: Mapping[str, Any]) -> dict[str, Any]:
    """通読 → 落ちを測る → 33% を超えるページを読み直す → もう一度測る。"""
    targets = ai_pages(ctx, org)
    started = time.perf_counter()
    size = ctx.pass1_batch if ctx.pass1_batch > 0 else len(targets)
    batches = [sorted(targets)[i:i + size] for i in range(0, len(targets), size)] if targets else []
    reqs = [pass1_request(ctx, b, "全ページ" if len(batches) == 1 else f"p{b[0]}-p{b[-1]}") for b in batches]
    firsts = ctx.caller.map(reqs, ctx.parallel)
    ctx.timings["読む: 通読(AI、いちばん長い 1 回)"] = max((f.seconds or 0.0) for f in firsts) if firsts else 0.0
    reading: dict[int, dict[str, Any]] = {}
    for b, f in zip(batches, firsts):
        reading.update({n: e for n, e in page_entries(f.payload).items() if n in b})
    got_any = any(f.payload is not None for f in firsts)
    if not all(f.payload is not None for f in firsts):
        ctx.stop("読む", "通読の答えが未取得の回がある。読みの無いページは要素 0 のまま進めた")
    for n in targets:
        if n not in reading:
            reading[n] = {"描かれているもの": "", "要素": [], "分からなかったもの": [], "形の崩れた要素": 0,
                          "注": "通読の答えにこのページが無い(未取得)"}
    t = time.perf_counter()
    measured = [n for n in targets if "注" not in reading[n]]
    before = measure_misses(ctx.pdf, reading, measured) if got_any else {}
    ctx.timings["読む: 落ちを測る(機械、1回目)"] = round(time.perf_counter() - t, 1)
    chosen = [n for n in targets if (before.get(n, {}).get("落ちた率") or 0) > REREAD_THRESHOLD]
    answers = ctx.caller.map([reread_request(ctx, n) for n in chosen], ctx.parallel)
    ctx.timings["読む: 読み直し(AI、いちばん長い 1 ページ)"] = max((a.seconds or 0.0) for a in answers) if answers else 0.0
    reread_ok, reread_missing = [], []
    for n, ans in zip(chosen, answers):
        entry = page_entries(ans.payload).get(n)
        if entry is None:
            reread_missing.append(n)
            continue
        entry["読み直した"] = True
        reading[n] = entry
        reread_ok.append(n)
    if reread_missing:
        ctx.stop("読む", f"読み直しの答えが未取得のページ {reread_missing}(通読の読みのまま進めた)")
    t = time.perf_counter()
    after = measure_misses(ctx.pdf, reading, targets) if reading else {}
    ctx.timings["読む: 落ちを測る(機械、2回目)"] = round(time.perf_counter() - t, 1)
    pages_out = {}
    for n in targets:
        b, a = before.get(n), after.get(n)
        rate = a["落ちた率"] if a else None
        pages_out[n] = {
            "通読の落ちた率": b["落ちた率"] if b else None,
            "落ちた率": rate,
            "読み直した": n in reread_ok,
            "読み落としの可能性が高い": rate is None or rate > HIGH_MISS_FLAG,
        }
    ctx.timings["読む(合計の壁時計)"] = round(time.perf_counter() - started, 1)
    return {
        "読み": reading,
        "ページ": pages_out,
        "通読の落ち": _totals(before) if before else UNKNOWN,
        "読み直した後の落ち": _totals(after) if after else UNKNOWN,
        "読み直したページ": reread_ok,
        "読み直しが未取得のページ": reread_missing,
    }


# ---------------------------------------------------------------------------
# 3. 理解
# ---------------------------------------------------------------------------

UNDERSTAND_SHAPE = (
    '{"ページ": n, "項目": [{"要素": ["pN-001"], "読み取った値": "...", "何": "...", "部位": "...", '
    '"場所": "...", "区分": "...", "工事": "...", "科目": "...", "品番": "", "数量": 数 または null, '
    '"単位": "...", "式": "", "状態": "観測|推論|仮説|問い", "確度": "高|中|低", '
    '"根拠の種類": "図面から読んだ|凡例から|公開基準から推論|仮に置いた", "理由": "...", '
    '"選択肢": []}], "決められなかった要素": [{"id": "...", "理由": "..."}]}'
)


def understand_request(ctx: Context, n: int, org: Mapping[str, Any], elements: list[dict[str, Any]]) -> AIRequest:
    legends = [m for m in pages_of_kind(org, KIND_LEGEND) if m != n][:2]
    kind = org["ページ"].get(n, {}).get("種類", UNKNOWN)
    return AIRequest(
        stage="理解",
        key=f"p{n}",
        instructions=prompt("理解", page=n),
        images=_images(ctx, [n]) + [ctx.page(m).image for m in legends],
        data={
            "要素": [{k: e[k] for k in ("id", "種類", "内容", "位置", "確かさ")} for e in elements],
            "ページの種類": {"このページ": kind, "凡例のページ": legends},
        },
        answer_shape=UNDERSTAND_SHAPE,
    )


def _quantity(value: Any) -> tuple[float | None, str]:
    """数量は数か null だけを受ける。**文字の数を数に直さない**(直すのは答えを書き換えること)。"""
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return None, "数量が真偽値だった(未取得として扱う)"
    if isinstance(value, (int, float)):
        return float(value), ""
    return None, f"数量が数ではなかった({value!r})。未取得として扱う"


def _union(boxes: Sequence[Sequence[float]]) -> list[float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def check_item(raw: Mapping[str, Any], n: int, by_id: Mapping[str, Mapping[str, Any]], index: int) -> dict[str, Any]:
    """AI の項目を機械が検算する。**答えは書き換えず、食い違いを「検算」に書く。**"""
    notes: list[str] = []
    ids = [nfkc(x) for x in (raw.get("要素") or []) if nfkc(x)]
    known = [i for i in ids if i in by_id]
    if len(known) < len(ids):
        notes.append(f"根拠の要素のうち {len(ids) - len(known)} 個が読みに無い id だった")
    quantity, qnote = _quantity(raw.get("数量"))
    if qnote:
        notes.append(qnote)
    state = nfkc(raw.get("状態"))
    if state not in STATES:
        notes.append(f"状態が決まりの言葉でなかった({state or '空'})。「問い」として扱う")
        state = "問い"
    if state == "仮説" and len(known) < 2:
        notes.append("根拠の要素が 1 つ以下の仮説(決まりは 2 つ以上)")
    conf = nfkc(raw.get("確度"))
    if conf not in CONFIDENCE:
        notes.append(f"確度が決まりの言葉でなかった({conf or '空'})。「低」として扱う")
        conf = "低"
    basis = nfkc(raw.get("根拠の種類"))
    if basis not in BASIS_KINDS:
        notes.append(f"根拠の種類が決まりの言葉でなかった({basis or '空'})。「仮に置いた」として扱う")
        basis = "仮に置いた"
    symbols = [i for i in known if "記号" in by_id[i].get("種類", "")]
    formula = str(raw.get("式") or "")
    if quantity is not None and "記号" in formula and symbols and len(symbols) != quantity:
        notes.append(f"数えた記号の要素は {len(symbols)} 個、数量は {quantity:g}(機械の数えと食い違う)")
    options = [str(o) for o in (raw.get("選択肢") or []) if str(o).strip()]
    place = nfkc(raw.get("場所")) or UNDECIDED
    return {
        "id": f"u-p{n}-{index:03d}",
        "ページ": n,
        "要素": known,
        "位置": [by_id[i]["位置"] for i in known if by_id[i].get("位置")],
        "囲み": _union([by_id[i]["位置"] for i in known if by_id[i].get("位置")]),
        "読み取った値": str(raw.get("読み取った値") or ""),
        "何": str(raw.get("何") or ""),
        "部位": nfkc(raw.get("部位")) or UNDECIDED,
        "場所": place,
        "区分": nfkc(raw.get("区分")) or UNDECIDED,
        "工事": nfkc(raw.get("工事")),
        "科目": nfkc(raw.get("科目")) or UNDECIDED,
        "品番": nfkc(raw.get("品番")),
        "数量": quantity,
        "単位": nfkc(raw.get("単位")),
        "式": formula,
        "状態": state,
        "確度": conf,
        "根拠の種類": basis,
        "理由": str(raw.get("理由") or ""),
        "選択肢": options,
        "検算": notes,
    }


def understand(ctx: Context, org: Mapping[str, Any], reading: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    # 読みが未取得のページ(通読の答えに無い)は理解に回さない。理解も未取得のまま表示する。
    pages = [n for n in org["読む順"] if n in reading["読み"] and "注" not in reading["読み"][n]]
    skipped = [n for n in org["読む順"] if n in reading["読み"] and "注" in reading["読み"][n]]
    reqs = [understand_request(ctx, n, org, reading["読み"][n]["要素"]) for n in pages]
    answers = ctx.caller.map(reqs, ctx.parallel)
    ctx.timings["理解(AI、いちばん長い 1 ページ)"] = max((a.seconds or 0.0) for a in answers) if answers else 0.0
    items: list[dict[str, Any]] = []
    undecided: list[dict[str, Any]] = []
    missing: list[int] = []
    for n, ans in zip(pages, answers):
        if not isinstance(ans.payload, Mapping):
            missing.append(n)
            continue
        by_id = {e["id"]: e for e in reading["読み"][n]["要素"]}
        for i, raw in enumerate(ans.payload.get("項目", []) or [], 1):
            if isinstance(raw, Mapping):
                items.append(check_item(raw, n, by_id, i))
        for u in ans.payload.get("決められなかった要素", []) or []:
            if isinstance(u, Mapping):
                eid = nfkc(u.get("id"))
                undecided.append({"ページ": n, "id": eid, "理由": str(u.get("理由") or ""),
                                  "位置": by_id.get(eid, {}).get("位置")})
    if missing:
        ctx.stop("理解", f"理解の答えが未取得のページ {missing}(そのページの項目は 0 件のまま。未取得と表示する)")
    if skipped:
        ctx.stop("理解", f"読みが未取得なので理解に回さなかったページ {skipped}")
        missing = sorted(set(missing) | set(skipped))
    link_same_things(items)
    ctx.timings["理解(合計の壁時計)"] = round(time.perf_counter() - started, 1)
    return {"項目": items, "決められなかった要素": undecided, "未取得のページ": missing}


#: K-64 周 1: 確度「高」を出してよいページの読めた割合の下限(= 1 − HIGH_MISS_FLAG)。**測る前に固定した値。**
PAGE_READ_FLOOR = round(1 - HIGH_MISS_FLAG, 4)


def page_read_rates(reading: Mapping[str, Any]) -> dict[int, float | None]:
    """ページごとの読めた割合(1 − 最後の読みの落ちた率)。測れなかったページは None。"""
    out: dict[int, float | None] = {}
    for n, v in (reading.get("ページ") or {}).items():
        rate = v.get("落ちた率") if isinstance(v, Mapping) else None
        out[int(n)] = None if rate is None else round(1 - float(rate), 4)
    return out


def cap_by_page_readability(items: list[dict[str, Any]], reading: Mapping[str, Any],
                            floor: float = PAGE_READ_FLOOR) -> dict[str, Any]:
    """読めた割合が ``floor`` 未満(または測れない)ページの項目は、確度「高」を「中」に下げる(K-64 周 1、旗の裏)。

    **下げるだけで上げない。中・低は動かさない。** 下げた項目には「確度の上限」に理由を書く。
    人の回答で決めた項目は下げない(読みではなく人の答えが根拠のため)。
    """
    rates = page_read_rates(reading)
    lowered: list[str] = []
    for it in items:
        if it["確度"] != "高" or it.get("根拠の種類") == "人の回答":
            continue
        r = rates.get(it["ページ"])
        if r is not None and r >= floor:
            continue
        it["確度"] = "中"
        it["確度の上限"] = (f"ページの読めた割合 {r:.0%} が {floor:.0%} 未満なので高を出さない" if r is not None
                        else "ページの読めた割合が測れなかったので高を出さない")
        lowered.append(it["id"])
    low_pages = sorted(n for n, r in rates.items() if r is None or r < floor)
    return {"読めた割合の下限": floor, "下限を下回ったページ": low_pages, "高から中に下げた項目": len(lowered),
            "下げた項目": lowered}


def same_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    return (nfkc(item["工事"]), room_key(item["場所"]), nfkc(item["品番"]), nfkc(item["区分"]))


def link_same_things(items: list[dict[str, Any]]) -> None:
    """別のページに同じ工事・場所・品番・区分の項目があれば結ぶ(**二重に数えないため**)。

    数量が同じなら 1 つとして組み立て、違えば数量を未取得にして両方を見せる(どちらかを選ばない)。
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in items:
        if item["工事"] and item["場所"] != UNDECIDED:
            groups.setdefault(same_key(item), []).append(item)
    for members in groups.values():
        pages = {m["ページ"] for m in members}
        if len(pages) < 2:
            continue
        ids = [m["id"] for m in members]
        for m in members:
            m["同じもの"] = [i for i in ids if i != m["id"]]


# ---------------------------------------------------------------------------
# 4. 仕上表(工事概略書)
# ---------------------------------------------------------------------------

ORIGINAL_SHAPE = (
    '{"行": [{"室": "...", "部位": "床|幅木|壁|天井|その他", "仕上": "...", "下地": "", '
    '"位置": [x0, y0, x1, y1], "確かさ": "..."}], "読めなかった所": [{"位置": [...], "理由": "..."}]}'
)


def _finish_pages(ctx: Context, org: Mapping[str, Any]) -> tuple[list[int], str]:
    pages = pages_of_kind(org, KIND_FINISH)
    if pages or org["出どころ"] == "AI":
        return pages, "整理の段(AI)"
    # 整理が未取得のときだけ、文字の層に「仕上表」「内部仕上」があるページを候補にする(仮の判断)。
    guess = [p.number for p in ctx.pages if re.search(r"仕上表|内部仕上", p.text[:TEXT_HEAD_CHARS])]
    return guess, "文字の層の語(整理が未取得のため)"


#: 同じ室の別の書き方(K-62 の 4)。**略し方が一意に決まるものだけ**。「リビングダイニング」と「LDK」のように
#: 中身が違うかもしれないものは揃えない。「トイレ・洗面室」のような 2 室の書き方も揃えない(残る揺れとして数える)。
ROOM_SYNONYMS = {
    "ウォークインクローゼット": "WIC", "ウォークインクロゼット": "WIC", "W.I.C": "WIC", "WCL": "WIC",
    "シューズインクローゼット": "SIC", "シューズインクロゼット": "SIC", "S.I.C": "SIC",
}
_LDK_PARTS = {"リビング": "L", "ダイニング": "D", "キッチン": "K"}


def room_key(text: Any) -> str:
    """室名を揃えた鍵。全角半角・空白・括弧の違いと、一意に決まる略し方だけを揃える。"""
    s = re.sub(r"[\s()()]", "", nfkc(text))
    upper = s.upper()
    for name, short in ROOM_SYNONYMS.items():
        if upper == nfkc(name).upper().replace(" ", ""):
            return short
    parts = [x for x in re.split(r"[/・、,]", nfkc(text).replace(" ", "/")) if x]
    if len(parts) > 1 and all(x in _LDK_PARTS for x in parts) and len(set(parts)) == len(parts):
        letters = "".join(sorted((_LDK_PARTS[x] for x in parts), key="LDK".index))
        if letters in ("LDK", "LD", "DK"):
            return letters
    return upper if re.fullmatch(r"[A-Za-z0-9.]+", s) else s


def room_variants(names: Sequence[Any]) -> list[dict[str, Any]]:
    """揃えた室名ごとに、元の書き方が 2 つ以上あったもの(揃えた件数を数えるため)。"""
    groups: dict[str, set[str]] = {}
    for n in names:
        if nfkc(n) and nfkc(n) != UNDECIDED:
            groups.setdefault(room_key(n), set()).add(nfkc(n))
    return [{"揃えた名前": k, "元の書き方": sorted(v)} for k, v in sorted(groups.items()) if len(v) > 1]


_norm_room = room_key


def template_rows(items: Sequence[Mapping[str, Any]], skip_pages: set[int], kinds: Mapping[int, Mapping[str, Any]],
                  extra_rooms: Sequence[str] = ()) -> list[dict[str, Any]]:
    """標準ひな型(室 × 床・幅木・壁・天井)を、仕上表以外のページの項目から埋める。**無ければ未取得。**"""
    rooms: list[str] = []
    for item in items:
        if item["ページ"] in skip_pages or item["場所"] in (UNDECIDED, ""):
            continue
        if item["部位"] in FINISH_PARTS and room_key(item["場所"]) not in {room_key(r) for r in rooms}:
            rooms.append(item["場所"])
    for room in extra_rooms:
        if _norm_room(room) not in {_norm_room(r) for r in rooms}:
            rooms.append(room)
    rows = []
    for room in rooms:
        for part in FINISH_PARTS:
            found = [
                it for it in items
                if it["ページ"] not in skip_pages and _norm_room(it["場所"]) == _norm_room(room) and it["部位"] == part
            ]
            if found:
                values = []
                for it in found:
                    v = " ".join(x for x in (it["工事"], it["品番"]) if x)
                    if v and v not in values:
                        values.append(v)
                order = {c: i for i, c in enumerate(CONFIDENCE)}
                conf = max((it["確度"] for it in found), key=lambda c: order.get(c, 2))
                rows.append({
                    "室": room, "部位": part, "仕上": " / ".join(values) or UNKNOWN, "確度": conf,
                    "根拠": [{"資料の種類": kinds.get(it["ページ"], {}).get("種類", UNKNOWN), "ページ": it["ページ"],
                              "項目": it["id"]} for it in found],
                })
            else:
                rows.append({"室": room, "部位": part, "仕上": UNKNOWN, "確度": "—", "根拠": []})
    return rows


def _norm_finish(text: str) -> str:
    return re.sub(r"[\s・、,/()()]", "", nfkc(text)).lower()


def compare(template: Sequence[Mapping[str, Any]], original: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """ひな型と原本を室・部位で並べる。**少しずれたもの(片方が片方を含む)は「近い」として残す。**"""
    orig_by = {}
    for row in original:
        orig_by.setdefault((_norm_room(row["室"]), row["部位"]), []).append(row)
    out = []
    used = set()
    for row in template:
        key = (_norm_room(row["室"]), row["部位"])
        o = orig_by.get(key, [])
        used.add(key)
        out.append(_pair(row, o))
    for key, rows in orig_by.items():
        if key not in used:
            out.append(_pair(None, rows))
    rank = {s: i for i, s in enumerate(("違う", "近い", "原本のみ", "ひな型のみ", "一致", "両方未取得"))}
    out.sort(key=lambda c: rank.get(c["照らし合わせ"], 9))  # 違いを先に見せる(並べ替えは安定)
    return out


def _pair(t: Mapping[str, Any] | None, o: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ov = " / ".join(r["仕上"] for r in o if r.get("仕上")) if o else ""
    tv = t["仕上"] if t else ""
    room = t["室"] if t else o[0]["室"]
    part = t["部位"] if t else o[0]["部位"]
    tn, on = _norm_finish(tv if tv != UNKNOWN else ""), _norm_finish(ov)
    if not o:
        status = "ひな型のみ" if tn else "両方未取得"
    elif not tn:
        status = "原本のみ"
    elif tn == on:
        status = "一致"
    elif tn in on or on in tn:
        status = "近い"
    else:
        status = "違う"
    return {
        "室": room, "部位": part,
        "原本": ov or ("空欄" if o else ""),
        "原本の位置": [{"ページ": r.get("ページ"), "位置": r.get("位置")} for r in o],
        "ひな型": tv or UNKNOWN,
        "ひな型の確度": t["確度"] if t else "—",
        "ひな型の根拠": t["根拠"] if t else [],
        "照らし合わせ": status,
    }


def finish_original(ctx: Context, org: Mapping[str, Any]) -> dict[str, Any]:
    """仕上表の原本を書き写す(AI)。ひな型とは別に先に呼べる(K-63 の V3 は室の表に使う)。"""
    pages, how = _finish_pages(ctx, org)
    original: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    missing: list[int] = []
    reqs = [
        AIRequest(stage="仕上表の原本", key=f"p{n}", instructions=prompt("仕上表の原本", page=n),
                  images=[ctx.page(n).image], data={"文字の層": {str(n): ctx.page(n).text}},
                  answer_shape=ORIGINAL_SHAPE)
        for n in pages
    ]
    for n, ans in zip(pages, ctx.caller.map(reqs, ctx.parallel)):
        if not isinstance(ans.payload, Mapping):
            missing.append(n)
            continue
        for r in ans.payload.get("行", []) or []:
            if isinstance(r, Mapping) and nfkc(r.get("室")):
                part = nfkc(r.get("部位"))
                original.append({
                    "ページ": n, "室": nfkc(r.get("室")), "部位": part if part in FINISH_PARTS else "その他",
                    "部位(書かれたまま)": part, "仕上": str(r.get("仕上") or ""), "下地": str(r.get("下地") or ""),
                    "位置": _clean_box(r.get("位置")), "確かさ": nfkc(r.get("確かさ")),
                })
        for u in ans.payload.get("読めなかった所", []) or []:
            if isinstance(u, Mapping):
                unreadable.append({"ページ": n, "位置": _clean_box(u.get("位置")), "理由": str(u.get("理由") or "")})
    if missing:
        ctx.stop("仕上表", f"仕上表の原本の書き写しが未取得のページ {missing}")
    return {"pages": pages, "how": how, "original": original, "unreadable": unreadable, "missing": missing}


def finish_schedule(ctx: Context, org: Mapping[str, Any], understanding: Mapping[str, Any],
                    transcribed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    t = transcribed if transcribed is not None else finish_original(ctx, org)
    pages, how, original, unreadable, missing = (t["pages"], t["how"], t["original"], t["unreadable"], t["missing"])
    rooms = [r["室"] for r in original]
    kinds = org["ページ"]
    template = template_rows(understanding["項目"], set(pages), kinds, rooms)
    # K-61 の判断 2: 仕上の表を持つ別の図面(カラースキームなど)も原本と数えるが、本来の仕上表ではないと示す。
    not_proper = []
    for n in pages:
        drawn = nfkc(kinds.get(n, {}).get("描かれているもの"))
        if drawn and not re.search(r"仕上(げ)?表|仕上(げ)?一覧", drawn):
            not_proper.append({"ページ": n, "図面": drawn})
    if not pages:
        status = NO_ORIGINAL
    elif missing and not original:
        status = "原本はあるが書き写しが未取得"
    else:
        status = "原本あり"
    return {
        "原本": status,
        "原本のページ": pages,
        "原本のページの見つけ方": how,
        "原本の行": original,
        "原本の読めなかった所": unreadable,
        "本来の仕上表ではないページ": not_proper,
        "室名を揃えた": room_variants([it["場所"] for it in understanding["項目"]] + rooms),
        "ひな型": template,
        "照らし合わせ": compare(template, [r for r in original if r["部位"] in FINISH_PARTS]) if original else
        [dict(_pair(t, []), 照らし合わせ=NO_ORIGINAL if not pages else UNKNOWN) for t in template],
    }


# ---------------------------------------------------------------------------
# 5. 質問
# ---------------------------------------------------------------------------

UNREADABLE_OPTIONS = [
    "工事に関係する書き込み(新設・撤去・交換など)",
    "寸法・注記など、見積の行にはならない書き込み",
    "図枠・凡例・表の飾りなど",
    "分からない(現地・設計者に確認する)",
]


def _kamoku_rank(kamoku: str) -> int:
    for i, k in enumerate(PRIORITY_KAMOKU):
        if k in kamoku or kamoku in k:
            return i
    return len(PRIORITY_KAMOKU)


def question_candidates(understanding: Mapping[str, Any], finish: Mapping[str, Any],
                        reading: Mapping[str, Any], cost_table: Mapping[str, float] | None) -> list[dict[str, Any]]:
    """問いの候補。**原本との違い・AI が決められなかった所・読めなかった所だけ。推奨は付けない。**"""
    items = understanding["項目"]
    qs: list[dict[str, Any]] = []
    for c in finish["照らし合わせ"]:
        if c["照らし合わせ"] not in ("違う", "近い", "原本のみ", "ひな型のみ"):
            continue
        if c["照らし合わせ"] in ("違う", "近い"):
            options = [f"原本(仕上表)のとおり: {c['原本']}", f"図面の読みのとおり: {c['ひな型']}",
                       "どちらでもない(現地・設計者に確認する)"]
        elif c["照らし合わせ"] == "原本のみ":
            options = [f"原本(仕上表)のとおり工事する: {c['原本']}", "この室・部位は今回の工事に入らない",
                       "分からない(現地・設計者に確認する)"]
        else:
            options = [f"図面の読みのとおり: {c['ひな型']}", "この室・部位は今回の工事に入らない",
                       "分からない(現地・設計者に確認する)"]
        related = [it["id"] for it in items if _norm_room(it["場所"]) == _norm_room(c["室"]) and it["部位"] == c["部位"]]
        pages = sorted({p["ページ"] for p in c["原本の位置"] if p.get("ページ")} |
                       {r["ページ"] for r in c["ひな型の根拠"]})
        qs.append({
            "種類": "原本との違い", "科目": "内装", "問い": f"{c['室']}の{c['部位']}の仕上は、どれですか",
            "選択肢": options, "見る所": pages, "関係する項目": related,
            "鍵": f"仕上:{_norm_room(c['室'])}:{c['部位']}", "位置": c["原本の位置"][:1],
        })
    for it in items:
        if it["状態"] != "問い":
            continue
        options = list(it["選択肢"]) or []
        if not any("分からない" in o or "確認" in o for o in options):
            options.append("分からない(現地・設計者に確認する)")
        qs.append({
            "種類": "決められなかった所", "科目": it["科目"],
            "問い": f"{it['ページ']}ページ: {it['何'] or it['読み取った値']}({it['場所']})は、どれですか",
            "選択肢": options, "見る所": [it["ページ"]], "関係する項目": [it["id"]] + it.get("同じもの", []),
            "鍵": f"項目:{it['id']}", "位置": [{"ページ": it["ページ"], "位置": it["囲み"]}],
            "数量": it["数量"], "工事": it["工事"], "品番": it["品番"],
        })
    for n, entry in reading["読み"].items():
        for i, u in enumerate(entry.get("分からなかったもの", []), 1):
            qs.append({
                "種類": "読めなかった所", "科目": UNDECIDED,
                "問い": f"{n}ページのこの場所が読めませんでした({u['理由'][:40]})。何が書かれていますか",
                "選択肢": list(UNREADABLE_OPTIONS), "見る所": [n], "関係する項目": [],
                "鍵": f"読めない:{n}:{i}", "位置": [{"ページ": n, "位置": u["位置"]}],
            })
    for q in qs:
        q["金額"] = _amount(q, cost_table)
    return qs


def _amount(q: Mapping[str, Any], cost_table: Mapping[str, float] | None) -> float | None:
    if not cost_table:
        return None
    price = cost_table.get(nfkc(q.get("品番"))) or cost_table.get(nfkc(q.get("工事")))
    qty = q.get("数量")
    return price * qty if price is not None and isinstance(qty, (int, float)) else None


def rank_questions(qs: list[dict[str, Any]], mode: str, answered: set[str], has_cost: bool) -> list[dict[str, Any]]:
    """段階(概算・通常・精密)の上限まで選ぶ。**原価表があれば金額の大きい順、無ければ科目の順と関係する行の数。**"""
    limit = MODES[mode]["問いの上限"]
    pool = [q for q in qs if q["鍵"] not in answered]
    if mode == "概算":
        pool = [q for q in pool if _kamoku_rank(q["科目"]) < len(PRIORITY_KAMOKU)]
    if has_cost:
        pool.sort(key=lambda q: (-(q["金額"] or -1), _kamoku_rank(q["科目"])))
    else:
        kind_rank = {"原本との違い": 0, "決められなかった所": 1, "読めなかった所": 2}
        pool.sort(key=lambda q: (_kamoku_rank(q["科目"]), kind_rank[q["種類"]], -len(q["関係する項目"]), q["鍵"]))
    chosen = []
    for i, q in enumerate(pool[:limit], 1):
        chosen.append(dict(q, 番号=f"Q{i}"))
    return chosen


def questions(understanding: Mapping[str, Any], finish: Mapping[str, Any], reading: Mapping[str, Any],
              cost_table: Mapping[str, float] | None, answers: Mapping[str, str] | None,
              applied: Sequence[str] | None = None) -> dict[str, Any]:
    qs = question_candidates(understanding, finish, reading, cost_table)
    answers = dict(answers or {})
    applied = set(applied or ())
    valid = {}
    for key, value in answers.items():
        match = next((q for q in qs if q["鍵"] == key), None)
        choice, fields = answer_parts(value)
        if key in applied or (match is not None and (choice in match["選択肢"] or (choice is None and fields))):
            valid[key] = value
    has_cost = bool(cost_table)
    out = {
        "並べ方": "1 つ答えると確定する金額の大きい順" if has_cost else
        "金額の順ではない(原価表 未取得)。木工事・内装・電気設備を先に、その中は関係する項目の多い順",
        "候補の数": {k: sum(1 for q in qs if q["種類"] == k) for k in ("原本との違い", "決められなかった所", "読めなかった所")},
        "段階ごと": {mode: rank_questions(qs, mode, set(valid), has_cost) for mode in MODES},
        "受け取った答え": [{"鍵": k, "答え": v} for k, v in valid.items()],
        "受け取れなかった答え": [{"鍵": k, "答え": v} for k, v in answers.items() if k not in valid],
    }
    return out


#: 答えに数・値で書いてよい欄(K-64 周 4)。**選択肢の文字からは値を推し量らない。**
ANSWER_FIELDS = ("数量", "単位", "場所", "区分", "工事", "科目", "品番")
DONT_KNOW = "分からない"
OUT_OF_SCOPE = "今回の工事に入らない"


def answer_parts(value: Any) -> tuple[str | None, dict[str, Any]]:
    """答えを (選んだ選択肢の文字, 値の欄) に分ける。

    答えは選択肢の文字か、``{"選択肢": "...", "数量": 3, "単位": "枚", ...}`` の形。数量は数だけを受ける(文字は受けない)。
    """
    if isinstance(value, Mapping):
        choice = value.get("選択肢")
        fields = {k: value[k] for k in ANSWER_FIELDS if k in value and value[k] not in (None, "")}
        if "数量" in fields and (isinstance(fields["数量"], bool) or not isinstance(fields["数量"], (int, float))):
            fields.pop("数量")
        return (str(choice) if choice not in (None, "") else None), fields
    return (str(value) if value not in (None, "") else None), {}


def _answer_text(choice: str | None, fields: Mapping[str, Any]) -> str:
    parts = [choice] if choice else []
    parts += [f"{k}: {v}" for k, v in fields.items()]
    return " / ".join(parts)


def apply_answers(understanding: dict[str, Any], finish: dict[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """人の答えを戻す(K-64 周 4: **内訳の数量と確度も変える**)。

    - 決められなかった所: 選択肢を選ぶと状態「観測」・確度「高」・根拠「人の回答」。数量などの値が書いてあればその値にする。
      **分からないと答えたら何も決めない**(状態は「問い」のまま)。
    - 原本との違い: 照らし合わせを「人の回答で決めた」にする。「図面の読みのとおり」なら同じ室・部位の項目を確度「高」・
      根拠「人の回答」にする。「原本のとおり」では図面の読みの項目を確かにしない(答えを書くだけ)。
      「今回の工事に入らない」と答えた室・部位の項目は内訳から外す(「外した行」に残す)。
    - 読めなかった所: 答えを記録するだけ(数を作らない)。
    """
    before = sum(1 for c in finish["照らし合わせ"] if c["照らし合わせ"] in ("違う", "近い", "原本のみ", "ひな型のみ"))
    items = understanding["項目"]
    applied: list[str] = []
    changes: list[dict[str, Any]] = []

    def decide(it: dict[str, Any], text: str, fields: Mapping[str, Any], key: str) -> None:
        old = {k: it.get(k) for k in ("数量", "単位", "確度", "状態")}
        it["人の回答"] = text
        it["状態"] = "観測"
        it["確度"] = "高"
        it["根拠の種類"] = "人の回答"
        for k, v in fields.items():
            it[k] = float(v) if k == "数量" else nfkc(v)
        it.pop("確度の上限", None)
        changes.append({"鍵": key, "項目": it["id"], "前": old, "後": {k: it.get(k) for k in old}})

    for c in finish["照らし合わせ"]:
        key = f"仕上:{_norm_room(c['室'])}:{c['部位']}"
        if key not in answers or c["照らし合わせ"] not in ("違う", "近い", "原本のみ", "ひな型のみ"):
            continue
        choice, fields = answer_parts(answers[key])
        if not choice or DONT_KNOW in choice:
            continue
        c["人の回答"] = choice
        c["照らし合わせ"] = "人の回答で決めた"
        applied.append(key)
        related = [it for it in items if _norm_room(it["場所"]) == _norm_room(c["室"]) and it["部位"] == c["部位"]]
        for it in related:
            if OUT_OF_SCOPE in choice:
                it["人の回答"] = choice
                it["外す"] = "人の回答: この室・部位は今回の工事に入らない"
                it["根拠の種類"] = "人の回答"
                changes.append({"鍵": key, "項目": it["id"], "前": {"外す": False}, "後": {"外す": True}})
            elif choice.startswith("図面の読みのとおり"):
                decide(it, choice, fields, key)
            else:
                # 原本のとおり・どちらでもない: 図面の読みの項目を確かにはしない(確度は動かさず、答えだけ書く)
                it["仕上の人の回答"] = choice
    for it in items:
        key = f"項目:{it['id']}"
        if key not in answers or it["状態"] != "問い":
            continue
        choice, fields = answer_parts(answers[key])
        if choice and choice not in it["選択肢"] and DONT_KNOW not in choice:
            continue  # 選択肢に無い文字は受け取らない
        if (choice and DONT_KNOW in choice) or (not choice and not fields):
            it["人の回答"] = choice or ""
            applied.append(key)
            continue
        decide(it, _answer_text(choice, fields), fields, key)
        applied.append(key)
    for key in answers:
        choice, _ = answer_parts(answers[key])
        if key.startswith("読めない:") and choice in UNREADABLE_OPTIONS:
            applied.append(key)
    after = sum(1 for c in finish["照らし合わせ"] if c["照らし合わせ"] in ("違う", "近い", "原本のみ", "ひな型のみ"))
    return {"答えた数": len(answers), "戻せた答え": len(applied), "戻した鍵": applied,
            "原本との違い(前)": before, "原本との違い(後)": after,
            "数量・確度が変わった項目": changes,
            "内訳から外した項目": [it["id"] for it in items if it.get("外す")]}


# ---------------------------------------------------------------------------
# 6. 組み立て
# ---------------------------------------------------------------------------


def assembly_rows(items: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """項目を内訳の行にする。**別のページの同じもの(同じ工事・場所・品番・区分)は 1 行にまとめ、二重に数えない。**"""
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    done: set[str] = set()
    by_id = {it["id"]: it for it in items}
    for it in items:
        if it["id"] in done or not it["工事"] or it.get("外す"):
            continue
        group = [it] + [by_id[i] for i in it.get("同じもの", []) if i in by_id and not by_id[i].get("外す")]
        done.update(g["id"] for g in group)
        qtys = {g["数量"] for g in group if g["数量"] is not None}
        quantity: float | None = None
        note = ""
        if len(qtys) == 1:
            quantity = qtys.pop()
            if len(group) > 1:
                note = f"{len(group)} ページに同じ数量(二重に数えない)"
        elif len(qtys) > 1:
            note = "ページで数量が違う: " + " / ".join(f"{g['ページ']}ページ {g['数量']}" for g in group)
            conflicts.append({"工事": it["工事"], "場所": it["場所"], "数量": [g["数量"] for g in group],
                              "項目": [g["id"] for g in group]})
        rows.append({
            "科目": it["科目"] if it["科目"] != UNDECIDED else "",
            "区分": it["区分"] if it["区分"] != UNDECIDED else "",
            "工事項目": it["工事"],
            "摘要": it["品番"],
            "場所": it["場所"] if it["場所"] != UNDECIDED else "",
            "数量": quantity,
            "単位": it["単位"],
            "項目": [g["id"] for g in group],
            "メモ": note,
        })
    return rows, conflicts


def materials(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """材料表: 品番ごとに数量・場所。**数量が未取得のものは合計に入れず「未取得」の件数で見せる。**"""
    table: dict[tuple[str, str], dict[str, Any]] = {}
    for it in items:
        if not it["品番"] or it.get("外す"):
            continue
        key = (it["品番"], it["単位"])
        row = table.setdefault(key, {"品番": it["品番"], "名称": it["何"] or it["工事"], "単位": it["単位"],
                                     "数量の合計(分かった分)": 0.0, "未取得の件数": 0, "場所": [], "項目": []})
        if it.get("同じもの") and any(i in row["項目"] for i in it["同じもの"]):
            row["項目"].append(it["id"])
            continue
        if it["数量"] is None:
            row["未取得の件数"] += 1
        else:
            row["数量の合計(分かった分)"] += it["数量"]
        if it["場所"] not in row["場所"]:
            row["場所"].append(it["場所"])
        row["項目"].append(it["id"])
    out = sorted(table.values(), key=lambda r: (r["品番"], r["単位"]))
    for r in out:
        if r["未取得の件数"] and not r["数量の合計(分かった分)"]:
            r["数量の合計(分かった分)"] = UNKNOWN
    return out


def labor(rows: Sequence[Mapping[str, Any]], rates: Mapping[str, Mapping[str, float]] | None) -> list[dict[str, Any]]:
    """時間の計算。**歩掛(職人 1 人 1 日あたりの進み方)が入っていなければ「未入力」。0 にしない。**"""
    out = []
    for r in rows:
        rate = (rates or {}).get(r["工事項目"])
        per_day = rate.get("1人1日あたり") if rate else None
        wage = rate.get("日当") if rate else None
        if per_day and r["数量"] is not None:
            days = r["数量"] / per_day
            out.append({"工事項目": r["工事項目"], "場所": r["場所"], "数量": r["数量"], "単位": r["単位"],
                        "1人1日あたり": per_day, "人日": round(days, 2), "時間": round(days * 8, 1),
                        "作業費": round(days * wage) if wage else "未入力"})
        else:
            out.append({"工事項目": r["工事項目"], "場所": r["場所"], "数量": r["数量"] if r["数量"] is not None else UNKNOWN,
                        "単位": r["単位"], "1人1日あたり": "未入力", "人日": "未入力", "時間": "未入力", "作業費": "未入力"})
    return out


def mode_outputs(rows: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """段階ごとの出力(K-61 追記)。

    - 概算(±15%): 金額の大きい科目(`PRIORITY_KAMOKU`)の行だけ。ほかの科目は「概算では拾わない」に名前だけ残す。
    - 通常(±10%): すべての科目の行。
    - 精密(±5%): 細目まで。**検算を全部通す**: 検算で食い違った項目・数量が未取得の行を「通っていない」として並べる。
    **どの段階も、数量が未取得の行は未取得のまま(足し上げに入れない)。**
    """
    def total(sel: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {"行": len(sel), "数量のある行": sum(1 for r in sel if r["数量"] is not None),
                "数量が未取得の行": sum(1 for r in sel if r["数量"] is None)}

    big = [r for r in rows if _kamoku_rank(r["科目"]) < len(PRIORITY_KAMOKU)]
    others = sorted({r["科目"] or "科目未定" for r in rows if r not in big})
    by_id = {it["id"]: it for it in items}
    not_passed = []
    for r in rows:
        problems = [n for i in r["項目"] for n in by_id.get(i, {}).get("検算", [])]
        if r["数量"] is None:
            problems.append("数量が未取得")
        if r["メモ"].startswith("ページで数量が違う"):
            problems.append(r["メモ"])
        if problems:
            not_passed.append({"工事項目": r["工事項目"], "場所": r["場所"], "通っていない検算": problems})
    return {
        "概算": {**MODES["概算"], **total(big), "拾う科目": list(PRIORITY_KAMOKU), "概算では拾わない科目": others,
               "注": "原価表が無いので「金額の大きい科目」は K-60 の P011 の見立て(木工・内装・電気)を仮に使った"},
        "通常": {**MODES["通常"], **total(rows)},
        "精密": {**MODES["精密"], **total(rows), "検算を通っていない行": not_passed,
               "検算を全部通ったか": not not_passed and bool(rows)},
    }
