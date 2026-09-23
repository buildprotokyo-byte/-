"""64周目: **見積の行から「何が効いたか」を辿れるか。**

基準は `docs/d_line_attribution_criteria.md`(測る前にコミット済み)。

**合成の数量と、同梱の合成の見本規則だけを使う**(取り決め④)。

対照を 2 つ置く。

- **C1**: 足す前のコード(`git show <基準のコミット>:estimating/mapping.py`)で
  同じ案件を通し、**行の基づきの内訳が 1 件も変わらない**こと。
- **C2**: 欄をわざと空にしたら、数え方が **0 に戻る**こと。

使い方::

    .venv/bin/python benchmarks/measure_line_attribution.py
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from estimating.mapping import map_quantities  # noqa: E402
from estimating.quantities import QuantityItem  # noqa: E402
from estimating.rules import load_rules  # noqa: E402

#: 足す前のコードを取り出すコミット。**基準をコミットした回**である。
BEFORE_REV = "f018034"

EXAMPLE_RULES = ROOT / "estimating" / "examples" / "synthetic_rules.json"

#: 数えたい欄。**行 1 つを見ただけで「何が効いたか」が分かるための欄。**
FIELDS = ("rule_id", "method_id", "axis_id", "tier", "action")


def synthetic_quantities() -> list[QuantityItem]:
    """合成の数量。**実案件の数量ではない。**"""
    return [
        QuantityItem(
            target="開き戸::1階",
            value_range=(3.0, 3.0),
            unit="箇所",
            method_id="pdf_vector_door_arc",
            axis_id="image",
            tier=3,
            action="requires_review",
        ),
        QuantityItem(
            target="施工対象床面積::全体",
            value_range=(55.0, 55.0),
            unit="㎡",
            method_id="pdf_text_area",
            axis_id="text",
            tier=3,
            action="requires_review",
        ),
    ]


def ratio_with_fields(lines) -> dict[str, float]:  # noqa: ANN001
    """行のうち、欄が埋まっているものの割合。**辞書に畳んだ形で数える。**"""
    if not lines:
        return {name: 0.0 for name in FIELDS}
    out: dict[str, float] = {}
    for name in FIELDS:
        filled = 0
        for line in lines:
            payload = line.as_dict() if hasattr(line, "as_dict") else {}
            value = payload.get(name)
            if value not in (None, ""):
                filled += 1
        out[name] = round(filled / len(lines), 3)
    return out


def basis_counts(result) -> dict[str, int]:  # noqa: ANN001
    counts: dict[str, int] = {}
    for mapping in result.mappings:
        for line in mapping.lines:
            counts[line.basis] = counts.get(line.basis, 0) + 1
    return dict(sorted(counts.items()))


def load_before_module():  # noqa: ANN201
    """足す前の `estimating/mapping.py` をそのまま読み込む。"""
    source = subprocess.run(
        ["git", "show", f"{BEFORE_REV}:estimating/mapping.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mapping_before.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("mapping_before", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # dataclass の処理が sys.modules を引くので、先に登録しておく。
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


def main() -> None:
    ruleset = load_rules(EXAMPLE_RULES)
    quantities = synthetic_quantities()

    after = map_quantities(quantities, ruleset)
    after_lines = [line for m in after.mappings for line in m.lines]

    before_module = load_before_module()
    before = before_module.map_quantities(quantities, ruleset)
    before_lines = [line for m in before.mappings for line in m.lines]

    # C2: 欄をわざと空にする。
    blanked = [
        dataclasses.replace(line, **{name: (None if name in ("tier", "action") else "")
                                     for name in FIELDS})
        for line in after_lines
    ]

    payload: dict[str, object] = {
        "行の数": len(after_lines),
        "M_足したあとに辿れる割合": ratio_with_fields(after_lines),
        "足す前に辿れる割合": ratio_with_fields(before_lines),
        "C1_基づきの内訳_足す前": basis_counts(before),
        "C1_基づきの内訳_足したあと": basis_counts(after),
        "C2_欄を空にしたときの割合": ratio_with_fields(blanked),
    }
    payload["C1_通過"] = payload["C1_基づきの内訳_足す前"] == payload["C1_基づきの内訳_足したあと"]
    payload["C2_通過"] = all(v == 0.0 for v in payload["C2_欄を空にしたときの割合"].values())  # type: ignore[union-attr]
    payload["判定"] = (
        "入れる"
        if payload["C1_通過"]
        and payload["C2_通過"]
        and all(v == 1.0 for v in payload["M_足したあとに辿れる割合"].values())  # type: ignore[union-attr]
        else "基準の分岐に従って止める"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
