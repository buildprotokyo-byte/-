"""画像だけで「消して確かめる」(K-64 周 2・周 2b)。**どちらも基準を割ったので一本道にはつないでいない(測る道具として残す)。**

結果: 周 2(墨の画素)も周 2b(墨の塊)も、スキャン版で囮には勝つが、図形の数え方(K-51)とのページの順位の相関が
0.003 / 0.171 で線 0.5 を割った(docs/k64_criteria.md の結果節)。

PDF の図形も文字の層も無いページ(スキャン)では、K-51 の数え方(図形を 1 つずつ数える)はページ全体の画像 1 枚を
1 つの図形として数えるので、どう読んでも落ちが 100% になる。ここでは画像の墨で数える:

1. ページを幅 2000 画素の灰色の画像にし、明るさ ``INK_THRESHOLD`` 未満を墨とする。ページの縁 ``EDGE_FRACTION`` は数えない
   (図面枠の代わり。**仮の判断**)。
2. 読みの四角(縦横 ``MARGIN_PX`` 画素広げる。ページの面積の ``area_cap`` を超える四角は塗らない。K-51 と同じ)を白で塗る。
3. 落ちた率 = 塗った後に残った墨の画素 / 塗る前の墨の画素。残った墨のつながった塊の数も数える。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

WIDTH_PX = 2000
INK_THRESHOLD = 128
EDGE_FRACTION = 0.02
MARGIN_PX = 3


def is_image_only(page: Any) -> bool:
    """図形も文字の層も無く、画像だけがあるページか(pymupdf のページ)。"""
    return bool(page.get_images()) and not page.get_drawings() and not page.get_text("words")


def ink_mask(page: Any) -> np.ndarray:
    """ページを幅 2000 画素の灰色にし、墨(明るさ ``INK_THRESHOLD`` 未満)を True にした配列。縁は False。"""
    import pymupdf

    zoom = WIDTH_PX / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    mask = gray < INK_THRESHOLD
    ex, ey = int(pix.width * EDGE_FRACTION), int(pix.height * EDGE_FRACTION)
    if ey:
        mask[:ey, :] = False
        mask[-ey:, :] = False
    if ex:
        mask[:, :ex] = False
        mask[:, -ex:] = False
    return mask


def _blobs(mask: np.ndarray) -> int | None:
    try:
        from scipy import ndimage
    except ImportError:  # 塊の数は数えられない(未取得)。落ちた率は出る
        return None
    return int(ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))[1])


def erase_count(mask: np.ndarray, boxes: Sequence[Sequence[float]], area_cap: float = 0.01) -> dict[str, Any]:
    """墨の配列に読みの四角を塗って数える。**元の配列は書き換えない。**"""
    h, w = mask.shape
    total = int(mask.sum())
    left = mask.copy()
    painted = skipped = 0
    for b in boxes:
        if not b or len(b) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in b)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if (x1 - x0) * (y1 - y0) > area_cap * w * h:
            skipped += 1
            continue
        xa, ya = max(int(x0) - MARGIN_PX, 0), max(int(y0) - MARGIN_PX, 0)
        xb, yb = min(int(np.ceil(x1)) + MARGIN_PX, w), min(int(np.ceil(y1)) + MARGIN_PX, h)
        if xb > xa and yb > ya:
            left[ya:yb, xa:xb] = False
            painted += 1
    remain = int(left.sum())
    return {
        "数える墨(画素)": total,
        "残った墨(画素)": remain,
        "拾えた墨(画素)": total - remain,
        "落ちた率": round(remain / total, 4) if total else 0.0,
        "残った塊": _blobs(left),
        "塗った四角": painted,
        "大きすぎて塗らなかった四角": skipped,
    }


#: 周 2b: 点の汚れとして数えない塊の面積(画素未満)。**仮の判断。**
MIN_BLOB_PX = 4


def label_blobs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """墨のつながった塊(8 近傍)に番号を付ける。返すのは (番号の配列, 番号ごとの画素数)。"""
    from scipy import ndimage

    labels, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    return labels, np.bincount(labels.ravel(), minlength=n + 1)


def blob_count(labels: np.ndarray, sizes: np.ndarray, boxes: Sequence[Sequence[float]],
               area_cap: float = 0.01) -> dict[str, Any]:
    """周 2b: 塊の画素の半分以上が読みの四角に入った塊を「拾えた」とする。**落ちた率 = 拾えなかった塊 / 全部の塊。**"""
    h, w = labels.shape
    covered = np.zeros(labels.shape, dtype=bool)
    skipped = 0
    for b in boxes:
        if not b or len(b) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in b)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if (x1 - x0) * (y1 - y0) > area_cap * w * h:
            skipped += 1
            continue
        xa, ya = max(int(x0) - MARGIN_PX, 0), max(int(y0) - MARGIN_PX, 0)
        xb, yb = min(int(np.ceil(x1)) + MARGIN_PX, w), min(int(np.ceil(y1)) + MARGIN_PX, h)
        if xb > xa and yb > ya:
            covered[ya:yb, xa:xb] = True
    inside = np.bincount(labels[covered].ravel(), minlength=len(sizes))
    counted = sizes >= MIN_BLOB_PX
    counted[0] = False  # 0 は墨でない所
    caught = counted & (inside * 2 >= sizes)
    total, got = int(counted.sum()), int(caught.sum())
    return {"数える塊": total, "拾えた塊": got, "落ちた塊": total - got,
            "落ちた率": round((total - got) / total, 4) if total else 0.0, "大きすぎて塗らなかった四角": skipped}


def page_misses(pdf: str | Path, number: int, boxes: Sequence[Sequence[float]], area_cap: float = 0.01) -> dict[str, Any]:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return erase_count(ink_mask(doc.load_page(number - 1)), boxes, area_cap)
