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
    # Exact text extracted from the PDF's own vector/text layer that falls
    # inside this tile's pixel region (see vector_extractor.py). Empty when
    # the source sheet is not a born-digital PDF page (e.g. a scanned image,
    # DWG/DXF/JWW already rasterized upstream). When non-empty this is
    # authoritative -- it is literally what the CAD software wrote, not a
    # transcription -- and downstream agents should trust it far more than
    # OCR or a VLM's own reading.
    ground_truth_text: str = ""
    has_vector_ground_truth: bool = False


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
    verified_by_vector: bool = False  # PDFのベクター/文字レイヤーと一致確認済みか


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
    verified_by_vector: bool = False


class DimensionReading(BaseModel):
    """One decoded dimension/number and its semantic role."""

    tile_id: str
    raw_text: str  # OCR/読み取り原文 例: "2,730"
    value: Optional[float] = None
    unit: Optional[str] = None
    role_ja: str = ""  # 例: "壁芯-芯距離", "天井高", "開口幅"
    associated_elements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    verified_by_vector: bool = False  # PDFの文字レイヤーに同一表記が存在した
    verified_by_geometry: bool = False  # 対応する線分の実寸換算長と数値が一致した


class ElementReading(BaseModel):
    """One physical element identified on a sheet (wall/opening/fixture/...)."""

    element_id: str
    element_type: str  # wall / opening / fixture / finish / pipe_run / room 等
    label_ja: str
    sheet_id: str
    tile_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    verified_by_vector: bool = False


class VerticalSynthesisNote(BaseModel):
    """平面図×立面/断面図を突き合わせて得た「立体としての」補足情報。"""

    room_or_zone: str
    floor_height_m: Optional[float] = None
    wall_height_m: Optional[float] = None
    notes_ja: str = ""
    source_sheet_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ScaleConsistencyFlag(BaseModel):
    """A dimension number whose printed value disagrees with the real-world
    length measured from its nearby vector line, via the sheet's known
    print scale. This is a purely geometric/mathematical check -- no AI
    involved -- so a flag here means either a genuine drafting error in the
    source drawing, or that the wrong line was matched to the wrong number
    (in which case treat it as "unverifiable", not "wrong")."""

    sheet_id: str
    raw_text: str
    printed_value_mm: float
    measured_length_mm: float
    delta_pct: float
    location_hint: str = ""


class DimensionChainFlag(BaseModel):
    """A run of collinear dimension numbers whose parts don't sum to the
    stated total (加算検算 -- the same arithmetic check a human estimator
    does by hand). Purely arithmetic, no AI involved."""

    sheet_id: str
    axis: str  # "horizontal" | "vertical"
    component_raw_texts: list[str] = Field(default_factory=list)
    component_sum_mm: float
    total_raw_text: str
    total_value_mm: float
    delta_mm: float


class WallSegmentGeometry(BaseModel):
    """One wall footprint polygon extracted directly from the PDF's filled
    vector paths (not inferred by any model), extruded to a height pulled
    from the nearest CH=(天井高) ground-truth text on the same sheet."""

    sheet_id: str
    polygon_mm: list[list[float]] = Field(default_factory=list)  # [[x,y], ...] real-world mm
    height_mm: Optional[float] = None
    height_source: str = ""  # e.g. "CH=2850 (nearest label, 340px away)"


class RoomHeightFact(BaseModel):
    """A room/zone label matched to its nearest CH=(ceiling height) text on
    the same sheet -- both sides of the match are exact vector text, so this
    is near-ground-truth once the spatial match itself is correct."""

    sheet_id: str
    room_label: str
    ceiling_height_mm: float
    match_distance_px: float
    confidence: float = Field(ge=0.0, le=1.0)


class SolidModel(BaseModel):
    """Lightweight parametric 3D structure assembled from verified 2D
    geometry (wall footprints) + verified height text (CH=), mirroring the
    human step of mentally standing the plan up into a building. This is
    not a full BIM model -- it is the smallest 3D representation that is
    fully traceable back to ground-truth text/geometry rather than model
    guesswork."""

    wall_segments: list[WallSegmentGeometry] = Field(default_factory=list)
    room_heights: list[RoomHeightFact] = Field(default_factory=list)
    sheet_scale_mm_per_px: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Phase3Result(BaseModel):
    """数字・線・記号を正確に読み取り、意味を理解した結果。"""

    dimensions: list[DimensionReading] = Field(default_factory=list)
    symbols: list[SymbolReading] = Field(default_factory=list)
    elements: list[ElementReading] = Field(default_factory=list)
    vertical_synthesis: list[VerticalSynthesisNote] = Field(default_factory=list)
    low_confidence_flags: list[str] = Field(default_factory=list)
    accuracy_estimate: float = Field(ge=0.0, le=1.0, default=0.0)
    scale_consistency_flags: list[ScaleConsistencyFlag] = Field(default_factory=list)
    dimension_chain_flags: list[DimensionChainFlag] = Field(default_factory=list)
    solid_model: SolidModel = Field(default_factory=SolidModel)


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
