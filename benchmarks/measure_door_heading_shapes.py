"""68周目: **見出しの当て方の形を、合成の升目で測る。**

基準は `docs/d_door_heading_shapes_criteria.md`(測る前にコミット済み)。

**図面は開かない。** 測るのは当て方そのもので、使う書き方は
**過去の報告書で名前が挙がったものだけ**である(この周で語を作らない)。

**実図面での測定は PC 側(Codex-A)。**
`benchmarks/measure_door_heading_matching.py --pdf <匿名化v2.pdf>` で、
39周目が先に引いた 3 つの線(当たり 1 個以上・誤爆÷当たり < 0.5・取り違え 0)が出る。

使い方::

    .venv/bin/python benchmarks/measure_door_heading_shapes.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from axes.image_axis.schedule_tables import DOOR_COLUMN_SYNONYMS  # noqa: E402
from benchmarks.measure_door_heading_matching import (  # noqa: E402
    roles_exact,
    roles_loose,
    roles_split,
    roles_suffix,
)

#: **当ててほしい書き方。** 出どころは `docs/b_door_schedule_zero_report.md`
#: (実測で名前が挙がった `巾 W`)と、いまの別名表にある形。
#: 右は、その升目が指しているはずの役割。
WANTED: tuple[tuple[str, str], ...] = (
    ("巾 W", "幅"),
    ("幅(W)", "幅"),
    ("有効幅", "幅"),
    ("開口幅", "幅"),
    ("内法高さ", "高さ"),
    ("建具No.", "建具番号"),
)

#: **当ててほしくない書き方。** 39周目の報告書が名指しした紛らわしい語。
UNWANTED: tuple[str, ...] = ("幅木", "巾木", "係数", "全幅", "床仕上")

STRATEGIES = (
    ("いまのまま(完全一致)", roles_exact),
    ("案1(含み一致)", roles_loose),
    ("案2a(後方一致)", roles_suffix),
    ("案2b(区切って完全一致)", roles_split),
)


def measure() -> dict:
    out: dict[str, dict] = {}
    for name, roles_of in STRATEGIES:
        hit: list[str] = []
        wrong_role: list[str] = []
        for text, expected in WANTED:
            roles = roles_of(text, DOOR_COLUMN_SYNONYMS)
            if expected in roles:
                hit.append(text)
            elif roles:
                wrong_role.append(text)
        false_hits = {
            text: sorted(roles_of(text, DOOR_COLUMN_SYNONYMS))
            for text in UNWANTED
            if roles_of(text, DOOR_COLUMN_SYNONYMS)
        }
        two_roles = [
            text
            for text, _ in WANTED
            if len(roles_of(text, DOOR_COLUMN_SYNONYMS)) >= 2
        ]
        out[name] = {
            "M1_当たった数": len(hit),
            "M1_当たった書き方": hit,
            "M1_別の役割に当たった書き方": wrong_role,
            "M2_誤って当たった数": len(false_hits),
            "M2_中身": false_hits,
            "1つの升目が2つ以上の役割に当たった書き方": two_roles,
        }
    return out


def main() -> int:
    results = measure()

    # C1: いまのままでは `巾 W` が当たらないこと(落ちどころの見立ての確認)
    c1 = "巾 W" not in results["いまのまま(完全一致)"]["M1_当たった書き方"]
    # C3: 実装に触っていないこと
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--", "axes/image_axis/schedule_tables.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    c3 = diff.stdout.strip() == ""

    payload: dict[str, object] = {
        "round": 68,
        "criteria_file": "docs/d_door_heading_shapes_criteria.md",
        "benchmark": "benchmarks/measure_door_heading_shapes.py",
        "synthetic_only": True,
        "当ててほしい書き方": [t for t, _ in WANTED],
        "当ててほしくない書き方": list(UNWANTED),
        "結果": results,
        "C1_いまのままでは巾Wが当たらない": c1,
        "C3_実装に触っていない": c3,
    }

    base = results["いまのまま(完全一致)"]["M1_当たった数"]
    verdicts = {}
    for name in ("案2a(後方一致)", "案2b(区切って完全一致)"):
        row = results[name]
        verdicts[name] = {
            "当たりが増えた": row["M1_当たった数"] > base,
            "誤爆なし": row["M2_誤って当たった数"] == 0,
            "PC側へ渡す候補": (
                c1
                and c3
                and row["M1_当たった数"] > base
                and row["M2_誤って当たった数"] == 0
            ),
        }
    payload["採否"] = verdicts
    payload["注"] = (
        "合成で増えたことは、実図面で増える理由にならない。"
        "3 つの線(当たり・誤爆÷当たり・取り違え)で決めるのは実図面での測定で、それは PC 側が行う。"
    )

    out = ROOT / "docs" / "d_door_heading_shapes_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
