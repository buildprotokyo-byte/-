"""Drawing AI: open-source, multi-agent drawing-to-estimate reading pipeline.

Phase 0: overview triage (sheet classification, rough construction-type guess)
Phase 1: site/lot facts (敷地情報) extraction, target: 100% accuracy
Phase 2: intent reading -> free text description of what work is being requested
Phase 3: precise reading of numbers/lines/symbols and their meaning

Phase 4 (quantity take-off / pricing / estimate document generation) is out of
scope for this package; it consumes the Phase 3 structured output.
"""
