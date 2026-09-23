"""室の輪郭と繰り返す記号を、ページの宣言で止めない修正の前後比較(K-04 の 5)。

**合成のベクター PDF の上でのみ測る。** 基準は測る前に
`docs/page_kind_gate_criteria.md` に置いてある。結果を見てから基準を変えない。

使い方(同じ台本を、コードの置き場所だけ替えて 2 回回す)::

    python benchmarks/measure_page_kind_gate.py measure --code-root <直す前の木> --label before --out before.json
    python benchmarks/measure_page_kind_gate.py measure --code-root <直した後の木> --label after --out after.json
    python benchmarks/measure_page_kind_gate.py combine before.json after.json --out docs/page_kind_gate_result.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

PAGES = ("室の平面図", "記号の平面図", "表のページ", "文字だけのページ", "矢印とハッチングだけ")

#: 条件ごとの宣言。None は宣言なし。
CONDITIONS: dict[str, dict[str, str | None]] = {
    "a_正しい宣言": {
        "室の平面図": "平面図",
        "記号の平面図": "設備図",
        "表のページ": "仕上表",
        "文字だけのページ": "その他",
        "矢印とハッチングだけ": "その他",
    },
    "b_宣言なし": {page: None for page in PAGES},
    "c_誤った宣言": {
        "室の平面図": "仕上表",
        "記号の平面図": "仕上表",
        "表のページ": "平面図",
        "文字だけのページ": "平面図",
        "矢印とハッチングだけ": "平面図",
    },
}

SYMBOL_TRUTH = {"埋込コンセント": 12, "片切スイッチ": 9, "引掛シーリング": 5, "TEL引出口": 3}
RULES = "estimating/examples/synthetic_standing_rules.json"
REPEATS = 3


def _build_pages(tmp: Path) -> dict[str, Path]:
    import pymupdf

    from benchmarks.run_repeated_symbol_eval import make_plan
    from benchmarks.run_room_outline_eval import build_plan
    from tests.test_drawing_intake_schedules import _schedule_page

    out: dict[str, Path] = {}
    out["室の平面図"] = build_plan(tmp / "rooms.pdf", "clean")
    out["記号の平面図"] = make_plan(tmp / "symbols.pdf", SYMBOL_TRUTH, seed=0, clutter=True)

    doc = pymupdf.open()
    _schedule_page(doc)
    doc.save(tmp / "schedule.pdf")
    doc.close()
    out["表のページ"] = tmp / "schedule.pdf"

    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50", fontname="japan")
    for row, text in enumerate(
        (
            "特記仕様書",
            "1. 一般事項 本工事は設計図書に基づき施工する。",
            "2. 仮設工事 養生は既存部分を汚損しないよう行う。",
            "3. 内装工事 仕上げは内装仕上表による。",
            "4. 電気設備工事 器具の位置は設備図による。",
        )
    ):
        page.insert_text(pymupdf.Point(80, 100 + row * 30), text, fontname="japan")
    doc.save(tmp / "text.pdf")
    doc.close()
    out["文字だけのページ"] = tmp / "text.pdf"

    out["矢印とハッチングだけ"] = make_plan(tmp / "clutter.pdf", {}, seed=9, clutter=True)
    return out


def _right_rooms(findings) -> int:
    from benchmarks.run_room_outline_eval import ALL_ROOMS

    truth = {room.name: room.area_sqm for room in ALL_ROOMS}
    used: set[str] = set()
    right = 0
    for finding in findings:
        name = finding.provenance.get("room_name")
        area = finding.provenance.get("area_sqm_unrounded")
        if name in truth and name not in used and area is not None:
            if abs(area - truth[name]) / truth[name] <= 0.01:
                used.add(name)
                right += 1
    return right


def _right_symbol_groups(findings, page: str) -> int:
    if page != "記号の平面図":
        return 0
    remaining = Counter(SYMBOL_TRUTH.values())
    right = 0
    for finding in findings:
        count = int(finding.value_range[0])
        if remaining[count] > 0:
            remaining[count] -= 1
            right += 1
    return right


def measure(code_root: Path, label: str) -> dict:
    sys.path.insert(0, str(code_root))
    import intake.drawing_intake as di
    from estimating.from_intake import quantities_from_intake
    from estimating.pipeline import build_estimate_draft
    from estimating.rules import load_rules
    from intake.start_kit import PageDeclaration, StartKit

    assert Path(di.__file__).resolve().is_relative_to(code_root.resolve()), di.__file__

    calls = Counter()
    real_rooms, real_symbols = di.find_room_outlines, di.find_repeated_symbols

    def counting_rooms(*args, **kwargs):
        calls["rooms"] += 1
        return real_rooms(*args, **kwargs)

    def counting_symbols(*args, **kwargs):
        calls["symbols"] += 1
        return real_symbols(*args, **kwargs)

    di.find_room_outlines = counting_rooms
    di.find_repeated_symbols = counting_symbols
    ruleset = load_rules(code_root / RULES)

    rows: list[dict] = []
    timings: list[float] = []
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        pages = _build_pages(tmp)
        for repeat in range(REPEATS):
            elapsed = 0.0
            for condition, declared in CONDITIONS.items():
                for page in PAGES:
                    kind = declared[page]
                    start_kit = (
                        StartKit(page_declarations=(PageDeclaration(page_number=1, kind=kind),))
                        if kind is not None
                        else StartKit()
                    )
                    calls.clear()
                    started = time.perf_counter()
                    result = di.read_drawing(
                        di.IntakeConfig(
                            pdf_path=pages[page],
                            case_id=f"PAGE-KIND-GATE-{repeat}",
                            answers_path=tmp / f"answers_{repeat}_{condition}_{page}.json",
                            start_kit=start_kit,
                        )
                    )
                    elapsed += time.perf_counter() - started
                    if repeat:
                        continue
                    rooms = [f for f in result.findings if f.target.startswith("室::")]
                    symbols = [f for f in result.findings if f.target.startswith("記号::")]
                    room_or_symbol = {f.target for f in rooms + symbols}
                    decisions = result.decisions
                    quantities = [
                        q for q in quantities_from_intake(result) if q.target in room_or_symbol
                    ]
                    draft = build_estimate_draft(result, ruleset)
                    rows.append(
                        {
                            "条件": condition,
                            "ページ": page,
                            "宣言": kind,
                            "室の探索が走った": calls["rooms"] > 0,
                            "記号の探索が走った": calls["symbols"] > 0,
                            "室_出た": len(rooms),
                            "室_正しい": _right_rooms(rooms),
                            "記号の群_出た": len(symbols),
                            "記号の群_正しい": _right_symbol_groups(symbols, page),
                            "室_仮説扱い": sum(1 for f in rooms if f.derivation == "assumed"),
                            "記号の群_仮説扱い": sum(1 for f in symbols if f.derivation == "assumed"),
                            "ページ種類の食い違い": sum(
                                1
                                for p in result.pending_decisions
                                if p.kind == "page_kind_disagreement"
                            ),
                            "食い違いの観測": [
                                list(p.observed)
                                for p in result.pending_decisions
                                if p.kind == "page_kind_disagreement"
                            ],
                            "階層1_全対象": sum(1 for d in decisions if d.tier == 1),
                            "自動確定_全対象": sum(1 for d in decisions if d.confirmed),
                            "階層1_室と記号": sum(
                                1 for d in decisions if d.target in room_or_symbol and d.tier == 1
                            ),
                            "自動確定_室と記号": sum(
                                1 for d in decisions if d.target in room_or_symbol and d.confirmed
                            ),
                            "数量_室と記号_根拠別": dict(Counter(q.basis for q in quantities)),
                            "見積_確定した行": len(draft.settled_lines),
                            "見積_候補の行": len(draft.candidate_lines),
                        }
                    )
            timings.append(round(elapsed, 3))
    return {
        "label": label,
        "code_root": str(code_root),
        "行": rows,
        "時間_秒_各回": timings,
        "時間_秒_中央値": round(statistics.median(timings), 3),
    }


def combine(before_path: Path, after_path: Path) -> dict:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    compared = []
    keys = [k for k in before["行"][0] if k not in ("条件", "ページ", "宣言")]
    for b_row, a_row in zip(before["行"], after["行"]):
        assert (b_row["条件"], b_row["ページ"]) == (a_row["条件"], a_row["ページ"])
        compared.append(
            {
                "条件": b_row["条件"],
                "ページ": b_row["ページ"],
                "宣言": b_row["宣言"],
                "直す前": {k: b_row[k] for k in keys},
                "直した後": {k: a_row[k] for k in keys},
                "変わった項目": [k for k in keys if b_row[k] != a_row[k]],
            }
        )

    def total(side: dict, key: str, condition: str | None = None) -> int:
        return sum(
            row[key]
            for row in side["行"]
            if condition is None or row["条件"] == condition
        )

    summary = {}
    for condition in CONDITIONS:
        summary[condition] = {
            side_name: {
                "室の探索が走ったページ": total(side, "室の探索が走った", condition),
                "記号の探索が走ったページ": total(side, "記号の探索が走った", condition),
                "室_出た": total(side, "室_出た", condition),
                "室_正しい": total(side, "室_正しい", condition),
                "記号の群_出た": total(side, "記号の群_出た", condition),
                "記号の群_正しい": total(side, "記号の群_正しい", condition),
                "室と記号_仮説扱い": total(side, "室_仮説扱い", condition)
                + total(side, "記号の群_仮説扱い", condition),
                "ページ種類の食い違い": total(side, "ページ種類の食い違い", condition),
                "階層1_全対象": total(side, "階層1_全対象", condition),
                "自動確定_全対象": total(side, "自動確定_全対象", condition),
                "階層1_室と記号": total(side, "階層1_室と記号", condition),
                "見積_確定した行": total(side, "見積_確定した行", condition),
            }
            for side_name, side in (("直す前", before), ("直した後", after))
        }
    return {
        "基準": "docs/page_kind_gate_criteria.md",
        "直す前のコード": before["code_root"],
        "直した後のコード": after["code_root"],
        "条件ごとの合計(各条件 5 ページ)": summary,
        "時間_秒": {
            "直す前_各回": before["時間_秒_各回"],
            "直す前_中央値": before["時間_秒_中央値"],
            "直した後_各回": after["時間_秒_各回"],
            "直した後_中央値": after["時間_秒_中央値"],
            "範囲": "15 通り(5 ページ × 3 条件)の read_drawing の合計",
        },
        "ページごと": compared,
        "測れていないこと": [
            "実図面での変化(実図面はこの作業環境では開かない。Codex-A に回す)",
            "合成の負の対照 3 種類以外のページ(展開図・凡例・スキャン混じりの実ページ)で出る誤った候補",
            "人へ回る質問が増えたことの重さ",
            "記号の群の正しさは個数の一致だけで見ている(位置では照合していない)",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--code-root", type=Path, required=True)
    m.add_argument("--label", required=True)
    m.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("combine")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    c.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "measure":
        report = measure(args.code_root.resolve(), args.label)
    else:
        report = combine(args.before, args.after)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
