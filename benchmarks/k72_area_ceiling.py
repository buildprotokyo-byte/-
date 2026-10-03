"""K-72 作業 C: 数量の無い行のうち、面積・長さの行が「根拠つきで」いくつ出せるかの上限を数える(診断だけ)。

基準は `docs/k72_area_ceiling_criteria.md`(数える前にコミットした)。**本番のコードは変えない。AI は呼ばない。
正解は開かない。数量は 1 つも計算して書き出さない**(出せるかどうかの件数だけ)。

使い方(K-61 が保存した全部あり版の 3 回と、匿名化 v4 の PDF と、K-37 の対応づけを読むだけ)::

    PYTHONPATH=. python -m benchmarks.k72_area_ceiling \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --k37-rooms <reports/K-37/p011_rooms.json> \\
      --sides <reports/K-72/作業C/k37_室の辺の写し.json> \\
      --out docs/k72_area_ceiling_result.json

出すのは件数と割合とページの種類だけ(室名・工事名・品番・寸法の値は書かない。室は番号でも書かない)。
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# --- 分け方(基準 3 節) -------------------------------------------------------

A = "A 数量にできる(いま)"
B1 = "B1 目盛りの直しだけ"
B2 = "B2 AI の対応づけのやり直しが要る"
B3 = "B3 直しでも読めない"
C = "C 分からない(図面に室の寸法が無い)"
D1 = "D1 場所が室に結べない"
D2 = "D2 室の寸法では決まらない"
U = "U 分けられない"
CATEGORIES = (A, B1, B2, B3, C, D1, D2, U)
#: 複数の室に結べた行は、いちばん悪いものにする(基準 3-2)。
SEVERITY = {A: 0, B1: 1, B2: 2, B3: 3, C: 4}

AREA_UNITS = ("m2", "㎡", "m²")
LENGTH_UNITS = ("m",)
#: 部位 → (単位の種類, 天井高が要るか)。ここに無い部位・単位の組は D2(基準 3-1)。
PART_NEEDS = {
    ("床", "面積"): False,
    ("天井", "面積"): False,
    ("壁", "面積"): True,
    ("幅木", "長さ"): False,
}
#: 開口(戸・窓)を引かないので、出せても上限の側に倒れる部位(基準 3-1)。
OPENING_PARTS = {("壁", "面積"), ("幅木", "長さ")}

#: 辺の状態(v4 で確かめた後)。
SIDE_READ = "v4 で読めている"
SIDE_RULER = "目盛りを当てた読みで読める(対応づけ済み)"
SIDE_NEEDS_AI = "機械が読めていない(目盛りで読めるが対応づけが無い)"
SIDE_UNREADABLE = "直しでも読めない"
SIDE_ABSENT = "図面に書いていない"
#: 写しの状態。
COPY_MAPPED = "対応づけ済み"
COPY_UNREAD = "機械が読めていない"
COPY_ABSENT = "図面に書いていない"

UNDECIDED = "未確定"
_SPLIT_PLACE = re.compile(r"[\s・/、,,]+")
VALUE_TOL_MM = 0.5


def nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def unit_kind(unit: Any) -> str | None:
    u = nfkc(unit)
    if u in {nfkc(x) for x in AREA_UNITS}:
        return "面積"
    if u in LENGTH_UNITS:
        return "長さ"
    return None


def rooms_of(place: Any, room_keys: Iterable[str], key=None) -> list[str] | None:
    """`場所` が指す室の鍵。**全部が知っている室に当たるときだけ**返す(1 つでも外れれば None = D1)。"""
    if key is None:
        from draft.stages import room_key as key
    keys = set(room_keys)
    text = nfkc(place)
    if not text or text == UNDECIDED:
        return None
    full = key(text)
    if full in keys:
        return [full]
    out: list[str] = []
    for part in (p for p in _SPLIT_PLACE.split(text) if p):
        k = key(part)
        if k not in keys:
            return None
        if k not in out:
            out.append(k)
    return out or None


def find_unique(readings: Sequence[Mapping[str, Any]], value: float, orientation: str | None) -> str | None:
    """値(±0.5mm)と向きが同じ読みが**ちょうど 1 つ**ならその id。0 か 2 つ以上なら None。"""
    hits = [r["id"] for r in readings if abs(float(r["値"]) - float(value)) <= VALUE_TOL_MM
            and (orientation is None or r["向き"] == orientation)]
    return hits[0] if len(hits) == 1 else None


def side_state(side: Mapping[str, Any], orientation: str, plain: Sequence[Mapping[str, Any]],
               ruler: Sequence[Mapping[str, Any]]) -> str:
    """写しの辺 1 つを、v4 の読みで確かめた状態にする(基準 2 節・3-2)。"""
    state = side.get("状態")
    if state == COPY_ABSENT:
        return SIDE_ABSENT
    if state == COPY_MAPPED:
        values = side.get("値") or []
        if values and all(find_unique(plain, v, orientation) for v in values):
            return SIDE_READ
        if values and all(find_unique(plain, v, orientation) or find_unique(ruler, v, orientation) for v in values):
            return SIDE_RULER
        return SIDE_UNREADABLE
    if state == COPY_UNREAD:
        values = side.get("値の候補") or []
        # 目盛りを当てた読みに値の候補が(向きを問わず)出ていれば、読めるが対応づけが無い。
        if values and all(any(abs(float(r["値"]) - float(v)) <= VALUE_TOL_MM for r in ruler) for v in values):
            return SIDE_NEEDS_AI
        return SIDE_UNREADABLE
    raise ValueError(f"写しの辺の状態が分からない: {state!r}")


def room_category(sides: Mapping[str, str], *, not_rectangle: bool = False) -> str:
    """室 1 つの、床・天井・幅木(天井高の要らない行)の分け先。"""
    states = list(sides.values())
    if SIDE_ABSENT in states:
        return C
    if all(s == SIDE_READ for s in states) and not not_rectangle:
        return A
    if SIDE_UNREADABLE in states:
        return B3
    if SIDE_NEEDS_AI in states or not_rectangle:
        return B2
    return B1


def classify_row(item: Mapping[str, Any], rooms: Mapping[str, Mapping[str, Any]], key=None) -> tuple[str, dict[str, Any]]:
    """数量の無い面積・長さの行 1 つを分ける(基準 3-2。上から順に見る)。

    ``rooms``: 室の鍵 → {"分け先": room_category の結果, "天井高": 印字が v4 に在るか, "長方形でない": bool}。
    返すのは (分け先, 印)。印は「開口を引かない」「長方形でない」「天井高が無い」。
    """
    kind = unit_kind(item.get("単位"))
    if kind is None:
        raise ValueError("面積・長さでない行は classify_row に渡さない")
    marks: dict[str, Any] = {}
    keys = rooms_of(item.get("場所"), rooms.keys(), key=key)
    if keys is None:
        return D1, marks
    part = nfkc(item.get("部位"))
    if (part, kind) not in PART_NEEDS:
        return D2, marks
    needs_ceiling = PART_NEEDS[(part, kind)]
    if (part, kind) in OPENING_PARTS:
        marks["開口を引かない"] = True
    worst = A
    for k in keys:
        r = rooms[k]
        cat = r["分け先"]
        if cat != C and needs_ceiling and not r.get("天井高"):
            cat = C
            marks["天井高が無い"] = True
        if r.get("長方形でない"):
            marks["長方形でない"] = True
        if SEVERITY[cat] > SEVERITY[worst]:
            worst = cat
    return worst, marks


def count_rows(items: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
               page_kind: Mapping[int, str], key=None) -> dict[str, Any]:
    """1 回ぶんの数量の無い行を数える(件数だけ)。"""
    missing = [it for it in items if it.get("数量") is None]
    other = Counter(nfkc(it.get("単位")) or "(空欄)" for it in missing if unit_kind(it.get("単位")) is None)
    cats: Counter = Counter()
    by_part: dict[str, Counter] = {}
    by_page: dict[str, Counter] = {}
    marks: Counter = Counter()
    per_unit: Counter = Counter()
    for it in missing:
        kind = unit_kind(it.get("単位"))
        if kind is None:
            continue
        per_unit[kind] += 1
        try:
            cat, m = classify_row(it, rooms, key=key)
        except Exception:  # noqa: BLE001  分けられないものは U に数える(理由を足さない)
            cat, m = U, {}
        cats[cat] += 1
        by_part.setdefault(cat, Counter())[f"{nfkc(it.get('部位')) or '(空欄)'}・{kind}"] += 1
        by_page.setdefault(cat, Counter())[page_kind.get(int(it.get("ページ") or 0), "(ページ不明)")] += 1
        for name in m:
            marks[f"{cat} / {name}"] += 1
    area_len = sum(per_unit.values())
    counts = {c: cats.get(c, 0) for c in CATEGORIES}
    return {
        "項目": len(items),
        "数量が無い項目": len(missing),
        "面積・長さの行": area_len,
        "面積・長さの行(単位の種類ごと)": dict(per_unit),
        "面積・長さでない行": sum(other.values()),
        "面積・長さでない行(単位ごと)": dict(other.most_common()),
        "分け先": counts,
        "分け先の割合(面積・長さの行に対して)": {c: (round(v / area_len, 4) if area_len else None)
                                       for c, v in counts.items()},
        "1行1つの確かめ(分け先の和 = 面積・長さの行)": sum(counts.values()) == area_len,
        "まとめ": {
            "数量にできる(A)": counts[A],
            "読み手の直し・AI の手順があれば(B1+B2+B3)": counts[B1] + counts[B2] + counts[B3],
            "上限(A+B1+B2)": counts[A] + counts[B1] + counts[B2],
            "図面に情報が無い(C)": counts[C],
            "その他(D1+D2)": counts[D1] + counts[D2],
            "分けられない(U)": counts[U],
        },
        "部位ごと": {c: dict(v.most_common()) for c, v in by_part.items()},
        "ページの種類ごと": {c: dict(v.most_common()) for c, v in by_page.items()},
        "印": dict(sorted(marks.items())),
    }


# --- v4 の読み(実データ。共有フォルダと PDF を読む) ---------------------------------


def _readings(page: Any) -> list[dict[str, Any]]:
    from intake.drawing_room_dimensions import dimension_ids

    return [{"id": i, "値": r.value_mm, "向き": r.orientation} for i, r in dimension_ids([page]).items()]


def v4_readings(pdf: Path, page_no: int, ruler_value_mm: float, ruler_orientation: str) -> dict[str, Any]:
    """素の読みと、K-37 の基準の寸法の比を当てた読み(K-38 の口)。基準が 1 つに決まらなければ目盛りは当てない。"""
    from axes.image_axis.pdf_dimensions import read_dimensions

    plain_page = read_dimensions(pdf, page_no - 1)
    plain = _readings(plain_page)
    ref_id = find_unique(plain, ruler_value_mm, ruler_orientation)
    ruler: list[dict[str, Any]] = []
    if ref_id is not None:
        ref = next(r for i, r in zip((x["id"] for x in plain), plain_page.readings) if i == ref_id)
        ruler_page = read_dimensions(pdf, page_no - 1, ruler_mm_per_point=ref.value_mm / ref.paper_distance_pt,
                                     ruler_tolerance=0.01)
        ruler = _readings(ruler_page)
    return {"素の読み": plain, "目盛りを当てた読み": ruler, "基準が 1 つに決まった": ref_id is not None,
            "表の升目で落とした数字": sum(1 for s in plain_page.skipped if "表の升目" in s.reason)}


def printed_ceilings(pdf: Path, page_no: int) -> set[int]:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        text = nfkc(doc.load_page(page_no - 1).get_text("text"))
    return {int(v) for v in re.findall(r"CH\s*=\s*(\d{3,5})", text)}


def room_table(k37_rooms: Mapping[str, Any], sides_copy: Mapping[str, Any], reads: Mapping[str, Any],
               ceilings: Mapping[int, set[int]], key=None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """室の鍵 → 分け先。名前は鍵としてだけ使い、外に書かない。"""
    if key is None:
        from draft.stages import room_key as key
    by_no = {int(r["番号"]): r for r in sides_copy["室"]}
    out: dict[str, dict[str, Any]] = {}
    side_counts: Counter = Counter()
    room_counts: Counter = Counter()
    for no, room in enumerate(k37_rooms["室"]):
        copy = by_no[no]
        sides = {o: side_state(copy[o], o, reads["素の読み"], reads["目盛りを当てた読み"]) for o in ("横", "縦")}
        for s in sides.values():
            side_counts[s] += 1
        nr = bool(copy.get("長方形でない"))
        cat = room_category(sides, not_rectangle=nr)
        room_counts[cat] += 1
        ch = room.get("天井高_mm")
        page = room.get("天井高のページ")
        has_ch = ch is not None and page is not None and int(ch) in ceilings.get(int(page), set())
        out[key(nfkc(room["室名"]).replace("\n", "・"))] = {"分け先": cat, "天井高": has_ch, "長方形でない": nr}
    summary = {
        "室の数": len(out),
        "室の分け先(床・天井・幅木)": {c: room_counts.get(c, 0) for c in (A, B1, B2, B3, C)},
        "辺の状態(20 辺)": dict(side_counts.most_common()),
        "天井高の印字が v4 に在る室": sum(1 for r in out.values() if r["天井高"]),
        "長方形でないとされた室": sum(1 for r in out.values() if r["長方形でない"]),
    }
    return out, summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-72 作業 C 面積・長さの数量の上限(診断だけ)")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--k37-rooms", type=Path, required=True)
    p.add_argument("--sides", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)

    from axes.image_axis.pdf_dimensions import read_dimensions

    k37 = json.loads(a.k37_rooms.read_text(encoding="utf-8"))
    copy = json.loads(a.sides.read_text(encoding="utf-8"))
    base = copy["基準の寸法"]
    reads = v4_readings(a.pdf, int(base["ページ"]), float(base["値_mm"]), base["向き"])
    ch_pages = {int(r["天井高のページ"]) for r in k37["室"] if r.get("天井高のページ") is not None}
    ceilings = {n: printed_ceilings(a.pdf, n) for n in ch_pages}
    rooms, room_summary = room_table(k37, copy, reads, ceilings)

    runs = {}
    plan_pages: set[int] = set()
    for d in a.runs:
        draft = json.loads((d / "下書き.json").read_text(encoding="utf-8"))
        kinds = {int(n): v["種類"] for n, v in draft["整理"]["ページ"].items()}
        plan_pages |= {n for n, k in kinds.items() if k == "平面図"}
        runs[d.name] = count_rows(draft["理解"]["項目"], rooms, kinds)

    plan = {}
    for n in sorted(plan_pages):
        pg = read_dimensions(a.pdf, n - 1)
        plan[str(n)] = {"素の読み": len(pg.readings),
                        "表の升目で落とした数字": sum(1 for s in pg.skipped if "表の升目" in s.reason),
                        "落とした数字の和": len(pg.skipped)}
    result = {
        "材料": "K-61 が保存した P011 匿名化 v4 の全部あり版 3 回・匿名化 v4 の PDF・K-37 の対応づけ(v2 の id。値で v4 に移した)",
        "AI を呼んだ回数": 0,
        "書き出した数量": 0,
        "385 の再現(基準の回 R1 の 数量が無い項目)": runs[a.runs[0].name]["数量が無い項目"],
        "回ごと": runs,
        "室の側": room_summary,
        "基準の寸法のページの読み": {
            "素の読み": len(reads["素の読み"]),
            "目盛りを当てた読み": len(reads["目盛りを当てた読み"]),
            "基準が 1 つに決まった": reads["基準が 1 つに決まった"],
        },
        "平面図のページ(素の読み)": plan,
    }
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
