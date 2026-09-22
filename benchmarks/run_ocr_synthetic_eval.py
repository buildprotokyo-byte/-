"""合成したスキャン図面に OCR を掛けて、**何が読めて何が化けたか**を数える。

使い方::

    python -m benchmarks.run_ocr_synthetic_eval
    python -m benchmarks.run_ocr_synthetic_eval \\
        --japanese-rec-model /path/to/japan_rec.onnx \\
        --japanese-rec-keys /path/to/japan_dict.txt

**実図面は使わない。** 元図をこのスクリプトが合成し、ラスター化して
「スキャンされた紙」にし、元図に書いてあった文字と読めた文字を突き合わせる。
元図の文字が正解なので、**正解ファイルをリポジトリに置く必要がない。**

出すもの
--------
- 語ごとの一致率(幅寄せ後の完全一致。似ている字は一致に数えない)
- **化けた語の一覧**(何を何と読んだか)。これが設計の根拠になる
- 2 つのモデルを渡したときに、突き合わせを通った語の割合

**この数値は合成した紙の上のものである。** 実図面のスキャンは、
紙の折れ・かすれ・手書きの書き込み・押印が乗るので、ここより悪くなる。
実案件での率は、実図面を持っている側(Codex)で測る必要がある。
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from axes.image_axis.ocr_backends import RapidOcrBackend, is_rapidocr_available
from axes.image_axis.ocr_text import normalize_ocr_text, recognize_page

TITLE_BLOCK = (
    ((820.0, 740.0), "縮尺 1/50"),
    ((820.0, 760.0), "専有延床面積 95.54 ㎡"),
    ((820.0, 780.0), "施工床面積 90.61 ㎡"),
)

TABLE = {
    "origin": (80.0, 120.0),
    "col_widths": (110.0, 90.0, 80.0, 80.0, 70.0),
    "row_height": 24.0,
    "rows": (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "2"),
        ("WD-02", "折戸", "1200", "2000", "1"),
        ("WD-03", "開き戸", "780", "2000", "3"),
    ),
    "caption": "建具表",
}

#: 紙の劣化の度合い。(名前, 傾き(度), ノイズの強さ)
CONDITIONS = (
    ("きれい", 0.0, 0.0),
    ("少し傾き", 0.7, 4.0),
    ("傾き+粒状", 1.5, 12.0),
)


def _build(tmp: Path, rotate: float, noise: float, dpi: int) -> tuple[Path, list[str]]:
    from tests.scan_fixtures import build_vector_pdf, scan_pdf, trace_words

    source = build_vector_pdf(
        tmp / "vector.pdf", title_block=TITLE_BLOCK, table=dict(TABLE)
    )
    scanned = scan_pdf(
        source,
        tmp / f"scan_{rotate}_{noise}.pdf",
        dpi=dpi,
        rotate_degrees=rotate,
        noise_sigma=noise,
    )
    expected = [word.text for word in trace_words(source)]
    return scanned, expected


def _recoverable(expected: list[str], got: list[str]) -> tuple[int, list[str]]:
    """**ページ全体の読みの中に、その語がそのまま出てくるか。**

    エンジンは隣り合う語を 1 つにまとめて返すことがある(``縮尺`` と ``1/50``
    が ``縮尺1/50`` になる)。語ごとの完全一致だとこれが外れに数えられるが、
    読み取りの経路は行に組んでから正規表現に掛けるので、**つながっていても
    読める。** そちらの見方での取れ高をこの関数で数える。
    """
    joined = "".join(normalize_ocr_text(item) for item in got).replace(" ", "")
    hits = 0
    missing: list[str] = []
    for word in expected:
        target = normalize_ocr_text(word).replace(" ", "")
        if target and target in joined:
            hits += 1
        else:
            missing.append(word)
    return hits, missing


def _score(expected: list[str], got: list[str]) -> tuple[int, list[tuple[str, str]]]:
    """読めた語を、正解の語と突き合わせる。**近い字は一致に数えない。**"""
    pool = [normalize_ocr_text(item) for item in got]
    hits = 0
    missed: list[tuple[str, str]] = []
    for word in expected:
        target = normalize_ocr_text(word)
        if target in pool:
            pool.remove(target)
            hits += 1
        else:
            # いちばん似ている読みを「何と読まれたか」として添える。
            closest = min(
                pool,
                key=lambda candidate: _distance(candidate, target),
                default="(読めず)",
            )
            missed.append((word, closest))
    return hits, missed


def _distance(first: str, second: str) -> int:
    if not first or not second:
        return max(len(first), len(second))
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, start=1):
        current = [i]
        for j, b in enumerate(second, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--scan-dpi", type=int, default=200)
    parser.add_argument("--japanese-rec-model", type=Path, default=None)
    parser.add_argument("--japanese-rec-keys", type=Path, default=None)
    args = parser.parse_args()

    if not is_rapidocr_available():
        print(
            "rapidocr-onnxruntime が入っていません。"
            "`pip install rapidocr-onnxruntime` を入れてから実行してください"
        )
        return 1

    backends = [RapidOcrBackend(name="ch+en")]
    if args.japanese_rec_model is not None:
        backends.append(
            RapidOcrBackend(
                name="ja",
                rec_model_path=args.japanese_rec_model,
                rec_keys_path=args.japanese_rec_keys,
            )
        )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for label, rotate, noise in CONDITIONS:
            scanned, expected = _build(tmp, rotate, noise, args.scan_dpi)
            print(f"\n=== {label}(傾き {rotate}度 / ノイズ {noise}) ===")
            print(f"元図に書かれた語: {len(expected)} 件")

            for backend in backends:
                started = time.time()
                page = recognize_page(
                    scanned, 0, backends=[backend], dpi=args.dpi
                )
                elapsed = time.time() - started
                texts = [span.text for span in page.spans]
                hits, missed = _score(expected, texts)
                found, missing = _recoverable(expected, texts)
                print(
                    f"  [{backend.name}] 読めた語 {len(page.spans)} 件 / "
                    f"語ごとの完全一致 {hits}/{len(expected)} "
                    f"({hits / len(expected) * 100:.1f}%) / "
                    f"そのまま出てくる語 {found}/{len(expected)} "
                    f"({found / len(expected) * 100:.1f}%) / {elapsed:.1f}秒"
                )
                print(f"      出てこなかった語: {missing}")
                for word, closest in missed:
                    print(f"      {word!r} → {closest!r}")

            if len(backends) >= 2:
                page = recognize_page(scanned, 0, backends=backends, dpi=args.dpi)
                texts = [span.text for span in page.spans]
                hits, _ = _score(expected, texts)
                wrong = [
                    span.text
                    for span in page.spans
                    if normalize_ocr_text(span.text).replace(" ", "")
                    not in "".join(normalize_ocr_text(w) for w in expected).replace(" ", "")
                ]
                print(
                    f"  [突き合わせ] 通った語 {len(page.spans)} 件 / "
                    f"そのうち元図に無い読み {len(wrong)} 件 / "
                    f"食い違いとして落ちた語 {len(page.conflicts)} 件"
                )
                if wrong:
                    print(f"      元図に無い読み: {wrong}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
