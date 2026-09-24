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

#: 目で見た判定に使ってよい語。**これ以外は撥ねる。**
#: ``合っている(上位語)`` は合っている側に数えるが、**別にも数える**。
EYE_VERDICTS = ("合っている", "合っている(上位語)", "違う", "どちらとも言えない")

#: 数える順。報告の表の列の順でもある。
COLUMNS = (
    "件数",
    "答えた",
    "不明",
    "答えなかった",
    "完全一致",
    "要目視",
    "目で見て合っている",
    "上位語",
    "目で見て違う",
    "目で見てどちらとも言えない",
    "囮に名前",
)


def normalize(text: str) -> str:
    """全角・半角、空白、中黒のゆれだけを均す。**語の中身は変えない。**"""
    body = unicodedata.normalize("NFKC", text or "")
    return "".join(body.split()).replace("・", "")


def _given(response: dict[str, Any] | None) -> str | None:
    """答えを取り出す。``None`` は「答えなかった」、``""`` は「不明」。"""
    if response is None:
        return None
    name = str(response.get("name", "")).strip()
    return UNKNOWN if not name else name


def eye_verdicts_needed(
    key: list[dict[str, Any]], responses: dict[str, Any]
) -> list[str]:
    """**目で見ないと判定できない番号**を、正解表の順で返す。"""
    needed: list[str] = []
    for row in key:
        given = _given(responses.get(row["id"]))
        if given is None or given == UNKNOWN:
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
        given = _given(responses.get(row["id"]))
        if given is None:
            bucket["答えなかった"] += 1
            continue
        if given == UNKNOWN:
            bucket["不明"] += 1
            continue
        bucket["答えた"] += 1
        expected = row.get("name")
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


def legend_only(key: list[dict[str, Any]], table_path: Path) -> dict[str, Any]:
    """**条件 1「凡例だけ」**を機械で出す。升目に刷られた文字だけを引き当てる。

    **絵は見ない。**これがいまの本番の読み方である。
    """
    table = LegendTable.load(table_path)
    responses: dict[str, Any] = {}
    for row in key:
        code = str(row.get("code") or "").strip()
        if not code:
            responses[row["id"]] = {"name": UNKNOWN, "confidence": UNKNOWN}
            continue
        match = match_marks([code], table)[0]
        value = match.meaning or match.name
        responses[row["id"]] = {
            "name": value if match.matched else UNKNOWN,
            "confidence": "確信" if match.matched else UNKNOWN,
        }
    return responses


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
        needed[name] = eye_verdicts_needed(key, responses)
        print(f"目で見るべき件数: {len(needed[name])}")

    if args.needed_out:
        args.needed_out.write_text(
            json.dumps(needed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n目で見るべき番号の一覧 -> {args.needed_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
