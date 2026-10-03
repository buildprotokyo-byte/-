"""K-66「同じ意味なら正解」の判定を 1 か所に集めた部品。

おーちゃんの K-66: 採点と 3 回の一致の比較が、どちらも**文字の比較**に寄っていた。
だから「大工工事」と「木工事」のように**同じ意味なのに外れ**になる行が出る。
この package だけが「同じか」を決め、呼ぶ側は文字を比べない。

基準は `docs/k66_sameness_criteria.md`(測る前にコミット済み)。

守ること
--------
1. **数字を上げるための緩めではない。**`estimating/scoring.py` の但し書き
   (「似ている語で寄せない」)を捨てるのではなく、「文字が似ている」を
   「**構造が同じ**」に差し替える。緩めていないことは囮で毎回測る
   (`benchmarks/measure_sameness.py`、線は囮の通過 0%)。
2. **名前の判定に数量を混ぜない。**数量は `quantity_verdict()` で別に判定する。
3. **AI を呼べない環境では、迷う対は `比較不能` のまま返す。**`○` を増やす
   方向に黙って倒れない。
4. **判定には必ず 1 行の理由を付ける**(`Verdict.reason`)。確認画面に出せる形。

使い方::

    from sameness import compare, quantity_verdict

    v = compare("壁クロス張替", "壁ビニルクロス貼替", level="細目")
    v.value   # "○"
    v.reason  # "細目 N04・部位 内壁・状態 張替・材料 クロス がどちらも同じ"
"""

from __future__ import annotations

from sameness.judge import (
    HIGHER_LOWER,
    GRAIN,
    INCOMPARABLE,
    NOT_SAME,
    SAME,
    VALUES,
    Verdict,
    compare,
    compare_keys,
    judge_with_ai,
)
from sameness.keys import StructureKey, structure_key
from sameness.normalize import canonical_unit, flatten, room_key
from sameness.quantity import QuantityVerdict, quantity_verdict, quantity_verdicts
from sameness.terms import Terms, load_terms

__all__ = [
    "GRAIN",
    "HIGHER_LOWER",
    "INCOMPARABLE",
    "NOT_SAME",
    "SAME",
    "VALUES",
    "QuantityVerdict",
    "StructureKey",
    "Terms",
    "Verdict",
    "canonical_unit",
    "compare",
    "compare_keys",
    "flatten",
    "judge_with_ai",
    "load_terms",
    "quantity_verdict",
    "quantity_verdicts",
    "room_key",
    "structure_key",
]
