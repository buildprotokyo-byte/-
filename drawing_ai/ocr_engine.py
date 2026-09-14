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


def _tile_core_rect(tile, neighbors: dict) -> tuple[float, float, float, float]:
    """This tile's exclusive "owned" region in full-sheet pixel coords: the
    midpoint line to each adjacent tile (row/col +-1), or the tile's own
    edge where there is no neighbor (a true sheet boundary).

    ``tile_sheet``'s overlap means every interior boundary is covered by
    TWO tiles; assigning each word to whichever single tile's core rect
    contains the word's own center point gives each word exactly one
    owner, and that owner is always the tile where the word sits closest
    to the middle (never the tile where it's cut by the crop edge) --
    see ``merge_tile_words`` for why this matters.
    """
    left = neighbors.get((tile.row, tile.col - 1))
    right = neighbors.get((tile.row, tile.col + 1))
    top = neighbors.get((tile.row - 1, tile.col))
    bottom = neighbors.get((tile.row + 1, tile.col))
    x0 = (tile.x0 + left.x1) / 2 if left else tile.x0
    x1 = (tile.x1 + right.x0) / 2 if right else tile.x1
    y0 = (tile.y0 + top.y1) / 2 if top else tile.y0
    y1 = (tile.y1 + bottom.y0) / 2 if bottom else tile.y1
    return x0, y0, x1, y1


def merge_tile_words(tiles_with_words: list[tuple]) -> list:
    """Merge one sheet's per-tile OCR word lists into a single deduplicated,
    full-sheet-coordinate list -- fixes a real bug found running this
    against 千倉相川邸 p.1 (README 3.13): with no merge step, a word sitting
    in the overlap band between two tiles is read TWICE, and the copy from
    whichever tile happens to crop through the middle of that word (not the
    word's own boundary, since the crop grid doesn't know where words are)
    comes back as garbled 1-2 character noise -- "A", "を", "に", "-" -- even
    though the SAME word was already read correctly and in full by the
    neighboring tile that contains it whole. That noise then gets shown to
    a human reviewer as if it were a real misread of legible text, which it
    is not: it's an artifact of not merging overlapping tiles.

    ``tiles_with_words``: list of ``(tile, [GroundTruthWord, ...])`` pairs,
    words already in full-sheet pixel coordinates (tile.x0/y0 already
    added), for every tile of ONE sheet. Returns the deduplicated list:
    each word kept exactly once, attributed to whichever tile's "core"
    (non-overlap-shared) region contains that word's own center -- see
    ``_tile_core_rect``.
    """
    neighbors = {(t.row, t.col): t for t, _ in tiles_with_words}
    merged = []
    for tile, words in tiles_with_words:
        cx0, cy0, cx1, cy1 = _tile_core_rect(tile, neighbors)
        for w in words:
            if cx0 <= w.cx < cx1 and cy0 <= w.cy < cy1:
                merged.append(w)
    return merged
