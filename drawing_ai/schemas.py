"""Data contracts shared by every stage of the pipeline.

These are also the contract this package hands to the outside world (the
future integration point with BUILD PRO OS's estimate tab): whatever
consumes ``Phase3Result`` should not need to know anything about tiles,
models, or how many child agents ran.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SheetType(str, Enum):
    """Phase 0 classification of a single drawing sheet/page."""

    SITE_PLAN = "site_plan"  # 配置図・敷地図
    FLOOR_PLAN = "floor_plan"  # 平面図
    ELEVATION = "elevation"  # 立面図
    SECTION = "section"  # 断面図
    STRUCTURAL = "structural"  # 構造図
    MEP = "mep"  # 設備図(電気・給排水・空調)
    DETAIL = "detail"  # 詳細図・矩計図
    SPECIFICATION = "specification"  # 仕様書・特記仕様
    LEGEND = "legend"  # 凡例・記号表
    OTHER = "other"
    UNKNOWN = "unknown"


class Tile(BaseModel):
    """One rendered image tile handed to a single child-agent call."""

    tile_id: str
    sheet_id: str
    sheet_index: int
    row: int
    col: int
    x0: int
    y0: int
    x1: int
    y1: int
    image_path: str
    ocr_text: str = ""


class SheetOverview(BaseModel):
    """Phase 0 output for one sheet: the human 'first glance' pass."""

    sheet_id: str
    sheet_index: int
    sheet_type: SheetType
    sheet_type_confidence: float = Field(ge=0.0, le=1.0)
    title_block_text: str = ""  # 図面名・縮尺・図番など、表題欄のOCR/読み取り
    one_line_summary: str = ""  # 「何の工事の図面か」を一言で
    scale_hint: Optional[str] = None  # 例: "1/100"
    notes: str = ""


class SiteFact(BaseModel):
    """A single, atomic fact about the site/lot (敷地).

    Phase 1 targets 100% accuracy, so every fact is individually sourced and
    scored rather than folded into a paragraph, so a human reviewer (or the
    parent agent) can see exactly which facts are certain vs. which need
    confirmation.
    """

    key: str  # e.g. "site_area_sqm", "frontage_road_width_m", "zoning", "setback_m"
    label_ja: str
    value: str
    unit: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_tile_ids: list[str] = Field(default_factory=list)
    source_sheet_ids: list[str] = Field(default_factory=list)
    raw_evidence_text: str = ""  # OCR/読み取り原文(監査用)
    needs_human_review: bool = False


class Phase1Result(BaseModel):
    """敷地情報の確定結果。"""

    facts: list[SiteFact] = Field(default_factory=list)
    completeness_score: float = Field(ge=0.0, le=1.0, default=0.0)
    unresolved_questions: list[str] = Field(default_factory=list)


class IntentStatement(BaseModel):
    """One inferred piece of construction intent/scope, in prose."""

    sheet_id: str
    text_ja: str  # 「何をしたいか」を表す一文〜数文
    construction_categories: list[str] = Field(default_factory=list)  # 大工/電気/管 等の想定工種
    confidence: float = Field(ge=0.0, le=1.0)
    source_tile_ids: list[str] = Field(default_factory=list)


class Phase2Result(BaseModel):
    """図面全体から読み取った「意図」の文章化結果。"""

    project_summary_ja: str = ""  # 全体を俯瞰した見積り用の導入文
    statements: list[IntentStatement] = Field(default_factory=list)
    inferred_construction_scope: list[str] = Field(default_factory=list)


class SymbolReading(BaseModel):
    """One decoded symbol/annotation and what it means."""

    tile_id: str
    symbol_text_or_glyph: str  # 記号そのもの or OCRされた表記
    meaning_ja: str  # 意味(例: "電源コンセント", "防水区画境界")
    location_hint: str = ""  # タイル内座標や近傍テキストなど
    confidence: float = Field(ge=0.0, le=1.0)


class DimensionReading(BaseModel):
    """One decoded dimension/number and its semantic role."""

    tile_id: str
    raw_text: str  # OCR/読み取り原文 例: "2,730"
    value: Optional[float] = None
    unit: Optional[str] = None
    role_ja: str = ""  # 例: "壁芯-芯距離", "天井高", "開口幅"
    associated_elements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ElementReading(BaseModel):
    """One physical element identified on a sheet (wall/opening/fixture/...)."""

    element_id: str
    element_type: str  # wall / opening / fixture / finish / pipe_run / room 等
    label_ja: str
    sheet_id: str
    tile_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class VerticalSynthesisNote(BaseModel):
    """平面図×立面/断面図を突き合わせて得た「立体としての」補足情報。"""

    room_or_zone: str
    floor_height_m: Optional[float] = None
    wall_height_m: Optional[float] = None
    notes_ja: str = ""
    source_sheet_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Phase3Result(BaseModel):
    """数字・線・記号を正確に読み取り、意味を理解した結果。"""

    dimensions: list[DimensionReading] = Field(default_factory=list)
    symbols: list[SymbolReading] = Field(default_factory=list)
    elements: list[ElementReading] = Field(default_factory=list)
    vertical_synthesis: list[VerticalSynthesisNote] = Field(default_factory=list)
    low_confidence_flags: list[str] = Field(default_factory=list)
    accuracy_estimate: float = Field(ge=0.0, le=1.0, default=0.0)


class PipelineRun(BaseModel):
    """Top-level result returned by the orchestrator / API."""

    run_id: str
    sheets: list[SheetOverview] = Field(default_factory=list)
    phase1: Phase1Result = Field(default_factory=Phase1Result)
    phase2: Phase2Result = Field(default_factory=Phase2Result)
    phase3: Phase3Result = Field(default_factory=Phase3Result)
    elapsed_s: float = 0.0
    tile_count: int = 0
    model_calls: int = 0
    warnings: list[str] = Field(default_factory=list)
