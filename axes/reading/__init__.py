"""読み取り工程(段階0.5)と、読み取り結果の受け取り。

外部の読み手(言語モデル)に資料を渡して数量を答えさせる経路を、
本番のコードとして持つ場所。ベンチマークの検証用プロンプトとは別に、
`arbitration.inference_orchestrator` が受け取れる形まで面倒を見る。
"""

from axes.reading.protocol import (
    DERIVATION_LABELS,
    FOUNDATION_ELEMENTS,
    ParsedReading,
    QuantityRequest,
    ReadingFinding,
    ReadingRequest,
    ReadingResponseError,
    answer_schema,
    build_reading_prompt,
    parse_reading_response,
    to_orchestrator_evidence,
)

__all__ = [
    "DERIVATION_LABELS",
    "FOUNDATION_ELEMENTS",
    "ParsedReading",
    "QuantityRequest",
    "ReadingFinding",
    "ReadingRequest",
    "ReadingResponseError",
    "answer_schema",
    "build_reading_prompt",
    "parse_reading_response",
    "to_orchestrator_evidence",
]
