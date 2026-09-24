"""凡例の見本を図面に重ねて、形が合うかを測る(K-20 3 番)。

**結論を先に書く: この点数は対照 C6 に落ちた。採用していない。**

おーちゃんの決め(K-20 3 番)は「凡例の見本と同じ形に重なる線の塊だけを 1 個とし、
重ならないものは不明」である。そこで、文字が当たった場所のまわりに凡例の見本を重ねて、
重なりの度合い(重なった面積 ÷ 合わせた面積)を測った。

対照 C6(**結果を見る前に `docs/k20_legend_lookup_criteria.md` に書いた**)は
「**ある記号の見本を、別の記号が当たった場所に重ねる。取り違えた組の点数が、
正しい組の点数よりはっきり低いこと**」だった。**落ちた。**取り違えた組のほうが
わずかに高いくらいで、差が無い。**つまりこの点数は「そこに記号らしい塊があるか」は
見ているが、「それがどの記号か」は見ていない。**

10 周目の「群が 674 個出るので個数の一致が証拠にならない」、12 周目の
「1 名前あたりの形 1.00 は何も測っていなかった」と**同じ形の失敗**である。
指標が、測りたかったものから外れている。

**この道具は結果を残すために置いてある。数字を作り直したくなったら、これを動かす。**
図面の中身は出さない(件数と点数だけ)。

実行::

    python -m benchmarks.measure_legend_shape_overlap --pdf <図面.pdf> \
        --table <対照表.json> --legend-pages 6 22
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path

import numpy as np
import pymupdf
from numpy.lib.stride_tricks import sliding_window_view

#: 画の細かさ(1pt あたりの画素)。
ZOOM = 3.0

#: 文字のまわりを探す幅(pt)。
SEARCH = 13.0

#: 表題欄はこれより左。**数にも入れない。**
TITLE_BLOCK_X = 75.0

#: 凡例(22 ページ)の記号の列の y の帯。
CODE_BANDS = ((198.0, 224.0), (462.0, 492.0), (736.0, 762.0))

#: 見本の升目の幅の半分(pt)。
CELL_HALF_X = 5.5

#: インクとみなす明るさ。
INK_LEVEL = 200

SEED = 20260924


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def _ink(page: pymupdf.Page, rect: pymupdf.Rect) -> np.ndarray:
    pixmap = page.get_pixmap(
        clip=rect * page.rotation_matrix,
        matrix=pymupdf.Matrix(ZOOM, ZOOM),
        colorspace=pymupdf.csGRAY,
    )
    grid = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width
    )
    return grid < INK_LEVEL


def _crop(mask: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def best_overlap(window: np.ndarray, sample: np.ndarray) -> float:
    """見本を窓の中で滑らせて、いちばん重なったところの度合いを返す。"""
    height, width = sample.shape
    if window.shape[0] < height or window.shape[1] < width or not sample.any():
        return 0.0
    views = np.ascontiguousarray(
        sliding_window_view(window.astype(np.float32), (height, width))
    ).reshape(-1, height * width)
    flat = sample.astype(np.float32).ravel()
    inter = views @ flat
    union = views.sum(1) + flat.sum() - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(np.where(union > 0, inter / union, 0.0).max())


def samples_from_legend(page: pymupdf.Page, rows) -> dict[str, list[list[np.ndarray]]]:
    """凡例の記号の升目から見本を取る。**記号の文字そのものは外す**

    (図面では文字の置き場所が凡例と違うため)。
    """
    words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
    out: dict[str, list[list[np.ndarray]]] = {}
    for row in rows:
        hit = [
            w
            for w in words
            if w[4].strip() == row["code"]
            and any(lo <= w[1] <= hi for lo, hi in CODE_BANDS)
        ]
        if not hit:
            continue
        word = hit[0]
        band = next(b for b in CODE_BANDS if b[0] <= word[1] <= b[1])
        centre = (word[0] + word[2]) / 2
        rect = pymupdf.Rect(centre - CELL_HALF_X, band[0], centre + CELL_HALF_X, band[1])
        mask = _ink(page, rect).copy()
        start = int((word[1] - rect.y0) * ZOOM)
        stop = int((word[3] - rect.y0) * ZOOM)
        mask[:, max(0, start - 2) : stop + 2] = False
        cropped = _crop(mask)
        if cropped is None or cropped.size < 25:
            continue
        out.setdefault(_norm(row["code"]), []).append(
            [np.rot90(cropped, turn) for turn in range(4)]
        )
    return out


def _stats(values: list[float]) -> dict[str, float]:
    array = np.array(values) if values else np.zeros(1)
    return {
        "件数": len(values),
        "中央値": round(float(np.median(array)), 3),
        "平均": round(float(array.mean()), 3),
        "9割目": round(float(np.percentile(array, 90)), 3),
        "最大": round(float(array.max()), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--legend-pages", type=int, nargs="*", default=())
    parser.add_argument("--sample-page", type=int, default=22)
    args = parser.parse_args()

    table = json.loads(args.table.read_text(encoding="utf-8"))
    doc = pymupdf.open(args.pdf)
    samples = samples_from_legend(doc[args.sample_page - 1], table.get("symbols", ()))
    keys = sorted(samples)
    rng = random.Random(SEED)

    right: list[float] = []
    wrong: list[float] = []
    blank: list[float] = []
    for number in range(1, len(doc) + 1):
        if number in set(args.legend_pages):
            continue
        page = doc[number - 1]
        for word in page.get_text("words"):
            if word[0] < TITLE_BLOCK_X:
                continue
            key = _norm(word[4])
            if key not in samples:
                continue
            cx = (word[0] + word[2]) / 2
            cy = (word[1] + word[3]) / 2
            window = _ink(
                page, pymupdf.Rect(cx - SEARCH, cy - SEARCH, cx + SEARCH, cy + SEARCH)
            )
            right.append(
                max(best_overlap(window, s) for group in samples[key] for s in group)
            )
            other = rng.choice([k for k in keys if k != key])
            wrong.append(
                max(best_overlap(window, s) for group in samples[other] for s in group)
            )
            blank.append(best_overlap(window, np.zeros(samples[key][0][0].shape, bool)))

    print(
        json.dumps(
            {
                "見本の数": sum(len(v) for v in samples.values()),
                "記号の数": len(samples),
                "正しい組": _stats(right),
                "C6 取り違え": _stats(wrong),
                "C7 白紙": _stats(blank),
                "判定": "C6 に落ちた(取り違えても点数が下がらない)ので採用しない",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
