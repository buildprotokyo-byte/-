"""Stage 1/2 review data: classify every ground-truth word as confident,
needs-light-check, or likely-misread, and split into the 文字識別/数字識別
sheets (see README 3.10 for the human-review-gate rationale).

The classification here is a transparent, inspectable *heuristic* over the
raw text -- not a learned model and not the pipeline's own grounding
confidence -- deliberately, so a human reviewer can see exactly why a token
was flagged and correct the rule itself over time, not just individual
readings. ``KNOWN_VOCAB`` is calibrated against the two real sample
projects used to build this tool (a KDX802 field-survey scan and a
vector-native amusement-facility floor plan) and is expected to need
extending per project/CAD vendor -- it is not a universal drawing
vocabulary.
"""
from __future__ import annotations

import re

from .. import vector_extractor as ve

KNOWN_VOCAB = {
    "下駄箱", "火", "AC", "ガス", "TVモニター", "給湯リモコン", "分", "×", "xH", "W", "H",
    "DN", "UP", "PS", "CH",
}

_PLAIN_NUMBER = re.compile(r"^\d{2,5}$")
_LONG_NUMBER = re.compile(r"^\d{6,}$")
_WH_TOKEN = re.compile(r"^[WH]\d{2,4}$", re.IGNORECASE)
_CH_TOKEN = re.compile(r"^CH\d{3,5}$", re.IGNORECASE)
_KAKU_TOKEN = re.compile(r"^\d+角$")
_BUN_TOKEN = re.compile(r"^\d+分$")


def classify_word(text: str) -> tuple[str, str]:
    """Return (tier, reason) for one ground-truth word's raw text.

    tier is one of "green" (confident), "amber" (plausible but wants a
    light check), "red" (doesn't match any known pattern -- likely OCR/
    text-layer misread).
    """
    if _PLAIN_NUMBER.match(text):
        return "green", "数字のみ・桁数が妥当(寸法値として整合)"
    if _LONG_NUMBER.match(text):
        return "amber", "数字のみだが桁数が異常(誤読/連結の疑い)"
    if _WH_TOKEN.match(text):
        return "green", "W(幅)/H(高さ)+数値の定型パターンに一致"
    if _CH_TOKEN.match(text):
        return "green", "CH(天井高)+数値の定型パターンに一致"
    if text in KNOWN_VOCAB:
        return "green", "既知の凡例語彙と完全一致"
    if _KAKU_TOKEN.match(text):
        return "amber", "数字+「角」(部材呼称の可能性) 要確認"
    if _BUN_TOKEN.match(text):
        return "amber", "数字+「分」(防火戸区分の可能性だが非定型) 要確認"
    return "red", "既知パターン・語彙に一致しない読み取り結果(OCR誤読の疑い)"


def _is_numeric_channel(text: str) -> bool:
    return bool(re.search(r"\d", text))


def build_character_review_data(gt: ve.SheetGroundTruth, page_key: str = "0") -> dict:
    """Turn one sheet's ground-truth words into the {page_key: [...]} shape
    the character_review.html template consumes."""
    rows = []
    for i, w in enumerate(gt.words):
        tier, reason = classify_word(w.text)
        rows.append(
            {
                "id": f"{page_key}-w{i}",
                "text": w.text,
                "x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1,
                "tier": tier,
                "reason": reason,
                "channel": "numeric" if _is_numeric_channel(w.text) else "text",
            }
        )
    return {page_key: rows}
