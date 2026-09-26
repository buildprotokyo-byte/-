"""周3「ページの種類を見分ける」の測定。

基準は `docs/loop_round3_page_kind_criteria.md`(**測る前にコミット済み**)。

**文字を読まない。**紙の上の墨の性質(図形・画像・罫線の升目・文字の数・紙の向き)
だけで、ページの種類を答える。

**正解は図面自身が 1 ページ目に書いている図面リストから取る。**実案件の図面名なので
**リポジトリには置かない。**`--answers` で共有フォルダから渡す。

**しきい値は、特徴の一覧を見てから決めた。**だから
**別の冊子で確かめるまで仮である**(`docs/provisional_decisions.md` 9 節)。

**本番の経路には繋がない。**
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from benchmarks.measure_rule_placed_reading import cells

#: 画像がこの枚数以上あれば「画像が主」。
MANY_IMAGES = 10

#: 図形がこの数以上あれば「線で描いた図」。
MANY_SHAPES = 300

#: 図形が少なく、升目がこの数以上あれば「表」。
MANY_CELLS = 200

KIND_TABLE = "表"
KIND_DRAWING = "図面"
KIND_IMAGE = "画像が主"

#: 手がかりの名前。**囮はこれをページのあいだでシャッフルする。**
FEATURE_KEYS = ("文字", "図形", "画像", "画像割合", "升目", "横長")


def features(page: pymupdf.Page) -> dict[str, Any]:
    """1 ページぶんの手がかり。**文字の中身は読まない。数だけ。**"""
    area = page.rect.width * page.rect.height
    covered = 0.0
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"])
        covered += (rect.width * rect.height) / area
    return {
        "文字": len(page.get_text().strip()),
        "図形": len(page.get_drawings()),
        "画像": len(page.get_images()),
        "画像割合": min(covered, 1.0),
        "升目": len(cells(page)),
        "横長": page.rect.width > page.rect.height,
    }


def decide(feature: dict[str, Any]) -> str:
    """手がかりだけから種類を答える。**順番も測る前に決めてある。**"""
    if feature["画像"] >= MANY_IMAGES:
        return KIND_IMAGE
    if feature["図形"] < MANY_SHAPES and feature["升目"] >= MANY_CELLS:
        return KIND_TABLE
    if feature["図形"] >= MANY_SHAPES:
        return KIND_DRAWING
    return KIND_TABLE


def _shuffled(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """**手がかりをページのあいだでシャッフルする**(囮、基準の線2)。

    1 つの手がかりごとに別々に混ぜるので、**紙 1 枚ぶんの組み合わせが壊れる。**
    """
    rng = random.Random(seed)
    columns = {key: [row[key] for row in rows] for key in FEATURE_KEYS}
    for key in FEATURE_KEYS:
        rng.shuffle(columns[key])
    return [
        {key: columns[key][index] for key in FEATURE_KEYS} for index in range(len(rows))
    ]


def measure(pdf: str, answers: dict[str, Any], seed: int) -> dict[str, Any]:
    truth = {
        row["ページ"]: row["種類"]
        for row in answers["ページごと"]
        if row["種類"]
    }
    with pymupdf.open(pdf) as doc:
        rows = [features(doc.load_page(index)) for index in range(doc.page_count)]

    scored = [
        (index + 1, decide(rows[index]))
        for index in range(len(rows))
        if index + 1 in truth
    ]
    fake_rows = _shuffled(rows, seed)
    fake = [
        (index + 1, decide(fake_rows[index]))
        for index in range(len(rows))
        if index + 1 in truth
    ]

    common = Counter(truth.values()).most_common(1)[0]
    per_kind: dict[str, dict[str, Any]] = {}
    for page, answer in scored:
        want = truth[page]
        row = per_kind.setdefault(
            want, {"枚数": 0, "当たり": 0, "取り違えた先": Counter()}
        )
        row["枚数"] += 1
        if answer == want:
            row["当たり"] += 1
        else:
            row["取り違えた先"][answer] += 1

    no_text = [page for page, _ in scored if rows[page - 1]["文字"] == 0]
    return {
        "分母": len(scored),
        "当たり": sum(1 for page, answer in scored if answer == truth[page]),
        "いちばん多い種類を全部に答えたときの当たり": common[1],
        "囮(手がかりをシャッフル)の当たり": sum(
            1 for page, answer in fake if answer == truth[page]
        ),
        "文字が0件のページ": len(no_text),
        "文字が0件のページの当たり": sum(
            1 for page, answer in scored if page in no_text and answer == truth[page]
        ),
        "種類ごと": {
            kind: {
                "枚数": row["枚数"],
                "当たり": row["当たり"],
                "取り違えた先": dict(row["取り違えた先"]),
            }
            for kind, row in per_kind.items()
        },
        "ページごと": [
            {"ページ": page, "正解": truth[page], "答え": answer}
            for page, answer in scored
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--answers", required=True, help="正解(共有フォルダ)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    result = measure(args.pdf, answers, args.seed)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "ページごと"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
