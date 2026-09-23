"""画像軸: CAD 由来の PDF から、**繰り返し現れる図形**を記号の候補として拾う。

**なぜテンプレートを持たないのか**

同じ図面の記号を、ラスター画像のテンプレート照合で探した結果は**的中 0 件**
だった(`docs/real_drawing_eval_report.md` 4 節・11 節)。原因は精度ではなく、
①探すべき形の出どころが無いことと、②相関スコアが正解を区別しないことである。
段階 A の「テンプレート照合 F1 1.00」は、テンプレートを合成図面の作図関数から
作ったベンチマーク専用の実装であって、実図面では使えない。

CAD 由来の PDF なら事情が変わる。**記号は同じ図形の複製として入っている。**
だから「何の形か」を先に知らなくても、「同じ形が何度も出てくる」ことだけを
手がかりに群にまとめて数えられる。テンプレートは要らない。

**何をもって「同じ形」とするか**

コンセントやスイッチは壁の向きに合わせて回して置かれ、左右反転して置かれる
こともある。回すたびに別の種類として数えたら個数は当てにならないので、
判定は**回転にも鏡像にも依存しない量**だけで行う。

1. 図形を構成する要素の種類ごとの本数(直線・ベジェ曲線・矩形・四辺形)
2. 各要素の長さを並べて揃えたもの
3. 図形の重心から各標本点までの距離を並べて揃えたもの

1 と 2 と 3 はどれも回転・平行移動・鏡像で変わらない。**大きさでは正規化しない。**
大きい丸と小さい丸は別の記号だからである。

**この手法が原理的に落とすもの(0 件を「無い」と読まないこと)**

- **1 回しか出てこない記号**(既定の ``min_count=2``)。繰り返しを手がかりに
  している以上、繰り返さないものは拾えない。
- **大きさの窓から外れた記号**。図面の外枠や通り芯を記号として数えないために
  窓を設けているが、窓の値は**実図面で校正していない暫定値**である。
- **スキャンされたページ**。図形データが無いので常に 0 件になる。
- **ハッチングや寸法線のように繰り返す、記号でないもの**。群としては出るので、
  名前が付かない群を「記号」と決めつけないこと。

**名前は図形からは決まらない**

繰り返しが見つかっても、それが何の記号かは図形からは分からない。
`read_legend_symbols()` が凡例のページから「名前 ↔ 図形」を読み、
`name_clusters()` が突き合わせる。**突き合わない群は名前なしのまま返す。**
図形の似ている順で当てはめて名前を作ることはしない。

**凡例との一致は独立した 2 つ目の軸ではない**

凡例も平面図も同じ 1 つの PDF から来ている。原則(スタートキット 3 節)の
「一致は経路が本当に独立している場合だけ根拠を強める」に従い、この手法は
`arbitration/method_policies.py` に **``calibrated=False`` / 上限 ``weak``**
で登録する。名前が付いたからといって階層 1 の根拠にはならない。
"""


from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
METHOD_REPEATED_SYMBOL = "pdf_vector_repeated_symbol"
METHOD_LEGEND_SYMBOL = "pdf_vector_legend_symbol"

#: 記号とみなす実寸の大きさの窓(ミリメートル、外接矩形の長いほうの辺)。
#:
#: **暫定値である。実図面で校正していない。** 1/50 の図面でコンセントや
#: スイッチの記号は紙の上で 3〜8mm 程度に描かれることが多く、実寸に直すと
#: 150〜400mm になる。下限は寸法線の矢印やハッチングの細片を、上限は
#: 図面の外枠・通り芯・室の輪郭を落とすために置いてある。
#: **この窓で落ちたものは「記号ではない」ではなく「この窓の外にあった」である。**
SYMBOL_MIN_MM = 30.0
SYMBOL_MAX_MM = 1500.0

#: 「同じ形」とみなす許容差(ポイント)。CAD の複製は座標まで同じなので
#: 本来 0 でよいが、PDF に書き出す際に座標が丸められること、回転して
#: 置かれたときに浮動小数の誤差が乗ることを見込んでいる。
SHAPE_TOLERANCE_PT = 0.05

#: ベジェ曲線 1 本から取る標本点の数。`pdf_vector_symbols` と揃えてある。
_SAMPLES_PER_CURVE = 8

#: 繰り返しとみなす最小の個数。
DEFAULT_MIN_COUNT = 2


