"""K-70 作業2: 工事チェック表の枠(16 → 大枠 18・小枠 5)で、K-67 の測りをやり直す。

基準は `docs/k70_checklist_frames_criteria.md`(測る前にコミット)。線:

1. 3 回(full_R1〜R3)とも、大枠 18・小枠 5 が全部出る
2. 5 版とも、語が見つかるのに「記載が見当たらない」とした枠(大枠・小枠)が 0
3. 欠けた資料の版(hidden_R1・hidden2_R1)でも、大枠 18・小枠 5 が全部出る

報告だけ: 状態が変わった枠の一覧(前の 16 枠 → 新しい枠の対応で)、「迷い」の件数。

**新しく AI を呼ばない**(K-61 が保存した P011 匿名化 v4 の読み)。**出すのは件数と枠の名前だけ**
(項目の名前・室名・金額は出さない)。正解ファイルは開かない。

実行::

    python -m benchmarks.measure_k70_checklist_frames --out /tmp/k70_checklist.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from draft import work_checklist as wc

RUNS = Path("/mnt/project-files/reports/K-61/結果/P011")
V4 = Path("/mnt/attach/project-files/anonymized/P011_匿名化v4.pdf")
HIDDEN = Path("/tmp/claude-0/k61")

#: 版 → (PDF、足りない資料)。K-67 の測り(`reports/K-67/測定の全部_P011v4.json`)と同じ。
VERSIONS: dict[str, tuple[Path, list[str]]] = {
    "full_R1": (V4, ["原価表なし"]),
    "full_R2": (V4, ["原価表なし"]),
    "full_R3": (V4, ["原価表なし"]),
    "hidden_R1": (HIDDEN / "P011_v4_仕上表を隠した版.pdf", ["仕上表の一部なし(3・4ページを隠した)", "原価表なし"]),
    "hidden2_R1": (HIDDEN / "P011_v4_仕上表と32頁を隠した版.pdf", ["仕上表の原本なし(3・4・32ページを隠した)", "原価表なし"]),
}

OLD_FRAMES = Path(wc.__file__).with_name("vocab") / "工事の枠_K67_16枠.json"

#: 前の 16 枠 → 新しい枠(大枠、または「大枠/小枠」)。基準の表と同じ。
OLD_TO_NEW: dict[str, list[str]] = {
    "仮設": ["仮設"], "解体・撤去": ["解体・撤去"], "木工・大工": ["木工・大工"],
    "建具": ["建具", "建具/外部建具", "建具/内部建具"],
    "内装仕上": ["内装仕上", "左官", "タイル"],
    "塗装": ["塗装"], "防水": ["防水"], "断熱": ["断熱"],
    "電気": ["電気", "電気/照明", "電気/電気配線"],
    "給排水衛生": ["給排水衛生", "住宅設備"],
    "空調換気": ["電気/空調・換気"],
    "ガス": ["ガス"], "防災": ["防災"], "クリーニング": ["クリーニング"], "諸経費": ["諸経費"], "その他": ["その他"],
}

EXPECTED_FRAMES = 18
EXPECTED_SUBS = 5


def _flat_states(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in result["枠"]:
        out[row["枠"]] = {"状態": row["状態"], "件数": row["件数"]}
        for sub in row.get("小枠") or ():
            out[f"{row['枠']}/{sub['枠']}"] = {"状態": sub["状態"], "件数": sub["件数"]}
    return out


def measure_version(name: str, pdf: Path, missing: list[str]) -> dict[str, Any]:
    draft = json.loads((RUNS / name / "下書き.json").read_text(encoding="utf-8"))
    reading = draft["読む"].get("読み") or {}
    pages = sorted(int(n) for n, v in reading.items() if "注" not in v)
    items = draft["理解"]["項目"]
    old = wc.build(items, pdf=pdf, pages=pages, missing_sources=missing, frames_path=OLD_FRAMES)
    new = wc.build(items, pdf=pdf, pages=pages, missing_sources=missing)
    old_states, new_states = _flat_states(old), _flat_states(new)
    changed = []
    for old_name, targets in OLD_TO_NEW.items():
        before = old_states[old_name]["状態"]
        for target in targets:
            after = new_states[target]["状態"]
            if after != before:
                changed.append({"前の枠": old_name, "前の状態": before, "新しい枠": target, "新しい状態": after,
                                "新しい枠の件数": new_states[target]["件数"]})
    return {
        "項目数": len(items),
        "前": {"枠の数": old["枠の数"], "状態の分布": old["状態の分布"],
               "ないと言った枠": old["語が見つかるのに記載が見当たらないとした枠"]},
        "後": {"枠の数": new["枠の数"], "小枠の数": new["小枠の数"], "状態の分布": new["状態の分布"],
               "小枠の状態の分布": new["小枠の状態の分布"],
               "ないと言った枠": new["語が見つかるのに記載が見当たらないとした枠"],
               "迷いの件数": new["迷い"]["件数"],
               "枠ごとの件数": {k: v["件数"] for k, v in new_states.items()},
               "大枠に直接置いた件数": {r["枠"]: r["大枠に直接置いた件数"] for r in new["枠"] if "小枠" in r}},
        "前の枠ごとの件数": {k: v["件数"] for k, v in old_states.items()},
        "状態が変わった枠": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-70 工事チェック表の枠を測る")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result: dict[str, Any] = {"版": {}}
    for name, (pdf, missing) in VERSIONS.items():
        result["版"][name] = measure_version(name, pdf, missing)

    versions = result["版"]
    full = [n for n in versions if n.startswith("full")]
    hidden = [n for n in versions if n.startswith("hidden")]
    complete = lambda n: (versions[n]["後"]["枠の数"] == EXPECTED_FRAMES and versions[n]["後"]["小枠の数"] == EXPECTED_SUBS)
    result["線1 3 回とも全部の枠が出る"] = all(complete(n) for n in full)
    result["線2 「ない」と言った枠が 5 版とも 0"] = all(not versions[n]["後"]["ないと言った枠"] for n in versions)
    result["線3 欠けた資料の版でも全部の枠が出る"] = all(complete(n) for n in hidden)
    result["迷いの件数"] = {n: versions[n]["後"]["迷いの件数"] for n in versions}
    print(json.dumps({k: v for k, v in result.items() if k != "版"}, ensure_ascii=False, indent=1))
    for n, v in versions.items():
        print(n, "前", v["前"]["状態の分布"], "後", v["後"]["状態の分布"], "小枠", v["後"]["小枠の状態の分布"],
              "変わった", len(v["状態が変わった枠"]))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"書いた: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
