"""Fill the review-page HTML templates with real data.

Deliberately simple string substitution, not a templating engine: the
placeholders are unique tokens (``__NAME__``) chosen not to collide with
any JS/CSS in the templates, and each render_* function is the only place
that needs to know both the placeholder names and the JSON shape a
template's own JS expects.
"""
from __future__ import annotations

import json
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_character_review(
    words_by_page: dict,
    *,
    project_label: str,
    image_map: dict,
    active_page: str,
    db_doc_path: str,
    cards: list[dict] | None = None,
) -> str:
    """``image_map`` is ``{page_key: {"src": ..., "w": ..., "h": ..., "label": ...}}``
    -- ``src`` must match a filename this page will be served/published
    alongside (see review_ui/README section in the main README for how the
    KDX802/amusement-facility demo instances did this via the Artifact
    tool's multi-file publish)."""
    html = (_TEMPLATES_DIR / "character_review_template.html").read_text(encoding="utf-8")
    html = html.replace("__WORDS_JSON__", json.dumps(words_by_page, ensure_ascii=False))
    html = html.replace("__PROJECT_LABEL__", project_label)
    html = html.replace("__IMG_JSON__", json.dumps(image_map, ensure_ascii=False))
    html = html.replace("__ACTIVE_PAGE__", json.dumps(active_page))
    html = html.replace("__DB_DOC_PATH_JSON__", json.dumps(db_doc_path))
    html = html.replace("__CARDS_JSON__", json.dumps(cards or [], ensure_ascii=False))
    return html


def render_solid_preview(
    data: dict, *, img_w: int, img_h: int, project_label: str, plan_image_src: str = "plan.png"
) -> str:
    html = (_TEMPLATES_DIR / "solid_preview_template.html").read_text(encoding="utf-8")
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__IMG_W__", str(img_w))
    html = html.replace("__IMG_H__", str(img_h))
    html = html.replace("__PROJECT_LABEL__", project_label)
    html = html.replace("__PLAN_IMAGE_SRC__", plan_image_src)
    return html