@dataclass(frozen=True)
class SymbolCluster:
    """**同じ形**が繰り返し現れたことを表す 1 群。名前は持たない。"""

    count: int
    """その形が現れた回数。"""

    positions_pt: tuple[tuple[float, float], ...]
    """各出現の重心(ページ座標・ポイント)。**件数ぶん必ず入る。**

    どこにあったかを言えない数量は根拠にならないので、数だけ返さない。
    """

    rects_pt: tuple[tuple[float, float, float, float], ...]
    """各出現の外接矩形(ページ座標・ポイント)。"""

    size_mm: float
    """記号 1 個の実寸の大きさ(外接矩形の長いほうの辺、ミリメートル)。"""

    page_index: int
    """0 始まりのページ番号。"""

    descriptor: tuple[float, ...] = field(repr=False, default=())
    """回転・鏡像に依存しない形の記述。凡例との突き合わせに使う。"""

    method_id: str = METHOD_REPEATED_SYMBOL

    limitation: str = (
        "繰り返しを手がかりにしているので、1 回しか出てこない記号と、"
        "大きさの窓の外にある記号は拾えない。0 件は「記号が無い」ではない"
    )


@dataclass(frozen=True)
class LegendSymbol:
    """凡例のページで読めた「名前 ↔ 図形」の対応 1 件。"""

    name: str
    """凡例に印字されている名前。**図形から作った名前ではない。**"""

    name_rect_pt: tuple[float, float, float, float]
    """名前が書かれている位置。根拠としてそのまま残す。"""

    symbol_rect_pt: tuple[float, float, float, float]
    """図形の外接矩形。"""

    size_mm: float
    page_index: int
    descriptor: tuple[float, ...] = field(repr=False, default=())
    method_id: str = METHOD_LEGEND_SYMBOL


@dataclass(frozen=True)
class NamedSymbolCount:
    """群と凡例を突き合わせた結果。``name`` が None なら**名前が付かなかった**。"""

    name: str | None
    count: int
    cluster: SymbolCluster
    legend: LegendSymbol | None
    basis: str
    """名前が付いた/付かなかった理由を、そのまま人が読める言葉で残す。"""


# ---------------------------------------------------------------------------
# 形の記述
# ---------------------------------------------------------------------------


def _item_points(item) -> list[tuple[float, float]]:
    """`get_drawings()` の要素 1 つを、通過点の並びに直す。"""
    kind = item[0]
    if kind == "l":
        p1, p2 = item[1], item[2]
        return [(p1.x, p1.y), (p2.x, p2.y)]
    if kind == "c":
        p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
        points = []
        for index in range(_SAMPLES_PER_CURVE + 1):
            t = index / _SAMPLES_PER_CURVE
            u = 1.0 - t
            x = u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x
            y = u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y
            points.append((x, y))
        return points
    if kind == "re":
        rect = item[1]
        return [
            (rect.x0, rect.y0),
            (rect.x1, rect.y0),
            (rect.x1, rect.y1),
            (rect.x0, rect.y1),
            (rect.x0, rect.y0),
        ]
    if kind == "qu":
        quad = item[1]
        return [
            (quad.ul.x, quad.ul.y),
            (quad.ur.x, quad.ur.y),
            (quad.lr.x, quad.lr.y),
            (quad.ll.x, quad.ll.y),
            (quad.ul.x, quad.ul.y),
        ]
    return []


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    )


def _describe(items: list) -> tuple[tuple[float, ...], list[tuple[float, float]]] | None:
    """図形 1 つから、**回転・平行移動・鏡像に依存しない記述**と標本点を作る。

    返すのは ``(記述, 標本点)``。点が足りなければ None。
    """
    kinds: dict[str, int] = defaultdict(int)
    lengths: list[float] = []
    points: list[tuple[float, float]] = []
    for item in items:
        item_points = _item_points(item)
        if not item_points:
            continue
        kinds[item[0]] += 1
        lengths.append(_path_length(item_points))
        points.extend(item_points)
    if len(points) < 2:
        return None

    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    radial = sorted(math.hypot(x - cx, y - cy) for x, y in points)

    # 種類ごとの本数 → 長さを揃えたもの → 重心からの距離を揃えたもの。
    head = (
        float(kinds.get("l", 0)),
        float(kinds.get("c", 0)),
        float(kinds.get("re", 0)),
        float(kinds.get("qu", 0)),
    )
    return head + tuple(sorted(lengths)) + tuple(radial), points


