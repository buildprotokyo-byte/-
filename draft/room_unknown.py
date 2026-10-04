"""図面に室の寸法が無い行に「分からない」を出し、室ごとに 1 問を作る(K-73 作業 4。旗 ``--with-room-unknown``、既定オフ)。

基準は `docs/k73_room_unknown_criteria.md`(測る前にコミットした)。

- (a) K-72 作業 C の分け方で **C(図面に室の寸法が無い)** になった行に、「分からない(図面に室の寸法が無い)」と、
  探したページ(平面図・展開図・仕上表・縮尺換算)を付ける。**数量は書き換えない(None のまま)。推測で埋めない。**
- (b) 室ごとに 1 問。メーター(確定する行数・金額の割合・回答時間の見積)。概算では聞かない。
- (c) **4 つの種類の全部で機械が「無かった」と確かめた室だけ**、図面に無い辺の数字(mm)の入力を認める
  (K-65 の「数字の入力は禁止」への例外。`docs/k73_k65_numeric_input_exception.md`)。入力には「人の入力」の印。
- (d) 答えが戻ったら、要る辺が全部そろった行だけ数量を出す(旗の欄にだけ。状態「推論」・確度「中」・自動確定にしない)。

**室の名前は図面の文字を探す鍵として中で使うだけ。出力には室の番号しか書かない。** AI は呼ばない。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REASON = "分からない(図面に室の寸法が無い)"
NO_CEILING = "図面に天井高が無い"
SEARCH_KINDS = ("平面図", "展開図", "仕上表", "縮尺換算")
FOUND_NONE = "無かった"
FOUND_CANDIDATE = "候補があった"
NOT_SEARCHED = "見られなかった"
HUMAN = "人の入力"
KEY_PREFIX = "室の寸法:"

OPT_NUMBER = "数字を入れる(縦・横 mm)"
OPT_DONT_KNOW = "分からない"
OPT_SITE = "現地で実測する"
OPT_NOT_IN_DRAWING = "図面に無い"
#: K-65 の型ごとの秒(`数量` 40 秒)。**未較正。**
SECONDS_PER_QUESTION = 40
#: 段階ごとの上限(K-37 / `draft.stages.MODES`)。概算では聞かない。
LEVEL_LIMITS = {"概算": 0, "通常": 5, "精密": 10}

# --- K-72 作業 C の分け方(基準 1 節。`benchmarks/k72_area_ceiling.py` と同じ決まり) -------------------
SIDE_READ = "v4 で読めている"
SIDE_RULER = "目盛りを当てた読みで読める(対応づけ済み)"
SIDE_NEEDS_AI = "機械が読めていない(目盛りで読めるが対応づけが無い)"
SIDE_UNREADABLE = "直しでも読めない"
SIDE_ABSENT = "図面に書いていない"
COPY_MAPPED = "対応づけ済み"
COPY_UNREAD = "機械が読めていない"
COPY_ABSENT = "図面に書いていない"
CAT_A, CAT_B1, CAT_B2, CAT_B3, CAT_C = "A", "B1", "B2", "B3", "C"
SEVERITY = {CAT_A: 0, CAT_B1: 1, CAT_B2: 2, CAT_B3: 3, CAT_C: 4}
AREA_UNITS = ("m2", "㎡", "m²")
#: 部位 → (単位の種類, 天井高が要るか)
PART_NEEDS = {("床", "面積"): False, ("天井", "面積"): False, ("壁", "面積"): True, ("幅木", "長さ"): False}
OPENING_PARTS = {("壁", "面積"), ("幅木", "長さ")}
VALUE_TOL_MM = 0.5
SIDES = ("横", "縦")
UNDECIDED = "未確定"
UNKNOWN = "未取得"
_SPLIT_PLACE = re.compile(r"[\s・/、,,]+")
_SPLIT_NAME = re.compile(r"[\s・\n]+")

# --- 機械の探し方(基準 2 節) ---------------------------------------------------------------
DIM_MIN_MM, DIM_MAX_MM = 300, 20000
_AREA_WORD = re.compile(r"^\d+(\.\d+)?(m2|帖|畳|J)$")
_PAIR_WORD = re.compile(r"\d{3,5}\s*[x×*]\s*\d{3,5}")
_PURE_NUMBER = re.compile(r"^\d{3,5}$")


def nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", "" if text is None else str(text)).strip()


def unit_kind(unit: Any) -> str | None:
    u = nfkc(unit)
    if u in {nfkc(x) for x in AREA_UNITS}:
        return "面積"
    if u == "m":
        return "長さ"
    return None


def _key(text: str) -> str:
    from draft.stages import room_key

    return room_key(text)


def rooms_of(place: Any, room_keys: Iterable[str]) -> list[str] | None:
    """`場所` が指す室の鍵。全部が知っている室に当たるときだけ(K-72 と同じ)。"""
    keys = set(room_keys)
    text = nfkc(place)
    if not text or text == UNDECIDED:
        return None
    full = _key(text)
    if full in keys:
        return [full]
    out: list[str] = []
    for part in (p for p in _SPLIT_PLACE.split(text) if p):
        k = _key(part)
        if k not in keys:
            return None
        if k not in out:
            out.append(k)
    return out or None


def find_unique(readings: Sequence[Mapping[str, Any]], value: float, orientation: str | None) -> str | None:
    hits = [r["id"] for r in readings if abs(float(r["値"]) - float(value)) <= VALUE_TOL_MM
            and (orientation is None or r["向き"] == orientation)]
    return hits[0] if len(hits) == 1 else None


def side_state(side: Mapping[str, Any], orientation: str, plain: Sequence[Mapping[str, Any]],
               ruler: Sequence[Mapping[str, Any]]) -> tuple[str, str | None]:
    """写しの辺 1 つを v4 の読みで確かめる。返すのは (状態, v4 の id)。"""
    state = side.get("状態")
    if state == COPY_ABSENT:
        return SIDE_ABSENT, None
    if state == COPY_MAPPED:
        values = side.get("値") or []
        ids = [find_unique(plain, v, orientation) for v in values]
        if values and all(ids):
            return SIDE_READ, ids[0] if len(ids) == 1 else None
        if values and all(find_unique(plain, v, orientation) or find_unique(ruler, v, orientation) for v in values):
            return SIDE_RULER, None
        return SIDE_UNREADABLE, None
    if state == COPY_UNREAD:
        values = side.get("値の候補") or []
        if values and all(any(abs(float(r["値"]) - float(v)) <= VALUE_TOL_MM for r in ruler) for v in values):
            return SIDE_NEEDS_AI, None
        return SIDE_UNREADABLE, None
    raise ValueError(f"写しの辺の状態が分からない: {state!r}")


def room_category(sides: Mapping[str, str], *, not_rectangle: bool = False) -> str:
    states = list(sides.values())
    if SIDE_ABSENT in states:
        return CAT_C
    if all(s == SIDE_READ for s in states) and not not_rectangle:
        return CAT_A
    if SIDE_UNREADABLE in states:
        return CAT_B3
    if SIDE_NEEDS_AI in states or not_rectangle:
        return CAT_B2
    return CAT_B1


def classify_row(item: Mapping[str, Any], rooms: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict[str, Any], list[str]]:
    """数量の無い面積・長さの行の分け先(K-72 3-2)。返すのは (分け先, 印, 結べた室の鍵)。D1・D2 は "D1"・"D2"。"""
    kind = unit_kind(item.get("単位"))
    if kind is None:
        raise ValueError("面積・長さでない行")
    keys = rooms_of(item.get("場所"), rooms.keys())
    if keys is None:
        return "D1", {}, []
    part = nfkc(item.get("部位"))
    if (part, kind) not in PART_NEEDS:
        return "D2", {}, keys
    needs_ceiling = PART_NEEDS[(part, kind)]
    marks: dict[str, Any] = {}
    if (part, kind) in OPENING_PARTS:
        marks["開口を引かない"] = True
    worst = CAT_A
    for k in keys:
        r = rooms[k]
        cat = r["分け先"]
        if cat != CAT_C and needs_ceiling and not r.get("天井高"):
            cat = CAT_C
            marks["天井高が無い"] = True
        if needs_ceiling and not r.get("天井高"):
            marks["天井高が無い"] = True
        if r.get("長方形でない"):
            marks["長方形でない"] = True
        if SEVERITY[cat] > SEVERITY[worst]:
            worst = cat
    return worst, marks, keys


def c_rows(items: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """C の行(数量が None・面積か長さ・分け先 C)。"""
    out = []
    for it in items:
        if it.get("数量") is not None or unit_kind(it.get("単位")) is None:
            continue
        try:
            cat, marks, keys = classify_row(it, rooms)
        except Exception:  # noqa: BLE001  分けられない行は C に入れない(K-72 の U)
            continue
        if cat == CAT_C:
            out.append({"item": it, "keys": keys, "marks": marks, "kind": unit_kind(it.get("単位")),
                        "part": nfkc(it.get("部位"))})
    return out


# --- 機械の確かめ(基準 2 節。純粋な判定) -----------------------------------------------------


def plan_candidates(label_centers: Sequence[tuple[float, float]], readings: Sequence[Mapping[str, Any]],
                    orientation: str, excluded_values: Iterable[float]) -> list[Mapping[str, Any]]:
    """平面図: 向きが同じで、寸法線の 2 点の範囲が室の名前の中心を挟む読み(ほかの室の値は除く)。"""
    excluded = list(excluded_values)
    axis = 0 if orientation == "横" else 1
    out = []
    for r in readings:
        if r["向き"] != orientation:
            continue
        if any(abs(float(r["値"]) - float(v)) <= VALUE_TOL_MM for v in excluded):
            continue
        lo, hi = sorted((r["始点"][axis], r["終点"][axis]))
        if any(lo <= c[axis] <= hi for c in label_centers):
            out.append(r)
    return out


def _pure_dimension(text: str, ceiling: float | None) -> bool:
    t = nfkc(text)
    if not _PURE_NUMBER.match(t):
        return False
    v = int(t)
    if not DIM_MIN_MM <= v <= DIM_MAX_MM:
        return False
    return ceiling is None or abs(v - float(ceiling)) > VALUE_TOL_MM


def elevation_candidates(readings: Sequence[Mapping[str, Any]], skipped_texts: Sequence[str],
                         ceiling: float | None) -> list[str]:
    """展開図: 室の名前があるページの横向きの読みと、寸法にしなかった数字(天井高と違う 300〜20000)。"""
    out = [f"横の寸法 {r['id']}" for r in readings if r["向き"] == "横"]
    out += ["寸法にしなかった数字" for t in skipped_texts if _pure_dimension(t, ceiling)]
    return out


def finish_candidates(band_words: Sequence[str], ceiling: float | None) -> list[str]:
    """仕上表: 室の行の語のうち、面積・畳・「数x数」・天井高でない 3〜5 桁の数。"""
    out = []
    prev = ""
    for w in band_words:
        t = nfkc(w).replace(" ", "")
        if _AREA_WORD.match(t):
            out.append("面積・畳")
        elif _PAIR_WORD.search(t):
            out.append("数x数")
        elif _pure_dimension(t, ceiling) and not re.search(r"(CH|天井高)=?$", prev):
            out.append("3〜5 桁の数")
        prev = t
    return out


def outline_owned(outline_name: str | None, polygon: Sequence[Sequence[float]], own_keys: set[str],
                  own_centers: Sequence[tuple[float, float]], other_centers: Sequence[tuple[float, float]]) -> bool:
    """輪郭がその室のものか(輪郭の中の文字がその室の名前、またはその室の名前だけが中にある)。"""
    from draft.flags import _inside

    if outline_name and _key(nfkc(outline_name)) in own_keys:
        return True
    return any(_inside(c, polygon) for c in own_centers) and not any(_inside(c, polygon) for c in other_centers)


def kind_verdict(found_any: bool, candidates: Sequence[Any]) -> str:
    if not found_any:
        return NOT_SEARCHED
    return FOUND_CANDIDATE if candidates else FOUND_NONE


def numeric_allowed(search: Mapping[str, Mapping[str, Any]]) -> bool:
    """4 つの種類の全部で「無かった」ときだけ数字の入力を認める(基準 2 節)。"""
    return all(search.get(k, {}).get("結果") == FOUND_NONE for k in SEARCH_KINDS)


# --- v4 を読む(実データ) ---------------------------------------------------------------------


def _readings(page: Any) -> list[dict[str, Any]]:
    from intake.drawing_room_dimensions import dimension_ids

    return [{"id": i, "値": r.value_mm, "向き": r.orientation, "始点": r.start_pt, "終点": r.end_pt}
            for i, r in dimension_ids([page]).items()]


def name_parts(name: Any) -> list[str]:
    return [p for p in _SPLIT_NAME.split(nfkc(name)) if p]


def _word_hits(words: Sequence[Sequence[Any]], parts: Sequence[str]) -> list[tuple[float, float, float, float]]:
    """語(pymupdf の words)のうち、室の名前の語を含むもの。1〜2 字の語は語全体が同じときだけ。"""
    out = []
    for w in words:
        t = nfkc(w[4])
        for p in parts:
            if (len(p) <= 2 and t == p) or (len(p) > 2 and p in t):
                out.append((w[0], w[1], w[2], w[3]))
                break
    return out


class Drawing:
    """v4 の PDF の読みを使い回す(ページごとに 1 回だけ読む)。"""

    def __init__(self, pdf: Path, base: Mapping[str, Any] | None = None) -> None:
        self.pdf = Path(pdf)
        self.base = base or {}
        self._words: dict[int, list[Any]] = {}
        self._dims: dict[int, Any] = {}
        self._ruler: dict[int, tuple[list[dict[str, Any]], float | None]] = {}
        self._outlines: dict[int, tuple[bool, list[Any]]] = {}

    def outlines(self, n: int) -> tuple[bool, list[Any]]:
        """(縮尺が決まったか, 室の輪郭)。ページごとに 1 回だけ測る。"""
        if n not in self._outlines:
            scale = _page_scale(self, n)
            if scale is None:
                self._outlines[n] = (False, [])
            else:
                from axes.image_axis.pdf_room_outlines import find_room_outlines

                self._outlines[n] = (True, list(find_room_outlines(self.pdf, n - 1, scale)))
        return self._outlines[n]

    def words(self, n: int) -> list[Any]:
        if n not in self._words:
            import pymupdf

            with pymupdf.open(self.pdf) as doc:
                self._words[n] = list(doc.load_page(n - 1).get_text("words"))
        return self._words[n]

    def dims(self, n: int) -> Any:
        if n not in self._dims:
            from axes.image_axis.pdf_dimensions import read_dimensions

            self._dims[n] = read_dimensions(self.pdf, n - 1)
        return self._dims[n]

    def ruler(self, n: int) -> tuple[list[dict[str, Any]], float | None]:
        """K-38 の目盛りを当てた読みと mm/pt(基準の寸法のページで、基準が 1 つに決まるときだけ)。"""
        if n in self._ruler:
            return self._ruler[n]
        out: tuple[list[dict[str, Any]], float | None] = ([], None)
        if self.base and int(self.base.get("ページ", -1)) == n:
            from axes.image_axis.pdf_dimensions import read_dimensions

            page = self.dims(n)
            plain = _readings(page)
            ref_id = find_unique(plain, float(self.base["値_mm"]), self.base.get("向き"))
            if ref_id is not None:
                ref = next(r for i, r in zip((x["id"] for x in plain), page.readings) if i == ref_id)
                mmpp = ref.value_mm / ref.paper_distance_pt
                out = (_readings(read_dimensions(self.pdf, n - 1, ruler_mm_per_point=mmpp, ruler_tolerance=0.01)), mmpp)
        self._ruler[n] = out
        return out

    def ceilings(self, n: int) -> set[int]:
        text = nfkc(" ".join(w[4] for w in self.words(n)))
        return {int(v) for v in re.findall(r"CH\s*=\s*(\d{3,5})", text)}


def load_rooms(drawing: Drawing, k37_rooms: Mapping[str, Any], sides_copy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """室の鍵 → 室の表(番号・辺の状態・分け先・天井高・名前の語)。名前の語は探す鍵としてだけ持つ(外に書かない)。"""
    base = sides_copy.get("基準の寸法") or {}
    n = int(base.get("ページ", 0)) if base else 0
    plain = _readings(drawing.dims(n)) if n else []
    ruler = drawing.ruler(n)[0] if n else []
    by_no = {int(r["番号"]): r for r in sides_copy["室"]}
    out: dict[str, dict[str, Any]] = {}
    for no, room in enumerate(k37_rooms["室"]):
        copy = by_no[no]
        sides, ids = {}, {}
        for o in SIDES:
            sides[o], ids[o] = side_state(copy[o], o, plain, ruler)
        nr = bool(copy.get("長方形でない"))
        ch, ch_page = room.get("天井高_mm"), room.get("天井高のページ")
        has_ch = ch is not None and ch_page is not None and int(ch) in drawing.ceilings(int(ch_page))
        name = nfkc(room["室名"]).replace("\n", "・")
        out[_key(name)] = {
            "番号": no, "辺": sides, "辺の id": ids, "分け先": room_category(sides, not_rectangle=nr),
            "天井高": has_ch, "天井高_mm": ch if has_ch else None, "天井高のページ": ch_page if has_ch else None,
            "長方形でない": nr, "_名前の語": name_parts(room["室名"]),
            "_鍵": {_key(name)} | {_key(p) for p in name_parts(room["室名"])},
            "_値": [float(v) for o in SIDES for v in (copy[o].get("値") or []) + (copy[o].get("値の候補") or [])],
            "_辺の値": {o: list(copy[o].get("値") or []) for o in SIDES},
        }
    return out


def search_room(drawing: Drawing, room: Mapping[str, Any], others: Sequence[Mapping[str, Any]],
                kinds: Mapping[int, str]) -> dict[str, dict[str, Any]]:
    """室 1 つの、図面に無い辺について 4 つの種類を探す(基準 2 節)。候補は種類とページだけ書く。"""
    absent = [o for o in SIDES if room["辺"][o] == SIDE_ABSENT]
    pages = {k: sorted(n for n, v in kinds.items() if v == k) for k in ("平面図", "展開図", "仕上表")}
    parts = room["_名前の語"]
    excluded = [v for r in others for v in r["_値"]]
    ceiling = room.get("天井高_mm")
    out: dict[str, dict[str, Any]] = {}

    def hits(n: int) -> list[tuple[float, float, float, float]]:
        return _word_hits(drawing.words(n), parts)

    def centers(rects: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        return [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for r in rects]

    # 1 平面図
    found, cands, errors = [], [], []
    for n in pages["平面図"]:
        try:
            cs = centers(hits(n))
            if not cs:
                continue
            found.append(n)
            readings = _readings(drawing.dims(n)) + drawing.ruler(n)[0]
            for o in absent:
                cands += [{"ページ": n, "辺": o} for _ in plan_candidates(cs, readings, o, excluded)]
        except Exception as exc:  # noqa: BLE001
            errors.append({"ページ": n, "理由": type(exc).__name__})
    out["平面図"] = {"ページ": pages["平面図"], "名前が見つかったページ": found,
                  "結果": NOT_SEARCHED if errors else kind_verdict(bool(found), cands),
                  "候補": _count_pages(cands), **({"読めなかった": errors} if errors else {})}
    plan_found = found

    # 2 展開図
    found, cands, errors = [], [], []
    for n in pages["展開図"]:
        try:
            if not hits(n):
                continue
            found.append(n)
            page = drawing.dims(n)
            cands += [{"ページ": n} for _ in elevation_candidates(_readings(page), [s.text for s in page.skipped], ceiling)]
        except Exception as exc:  # noqa: BLE001
            errors.append({"ページ": n, "理由": type(exc).__name__})
    if errors:
        verdict = NOT_SEARCHED
    elif not pages["展開図"]:
        verdict = FOUND_NONE
    else:
        verdict = FOUND_CANDIDATE if cands else FOUND_NONE
    out["展開図"] = {"ページ": pages["展開図"], "名前が見つかったページ": found, "結果": verdict,
                  "候補": _count_pages(cands),
                  **({"注": "展開図のページが無い"} if not pages["展開図"] else
                     {"注": "展開図にこの室の名前が無い"} if not found else {}),
                  **({"読めなかった": errors} if errors else {})}

    # 3 仕上表
    found, cands, errors = [], [], []
    for n in pages["仕上表"]:
        try:
            words = drawing.words(n)
            rects = hits(n)
            if not rects:
                continue
            found.append(n)
            for r in rects:
                band = sorted((w for w in words if r[1] - 2 <= (w[1] + w[3]) / 2 <= r[3] + 2
                               and (w[0], w[1], w[2], w[3]) != tuple(r)), key=lambda w: w[0])
                cands += [{"ページ": n} for _ in finish_candidates([w[4] for w in band], ceiling)]
        except Exception as exc:  # noqa: BLE001
            errors.append({"ページ": n, "理由": type(exc).__name__})
    out["仕上表"] = {"ページ": pages["仕上表"], "名前が見つかったページ": found,
                  "結果": NOT_SEARCHED if errors else kind_verdict(bool(found), cands),
                  "候補": _count_pages(cands),
                  **({"注": "仕上表のページが無い"} if not pages["仕上表"] else {}),
                  **({"読めなかった": errors} if errors else {})}

    # 4 縮尺換算
    cands, errors, scales = [], [], []
    for n in plan_found:
        try:
            has_scale, outlines = drawing.outlines(n)
            scales.append({"ページ": n, "縮尺": "決まった" if has_scale else "決まらない"})
            if not has_scale:
                continue
            own = centers(hits(n))
            other = [c for r in others for c in centers(_word_hits(drawing.words(n), r["_名前の語"]))]
            for o in outlines:
                if outline_owned(o.name, o.polygon_pt, room["_鍵"], own, other):
                    cands.append({"ページ": n})
        except Exception as exc:  # noqa: BLE001
            errors.append({"ページ": n, "理由": type(exc).__name__})
    out["縮尺換算"] = {"ページ": plan_found, "縮尺": scales,
                   "結果": NOT_SEARCHED if errors else kind_verdict(bool(plan_found), cands),
                   "候補": _count_pages(cands), **({"読めなかった": errors} if errors else {})}
    return out


def _count_pages(cands: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cands:
        out[str(c["ページ"])] = out.get(str(c["ページ"]), 0) + 1
    return out


def _page_scale(drawing: Drawing, n: int) -> Any:
    from axes.image_axis.pdf_vector_symbols import MM_PER_POINT, DrawingScale
    from draft.flags import page_scale

    scale, _info = page_scale(drawing.pdf, n)
    if scale is not None:
        return scale
    _r, mmpp = drawing.ruler(n)
    if mmpp:
        return DrawingScale(denominator=mmpp / MM_PER_POINT, source_text="K-38 の目盛り(基準の寸法の比)")
    return None


# --- 行の印・質問・メーター(基準 1・3 節) ----------------------------------------------------


def searched_summary(search: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out = {}
    for k in SEARCH_KINDS:
        s = search.get(k, {})
        pages = s.get("ページ") or []
        out[k] = {"ページ": pages, "結果": s.get("結果", NOT_SEARCHED),
                  **({"注": s["注"]} if s.get("注") else {}),
                  **({"注": "そのページの種類が無い"} if not pages and not s.get("注") else {})}
    return out


def mark_rows(rows: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
              searches: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """C の行に「分からない」と探したページを付ける(基準 1 節)。**数量は None のまま。**"""
    out = []
    for r in rows:
        it = r["item"]
        reason = REASON + (f"・{NO_CEILING}" if r["marks"].get("天井高が無い") else "")
        per_room = []
        for k in r["keys"]:
            room = rooms[k]
            absent = [o for o in SIDES if room["辺"][o] == SIDE_ABSENT]
            per_room.append({"室": room["番号"], "図面に無い辺": absent, **({
                "探したページ": searched_summary(searches.get(room["番号"], {})),
                "機械で無いと確かめた": numeric_allowed(searches.get(room["番号"], {})),
            } if absent else {"注": "この室の辺は図面にある(図面に無いのは別の室の辺か天井高)"})})
        out.append({"項目": it["id"], "数量": None, "単位": nfkc(it.get("単位")), "部位": r["part"],
                    "分からない": reason, "室": per_room,
                    "出どころ": "「図面に書いていない」は K-37(匿名化 v2 の AI と人の確認)の判断。v4 での機械の確かめを「探したページ」に並べた",
                    **({"上限の側": "開口を引かない"} if r["marks"].get("開口を引かない") else {})})
    return out


def _room_ready(room: Mapping[str, Any], answered: set[int], allowed: Mapping[int, bool], ruler_ok: bool) -> bool:
    for o in SIDES:
        s = room["辺"][o]
        if s == SIDE_READ:
            continue
        if s == SIDE_RULER and ruler_ok:
            continue
        if s == SIDE_ABSENT and room["番号"] in answered and allowed.get(room["番号"]):
            continue
        return False
    return not room.get("長方形でない")


def row_confirmable(row: Mapping[str, Any], rooms: Mapping[str, Mapping[str, Any]], answered: set[int],
                    allowed: Mapping[int, bool], ruler_ok: bool = False) -> bool:
    needs_ceiling = PART_NEEDS.get((row["part"], row["kind"]), False)
    for k in row["keys"]:
        room = rooms[k]
        if needs_ceiling and not room.get("天井高"):
            return False
        if not _room_ready(room, answered, allowed, ruler_ok):
            return False
    return True


def rooms_to_ask(rows: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]]) -> list[int]:
    """C の行に結ばれた室のうち、図面に無い辺がある室(室ごとに 1 問)。"""
    return sorted({rooms[k]["番号"] for r in rows for k in r["keys"]
                   if any(rooms[k]["辺"][o] == SIDE_ABSENT for o in SIDES)})


def build_questions(rows: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
                    searches: Mapping[int, Mapping[str, Any]], n_items: int,
                    cost_table: Any = None) -> list[dict[str, Any]]:
    """室ごとに 1 問(C の行を持つ室だけ)とメーター。"""
    by_no = {r["番号"]: r for r in rooms.values()}
    allowed = {no: numeric_allowed(searches.get(no, {})) for no in by_no}
    qs = []
    for no in rooms_to_ask(rows, rooms):
        room = by_no[no]
        absent = [o for o in SIDES if room["辺"][o] == SIDE_ABSENT]
        mine = [r for r in rows if any(rooms[k]["番号"] == no for k in r["keys"])]
        single = [r for r in mine if len(r["keys"]) == 1]
        shared = [r for r in mine if len(r["keys"]) > 1]
        direct = sum(1 for r in single if row_confirmable(r, rooms, {no}, allowed))
        need_others = sum(1 for r in shared if row_confirmable(r, rooms, set(allowed), allowed))
        why_not = []
        if not allowed[no]:
            why_not.append("数字の入力を認めない室(4 つの種類のどれかで候補があった・見られなかった)")
        else:
            if any(room["辺"][o] not in (SIDE_READ, SIDE_ABSENT) for o in SIDES):
                why_not.append("図面にある辺が v4 で読めていない(目盛りの直しか AI の対応づけが要る)")
            if not room.get("天井高") and any(PART_NEEDS.get((r["part"], r["kind"])) for r in mine):
                why_not.append("壁の行: " + NO_CEILING)
        search = searched_summary(searches.get(no, {}))
        if allowed[no]:
            text = (f"室 {no} の内法の{'・'.join(absent)}の寸法(mm)を入れてください。図面(平面図・展開図・仕上表・縮尺換算)を"
                    "探しましたが書いてありません。長方形とみなせない室は「分からない」を選んでください")
            options = [OPT_NUMBER, OPT_DONT_KNOW, OPT_SITE]
            inputs = [f"{o}_mm" for o in absent]
        else:
            text = (f"室 {no} の{'・'.join(absent)}の寸法が、機械では図面に見つかっていません(K-37 の判断では書いていない)。"
                    "どこで分かりますか")
            options = [f"{k} {p}ページに書いてある" for k in SEARCH_KINDS
                       for p in sorted(int(x) for x in (searches.get(no, {}).get(k, {}).get("候補") or {}))]
            options += [OPT_NOT_IN_DRAWING, OPT_DONT_KNOW, OPT_SITE]
            inputs = []
        amount = _amount_meter(mine, cost_table)
        qs.append({
            "鍵": f"{KEY_PREFIX}{no}", "室": no, "問い": text, "選択肢": options, "数字の入力": inputs,
            "数字の入力の印": HUMAN if inputs else None, "推奨": None,
            "図面に無い辺": absent, "探したページ": search,
            "メーター": {
                "この答えで確定する行数": direct if allowed[no] else 0,
                "ほかの室の答えも要る行数": need_others if allowed[no] else 0,
                "この室の C の行": len(mine),
                "確定しない理由": why_not,
                "金額の割合": amount,
                "行数の割合(金額ではない)": round((direct if allowed[no] else 0) / n_items, 4) if n_items else UNKNOWN,
                "回答時間の見積(秒)": SECONDS_PER_QUESTION, "時間の注": "未較正(K-65 の型ごとの秒。数量 40 秒)",
            },
        })
    return qs


def _amount_meter(rows: Sequence[Mapping[str, Any]], cost_table: Any) -> Any:
    if not cost_table:
        return f"{UNKNOWN}(原価表 未取得)"
    from draft.cost_table import load_cost_table, match

    table = load_cost_table(cost_table) if not (isinstance(cost_table, Mapping) and "行" in cost_table) else cost_table
    hit = sum(1 for r in rows if match(table, r["item"].get("品番"), r["item"].get("工事"), r["item"].get("単位")))
    return {"値": "答えの後に出る(数量が答えで決まるまで金額は出ない)", "単価が当たった行": hit}


def by_level(qs: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """概算 0・通常 5・精密 10。確定する行数の多い順 → C の行の多い順 → 番号の順。"""
    order = sorted(qs, key=lambda q: (-q["メーター"]["この答えで確定する行数"], -q["メーター"]["この室の C の行"], q["室"]))
    return {lvl: [dict(q, 番号=f"R{i}") for i, q in enumerate(order[:lim], 1)] for lvl, lim in LEVEL_LIMITS.items()}


# --- 答えを戻す(基準 4 節) --------------------------------------------------------------


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return float(v)


def take_answers(qs: Sequence[Mapping[str, Any]], answers: Mapping[str, Any] | None) -> tuple[dict[int, dict[str, float]], dict[int, str], list[dict[str, Any]]]:
    """答えを (室 → 辺の値, 室 → 選んだ選択肢, 受け取れなかった答え) にする。数字は認めた室・図面に無い辺だけ受ける。"""
    by_key = {q["鍵"]: q for q in qs}
    values: dict[int, dict[str, float]] = {}
    choices: dict[int, str] = {}
    rejected = []
    for key, value in (answers or {}).items():
        q = by_key.get(key)
        if q is None:
            rejected.append({"鍵": key, "理由": "問いが無い"})
            continue
        choice = value.get("選択肢") if isinstance(value, Mapping) else value
        if choice not in q["選択肢"]:
            rejected.append({"鍵": key, "理由": "選択肢に無い"})
            continue
        choices[q["室"]] = str(choice)
        if choice != OPT_NUMBER:
            continue
        got = {}
        for o in q["図面に無い辺"]:
            v = _number(value.get(f"{o}_mm")) if isinstance(value, Mapping) else None
            if v is None:
                break
            got[o] = v
        extra = [k for k in (value if isinstance(value, Mapping) else {}) if k.endswith("_mm") and k[:-3] not in q["図面に無い辺"]]
        if not q["数字の入力"] or len(got) != len(q["図面に無い辺"]) or extra:
            rejected.append({"鍵": key, "理由": "数字を受けない(認めない室・図面にある辺・数でない・足りない)"})
            choices.pop(q["室"], None)
            continue
        values[q["室"]] = got
    return values, choices, rejected


def apply_answers(rows: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
                  qs: Sequence[Mapping[str, Any]], answers: Mapping[str, Any] | None,
                  cost_table: Any = None, assembly_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """答えで要る辺が全部そろった行だけ数量を出す。**人の入力の印・状態「推論」・確度「中」・自動確定にしない。**"""
    values, choices, rejected = take_answers(qs, answers)
    allowed = {q["室"]: bool(q["数字の入力"]) for q in qs}
    confirmed, left = [], []
    for r in rows:
        if not row_confirmable(r, rooms, set(values), allowed):
            left.append(r["item"]["id"])
            continue
        total, human, drawn = 0.0, [], []
        ok = True
        for k in r["keys"]:
            room = rooms[k]
            side = {}
            for o in SIDES:
                if room["辺"][o] == SIDE_ABSENT:
                    side[o] = values[room["番号"]][o]
                    human.append({"室": room["番号"], "辺": o, "値_mm": side[o], "印": HUMAN})
                else:
                    vs = room["_辺の値"][o]
                    if len(vs) != 1:
                        ok = False
                        break
                    side[o] = float(vs[0])
                    drawn.append({"室": room["番号"], "辺": o, "id": room["辺の id"][o]})
            if not ok:
                break
            w, d = side["横"], side["縦"]
            if r["kind"] == "面積" and r["part"] in ("床", "天井"):
                total += w * d / 1e6
            elif r["kind"] == "長さ" and r["part"] == "幅木":
                total += 2 * (w + d) / 1000
            elif r["kind"] == "面積" and r["part"] == "壁":
                total += 2 * (w + d) * float(room["天井高_mm"]) / 1e6
            else:
                ok = False
        if not ok:
            left.append(r["item"]["id"])
            continue
        confirmed.append({
            "項目": r["item"]["id"], "工事": r["item"].get("工事"), "場所": r["item"].get("場所"),
            "数量": round(total, 2), "単位": nfkc(r["item"].get("単位")), "状態": "推論", "確度": "中",
            "印": HUMAN, "自動確定": False,
            "根拠": {"人の入力": human, "図面から読んだ辺": drawn,
                   **({"天井高": "図面の印字(CH=)"} if r["part"] == "壁" else {})},
            **({"上限の側": "開口を引かない"} if r["marks"].get("開口を引かない") else {}),
        })
    return {"受け取った答え": {str(k): v for k, v in choices.items()}, "受け取れなかった答え": rejected,
            "確定した行": confirmed, "確定した行数": len(confirmed), "確定しなかった行数": len(left),
            "金額の割合": _amount_share(confirmed, rows, cost_table, assembly_rows)}


def _amount_share(confirmed: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], cost_table: Any,
                  assembly_rows: Sequence[Mapping[str, Any]] | None) -> Any:
    """確定した行の金額 / 組み立ての行の金額(出力の中だけで計算する)。原価表が無ければ未取得。"""
    if not cost_table:
        return f"{UNKNOWN}(原価表 未取得)"
    from draft.cost_table import load_cost_table, match

    table = load_cost_table(cost_table) if not (isinstance(cost_table, Mapping) and "行" in cost_table) else cost_table
    by_id = {r["item"]["id"]: r["item"] for r in rows}
    got = 0.0
    for c in confirmed:
        it = by_id[c["項目"]]
        m = match(table, it.get("品番"), it.get("工事"), it.get("単位"))
        if m and m.get("単価") is not None:
            got += m["単価"] * c["数量"]
    base = 0.0
    for a in assembly_rows or []:
        m = match(table, a.get("摘要"), a.get("工事項目"), a.get("単位"))
        if m and m.get("単価") is not None and a.get("数量") is not None:
            base += m["単価"] * a["数量"]
    denom = base + got
    return round(got / denom, 4) if denom > 0 else f"{UNKNOWN}(単価が当たる行が無い)"


def structural(rows: Sequence[Mapping[str, Any]], rooms: Mapping[str, Mapping[str, Any]],
               qs: Sequence[Mapping[str, Any]], n_items: int) -> dict[str, Any]:
    """(d) 実データでは寸法を作らず、数字の入力を認めた室が全部答えたら何行確定するかだけ数える。"""
    allowed = {q["室"]: bool(q["数字の入力"]) for q in qs}
    answered = {no for no, ok in allowed.items() if ok}
    now = sum(1 for r in rows if row_confirmable(r, rooms, answered, allowed))
    ruler = sum(1 for r in rows if row_confirmable(r, rooms, answered, allowed, ruler_ok=True))
    return {
        "数字の入力を認めた室が全部答えたとき 確定する行数": now,
        "同じ・行数の割合(理解の項目に対して。金額ではない)": round(now / n_items, 4) if n_items else UNKNOWN,
        "目盛りの直し(作業 5)もつないだとき 確定する行数": ruler,
        "C の行": len(rows),
    }


# --- 一本道から呼ぶ(旗オンのときだけ) ----------------------------------------------------


def run(pdf: Path, org: Mapping[str, Any], understanding: Mapping[str, Any], k37_rooms: Mapping[str, Any],
        sides_copy: Mapping[str, Any], answers: Mapping[str, Any] | None = None, cost_table: Any = None,
        assembly_rows: Sequence[Mapping[str, Any]] | None = None, drawing: Drawing | None = None) -> dict[str, Any]:
    drawing = drawing or Drawing(pdf, sides_copy.get("基準の寸法"))
    rooms = load_rooms(drawing, k37_rooms, sides_copy)
    kinds = {int(n): v.get("種類") for n, v in (org.get("ページ") or {}).items()}
    items = understanding.get("項目", [])
    rows = c_rows(items, rooms)
    need = rooms_to_ask(rows, rooms)
    by_no = {r["番号"]: r for r in rooms.values()}
    searches = {no: search_room(drawing, by_no[no], [r for r in rooms.values() if r["番号"] != no], kinds)
                for no in need}
    marked = mark_rows(rows, rooms, searches)
    qs = build_questions(rows, rooms, searches, len(items), cost_table)
    out = {
        "旗": "--with-room-unknown",
        "C の行": len(rows),
        "行": marked,
        "室": [{"室": no, "図面に無い辺": [o for o in SIDES if by_no[no]["辺"][o] == SIDE_ABSENT],
               "数字の入力を認める": numeric_allowed(searches[no]), "探したページ": searches[no]} for no in need],
        "問いの候補": qs,
        "段階ごと": by_level(qs),
        "答えが戻ったとき(構造)": structural(rows, rooms, qs, len(items)),
        "書き出した寸法": 0,
        "注": "数量は書き換えない(None のまま)。室は番号だけ。図面に無い数字の入力は K-73 の例外(人の入力・自動確定にしない)",
    }
    if answers:
        out["答えの往復"] = apply_answers(rows, rooms, qs, answers, cost_table, assembly_rows)
    return out


def annotate(understanding: dict[str, Any], assembly_rows: list[dict[str, Any]], section: Mapping[str, Any]) -> None:
    """旗オンのとき、理解の項目と内訳の行に「分からない」を付ける(数量は書き換えない)。"""
    reasons = {r["項目"]: r["分からない"] for r in section.get("行", [])}
    for it in understanding.get("項目", []):
        if it["id"] in reasons:
            it["分からない"] = reasons[it["id"]]
    for row in assembly_rows:
        hit = [reasons[i] for i in row.get("項目", []) if i in reasons]
        if hit and row.get("数量") is None:
            row["分からない"] = hit[0]


# --- 携帯で答える PDF(基準 5 節) ------------------------------------------------------------


def card_pdf(qs: Sequence[Mapping[str, Any]], path: Path, title: str = "室の寸法の問い") -> Path:
    """1 問 1 ページ(360×640pt)。室は番号だけ。選択肢はチェックの欄、数字の入力は認めた室だけ(縦・横 mm)。

    名前・金額は載せない(問いの文には室の番号しか入っていない)。
    """
    import pymupdf

    doc = pymupdf.open()
    w, h, m = 360, 640, 18
    for i, q in enumerate(qs, 1):
        page = doc.new_page(width=w, height=h)
        y = m
        page.insert_textbox(pymupdf.Rect(m, y, w - m, y + 22), f"{title}  {i}/{len(qs)}", fontname="japan", fontsize=10,
                            color=(0.35, 0.35, 0.35))
        y += 24
        page.insert_textbox(pymupdf.Rect(m, y, w - m, y + 40), f"室 {q['室']}", fontname="japan", fontsize=20)
        y += 42
        rect = pymupdf.Rect(m, y, w - m, y + 120)
        page.insert_textbox(rect, q["問い"], fontname="japan", fontsize=12, lineheight=1.35)
        y += 126
        for o in q["選択肢"]:
            box = pymupdf.Rect(m, y + 2, m + 20, y + 22)
            wd = pymupdf.Widget()
            wd.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
            wd.field_name = f"{q['鍵']}|{o}"
            wd.rect = box
            wd.border_color, wd.border_width = (0, 0, 0), 1
            page.add_widget(wd)
            page.insert_textbox(pymupdf.Rect(m + 28, y + 4, w - m, y + 40), o, fontname="japan", fontsize=12)
            y += 36
        for f in q["数字の入力"]:
            side = f.split("_")[0]
            page.insert_textbox(pymupdf.Rect(m, y + 6, m + 80, y + 30), f"{side}(mm)", fontname="japan", fontsize=12)
            wd = pymupdf.Widget()
            wd.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            wd.field_name = f"{q['鍵']}|{f}"
            wd.rect = pymupdf.Rect(m + 84, y, w - m, y + 30)
            wd.text_fontsize = 14
            wd.field_flags = 0
            wd.border_color, wd.border_width = (0, 0, 0), 1
            page.add_widget(wd)
            y += 38
        if q["数字の入力"]:
            page.insert_textbox(pymupdf.Rect(m, y, w - m, y + 34), f"入れた数字には「{HUMAN}」の印が付きます。自動では確定しません",
                                fontname="japan", fontsize=9, color=(0.35, 0.35, 0.35))
            y += 36
        meter = q["メーター"]
        amount = meter["金額の割合"]
        amount_text = amount if isinstance(amount, str) else amount.get("値", "")
        lines = [
            f"この答えで確定する行: {meter['この答えで確定する行数']} 行(ほかの室の答えも要る {meter['ほかの室の答えも要る行数']} 行)",
            f"金額の割合: {amount_text}",
            f"回答時間の見積: {meter['回答時間の見積(秒)']} 秒(未較正)",
            "探したページ: " + " / ".join(f"{k} {q['探したページ'][k]['結果']}" for k in SEARCH_KINDS),
        ]
        page.insert_textbox(pymupdf.Rect(m, max(y + 6, h - 150), w - m, h - m), "\n".join(lines), fontname="japan",
                            fontsize=9, lineheight=1.4, color=(0.2, 0.2, 0.2))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    return path
