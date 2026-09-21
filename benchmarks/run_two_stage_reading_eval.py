"""トライアル15: 「概要を先に確定してから詳細を読む」2段階読みの効果検証。

3つの読み方を同じ資料・同じ設問で比べる。

  A   1段階読み        : 全ページを並び順どおりに1回で読み、数量を答える
  A2  1段階読み+明示化  : 全ページを1回で読むが、先に3要素を書き出してから答える
                          (「2回に分ける」効果と「3要素を明示する」効果を切り分ける対照)
  B   2段階読み        : 段階1で概要ページだけを読み3要素を確定 ->
                          段階2はその確定事項と詳細ページだけで数量を答える

読み取りは外部の読み手(言語モデル)が行う。このスクリプトは
  * 読み手に渡すプロンプトを書き出す (``prompts``)
  * 読み手が返した回答を凍結済み正解値と突き合わせる (``score``)
の2つだけを行い、正解値は ``prompts`` の経路には一切現れない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.two_stage_reading_fixtures import (
    ALL_SETS,
    CaseSet,
    format_questions,
    ground_truth,
    question_traps,
)
from benchmarks.two_stage_reading_padding import padded_detail_pages, padded_pages
from benchmarks.two_stage_reading_hard import hard_detail_pages, hard_pages
from benchmarks.two_stage_reading_prose import (
    prose_detail_pages,
    prose_overview_pages,
    prose_pages,
)

KEY_PATH = Path(__file__).with_name("two_stage_reading_key.json")

ARMS = ("A", "A2", "B")

_JSON_TAIL = """
回答は次の形の JSON オブジェクトだけを出力してください。前後に説明文を書かないでください。
{json_shape}
値は単位を付けない数値にしてください。資料から確定できない設問は null にしてください。
"""


def _pages(case: CaseSet, level: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(全ページ, 方式Bの段階2に渡すページ) をレベルごとに返す。

    レベル1は概要1〜2 + 詳細3〜5ページの短い資料。
    レベル2は同じ内容を詰め物ページで24ページまで薄めた資料。
    レベル3はレベル2の概要ページを、同じ中身のまま散文に書き換えたもの。
    レベル4は、おとりから但し書きを外し、食い違う旧版の概要書を足して
    40ページにしたもの(10章23項。`two_stage_reading_hard.py`)。
    概要ページの位置はどのレベルでも先頭である。
    """
    if level == 1:
        return case.overview_pages + case.detail_pages, case.detail_pages
    if level == 2:
        return padded_pages(case), padded_detail_pages(case)
    if level == 3:
        return prose_pages(case), prose_detail_pages(case)
    if level == 4:
        return hard_pages(case), hard_detail_pages(case)
    raise ValueError(f"unknown level: {level}")


def _answer_shape(case: CaseSet) -> str:
    inner = ", ".join(f'"{q.qid}": <数値>' for q in case.questions)
    return "{" + inner + "}"


def prompt_arm_a(case: CaseSet, level: int = 1) -> str:
    all_pages, _ = _pages(case, level)
    pages = "\n".join(all_pages)
    return f"""あなたは建築改修工事の積算担当です。
次の資料一式(全{len(all_pages)}ページ)を、並び順どおりに読んでください。

===== 資料ここから =====
{pages}
===== 資料ここまで =====

設問:
{format_questions(case)}
{_JSON_TAIL.format(json_shape=_answer_shape(case))}"""


def prompt_arm_a2(case: CaseSet, level: int = 1) -> str:
    all_pages, _ = _pages(case, level)
    pages = "\n".join(all_pages)
    shape = (
        '{"基準寸法": "<文章>", "工事対象範囲": "<文章>", "現況": "<文章>", '
        + ", ".join(f'"{q.qid}": <数値>' for q in case.questions)
        + "}"
    )
    return f"""あなたは建築改修工事の積算担当です。
次の資料一式(全{len(all_pages)}ページ)を、並び順どおりに読んでください。

===== 資料ここから =====
{pages}
===== 資料ここまで =====

まず「基準寸法」「工事対象範囲」「現況」の3点を資料から書き出し、
そのうえで、その3点を前提として設問に答えてください。

設問:
{format_questions(case)}
{_JSON_TAIL.format(json_shape=shape)}"""


