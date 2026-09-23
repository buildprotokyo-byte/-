"""OCR エンジンの差し込み口(`OcrBackend`)の実装。**どれも任意の依存である。**

なぜ本体(`ocr_text.py`)と分けてあるか
--------------------------------------
OCR のエンジンは、このリポジトリの `requirements.txt` に入っていない。
入れると全員のテスト実行と CI に 400MB 級の追加インストールが乗るうえ、
**エンジンが無くても経路の振る舞いは試験できる**からである
(`tests/scan_fixtures.py` の試験体)。

そこで、エンジンを実際に呼ぶコードはこのモジュールにまとめ、
**読み込みに失敗したら `OcrUnavailable` を投げて止める。** 黙って空の
結果を返すと「掛けたが 1 文字も無かった」と区別できない。

RapidOCR を選んだ理由(2026-09-22 時点)
--------------------------------------
- **pip だけで入る。** `pip install rapidocr-onnxruntime`。モデルが wheel に
  同梱されるので、実行時にモデルを取りに行かない。
- **CPU で動く。** この環境は GPU 無し・4 コア。合成した A3 のスキャン 1 枚
  (200〜300dpi)で 1〜3 秒だった。
- PyTorch を要求しない。この環境では `download.pytorch.org` が 403 で、
  PyTorch 系のエンジン(EasyOCR・manga-ocr)は入れにくい。

**Tesseract は採っていない。** この環境では `apt-get` が 403 で遮断されて
いて、`tesseract` の本体を入れられず、**動かして確かめられなかった**ため。
確かめていないものを「使える」と書かない。GitHub Actions のように apt が
通る場所では選択肢になる(`docs/ocr_scanned_pages_report.md` 5節)。

モデルの選び方(**ここが一番大事**)
----------------------------------
RapidOCR の既定の文字認識モデルは**中国語・英語**のものである。実測では

- 既定(中国語・英語): 数字と英数字の記号(``1650`` ``WD-01``)を正しく読み、
  **日本語の語を中国語の字に化けさせた**(``種別`` → ``种别`` 確信度 0.99)
- 日本語モデル(PP-OCR の japan): 語を正しく読み、**数字を壊した**
  (``1650`` → ``16.0``、``95.54`` → ``ｓｓ・ｓ４``)

つまり**片方だけでは足りない。** `recognize_page(backends=[...])` に
2 つ渡して、両方が同じに読んだ語だけを通す使い方を想定している。
日本語モデルは wheel に同梱されていないので、使う場合は
`rec_model_path` / `rec_keys_path` に置き場所を渡す
(取り方は `docs/ocr_scanned_pages_report.md` 3節)。
**モデルの取得をこのコードが勝手に行うことはしない。**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from axes.image_axis.ocr_text import OcrUnavailable, OcrWord

#: 既定のモデルが何語のものかを、証拠に残すための名前。
DEFAULT_MODEL_ID = "rapidocr-onnxruntime-default(ch+en)"


class RapidOcrBackend:
    """RapidOCR(ONNX Runtime)を `OcrBackend` の形にしたもの。

    `rec_model_path` と `rec_keys_path` を渡すと、文字認識のモデルだけを
    差し替えられる(日本語モデルを使う場合)。**検出のモデルは共通のまま**で、
    どこに文字があるかの判定は変わらない。
    """

    def __init__(
        self,
        *,
        name: str = "rapidocr",
        rec_model_path: str | Path | None = None,
        rec_keys_path: str | Path | None = None,
        model_id: str | None = None,
        **options: Any,
    ) -> None:
        self.name = name
        self.model_id = model_id or (
            f"rapidocr-rec:{Path(rec_model_path).name}"
            if rec_model_path is not None
            else DEFAULT_MODEL_ID
        )
        self._options = dict(options)
        if rec_model_path is not None:
            path = Path(rec_model_path)
            if not path.exists():
                raise OcrUnavailable(f"文字認識モデルが見つかりません: {path}")
            self._options["rec_model_path"] = str(path)
        if rec_keys_path is not None:
            keys = Path(rec_keys_path)
            if not keys.exists():
                raise OcrUnavailable(f"文字の一覧が見つかりません: {keys}")
            self._options["rec_keys_path"] = str(keys)
        self._engine = _load_engine(self._options)

    def recognize(self, image: np.ndarray, *, dpi: int) -> Sequence[OcrWord]:
        """画像 1 枚を読む。**返すのは読めた語だけで、直しはしない。**"""
        result, _elapsed = self._engine(image)
        out: list[OcrWord] = []
        for entry in result or ():
            box, text, score = entry[0], entry[1], entry[2]
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            out.append(
                OcrWord(
                    text=str(text),
                    rect_px=(min(xs), min(ys), max(xs), max(ys)),
                    confidence=float(score),
                )
            )
        return out


def _load_engine(options: dict[str, Any]):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:  # pragma: no cover - 環境に依存する
        raise OcrUnavailable(
            "rapidocr-onnxruntime が入っていません。"
            "`pip install rapidocr-onnxruntime` で入ります"
            "(requirements.txt には入れていない。理由はこのモジュールの冒頭)"
        ) from error
    return RapidOCR(**options)


def is_rapidocr_available() -> bool:
    """RapidOCR が読み込めるか。**テストの skip 判定にだけ使う。**"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:  # pragma: no cover - 環境に依存する
        return False
    return True
