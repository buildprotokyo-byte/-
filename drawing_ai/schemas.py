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
    # Ground-truth words inside this tile's bbox, already grouped into
    # visual table rows (see vector_extractor.extract_table_rows). Empty
    # when the tile has no vector ground truth, or the region isn't
    # table-shaped. Populated once in the orchestrator alongside
    # ground_truth_text so every agent can use it, but it is currently
    # consumed only by spec_agent (COAI-01), where a specification sheet's
    # 仕上表/建具表 layout varies enough between design offices that
    # structure should be extracted before meaning is inferred.
    ground_truth_table_rows: list[list[str]] = Field(default_factory=list)


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
    # room要素向け: 図面に印字された面積表記(例: "5.3m2", "12.5畳")を
    # area_parsing.parse_area_text() で数値化したもの。壁ポリゴンからの
    # 算出(SolidModel)が使えない図面でも、印字された面積を直接使えるように
    # するためのフォールバック経路(未算出ならNone)。
    area_sqm: Optional[float] = None
    # "printed_sqm" | "tatami_conversion" | "width_depth_estimate" | "none"
    # width_depth_estimate: 印字された面積が無い場合、子AIが画像上でその部屋の
    # 境界だと判断した幅・奥行の数字(CADの整列した寸法チェーンでなくても、
    # 現地実測でバラバラに書き込まれた数字でもよい)から算出。ベクター根拠が
    # ある場合は両方の数字が原文と一致することを条件とする(未検証のペアは
    # 採用しない)。
    area_source: str = "none"


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


class SpecProduct(BaseModel):
    """メーカー製品情報。仕様書に型番等の記載がある場合のみ意味を持つ。"""

    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    category: str = ""
    looked_up_details: str = ""  # Web調査等で得られた寸法・色・施工条件(取得できた場合のみ)
    lookup_status: str = "not_attempted"  # found | not_found | not_attempted


class SpecItem(BaseModel):
    """仕様書の1行(場所×仕上げ×下地)。COAI-01(仕様書読解AI)が扱う最小単位。

    「記載がある=改修工事の対象範囲」「下地が既存利用か新規か」という、
    仕様書読解AIの役割定義(COAI_DEFINITIONS.md参照)で確定した2つの含意を、
    in_scope / substrate_status としてそれぞれ独立に保持する。
    """

    finish: str  # 正規化された仕上げ名(正規化できなければraw_finish_termと同じ)
    substrate: str = ""
    substrate_status: str = "unknown"  # existing | new | unknown
    notes: str = ""
    in_scope: bool = True
    product: SpecProduct = Field(default_factory=SpecProduct)
    raw_finish_term: str = ""  # 仕様書に実際に書かれていた原文表記(正規化前)
    canonical_finish_term: Optional[str] = None  # 公開基準用語への正規化結果。見つからなければnull
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    verified_by_vector: bool = False
    needs_human_review: bool = False
    source_tile_ids: list[str] = Field(default_factory=list)


class SpecRoom(BaseModel):
    """仕様書上の1区画。設計者の区切り方(部屋単位)をそのまま保持する。"""

    room_name: str
    specs: list[SpecItem] = Field(default_factory=list)
    source_sheet_ids: list[str] = Field(default_factory=list)


class QAItem(BaseModel):
    """質疑書(RFI)形式の資料の1行(質問No./工種/質疑事項/回答)。

    ブラインドテスト(KDX802号室)で、ある行の回答が別の行を指す
    クロスリファレンス(例: "質疑No.6参照")を正しく追えず誤読した
    実例が見つかったため、resolved_answer/resolved_from_item_noで
    解決結果を明示的に保持する(reference_resolution.py参照)。
    """

    item_no: Optional[int] = None
    category: str = ""  # 工種(建築/設備/電気/共通 等)
    question: str = ""
    answer: str = ""
    # answerに "質疑No.X参照" が含まれていた場合、解決後の全文がここに入る。
    # 参照が無い、または解決できた場合はanswerと同じ。
    resolved_answer: str = ""
    resolved_from_item_no: Optional[int] = None
    unresolved_chained_reference: Optional[int] = None


class SpecResult(BaseModel):
    """仕様書読解AI(COAI-01)の最終出力。"""

    rooms: list[SpecRoom] = Field(default_factory=list)
    qa_items: list[QAItem] = Field(default_factory=list)
    # 「工事範囲を絞り込む言葉」(例: 2階居室のみ, 外壁のみ) -- 既存のPhase1/2の
    # どちらにも対応物が無い、仕様書読解AI固有の出力。
    scope_target_terms: list[str] = Field(default_factory=list)
    # タイル間(=仕様書ページの異なる領域)で同じ部屋・同じ仕上げの下地判定が
    # 食い違った場合など、黙って片方を採用せず正直に記録する項目。
    ambiguous_flags: list[str] = Field(default_factory=list)
    completeness_note: str = ""


class PipelineRun(BaseModel):
    """Top-level result returned by the orchestrator / API."""

    run_id: str
    sheets: list[SheetOverview] = Field(default_factory=list)
    spec: SpecResult = Field(default_factory=SpecResult)
    phase1: Phase1Result = Field(default_factory=Phase1Result)
    phase2: Phase2Result = Field(default_factory=Phase2Result)
    phase3: Phase3Result = Field(default_factory=Phase3Result)
    elapsed_s: float = 0.0
    tile_count: int = 0
    model_calls: int = 0
    warnings: list[str] = Field(default_factory=list)
