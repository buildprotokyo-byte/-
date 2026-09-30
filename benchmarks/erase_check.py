"""K-51: 読み取りの落ちを、正解を作らずに数える(消して確かめる)。

**やり方**

1. PDF のページから図形を全部取り出し、1 つずつ通し番号を付ける
   (線・曲線・矩形・塗り・文字・画像。座標・種類・色・太さを持たせる)。
2. 読み取りの結果(要素ごとの位置)を受け取る。
3. 読み取った要素の位置に入っている図形に「印」を付ける。**機械が座標で突き合わせるだけで、
   AI に「同じか」を判定させない。**
4. 印の付かなかった図形を「落ち」として数える。

**決め事(仮の判断。記録として `SETTINGS` に残す)**

- 図形の単位: 線・曲線・矩形は、PDF の描画命令の 1 項目ずつ。文字は 1 語ずつ
  (`get_text("words")`)。画像は 1 枚ずつ。塗りだけの描画(線を引かない)は 1 描画を 1 つ。
- **ハッチング**: 1 つの描画の中に、同じ向き(差 1 度以内)の直線が 5 本以上だけで並ぶものは、
  まとめて 1 つ(種類「ハッチング」)と数える。
- **落ちに数えないもの**(件数と理由を必ず出す):
  - 図面枠: ページの縦横の 6 割以上を囲む矩形、またはページの端から 5% 以内を走る
    長さ 8 割以上の直線。
  - 表題欄: 図面枠の内側で、下端から 20% 以内にあるページ幅 8 割以上の横線より下
    (右端から 20% 以内にある高さ 8 割以上の縦線より右も同じ)にある図形。
  - 凡例の枠線は、機械で見分けられないので**除外しない**(落ちに数える側に倒す)。
- **印の付け方**: 要素の位置(縦横を 3 画素ずつ広げる)の中に、図形の見本の点が半分以上入れば印。
  見本の点は、直線は 5 点、曲線は制御点 4 点と中点、それ以外は外接矩形の四隅と中心。
- **大きすぎる要素は印を付けない**: 要素の位置がページの面積の 1% を超えるもの
  (「平面図全体」「表全体」のような要素)は印を付けない。付けると、中身を読んでいなくても
  全部に印が付くため。上限の値で落ちの数が大きく動くので、1% 以外の値での結果も並べて出す。
- **種類の分け方**(K-51 第 2 節): 文字 → 文字、塗り → 塗り、画像 → 画像、
  ハッチング → 線、外接矩形の長い辺が 8 画素未満 → 点・小さい図形、
  8〜40 画素の曲線・矩形・四角形 → 記号(記号の部品の見込み)、それ以外 → 線。
  **記号かどうかを意味で当てたものではない**(形の大きさだけで分けた)。

座標は、ページを幅 2000 画素で描いた画像の画素(回転は表示の向きに直す)。
AI の読み取り(`docs/k50_recognition_loop.md` の形)の「位置」も同じ座標。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pymupdf

WIDTH_PX = 2000
SETTINGS = {
    "幅の画素": WIDTH_PX,
    "印の余白(画素)": 3,
    "印に要る見本の点の割合": 0.5,
    "印を付ける要素の面積の上限(ページ比)": 0.01,
    "ハッチングとする平行な直線の最小本数": 5,
    "ハッチングの向きの差(度)": 1.0,
    "点・小さい図形の上限(画素)": 8,
    "記号の部品とする大きさの上限(画素)": 40,
    "図面枠の辺とする端からの距離(ページ比)": 0.05,
    "図面枠・表題欄の線とする長さ(ページ比)": 0.8,
    "表題欄の線を探す範囲(ページ比)": 0.2,
}


@dataclass
class Primitive:
    id: int
    page: int
    kind: str  # 直線・曲線・矩形・四角形・塗り・ハッチング・文字・画像
    bbox: tuple[float, float, float, float]
    points: np.ndarray = field(repr=False)
    color: tuple | None = None
    fill: tuple | None = None
    width: float | None = None
    length: float = 0.0
    area: float = 0.0
    text: str = ""
    excluded: str = ""  # 空でなければ除外の理由
    marked: bool = False

    @property
    def size(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return max(x1 - x0, y1 - y0)

    @property
    def category(self) -> str:
        if self.kind in ("文字", "塗り", "画像"):
            return self.kind
        if self.kind == "ハッチング":
            return "線"
        if self.size < SETTINGS["点・小さい図形の上限(画素)"]:
            return "点・小さい図形"
        if self.kind in ("曲線", "矩形", "四角形") and self.size <= SETTINGS["記号の部品とする大きさの上限(画素)"]:
            return "記号"
        return "線"


def _bbox(pts: np.ndarray) -> tuple[float, float, float, float]:
    return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))


def _box_points(b) -> np.ndarray:
    x0, y0, x1, y1 = b
    return np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2]])


def extract_primitives(page: pymupdf.Page, page_no: int) -> list[Primitive]:
    """ページの図形を全部取り出す(座標は幅 2000 画素の表示の向き)。"""
    s = WIDTH_PX / page.rect.width
    m = page.rotation_matrix * pymupdf.Matrix(s, s)

    def tp(p) -> list[float]:
        q = pymupdf.Point(p) * m
        return [q.x, q.y]

    def trect(r) -> tuple:
        return tuple(pymupdf.Rect(r).transform(m).normalize())

    out: list[Primitive] = []

    def add(kind, pts, **kw):
        pts = np.asarray(pts, dtype=float)
        out.append(Primitive(id=len(out), page=page_no, kind=kind, bbox=kw.pop("bbox", None) or _bbox(pts), points=pts, **kw))

    for d in page.get_drawings():
        style = dict(color=d.get("color"), fill=d.get("fill"), width=d.get("width"))
        items = d["items"]
        if d["type"] == "f":  # 塗りだけ
            b = trect(d["rect"])
            add("塗り", _box_points(b), bbox=b, area=(b[2] - b[0]) * (b[3] - b[1]), **style)
            continue
        lines = [it for it in items if it[0] == "l"]
        if len(lines) >= SETTINGS["ハッチングとする平行な直線の最小本数"] and len(lines) == len(items):
            angs = [math.degrees(math.atan2(it[2].y - it[1].y, it[2].x - it[1].x)) % 180 for it in lines]
            if max(angs) - min(angs) <= SETTINGS["ハッチングの向きの差(度)"]:
                pts = np.array([tp((it[1] + it[2]) / 2) for it in lines])
                ends = np.array([tp(it[1]) for it in lines] + [tp(it[2]) for it in lines])
                add("ハッチング", pts, bbox=_bbox(ends),
                    length=sum(math.dist(tp(it[1]), tp(it[2])) for it in lines), **style)
                continue
        for it in items:
            op = it[0]
            if op == "l":
                a, b = np.array(tp(it[1])), np.array(tp(it[2]))
                pts = [a + (b - a) * t for t in (0, 0.25, 0.5, 0.75, 1)]
                add("直線", pts, length=float(np.linalg.norm(b - a)), **style)
            elif op == "c":
                cps = np.array([tp(p) for p in it[1:5]])
                mid = (cps[0] + 3 * cps[1] + 3 * cps[2] + cps[3]) / 8
                add("曲線", np.vstack([cps, mid]), bbox=_bbox(np.vstack([cps[[0, 3]], mid])),
                    length=float(np.linalg.norm(cps[3] - cps[0])), **style)
            elif op == "re":
                b = trect(it[1])
                add("矩形", _box_points(b), bbox=b, area=(b[2] - b[0]) * (b[3] - b[1]), **style)
            elif op == "qu":
                q = it[1]
                pts = np.array([tp(q.ul), tp(q.ur), tp(q.ll), tp(q.lr)])
                add("四角形", np.vstack([pts, pts.mean(axis=0)]), **style)
    for w in page.get_text("words"):
        b = trect(w[:4])
        add("文字", _box_points(b), bbox=b, text=w[4])
    for info in page.get_image_info():
        b = trect(info["bbox"])
        add("画像", _box_points(b), bbox=b, area=(b[2] - b[0]) * (b[3] - b[1]))
    return out


def mark_exclusions(prims: list[Primitive], page_w: float, page_h: float) -> Counter:
    """図面枠・表題欄の図形に除外の印を付け、理由ごとの件数を返す。"""
    edge = SETTINGS["図面枠の辺とする端からの距離(ページ比)"]
    long_ = SETTINGS["図面枠・表題欄の線とする長さ(ページ比)"]
    band = SETTINGS["表題欄の線を探す範囲(ページ比)"]
    reasons: Counter = Counter()
    frame_x0, frame_y0, frame_x1, frame_y1 = 0.0, 0.0, page_w, page_h
    for p in prims:
        x0, y0, x1, y1 = p.bbox
        w, h = x1 - x0, y1 - y0
        if p.kind in ("矩形", "塗り") and w >= 0.6 * page_w and h >= 0.6 * page_h:
            p.excluded = "図面枠"
        elif p.kind == "直線":
            near = (min(y0, page_h - y1) <= edge * page_h and w >= long_ * page_w) or \
                   (min(x0, page_w - x1) <= edge * page_w and h >= long_ * page_h)
            if near:
                p.excluded = "図面枠"
    # 表題欄の境の線(図面枠の内側)
    title_y, title_x = None, None
    for p in prims:
        if p.kind != "直線" or p.excluded:
            continue
        x0, y0, x1, y1 = p.bbox
        if x1 - x0 >= long_ * page_w and y1 - y0 < 2 and y0 >= (1 - band) * page_h:
            title_y = y0 if title_y is None else min(title_y, y0)
        if y1 - y0 >= long_ * page_h and x1 - x0 < 2 and x0 >= (1 - band) * page_w:
            title_x = x0 if title_x is None else min(title_x, x0)
    for p in prims:
        if p.excluded:
            continue
        x0, y0, x1, y1 = p.bbox
        if title_y is not None and y0 >= title_y - 1:
            p.excluded = "表題欄"
        elif title_x is not None and x0 >= title_x - 1:
            p.excluded = "表題欄"
    for p in prims:
        if p.excluded:
            reasons[p.excluded] += 1
    return reasons


def mark_read(prims: list[Primitive], elements: list[dict], page_area: float,
              area_cap: float | None = None) -> None:
    """読み取った要素の位置に入る図形に印を付ける(座標の突き合わせだけ)。"""
    cap = SETTINGS["印を付ける要素の面積の上限(ページ比)"] if area_cap is None else area_cap
    mg = SETTINGS["印の余白(画素)"]
    boxes = []
    for e in elements:
        pos = e.get("位置")
        if not pos or len(pos) != 4:
            continue
        x0, y0 = min(pos[0], pos[2]), min(pos[1], pos[3])
        x1, y1 = max(pos[0], pos[2]), max(pos[1], pos[3])
        if (x1 - x0) * (y1 - y0) > cap * page_area:
            continue
        boxes.append((x0 - mg, y0 - mg, x1 + mg, y1 + mg))
    if not boxes:
        return
    B = np.array(boxes)
    need = SETTINGS["印に要る見本の点の割合"]
    for p in prims:
        pts = p.points
        inside = ((pts[:, None, 0] >= B[None, :, 0]) & (pts[:, None, 0] <= B[None, :, 2])
                  & (pts[:, None, 1] >= B[None, :, 1]) & (pts[:, None, 1] <= B[None, :, 3]))
        p.marked = bool((inside.mean(axis=0) >= need).any())


def summarize(prims: list[Primitive]) -> dict:
    """落ちの件数・率・内訳・大きさの分布。除外した図形は数えない(件数だけ別に出す)。"""
    live = [p for p in prims if not p.excluded]
    miss = [p for p in live if not p.marked]
    by_cat_all = Counter(p.category for p in live)
    by_cat_miss = Counter(p.category for p in miss)
    by_kind_miss = Counter(p.kind for p in miss)
    sizes = np.array([p.size for p in miss]) if miss else np.array([0.0])
    bins = [0, 8, 20, 40, 100, 300, 1e9]
    labels = ["8未満", "8〜20", "20〜40", "40〜100", "100〜300", "300以上"]
    hist = np.histogram(sizes, bins=bins)[0] if miss else np.zeros(len(labels), int)
    lin = [p for p in live if p.kind in ("直線", "曲線", "ハッチング")]
    tot_len = sum(p.length for p in lin)
    miss_len = sum(p.length for p in lin if not p.marked)
    return {
        "図形の総数": len(prims),
        "除外": dict(Counter(p.excluded for p in prims if p.excluded)),
        "数える図形": len(live),
        "拾えた": len(live) - len(miss),
        "落ちた": len(miss),
        "落ちた率": round(len(miss) / len(live), 4) if live else None,
        "種類ごと(数える/落ちた)": {k: [by_cat_all[k], by_cat_miss.get(k, 0)] for k in sorted(by_cat_all)},
        "落ちた図形の元の種類": dict(by_kind_miss),
        "落ちた図形の大きさ(画素、長い辺)": dict(zip(labels, [int(x) for x in hist])),
        "線の長さで見た落ちた率": round(miss_len / tot_len, 4) if tot_len else None,
    }


def check_page(page: pymupdf.Page, page_no: int, elements: list[dict], area_cap: float | None = None):
    prims = extract_primitives(page, page_no)
    s = WIDTH_PX / page.rect.width
    w, h = page.rect.width * s, page.rect.height * s
    mark_exclusions(prims, w, h)
    mark_read(prims, elements, w * h, area_cap)
    return prims, summarize(prims)


# --- 周 3(K-53): 端点のつながる線を繋いで数える / 線の落ちを種類で分ける ---

CHAIN_SETTINGS = {
    "端点が重なるとみなす距離(画素)": 0.5,
    "拾えたとするつながった線の長さの割合": 0.5,
}


def _ends(p: Primitive) -> tuple[np.ndarray, np.ndarray] | None:
    if p.kind == "直線":
        return p.points[0], p.points[-1]
    if p.kind == "曲線":
        return p.points[0], p.points[3]
    return None


def chain_lines(prims: list[Primitive]) -> list[list[int]]:
    """除外していない直線・曲線を、端点が重なるものどうしで繋ぐ。ほかの図形はそれぞれ 1 つ。"""
    tol = CHAIN_SETTINGS["端点が重なるとみなす距離(画素)"]
    parent = list(range(len(prims)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    grid: dict[tuple[int, int], list[tuple[int, np.ndarray]]] = {}
    for i, p in enumerate(prims):
        if p.excluded:
            continue
        e = _ends(p)
        if e is None:
            continue
        for pt in e:
            gx, gy = int(math.floor(pt[0] / tol)), int(math.floor(pt[1] / tol))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, q in grid.get((gx + dx, gy + dy), ()):
                        if j != i and math.dist(pt, q) <= tol:
                            ra, rb = find(i), find(j)
                            if ra != rb:
                                parent[ra] = rb
            grid.setdefault((gx, gy), []).append((i, pt))
    groups: dict[int, list[int]] = {}
    for i, p in enumerate(prims):
        if p.excluded:
            continue
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def summarize_chained(prims: list[Primitive]) -> dict:
    """繋いだ単位での落ち。点・小さい図形の部品が、繋ぐと大きい線の一部になる件数も出す。"""
    need = CHAIN_SETTINGS["拾えたとするつながった線の長さの割合"]
    small = SETTINGS["点・小さい図形の上限(画素)"]
    units = chain_lines(prims)
    cat_all: Counter = Counter()
    cat_miss: Counter = Counter()
    small_parts_joined = 0
    small_parts_missed_joined = 0
    for u in units:
        ps = [prims[i] for i in u]
        if len(ps) == 1:
            p = ps[0]
            cat, marked = p.category, p.marked
        else:
            b = (min(p.bbox[0] for p in ps), min(p.bbox[1] for p in ps),
                 max(p.bbox[2] for p in ps), max(p.bbox[3] for p in ps))
            size = max(b[2] - b[0], b[3] - b[1])
            cat = "点・小さい図形" if size < small else "線"
            w = [p.length if p.length > 0 else 1.0 for p in ps]
            marked = sum(wi for wi, p in zip(w, ps) if p.marked) >= need * sum(w)
            if cat == "線":
                parts = [p for p in ps if p.category == "点・小さい図形"]
                small_parts_joined += len(parts)
                small_parts_missed_joined += sum(1 for p in parts if not p.marked)
        cat_all[cat] += 1
        if not marked:
            cat_miss[cat] += 1
    n, m = sum(cat_all.values()), sum(cat_miss.values())
    return {
        "数える単位": n,
        "落ちた": m,
        "落ちた率": round(m / n, 4) if n else None,
        "種類ごと(数える/落ちた)": {k: [cat_all[k], cat_miss.get(k, 0)] for k in sorted(cat_all)},
        "繋ぐと8画素以上の線の一部になる点・小さい図形の部品": small_parts_joined,
        "そのうち部品の単位で落ちていたもの": small_parts_missed_joined,
    }


LINE_TYPES = ["ハッチング", "寸法線", "寸法補助線", "引出線", "壁", "家具・建具", "その他"]


def _angle(p: Primitive) -> float:
    a, b = _ends(p)
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180


def _seg_dist(pt, a, b) -> float:
    ab = b - a
    L = float(ab @ ab)
    t = 0.0 if L == 0 else max(0.0, min(1.0, float((pt - a) @ ab) / L))
    return float(np.linalg.norm(pt - (a + t * ab)))


def classify_lines(prims: list[Primitive]) -> dict[int, str]:
    """「線」に分けた図形(除外なし)を、決まりを上から順に当てて種類に分ける。返り値は id → 種類。"""
    lines = [p for p in prims if not p.excluded and p.category == "線"]
    words = [p for p in prims if p.kind == "文字" and not p.excluded]
    digit_words = [w for w in words if re.fullmatch(r"[0-9０-９,，.]+", w.text or "")]
    out: dict[int, str] = {}
    straight = [p for p in lines if p.kind == "直線"]
    curves = [p for p in prims if p.kind == "曲線" and not p.excluded]
    curve_ends = [e for c in curves for e in _ends(c)]

    def horiz(a):
        return min(a, 180 - a) < 3

    def vert(a):
        return abs(a - 90) < 3

    for p in lines:
        if p.kind == "ハッチング":
            out[p.id] = "ハッチング"
    dims = []
    for p in straight:
        if p.id in out:
            continue
        a, b = _ends(p)
        ang = _angle(p)
        for w in digit_words:
            x0, y0, x1, y1 = w.bbox
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            text_horiz = (x1 - x0) >= (y1 - y0)
            if not ((text_horiz and horiz(ang)) or (not text_horiz and vert(ang))):
                continue
            ab = b - a
            L = float(ab @ ab)
            if L == 0:
                continue
            t = float((c - a) @ ab) / L
            if 0 <= t <= 1 and _seg_dist(c, a, b) <= 20:
                out[p.id] = "寸法線"
                dims.append(p)
                break
    for p in straight:
        if p.id in out:
            continue
        ang = _angle(p)
        for d in dims:
            da, db = _ends(d)
            dang = _angle(d)
            perp = abs(((ang - dang) % 180) - 90) < 3
            if perp and any(_seg_dist(e, da, db) <= 3 for e in _ends(p)):
                out[p.id] = "寸法補助線"
                break
    wboxes = np.array([w.bbox for w in words]) if words else np.zeros((0, 4))

    def near_text(pt, r=20):
        if not len(wboxes):
            return False
        dx = np.maximum(np.maximum(wboxes[:, 0] - pt[0], 0), pt[0] - wboxes[:, 2])
        dy = np.maximum(np.maximum(wboxes[:, 1] - pt[1], 0), pt[1] - wboxes[:, 3])
        return bool((np.hypot(dx, dy) <= r).any())

    for p in straight:
        if p.id in out:
            continue
        ang = _angle(p)
        if horiz(ang) or vert(ang):
            continue
        a, b = _ends(p)
        if near_text(a) != near_text(b):
            out[p.id] = "引出線"
    for p in straight:
        if p.id in out or p.length < 40:
            continue
        a, b = _ends(p)
        ang = _angle(p)
        u = (b - a) / p.length
        n = np.array([-u[1], u[0]])
        for q in straight:
            if q is p or abs(((_angle(q) - ang + 90) % 180) - 90) > 1:
                continue
            qa, qb = _ends(q)
            gap = abs(float((qa - a) @ n))
            if not 2 <= gap <= 25:
                continue
            s = sorted([float((qa - a) @ u), float((qb - a) @ u)])
            ov = min(p.length, s[1]) - max(0.0, s[0])
            if ov >= 0.5 * p.length:
                out[p.id] = "壁"
                break
    for p in lines:
        if p.id in out:
            continue
        if p.kind == "曲線":
            out[p.id] = "家具・建具"
            continue
        e = _ends(p)
        if e is not None and curve_ends and any(math.dist(x, c) <= 3 for x in e for c in curve_ends):
            out[p.id] = "家具・建具"
    for p in lines:
        out.setdefault(p.id, "その他")
    return out