def prompt_arm_b_stage1(case: CaseSet, level: int = 1) -> str:
    overview = prose_overview_pages(case) if level == 3 else case.overview_pages
    pages = "\n".join(overview)
    return f"""次に示すのは、ある改修工事の資料一式のうち「概要」にあたるページだけです。
詳細ページはあなたには渡されていません。

===== 概要ページここから =====
{pages}
===== 概要ページここまで =====

このあと別の担当者が、詳細ページだけを読んで数量を算出します。
その担当者に引き継ぐため、次の3つの根本要素を確定してください。

1. 基準寸法 … 基準点・基準線・基準となる寸法や縮尺
2. 工事対象範囲 … どの場所・範囲・箇所が工事の対象か
3. 現況 … 既存が何で、どういう状態で、どう扱うか

詳細ページを読む担当者は概要ページを見られません。
あとから必要になる数値や条件は、要素の文章の中に具体的に書き切ってください。

回答は次の形の JSON オブジェクトだけを出力してください。前後に説明文を書かないでください。
{{"基準寸法": "<文章>", "工事対象範囲": "<文章>", "現況": "<文章>", "確定できなかった要素": ["<要素名>", ...]}}
概要ページから確定できない要素は値を null にし、その要素名を「確定できなかった要素」に入れてください。
確定できなかった要素が無い場合は空のリストにしてください。
"""


def prompt_arm_b_stage2(case: CaseSet, fixed: dict[str, Any], level: int = 1) -> str:
    _, stage2_pages = _pages(case, level)
    pages = "\n".join(stage2_pages)

    def _v(key: str) -> str:
        value = fixed.get(key)
        return "(確定できなかった)" if value in (None, "") else str(value)

    return f"""あなたは建築改修工事の積算担当です。
概要資料は別の担当者がすでに読み、次の3点を確定済みとして引き渡しています。
あなたに概要ページは渡されていません。下の確定事項を前提として作業してください。

===== 確定事項ここから =====
基準寸法: {_v("基準寸法")}
工事対象範囲: {_v("工事対象範囲")}
現況: {_v("現況")}
===== 確定事項ここまで =====

===== 詳細ページここから =====
{pages}
===== 詳細ページここまで =====

設問:
{format_questions(case)}
{_JSON_TAIL.format(json_shape=_answer_shape(case))}"""


# --------------------------------------------------------------------------
# 採点
# --------------------------------------------------------------------------
EXACT_UNITS = {"台", "箇所", "枚"}


def is_correct(qid: str, given: Any, expected: float, unit: str) -> bool:
    if given is None:
        return False
    try:
        value = float(given)
    except (TypeError, ValueError):
        return False
    if unit in EXACT_UNITS:
        return abs(value - expected) < 1e-9
    tolerance = max(abs(expected) * 0.01, 0.05)
    return abs(value - expected) <= tolerance


def relative_error(given: Any, expected: float) -> float | None:
    """正解値に対する相対誤差。正解が 0 の場合は絶対誤差を返す。"""
    if given is None:
        return None
    try:
        value = float(given)
    except (TypeError, ValueError):
        return None
    if expected == 0.0:
        return abs(value)
    return abs(value - expected) / abs(expected)


def load_key() -> dict[str, float]:
    if KEY_PATH.exists():
        return json.loads(KEY_PATH.read_text(encoding="utf-8"))
    return ground_truth()