def _quantize(descriptor: tuple[float, ...], tolerance_pt: float) -> tuple:
    """記述を許容差の升目に落として、辞書の鍵にできる形にする。

    **升目の境目で分かれてしまう場合がある**ので、この後で
    `_merge_near()` が隣り合う鍵を寄せ直す。丸めだけで済ませない。
    """
    # 先頭 4 つは本数なので丸めない(0.5 本は無い)。
    counts = tuple(int(round(v)) for v in descriptor[:4])
    rest = tuple(int(round(v / tolerance_pt)) for v in descriptor[4:])
    return counts + rest


def _close_enough(a: tuple[float, ...], b: tuple[float, ...], tolerance_pt: float) -> bool:
    if len(a) != len(b):
        return False
    if a[:4] != b[:4]:
        return False
    return all(abs(x - y) <= tolerance_pt for x, y in zip(a[4:], b[4:]))


def _merge_near(
    groups: dict[tuple, list[int]],
    descriptors: list[tuple[float, ...]],
    tolerance_pt: float,
) -> list[list[int]]:
    """丸めの升目の境目で分かれた群を寄せ直す。

    群の代表の記述どうしを比べ、全ての成分が許容差以内なら 1 つにする。
    群の数は記号の種類ぶんしかないので、総当たりで足りる。
    """
    keys = sorted(groups)
    merged: list[list[int]] = []
    representatives: list[tuple[float, ...]] = []
    for key in keys:
        members = groups[key]
        rep = descriptors[members[0]]
        for index, other in enumerate(representatives):
            if _close_enough(rep, other, tolerance_pt):
                merged[index].extend(members)
                break
        else:
            merged.append(list(members))
            representatives.append(rep)
    return merged


# ---------------------------------------------------------------------------
# 取り出し
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shape:
    descriptor: tuple[float, ...]
    center_pt: tuple[float, float]
    rect_pt: tuple[float, float, float, float]
    size_mm: float


def _page_shapes(pdf_path: str | Path, page_index: int, scale: DrawingScale) -> list[_Shape]:
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません")
        page = doc.load_page(page_index)
        out: list[_Shape] = []
        for drawing in page.get_drawings():
            described = _describe(drawing["items"])
            if described is None:
                continue
            descriptor, points = described
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            rect = (min(xs), min(ys), max(xs), max(ys))
            size_pt = max(rect[2] - rect[0], rect[3] - rect[1])
            out.append(
                _Shape(
                    descriptor=descriptor,
                    center_pt=(sum(xs) / len(xs), sum(ys) / len(ys)),
                    rect_pt=rect,
                    size_mm=size_pt * scale.mm_per_point,
                )
            )
    return out


def find_repeated_symbols(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    min_count: int = DEFAULT_MIN_COUNT,
    min_mm: float = SYMBOL_MIN_MM,
    max_mm: float = SYMBOL_MAX_MM,
    tolerance_pt: float = SHAPE_TOLERANCE_PT,
) -> list[SymbolCluster]:
    """繰り返し現れる図形を群にまとめて返す。**名前は付けない。**

    大きさの窓(``min_mm``〜``max_mm``、外接矩形の長いほうの辺)から外れた図形は
    記号として数えない。窓の既定値は暫定で、実図面で校正していない。

    スキャンされただけのページには図形が入っていないので、常に空を返す。
    **空は「記号が無い」ではなく「この手法では読めていない」である。**
    """
    if min_count < 1:
        raise ValueError("min_count は 1 以上でなければなりません")
    if min_mm <= 0 or max_mm <= min_mm:
        raise ValueError("大きさの窓が不正です")
    if tolerance_pt <= 0:
        raise ValueError("許容差は正でなければなりません")

    shapes = [s for s in _page_shapes(pdf_path, page_index, scale) if min_mm <= s.size_mm <= max_mm]
    if not shapes:
        return []

    descriptors = [s.descriptor for s in shapes]
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, descriptor in enumerate(descriptors):
        groups[_quantize(descriptor, tolerance_pt)].append(index)

    clusters: list[SymbolCluster] = []
    for members in _merge_near(groups, descriptors, tolerance_pt):
        if len(members) < min_count:
            continue
        members.sort(key=lambda i: (round(shapes[i].center_pt[1], 1), round(shapes[i].center_pt[0], 1)))
        first = shapes[members[0]]
        clusters.append(
            SymbolCluster(
                count=len(members),
                positions_pt=tuple(shapes[i].center_pt for i in members),
                rects_pt=tuple(shapes[i].rect_pt for i in members),
                size_mm=first.size_mm,
                page_index=page_index,
                descriptor=first.descriptor,
            )
        )
    # 呼び出し順に依存しないよう、件数の多い順・位置順に並べる。
    clusters.sort(key=lambda c: (-c.count, round(c.positions_pt[0][1], 1), round(c.positions_pt[0][0], 1)))
    return clusters


