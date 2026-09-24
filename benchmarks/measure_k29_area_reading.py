"""K-29 の採点。**見積の数量を正解にして、条件ごとに室の数を数える。**

基準は `docs/k29_area_expert_reading_criteria.md`(**測る前にコミット済み**)。

この道具が守ること
------------------
**実案件の数字を画面に出さない。** 標準出力に出すのは**件数と割合だけ**である。
室名・見積の数量・金額・品目名は、``--detail`` を渡したときだけ、
**共有フォルダの中のファイル**に書き出す。**リポジトリには決して書かない。**

正解の取り方(**測る前に決めた規則**)
--------------------------------------
見積(正解ファイル)の行のうち、

1. 単位が ``㎡`` で、
2. 品目名に ``床`` が入っていて、
3. 品目名か手がかり語に**その室の名前**が入っているもの

を、その室の床面積の正解とする。

- **合う行が 1 つも無い室は「正解なし」**とし、分母から外さず別に数える
  (おーちゃんの指示)。
- **合う行が 2 つ以上あるとき**は、互いが ±5% の中に収まっていれば中央値を正解とし、
  ばらけていれば「**正解が 1 つに決まらない**」として、合った・外れたのどちらにも
  数えない。**どちらか都合のよいほうを選ばない。**

採点(**測る前に決めた規則**)
------------------------------
- **合った** … 読み手の面積が正解の ±5% の中(``CENTER_TOLERANCES`` の連続量。**緩めない**)
- **基準の違いで説明が付く外れ** … 外れたが、申告が ``芯々`` で正解より 5〜35% 大きい、
  または申告が ``内法`` で正解より 5〜35% 小さい。**合った件数には入れない。**
- **でたらめ** … 面積を答えたのに、**使った数字も理由も空**。
  ``formula``(式)は算数であって根拠ではないので、式だけでは根拠と認めない
- **囮に面積** … 図面に無い室名に面積が付いた

実行::

    .venv/bin/python -m benchmarks.measure_k29_area_reading \\
        --golden <採点用.json> --rooms <rooms.json> \\
        --answer 前提なし=<answers.json> --answer 専門家4人=<answers.json> \\
        [--detail /mnt/project-files/reports/K-29_室ごと.md]
"""

from __future__ import annotations

import argparse
import json
import statistics
import unicodedata
from pathlib import Path
from typing import Any

#: 連続量の許容差。`arbitration/axis_quality_firewall.py` の `CENTER_TOLERANCES` と同じ。
TOLERANCE = 0.05

#: 芯々と内法のずれの幅(`docs/a2_room_outline_report.md` の実測 10〜32%)に
#: 少し余裕を見た上限。**この幅の外は「基準の違い」で説明しない。**
BASIS_BAND = (0.05, 0.35)

#: 床面積の行を選ぶ語。**品目名そのものは見ていない。**
FLOOR_WORD = "床"

#: 面積の単位。
AREA_UNIT = "㎡"

NAMED, QUESTION, UNREADABLE = "面積を言えた", "質疑にあたる", "読めない"

COLUMNS = (
    "室数",
    "面積を言えた",
    "質疑にあたる",
    "読めない",
    "答えなかった",
    "合った",
    "基準の違いで説明が付く外れ",
    "外れた",
    "でたらめ",
    "囮に面積",
    "囮に質疑",
    "正解なし",
    "正解が1つに決まらない",
)


def normalize(text: str) -> str:
    """全角・半角と空白のゆれだけを均す。**語の中身は変えない。**"""
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def room_keys(name: str) -> tuple[str, ...]:
    """室名を、突き合わせに使う語に割る。

    仕上表の升目には ``キッチン\\nダイニング\\nリビング`` のように
    **改行で複数の室名が入っている**ことがある。行ごとに別の語として扱う。
    """
    parts = [normalize(part) for part in (name or "").splitlines()]
    return tuple(part for part in parts if part)


def golden_area_rows(golden: Path) -> list[dict[str, Any]]:
    """見積の行のうち、**単位が ㎡ で品目名に「床」が入るもの**だけ。"""
    payload = json.loads(Path(golden).read_text(encoding="utf-8"))
    rows = []
    for item in payload.get("expected_items", []):
        if str(item.get("unit") or "") != AREA_UNIT:
            continue
        if FLOOR_WORD not in str(item.get("work_item") or ""):
            continue
        if item.get("quantity") is None:
            continue
        rows.append(item)
    return rows


def _haystack(row: dict[str, Any]) -> str:
    terms = " ".join(str(t) for t in (row.get("trigger_terms") or []))
    return normalize(f"{row.get('work_item') or ''} {terms}")


def truth_for(rows: list[dict[str, Any]], name: str) -> tuple[float | None, str, list[str]]:
    """室 1 つぶんの正解。``(数量, 状態, 使った行の番号)`` を返す。"""
    keys = room_keys(name)
    hits = [r for r in rows if any(k in _haystack(r) for k in keys)]
    codes = [str(r.get("code") or "") for r in hits]
    if not hits:
        return None, "正解なし", codes
    values = [float(r["quantity"]) for r in hits]
    if len(values) == 1:
        return values[0], "あり", codes
    centre = statistics.median(values)
    if centre <= 0:
        return None, "正解が1つに決まらない", codes
    if all(abs(v - centre) / centre <= TOLERANCE for v in values):
        return centre, "あり", codes
    return None, "正解が1つに決まらない", codes


def _kind(answer: dict[str, Any] | None) -> str | None:
    if answer is None:
        return None
    kind = str(answer.get("kind") or "")
    if kind in (NAMED, QUESTION, UNREADABLE):
        return kind
    return NAMED if answer.get("area_sqm") is not None else UNREADABLE


