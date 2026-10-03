"""K-33 一式の前後(01 v1 → 01 v2)の読み取りを採点する。

**基準は `docs/k33_prompt_set_reading_criteria.md`(追記1 まで)に先に固定してある。**
このスクリプトはその基準をそのまま写したもので、答えを見てから直していない。

正解ファイルは実図面の凡例から取った名前なので、**共有フォルダにしかない。**
パスは引数で渡す。**名前そのものは出力しない。件数だけを出す。**

実行::

    python -m benchmarks.score_k33_prompt_set_reading \\
        --answers <共有フォルダ>/K-33_凡例升目_正解.json \\
        --runs <作業用フォルダ>/answers_P1.json ... 
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from pathlib import Path
from typing import Any

# 追記1 でそろえると決めた記号
_DROP = re.compile(r"[\s・,、()（）「」【】]")
# 追記1 で置いた、含む判定の下限
CONTAIN_MIN_RATIO = 0.60


def normalize(text: str) -> str:
    """追記1 の「そろえる」。NFKC → 記号を取る → 英字を小文字に。"""
    folded = unicodedata.normalize("NFKC", text)
    folded = _DROP.sub("", folded)
    return folded.lower()


def is_correct(given: str, truth: str) -> bool:
    """追記1 の「そろえたあとの判定」。一致、または 60% 以上を占める包含。"""
    a, b = normalize(given), normalize(truth)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        short, long = sorted((len(a), len(b)))
        return short / long >= CONTAIN_MIN_RATIO
    return False


#: 追記2 の「近い」のしきい。2-gram の Dice 係数がこれ以上なら近いとする。
NEAR_MIN_DICE = 0.5


def _bigrams(text: str) -> set[str]:
    """そろえたあとの文字列の 2 文字ずつの集合。1 文字のときはその 1 文字。"""
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def is_near(given: str, truth: str) -> bool:
    """追記2 の補助の判定。**結果を見てから足したので、線の判定には使わない。**

    送り仮名が 1 文字違うだけ、語順が入れ替わっているだけ、を当てるための
    当て方。完全一致は Dice = 1.0 なので、`is_correct` が真なら必ず真になる。
    **言い換え(別の言葉で同じものを指す)は文字が重ならないので当たらない。**
    """
    a, b = normalize(given), normalize(truth)
    if not a or not b:
        return False
    if a == b:
        return True
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return False
    return 2 * len(ga & gb) / (len(ga) + len(gb)) >= NEAR_MIN_DICE


def load_runs(paths: list[Path]) -> dict[str, dict[str, dict[str, Any]]]:
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        runs[path.stem] = {str(row["id"]): row for row in rows if row.get("id")}
    return runs


def score_run(
    rows: dict[str, dict[str, Any]], answers: list[dict[str, Any]]
) -> dict[str, Any]:
    """1 回ぶんを採点する。**名前は返さない。件数だけ。**"""
    tally: dict[str, Any] = {
        "答えた件数": 0,
        "欠けた件数": 0,
        "捏造": 0,
        "群": {},
        "読めなかった4区分": {1: 0, 2: 0, 3: 0, 4: 0, "振り分け無し": 0},
    }
    for entry in answers:
        cell_id = entry["id"]
        kind = entry["kind"]
        bucket = tally["群"].setdefault(
            kind, {"正しい": 0, "近い(追記2)": 0, "外れ": 0, "読めなかった": 0}
        )
        row = rows.get(cell_id)
        if row is None:
            tally["欠けた件数"] += 1
            continue
        name = row.get("name")
        name = name.strip() if isinstance(name, str) else None
        if not name:
            bucket["読めなかった"] += 1
            code = row.get("unreadable")
            if code in (1, 2, 3, 4):
                tally["読めなかった4区分"][code] += 1
            else:
                tally["読めなかった4区分"]["振り分け無し"] += 1
            continue
        tally["答えた件数"] += 1
        if kind.startswith("囮"):
            # 囮には正解が無い。名前が出ること自体が捏造(追記1)。
            tally["捏造"] += 1
            bucket["外れ"] += 1
            continue
        truth = entry.get("name")
        if truth and is_correct(name, truth):
            bucket["正しい"] += 1
            bucket["近い(追記2)"] += 1
        elif truth and is_near(name, truth):
            # **追記2 の補助の数字。**「正しい」は必ずここにも入る。
            bucket["近い(追記2)"] += 1
            bucket["外れ"] += 1
        else:
            bucket["外れ"] += 1
    return tally


def summarize(scored: dict[str, dict[str, Any]], prefix: str) -> dict[str, Any]:
    """同じ条件の複数回を、中央値と幅でまとめる。"""
    picked = {k: v for k, v in scored.items() if k.startswith(prefix)}
    if not picked:
        return {}

    def values(getter: Any) -> list[int]:
        return [getter(v) for v in picked.values()]

    def spread(nums: list[int]) -> dict[str, Any]:
        return {"中央値": statistics.median(nums), "最小": min(nums), "最大": max(nums), "全部": nums}

    groups = sorted({g for v in picked.values() for g in v["群"]})
    return {
        "回数": len(picked),
        "答えた件数": spread(values(lambda v: v["答えた件数"])),
        "捏造": spread(values(lambda v: v["捏造"])),
        "群ごとの正しい件数": {
            g: spread(values(lambda v, g=g: v["群"].get(g, {}).get("正しい", 0))) for g in groups
        },
        "群ごとの近い件数(追記2)": {
            g: spread(values(lambda v, g=g: v["群"].get(g, {}).get("近い(追記2)", 0)))
            for g in groups
        },
        "群ごとの読めなかった件数": {
            g: spread(values(lambda v, g=g: v["群"].get(g, {}).get("読めなかった", 0))) for g in groups
        },
        "読めなかった4区分": {
            str(code): spread(values(lambda v, code=code: v["読めなかった4区分"][code]))
            for code in (1, 2, 3, 4, "振り分け無し")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, type=Path, help="正解(共有フォルダ)")
    parser.add_argument("--runs", required=True, nargs="+", type=Path)
    parser.add_argument("--out", type=Path, help="件数だけの結果を書き出す先")
    args = parser.parse_args(argv)

    answers = json.loads(args.answers.read_text(encoding="utf-8"))["answers"]
    runs = load_runs(args.runs)
    scored = {name: score_run(rows, answers) for name, rows in runs.items()}

    result = {
        "母集団": {entry["kind"]: 0 for entry in answers},
        "条件": {"前(v1)": summarize(scored, "answers_P"), "後(v2)": summarize(scored, "answers_Q")},
        "1回ごと": scored,
    }
    for entry in answers:
        result["母集団"][entry["kind"]] += 1

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