def read_legend_symbols(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    min_mm: float = SYMBOL_MIN_MM,
    max_mm: float = SYMBOL_MAX_MM,
    max_gap_pt: float = 400.0,
) -> list[LegendSymbol]:
    """凡例のページから「名前 ↔ 図形」の対応を読む。

    凡例は**名前と図形が同じ行に並ぶ**という書き方の決まりに頼る。
    図形の縦の帯に重なる文字のうち、図形の**左側にあって最も近いもの**を
    名前とする。見つからなければその図形は返さない(**名前を作らない**)。

    ``max_gap_pt`` より離れた文字は、別の欄の文字とみなして採らない。
    """
    shapes = [s for s in _page_shapes(pdf_path, page_index, scale) if min_mm <= s.size_mm <= max_mm]
    if not shapes:
        return []

    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        spans: list[tuple[str, tuple[float, float, float, float]]] = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if text:
                        spans.append((text, tuple(span["bbox"])))

    out: list[LegendSymbol] = []
    for shape in shapes:
        x0, y0, x1, y1 = shape.rect_pt
        best: tuple[float, str, tuple[float, float, float, float]] | None = None
        for text, bbox in spans:
            text_center_y = (bbox[1] + bbox[3]) / 2.0
            if not y0 <= text_center_y <= y1:
                continue  # 同じ行に無い
            if bbox[2] > x0:
                continue  # 図形より左にない
            gap = x0 - bbox[2]
            if gap > max_gap_pt:
                continue
            if best is None or gap < best[0]:
                best = (gap, text, bbox)
        if best is None:
            continue
        out.append(
            LegendSymbol(
                name=best[1],
                name_rect_pt=best[2],
                symbol_rect_pt=shape.rect_pt,
                size_mm=shape.size_mm,
                page_index=page_index,
                descriptor=shape.descriptor,
            )
        )
    out.sort(key=lambda s: (round(s.symbol_rect_pt[1], 1), round(s.symbol_rect_pt[0], 1)))
    return out


def name_clusters(
    clusters: list[SymbolCluster],
    legend: list[LegendSymbol],
    tolerance_pt: float = SHAPE_TOLERANCE_PT,
) -> list[NamedSymbolCount]:
    """群と凡例を突き合わせる。**突き合わない群は名前なしのまま返す。**

    形が同じ(回転・鏡像を除いて)ときだけ名前を付ける。似ている順に
    当てはめることはしない。凡例に 2 つ以上当たった場合も名前を付けない
    (どちらか選ぶ根拠が無いため、食い違いとして残す)。
    """
    out: list[NamedSymbolCount] = []
    for cluster in clusters:
        hits = [s for s in legend if _close_enough(cluster.descriptor, s.descriptor, tolerance_pt)]
        if len(hits) == 1:
            out.append(
                NamedSymbolCount(
                    name=hits[0].name,
                    count=cluster.count,
                    cluster=cluster,
                    legend=hits[0],
                    basis=(
                        f"凡例(ページ{hits[0].page_index + 1})の"
                        f"「{hits[0].name}」と形が一致"
                    ),
                )
            )
        elif len(hits) > 1:
            names = "・".join(sorted(s.name for s in hits))
            out.append(
                NamedSymbolCount(
                    name=None,
                    count=cluster.count,
                    cluster=cluster,
                    legend=None,
                    basis=f"凡例の複数の記号と形が一致したため名前を決めない({names})",
                )
            )
        else:
            out.append(
                NamedSymbolCount(
                    name=None,
                    count=cluster.count,
                    cluster=cluster,
                    legend=None,
                    basis="凡例に同じ形が無いため名前を付けない",
                )
            )
    return out
