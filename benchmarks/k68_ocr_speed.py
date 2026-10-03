"""K-68 C 周 4: OCR(PaddleOCR)の速さを測る(パソコン側で動かす道具)。

一本道と同じ幅 2000 画素の画像(`draft.pages.render`)を作り、1 ページずつ OCR を掛けて時間を測る。
書く JSON は `draft/ocr.py` がそのまま読む形(``{"ページ": [{"ページ", "幅", "高さ", "結果", "秒"}]}``)。

**時間の数え方**: モデルの読み込みと 1 枚目の慣らし(同じページをもう 1 度掛ける前の 1 回)は、ページの秒に入れない。
別の欄(``読み込みの秒``・``慣らしの秒``)に書く。白紙の囮(幅 2000 画素の白い画像)の結果も書く。

使い方(パソコン側。PaddleOCR はこのリポジトリの依存に入れていない)::

    python -m benchmarks.k68_ocr_speed 図面.pdf 出力.json [--device cpu|gpu]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

Engine = Callable[[Path], Any]


def paddle_engine(device: str = "cpu") -> tuple[Engine, str]:
    """PaddleOCR の 1 ページを読む関数と版。2.x と 3.x の両方を受ける(`draft/ocr.py` が読む形で返す)。"""
    import paddleocr  # noqa: PLC0415  パソコン側だけで入れる

    version = str(getattr(paddleocr, "__version__", "?"))
    if version.startswith("2."):
        ocr = paddleocr.PaddleOCR(use_angle_cls=True, lang="japan", show_log=False, use_gpu=(device == "gpu"))

        def run2(path: Path) -> Any:
            res = ocr.ocr(str(path), cls=True)
            return (res[0] if res else None) or []

        return run2, version
    ocr = paddleocr.PaddleOCR(lang="japan", device=device)

    def run3(path: Path) -> Any:
        res = ocr.predict(str(path))
        if not res:
            return {}
        r = res[0]
        d = r.json if hasattr(r, "json") else dict(r)
        d = d.get("res", d) if isinstance(d, dict) else {}
        return {k: d[k] for k in ("rec_texts", "rec_scores", "rec_polys", "rec_boxes", "dt_polys") if k in d}

    return run3, version


def _jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def word_count(result: Any) -> int:
    from draft.ocr import _words_of

    return len(_words_of({"結果": result}))


def measure(pdf: Path, make_engine: Callable[[], tuple[Engine, str]], work: Path) -> dict[str, Any]:
    """全ページの OCR の時間と結果。"""
    from PIL import Image

    from draft.pages import WIDTH_PX, render

    pages = render(pdf, work / "ページ")
    started = time.perf_counter()
    engine, version = make_engine()
    load = time.perf_counter() - started

    warm = None
    if pages:
        t = time.perf_counter()
        engine(pages[0].image)
        warm = time.perf_counter() - t

    rows = []
    for p in pages:
        with Image.open(p.image) as im:
            w, h = im.size
        t = time.perf_counter()
        result = engine(p.image)
        sec = time.perf_counter() - t
        result = _jsonable(result)
        rows.append({"ページ": p.number, "幅": w, "高さ": h, "白紙": p.blank, "文字の層あり": bool(p.text.strip()),
                     "秒": round(sec, 3), "語の数": word_count(result), "結果": result})

    blank = work / "白紙の囮.png"
    Image.new("RGB", (WIDTH_PX, int(WIDTH_PX / 1.414)), "white").save(blank)
    t = time.perf_counter()
    decoy = _jsonable(engine(blank))
    decoy_sec = time.perf_counter() - t

    secs = [r["秒"] for r in rows]
    inked = [r for r in rows if not r["白紙"]]
    return {
        "ページ": rows,
        "まとめ": {
            "ページ数": len(rows),
            "合計の秒(読み込み・慣らしを除く)": round(sum(secs), 2),
            "ページあたりの秒の中央値": round(statistics.median(secs), 3) if secs else None,
            "ページあたりの秒の最大": round(max(secs), 3) if secs else None,
            "読み込みの秒": round(load, 2),
            "慣らしの秒": None if warm is None else round(warm, 2),
            "墨のあるページ": len(inked),
            "語が取れたページ(墨のあるページのうち)": sum(1 for r in inked if r["語の数"] > 0),
            "語の数の中央値(墨のあるページ)": statistics.median([r["語の数"] for r in inked]) if inked else None,
            "白紙の囮の語の数": word_count(decoy),
            "白紙の囮の秒": round(decoy_sec, 3),
            "文字の層の無いページ": sum(1 for r in rows if not r["文字の層あり"] and not r["白紙"]),
        },
        "環境": {
            "OCR の版": version,
            "Python": platform.python_version(),
            "OS": platform.system(),
            "CPU のコア数": os.cpu_count(),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pdf")
    p.add_argument("out")
    p.add_argument("--device", default="cpu", choices=("cpu", "gpu"))
    a = p.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        result = measure(Path(a.pdf), lambda: paddle_engine(a.device), Path(tmp))
    result["環境"]["装置"] = a.device
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["まとめ"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