def score(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """読み手の回答一覧を採点する。

    各 run は {"arm": "A", "level": 1, "set_id": "S1", "rep": 1, "answers": {...}} の形。
    """
    key = load_key()
    units = {q.qid: q.unit for case in ALL_SETS for q in case.questions}
    traps = question_traps()

    items: list[dict[str, Any]] = []
    for run in runs:
        for qid, expected in key.items():
            if not qid.startswith(run["set_id"]):
                continue
            given = run["answers"].get(qid)
            items.append(
                {
                    "arm": run["arm"],
                    "level": run.get("level", 1),
                    "set_id": run["set_id"],
                    "rep": run["rep"],
                    "qid": qid,
                    "trap": traps[qid],
                    "given": given,
                    "expected": expected,
                    "correct": is_correct(qid, given, expected, units[qid]),
                    "rel_error": relative_error(given, expected),
                    "abstained": given is None,
                }
            )

    summary: dict[str, Any] = {"by_arm": {}, "by_arm_trap": {}, "by_arm_set": {}}
    for level in sorted({i["level"] for i in items}):
        for arm in ARMS:
            arm_items = [
                i for i in items if i["arm"] == arm and i["level"] == level
            ]
            if not arm_items:
                continue
            tag = f"L{level}-{arm}"
            summary["by_arm"][tag] = _aggregate(arm_items)
            for trap in sorted({i["trap"] for i in arm_items}):
                summary["by_arm_trap"].setdefault(tag, {})[trap] = _aggregate(
                    [i for i in arm_items if i["trap"] == trap]
                )
            for set_id in sorted({i["set_id"] for i in arm_items}):
                summary["by_arm_set"].setdefault(tag, {})[set_id] = _aggregate(
                    [i for i in arm_items if i["set_id"] == set_id]
                )
    return {"items": items, "summary": summary}


def _aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    correct = sum(1 for i in items if i["correct"])
    abstained = sum(1 for i in items if i["abstained"])
    errors = [i["rel_error"] for i in items if i["rel_error"] is not None]
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else None,
        "abstained": abstained,
        "median_rel_error": round(sorted(errors)[len(errors) // 2], 4) if errors else None,
    }


# --------------------------------------------------------------------------
def cmd_prompts(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    level = args.level
    written = 0
    for case in ALL_SETS:
        stem = f"{case.set_id}_L{level}"
        (out / f"{stem}_A.txt").write_text(prompt_arm_a(case, level), encoding="utf-8")
        (out / f"{stem}_A2.txt").write_text(prompt_arm_a2(case, level), encoding="utf-8")
        (out / f"{stem}_B1.txt").write_text(
            prompt_arm_b_stage1(case, level), encoding="utf-8"
        )
        written += 3
    print(f"wrote {written} prompt files (level {level}) to {out}")


def cmd_stage2(args: argparse.Namespace) -> None:
    """段階1の出力を読んで段階2のプロンプトを書き出す。"""
    from benchmarks.two_stage_reading_fixtures import get_set

    fixed = json.loads(Path(args.fixed).read_text(encoding="utf-8"))
    case = get_set(args.set_id)
    Path(args.out).write_text(
        prompt_arm_b_stage2(case, fixed, args.level), encoding="utf-8"
    )
    print(args.out)


def cmd_freeze(args: argparse.Namespace) -> None:
    KEY_PATH.write_text(
        json.dumps(ground_truth(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"froze {len(ground_truth())} answers to {KEY_PATH}")


def cmd_score(args: argparse.Namespace) -> None:
    runs = json.loads(Path(args.runs).read_text(encoding="utf-8"))
    result = score(runs)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prompts")
    p.add_argument("--out", required=True)
    p.add_argument("--level", type=int, default=1, choices=(1, 2, 3, 4))
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("stage2")
    p.add_argument("--set-id", required=True)
    p.add_argument("--fixed", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--level", type=int, default=1, choices=(1, 2, 3, 4))
    p.set_defaults(func=cmd_stage2)

    p = sub.add_parser("freeze")
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser("score")
    p.add_argument("--runs", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