def _groundless(answer: dict[str, Any]) -> bool:
    """**根拠が空の数字**。使った数字も理由も無ければ「でたらめ」に数える。

    ``formula``(式)は算数であって根拠ではない。**どの数字をどこから取ったか**が
    書かれていなければ、式が埋まっていても根拠にならない。
    """
    used = answer.get("used_numbers") or []
    why = str(answer.get("why") or "").strip()
    return not used and not why


def _basis_explains(answer: dict[str, Any], value: float, truth: float) -> bool:
    basis = str(answer.get("basis") or "")
    if truth <= 0:
        return False
    gap = (value - truth) / truth
    low, high = BASIS_BAND
    if basis == "芯々":
        return low < gap <= high
    if basis == "内法":
        return low < -gap <= high
    return False


def score(
    rooms: list[dict[str, Any]],
    answers: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {column: 0 for column in COLUMNS}
    detail: list[dict[str, Any]] = []
    for room in rooms:
        counts["室数"] += 1
        answer = answers.get(room["id"])
        kind = _kind(answer)
        line: dict[str, Any] = {"id": room["id"], "name": room["name"], "decoy": room["decoy"]}
        if kind is None:
            counts["答えなかった"] += 1
            detail.append({**line, "判定": "答えなかった"})
            continue
        counts[kind] += 1
        if room["decoy"]:
            if kind == NAMED:
                counts["囮に面積"] += 1
            elif kind == QUESTION:
                counts["囮に質疑"] += 1
            detail.append({**line, "判定": f"囮 / {kind}"})
            continue
        truth, state, codes = truth_for(rows, room["name"])
        if kind != NAMED:
            detail.append({**line, "判定": kind, "正解の状態": state})
            if state == "正解なし":
                counts["正解なし"] += 1
            elif state == "正解が1つに決まらない":
                counts["正解が1つに決まらない"] += 1
            continue
        assert answer is not None
        if _groundless(answer):
            counts["でたらめ"] += 1
        value = answer.get("area_sqm")
        if state == "正解なし":
            counts["正解なし"] += 1
            detail.append({**line, "判定": "正解なし", "答え": value})
            continue
        if state == "正解が1つに決まらない" or truth is None:
            counts["正解が1つに決まらない"] += 1
            detail.append({**line, "判定": "正解が1つに決まらない", "答え": value, "行": codes})
            continue
        if value is None:
            counts["外れた"] += 1
            detail.append({**line, "判定": "外れた(数字が無い)", "正解": truth, "行": codes})
            continue
        value = float(value)
        gap = abs(value - truth) / truth if truth else 1.0
        if gap <= TOLERANCE:
            counts["合った"] += 1
            verdict = "合った"
        elif _basis_explains(answer, value, truth):
            counts["基準の違いで説明が付く外れ"] += 1
            verdict = "基準の違いで説明が付く外れ"
        else:
            counts["外れた"] += 1
            verdict = "外れた"
        detail.append(
            {
                **line,
                "判定": verdict,
                "答え": value,
                "正解": truth,
                "ずれ": round((value - truth) / truth, 4) if truth else None,
                "基準": answer.get("basis"),
                "行": codes,
            }
        )
    return counts, detail


def _write_detail(path: Path, per_condition: dict[str, list[dict[str, Any]]]) -> None:
    """**室名と数量を書くので、共有フォルダの中だけ**に書き出す。"""
    resolved = path.resolve()
    if not str(resolved).startswith("/mnt/project-files/"):
        raise SystemExit("室ごとの表は /mnt/project-files/ の下にしか書けません")
    lines = ["# K-29 室ごとの答えと正解", "", "**この表はリポジトリに入れない。**", ""]
    for condition, detail in per_condition.items():
        lines += [f"## {condition}", "", "| id | 室名 | 判定 | 答え | 正解 | ずれ | 基準 |", "|---|---|---|---:|---:|---:|---|"]
        for row in detail:
            lines.append(
                "| {id} | {name} | {判定} | {answer} | {truth} | {gap} | {basis} |".format(
                    id=row["id"],
                    name=str(row["name"]).replace("\n", "／"),
                    判定=row["判定"],
                    answer=row.get("答え", ""),
                    truth=row.get("正解", ""),
                    gap=row.get("ずれ", ""),
                    basis=row.get("基準", ""),
                )
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--rooms", type=Path, required=True)
    parser.add_argument("--answer", action="append", default=[], metavar="名前=パス")
    parser.add_argument("--detail", type=Path, default=None)
    args = parser.parse_args(argv)

    rooms = json.loads(args.rooms.read_text(encoding="utf-8"))
    rows = golden_area_rows(args.golden)
    print(f"見積の床面積の行: {len(rows)} 件")

    per_condition: dict[str, list[dict[str, Any]]] = {}
    table: dict[str, dict[str, int]] = {}
    for spec in args.answer:
        name, _, path = spec.partition("=")
        answers = json.loads(Path(path).read_text(encoding="utf-8"))
        counts, detail = score(rooms, answers, rows)
        table[name] = counts
        per_condition[name] = detail

    print("\n| 条件 | " + " | ".join(COLUMNS) + " |")
    print("|---" * (len(COLUMNS) + 1) + "|")
    for name, counts in table.items():
        print(f"| {name} | " + " | ".join(str(counts[c]) for c in COLUMNS) + " |")

    if args.detail is not None:
        _write_detail(args.detail, per_condition)
        print(f"\n室ごとの表: {args.detail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
