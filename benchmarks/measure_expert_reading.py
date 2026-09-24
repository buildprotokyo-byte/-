"""K-21 の採点。**凡例が刷っている名前を正解にして、条件ごとに数える。**

この道具がしないこと
--------------------
**機械が「違う」と決めない。**完全一致しなかった答えは ``要目視`` に積むだけで、
**合っているか違うかは人が目で見て決める。**「コンセント」と「二口コンセント」は
器具が同じで語が違う。機械に判定させると、この差が全部「違う」になる。

数える区分(基準 `docs/k21_expert_reading_criteria.md` のとおり)
--------------------------------------------------------------

- ``件数`` … その群の枚数
- ``答えた`` … 「不明」と言わなかった件数
- ``不明`` / ``答えなかった`` … 「不明」と答えた / 番号ごと返ってこなかった
- ``完全一致`` … 正規化して完全に一致した
- ``要目視`` … 答えたが完全一致しなかった。**目で見るまで判定しない**
- ``目で見て合っている`` / ``目で見て違う`` / ``目で見てどちらとも言えない``
- ``上位語`` … 合っているが、凡例の名前より情報が落ちている(別に数える)
- ``囮に名前`` … 凡例に無いもの(囮)に名前が付いた

実行::

    python -m benchmarks.measure_expert_reading --answers <正解.json> \\
        --condition 前提なし=<答え.json> --condition 専門家=<答え.json> \\
        [--legend-only <対照表.json>] [--eye <目で見た判定.json>]
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from axes.image_axis.legend_lookup import LegendTable, match_marks

#: 「分からない」の書き方。**答えなかったことと区別する。**
UNKNOWN = "不明"

#: 答えの 3 つの区分(基準の追記 4)。**質疑を「読めない」に混ぜない。**
NAMED, QUESTION, UNREADABLE = "名前を言えた", "質疑にあたる", "読めない"
ANSWER_KINDS = (NAMED, QUESTION, UNREADABLE)

#: 「一般的な決まりか、この図面の癖か」の札。**読み手の自己申告**である。
RULE_TAGS = ("一般的な決まり", "この図面の癖", "判断できない")

#: 目で見た判定に使ってよい語。**これ以外は撥ねる。**
#: ``合っている(上位語)`` は合っている側に数えるが、**別にも数える**。
EYE_VERDICTS = ("合っている", "合っている(上位語)", "違う", "どちらとも言えない")

#: 数える順。報告の表の列の順でもある。
COLUMNS = (
    "件数",
    "名前を言えた",
    "質疑にあたる",
    "読めない",
    "答えなかった",
    "完全一致",
    "要目視",
    "目で見て合っている",
    "上位語",
    "目で見て違う",
    "目で見てどちらとも言えない",
    "囮に名前",
    "囮に質疑",
)


def normalize(text: str) -> str:
    """全角・半角、空白、中黒のゆれだけを均す。**語の中身は変えない。**"""
    body = unicodedata.normalize("NFKC", text or "")
    return "".join(body.split()).replace("・", "")


def _answer(response: dict[str, Any] | None) -> tuple[str | None, str]:
    """答えを ``(区分, 名前)`` で返す。``区分`` が ``None`` なら答えが返ってこなかった。

    ``answer`` の欄が無い答えは、**名前が「不明」かどうかで区分を決める。**
    古い形の答えを黙って落とさないため。
    """
    if response is None:
        return None, ""
    name = str(response.get("name", "")).strip()
    kind = str(response.get("answer", "")).strip()
    if kind not in ANSWER_KINDS:
        kind = UNREADABLE if (not name or name == UNKNOWN) else NAMED
    if kind == NAMED and (not name or name == UNKNOWN):
        kind = UNREADABLE
    return kind, name


def eye_verdicts_needed(
    key: list[dict[str, Any]], responses: dict[str, Any]
) -> list[str]:
    """**目で見ないと判定できない番号**を、正解表の順で返す。"""
    needed: list[str] = []
    for row in key:
        kind, given = _answer(responses.get(row["id"]))
        if kind != NAMED:
            continue
        expected = row.get("name")
        if expected is None:
            continue
        if normalize(given) != normalize(expected):
            needed.append(row["id"])
    return needed


def tally(
    key: list[dict[str, Any]],
    responses: dict[str, Any],
    eye: dict[str, str] | None = None,
) -> dict[str, Counter]:
    """群ごとに数える。``eye`` は目で見た判定(番号 → 判定の語)。"""
    eye = eye or {}
    for identifier, verdict in eye.items():
        if verdict not in EYE_VERDICTS:
            raise ValueError(
                f"{identifier} の判定 {verdict!r} は使えません。"
                f"使えるのは {EYE_VERDICTS} だけです"
            )
    counts: dict[str, Counter] = {}
    for row in key:
        bucket = counts.setdefault(row["kind"], Counter())
        bucket["件数"] += 1
        kind, given = _answer(responses.get(row["id"]))
        if kind is None:
            bucket["答えなかった"] += 1
            continue
        if kind == UNREADABLE:
            bucket["読めない"] += 1
            continue
        expected = row.get("name")
        if kind == QUESTION:
            bucket["質疑にあたる"] += 1
            if expected is None:
                bucket["囮に質疑"] += 1
            continue
        bucket["名前を言えた"] += 1
        if expected is None:
            bucket["囮に名前"] += 1
            continue
        if normalize(given) == normalize(expected):
            bucket["完全一致"] += 1
            continue
        verdict = eye.get(row["id"])
        if verdict is None:
            bucket["要目視"] += 1
        elif verdict.startswith("合っている"):
            bucket["目で見て合っている"] += 1
            if verdict == "合っている(上位語)":
                bucket["上位語"] += 1
        elif verdict == "違う":
            bucket["目で見て違う"] += 1
        else:
            bucket["目で見てどちらとも言えない"] += 1
    return counts


def rule_split(
    key: list[dict[str, Any]], responses: dict[str, Any]
) -> dict[str, Counter]:
    """「一般的な決まり / この図面の癖」の札を群ごとに数える。

    **札は読み手の自己申告**であって、正解と突き合わせる手立ては無い。
    """
    counts: dict[str, Counter] = {}
    for row in key:
        response = responses.get(row["id"])
        if response is None:
            continue
        tag = str(response.get("rule", "")).strip()
        counts.setdefault(row["kind"], Counter())[
            tag if tag in RULE_TAGS else "札なし"
        ] += 1
    return counts


def legend_only(key: list[dict[str, Any]], table_path: Path) -> dict[str, Any]:
    """**条件 1「凡例だけ」**を機械で出す。升目に刷られた文字だけを引き当てる。

    **絵は見ない。**これがいまの本番の読み方である。
    """
    table = LegendTable.load(table_path)
    responses: dict[str, Any] = {}
    for row in key:
        code = str(row.get("code") or "").strip()
        if not code:
            responses[row["id"]] = {
                "answer": UNREADABLE,
                "name": UNKNOWN,
                "confidence": UNKNOWN,
            }
            continue
        match = match_marks([code], table)[0]
        value = match.meaning or match.name
        responses[row["id"]] = {
            "answer": NAMED if match.matched else UNREADABLE,
            "name": value if match.matched else UNKNOWN,
            "confidence": "確信" if match.matched else UNKNOWN,
        }
    return responses


def rows_table(
    key: list[dict[str, Any]],
    conditions: list[tuple[str, dict[str, Any]]],
    kind: str,
    eye: dict[str, dict[str, str]] | None = None,
) -> str:
    """ある群を **1 件ずつの表**にする。**共有フォルダに置くこと**(図面の中身なので)。"""
    eye = eye or {}
    header = ["番号", "凡例の名前", *[name for name, _ in conditions]]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in key:
        if row["kind"] != kind:
            continue
        cells = [row["id"], str(row.get("name") or "(凡例に無い)")]
        for name, responses in conditions:
            answer, given = _answer(responses.get(row["id"]))
            verdict = eye.get(name, {}).get(row["id"])
            if answer is None:
                cells.append("答えなかった")
            elif answer == QUESTION:
                reason = str((responses.get(row["id"]) or {}).get("reason", "")).strip()
                cells.append(f"質疑: {reason}" if reason else "質疑")
            elif answer == UNREADABLE:
                cells.append("読めない")
            else:
                mark = "◯" if normalize(given) == normalize(str(row.get("name") or "")) else (
                    {"合っている": "◯", "合っている(上位語)": "△上位語", "違う": "×",
                     "どちらとも言えない": "?"}.get(verdict or "", "(未判定)")
                )
                cells.append(f"{given} {mark}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _print(title: str, counts: dict[str, Counter]) -> None:
    print(f"\n## {title}")
    header = ["群", *COLUMNS]
    print(" | ".join(header))
    for kind in sorted(counts):
        bucket = counts[kind]
        print(" | ".join([kind, *(str(bucket[column]) for column in COLUMNS)]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", required=True, type=Path, help="正解の対応表")
    parser.add_argument(
        "--condition",
        action="append",
        default=[],
        metavar="名前=答え.json",
        help="条件の名前と、その条件の答えのファイル",
    )
    parser.add_argument("--legend-only", type=Path, help="対照表。条件「凡例だけ」を機械で出す")
    parser.add_argument("--eye", type=Path, help="目で見た判定(番号 → 判定の語)")
    parser.add_argument("--needed-out", type=Path, help="目で見るべき番号の一覧を書き出す")
    parser.add_argument(
        "--rows-out", type=Path, help="1 件ずつの表を書き出す。**共有フォルダのパスを渡すこと**"
    )
    parser.add_argument("--rows-kind", default="群B 図形だけ", help="1 件ずつの表にする群")
    args = parser.parse_args(argv)

    key = json.loads(args.answers.read_text(encoding="utf-8"))["answers"]
    eye = json.loads(args.eye.read_text(encoding="utf-8")) if args.eye else {}

    conditions: list[tuple[str, dict[str, Any]]] = []
    if args.legend_only:
        conditions.append(("凡例だけ", legend_only(key, args.legend_only)))
    for item in args.condition:
        name, _, path = item.partition("=")
        conditions.append((name, json.loads(Path(path).read_text(encoding="utf-8"))))

    needed: dict[str, list[str]] = {}
    for name, responses in conditions:
        _print(name, tally(key, responses, eye.get(name)))
        tags = rule_split(key, responses)
        for kind in sorted(tags):
            printable = ", ".join(f"{tag} {count}" for tag, count in sorted(tags[kind].items()))
            print(f"札 {kind}: {printable}")
        needed[name] = eye_verdicts_needed(key, responses)
        print(f"目で見るべき件数: {len(needed[name])}")

    if args.rows_out:
        args.rows_out.parent.mkdir(parents=True, exist_ok=True)
        args.rows_out.write_text(
            rows_table(key, conditions, args.rows_kind, eye), encoding="utf-8"
        )
        print(f"\n{args.rows_kind} の 1 件ずつの表 -> {args.rows_out}")

    if args.needed_out:
        args.needed_out.write_text(
            json.dumps(needed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n目で見るべき番号の一覧 -> {args.needed_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
