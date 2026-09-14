"""Pluggable OCR used to *ground* the vision-language model, not replace it.

Rationale: small dimension numerals ("2,730", "FL+450") and half-width/
full-width mixed Japanese annotations are exactly what general-purpose VLMs
hallucinate on. Running a dedicated OCR engine over every tile first and
handing its raw text to the child agent as grounding context (alongside the
image) measurably reduces numeric transcription errors versus vision-only
reading, and gives the parent agent an independent signal to cross-check
against when child agents disagree.

Two engines are supported, both open source:
- PaddleOCR (better recall on rotated/vertical Japanese text common in
  architectural drawings; default)
- Tesseract with the `jpn` traineddata (lighter weight, no model download of
  its own beyond the traineddata, easy to install everywhere)

Both are imported lazily so a missing dependency only breaks OCR calls, not
the rest of the package.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from .config import settings

logger = logging.getLogger("drawing_ai.ocr_engine")


@dataclass
class OcrWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in tile-local pixels


@dataclass
class OcrResult:
    words: list[OcrWord]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


class OcrEngine:
    def run(self, image_path: str) -> OcrResult:  # pragma: no cover - interface
        raise NotImplementedError


class PaddleOcrEngine(OcrEngine):
    def __init__(self, lang: str = "japan") -> None:
        from paddleocr import PaddleOCR  # lazy import

        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def run(self, image_path: str) -> OcrResult:
        raw = self._ocr.ocr(image_path, cls=True)
        words: list[OcrWord] = []
        for line in raw or []:
            for box, (text, conf) in line:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append(
                    OcrWord(
                        text=text,
                        confidence=float(conf),
                        box=(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
                    )
                )
        return OcrResult(words=words)


class TesseractOcrEngine(OcrEngine):
    def __init__(self, lang: str = "jpn+eng") -> None:
        import pytesseract  # lazy import, noqa: F401

        self._lang = lang

    def run(self, image_path: str) -> OcrResult:
        import pytesseract
        from PIL import Image

        with Image.open(image_path) as im:
            data = pytesseract.image_to_data(
                im, lang=self._lang, output_type=pytesseract.Output.DICT
            )
        words: list[OcrWord] = []
        for i, text in enumerate(data.get("text", [])):
            text = text.strip()
            if not text:
                continue
            conf_raw = data.get("conf", ["-1"])[i]
            try:
                conf = max(0.0, float(conf_raw)) / 100.0
            except ValueError:
                conf = 0.0
            x, y, w, h = (
                data["left"][i],
                data["top"][i],
                data["width"][i],
                data["height"][i],
            )
            words.append(OcrWord(text=text, confidence=conf, box=(x, y, x + w, y + h)))
        return OcrResult(words=words)


class NullOcrEngine(OcrEngine):
    """No-op engine used when no OCR backend is installed.

    The pipeline still runs (vision-only), it just loses the OCR grounding
    signal described above -- accuracy degrades, but it doesn't hard-fail.
    """

    def run(self, image_path: str) -> OcrResult:
        return OcrResult(words=[])


@lru_cache(maxsize=1)
def get_ocr_engine() -> OcrEngine:
    """Instantiate the configured OCR engine, falling back to a no-op
    engine if its dependency isn't installed.

    That fallback used to be silent -- for a sheet with no vector text
    layer at all (a plain scanned drawing; confirmed on a real project's
    11-page drawing set, every page of which was exactly this), OCR is
    the *only* text-recognition path available, so losing it invisibly
    means that sheet's Stage 1 (character recognition) silently produces
    nothing, with no signal to the operator that the configured engine
    never actually loaded. Now logged at warning level so a missing
    dependency shows up in the logs instead of just quietly degrading
    accuracy.
    """
    engine = settings.ocr_engine.lower()
    try:
        if engine == "paddleocr":
            return PaddleOcrEngine(lang=settings.ocr_lang)
        if engine == "tesseract":
            return TesseractOcrEngine()
    except ImportError as exc:
        logger.warning(
            "configured OCR engine '%s' is not installed (%s) -- falling back to "
            "no OCR grounding. Sheets with no vector text layer will lose their only "
            "text-recognition path entirely.",
            engine, exc,
        )
        return NullOcrEngine()

    logger.warning("unknown DRAWING_AI_OCR_ENGINE '%s' -- falling back to no OCR grounding.", engine)
    return NullOcrEngine()


def ocr_tile(image_path: str) -> OcrResult:
    return get_ocr_engine().run(image_path)
