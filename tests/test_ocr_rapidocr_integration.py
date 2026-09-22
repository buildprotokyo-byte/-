"""本物の OCR エンジン(RapidOCR)を通す試験。**入っていなければ skip する。**

`requirements.txt` にエンジンを入れていないので、CI ではこの試験は skip される
(理由は `axes/image_axis/ocr_backends.py` の冒頭)。手元で
`pip install rapidocr-onnxruntime` を入れたときだけ走る。

**この試験は「OCR が当たる」ことの証拠ではない。** 当たり外れは
`benchmarks/run_ocr_synthetic_eval.py` で測る。ここで固定するのは、

1. 本物のエンジンが `OcrBackend` の口に収まっていること
2. 返ってきた位置がページの中に収まっていること(画素とポイントの取り違えは、
   ここで落ちる)

の 2 つだけである。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axes.image_axis.ocr_backends import RapidOcrBackend, is_rapidocr_available
from axes.image_axis.ocr_text import recognize_page
from tests.scan_fixtures import SCAN_DPI, build_vector_pdf, scan_pdf

pytestmark = pytest.mark.skipif(
    not is_rapidocr_available(),
    reason="rapidocr-onnxruntime が入っていない(requirements.txt には入れていない)",
)


def test_the_real_engine_reads_a_synthetic_scan(tmp_path: Path) -> None:
    source = build_vector_pdf(
        tmp_path / "vector.pdf",
        title_block=(
            ((820.0, 760.0), "縮尺 1/50"),
            ((820.0, 780.0), "専有延床面積 95.54 ㎡"),
        ),
    )
    scanned = scan_pdf(source, tmp_path / "scanned.pdf", dpi=SCAN_DPI)

    page = recognize_page(
        scanned, 0, backends=[RapidOcrBackend(name="rapidocr")], dpi=SCAN_DPI
    )

    assert page.spans, "本物のエンジンが 1 語も返さなかった"
    # 位置はページの中に収まっている(1190 x 842 のページ)。
    for span in page.spans:
        assert 0.0 <= span.rect_pt[0] <= 1190.0
        assert 0.0 <= span.rect_pt[1] <= 842.0
        assert span.rect_pt[2] > span.rect_pt[0]
        assert span.rect_pt[3] > span.rect_pt[1]

    # 数字は既定のモデル(中国語・英語)でも読める、というのが実測だった。
    # **読めなかったらこの試験は落ちてよい。** 落ちたら、エンジンかモデルが
    # 変わったということなので、報告書の数字を測り直す合図になる。
    assert any("95.54" in span.normalized for span in page.spans)
