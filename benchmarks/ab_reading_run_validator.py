"""読み方の比較実験: 1 回ぶんの答案 JSON を機械で検査する。

**この道具を作った理由。** 2026-09-22 の 1 回目で、答案の中身の検査を
手書きの使い捨てスクリプトでやっていた。同じ検査を毎回書き直すと、書き方が
毎回ぶれる。実際に 1 回目の検査では、**仕様では `null` でよい欄
（`数量`・`単位`・`途中で変えた`）を「空だから欠け」と数えて、
337 件の偽の欠けを出した。** 検査そのものが毎回違うなら、
6 回の答案を同じ物差しで見たことにならない。

**この道具は答案の中身を持たない。**欄の名前と、欄が満たすべき形だけを知っている。
実図面から作った答案そのものはリポジトリに入れない（設定でパスを渡す）。

検査するのは 4 つ。

1. **記録欄の欠け** — 仕様で必須の欄が空でないか。
   仕様で `null` を許している欄（`数量`・`単位`・`途中で変えた`・`波及の道筋`・
   `備考`）は、**空でも欠けではない。**
2. **金額が混ざっていないか** — この作業は金額を出す作業ではない。
3. **ページの数が合うか** — 読んだページ ＋ 開かなかったページ ＝ 全ページ、
   かつ両方に出てくるページが無いこと。
4. **渡したフォルダの外を指していないか** — 答案が外の場所に触れていたら、
   読む側がそこを見に行った跡である。

使い方::

    python3 -m benchmarks.ab_reading_run_validator 答案.json --pages 34
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from typing import Any, Sequence

# 仕様（出力の形.md）で必ず埋まっている欄。
REQUIRED_ITEM_FIELDS: tuple[str, ...] = (
    "番号",
    "工事項目",
    "区分",
    "場所",
    "根拠",
    "確かさ",
    "出どころ",
    "問いの出どころ",
    "区分の根拠",
    "段の数",
)

# **欄としては必ず在るが、中身が `null` でよいもの。**
# 「単位が書かれていないときは null（勝手に mm や m と決めない）」
# 「変えていなければ null」が仕様なので、空を欠けと数えてはいけない。
NULLABLE_ITEM_FIELDS: tuple[str, ...] = ("数量", "単位", "途中で変えた")

REQUIRED_CHECK_FIELDS: tuple[str, ...] = ("番号", "内容", "なぜ", "種類")

REQUIRED_RECORD_FIELDS: tuple[str, ...] = (
    "所要時間_秒",
    "読んだページ",
    "開かなかったページ",
    "ページを開いた順",
    "読んだ文字量",
    "推論の数",
    "計算の数",
    "往復の回数",
    "最初の工事項目が出た時刻",
    "全部出そろった時刻",
)

MONEY_TERMS: tuple[str, ...] = ("金額", "単価", "価格", "¥")

# 答案が触れていてはいけない場所。渡したフォルダの外である。
OUTSIDE_TERMS: tuple[str, ...] = (
    "renovation-golden-001",
    "uploads/hearth",
    "/home/user/",
    "docs/experiments",
    "ab_sealed",
    ".sealed",
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """見つかった欠陥 1 件。"""

    kind: str
    where: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - 表示のため
        return f"[{self.kind}] {self.where}: {self.detail}"


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def check_items(items: Sequence[dict]) -> list[Finding]:
    """工事項目の欄の欠けを見る。"""
    findings: list[Finding] = []
    for item in items:
        where = f"工事項目 {item.get('番号', '(番号なし)')}"
        for field in REQUIRED_ITEM_FIELDS:
            if _empty(item.get(field)):
                findings.append(Finding("欄の欠け", where, f"{field} が空"))
        for field in NULLABLE_ITEM_FIELDS:
            if field not in item:
                findings.append(Finding("欄の欠け", where, f"{field} の欄が無い"))
        if not _empty(item.get("数量")) and _empty(item.get("根拠")):
            findings.append(Finding("根拠なし", where, "数量があるのに根拠が無い"))
    return findings


def check_checks(checks: Sequence[dict]) -> list[Finding]:
    """要確認の欄の欠けを見る。"""
    findings: list[Finding] = []
    for check in checks:
        where = f"要確認 {check.get('番号', '(番号なし)')}"
        for field in REQUIRED_CHECK_FIELDS:
            if _empty(check.get(field)):
                findings.append(Finding("欄の欠け", where, f"{field} が空"))
    return findings


def check_record(record: dict, total_pages: int) -> list[Finding]:
    """作業記録の欠けと、ページの数が合うかを見る。"""
    findings: list[Finding] = []
    for field in REQUIRED_RECORD_FIELDS:
        if field not in record or record[field] is None:
            findings.append(Finding("欄の欠け", "作業記録", f"{field} が無い"))

    read = set(record.get("読んだページ") or [])
    unread = set(record.get("開かなかったページ") or [])
    both = read & unread
    if both:
        findings.append(
            Finding("ページ", "作業記録", f"読んだと開かなかったの両方に出るページ: {sorted(both)}")
        )
    missing = set(range(1, total_pages + 1)) - (read | unread)
    if missing:
        findings.append(
            Finding("ページ", "作業記録", f"どちらにも出てこないページ: {sorted(missing)}")
        )
    extra = (read | unread) - set(range(1, total_pages + 1))
    if extra:
        findings.append(
            Finding("ページ", "作業記録", f"存在しないページ番号: {sorted(extra)}")
        )
    return findings


def check_no_money(blob: str) -> list[Finding]:
    """金額が混ざっていないか。"""
    findings = [
        Finding("金額", "答案全体", f"金額らしい語: {term}")
        for term in MONEY_TERMS
        if term in blob
    ]
    # 「円」は「円形」「円弧」に出るので、欄の名前に使われているときだけ拾う。
    if re.search(r'"[^"]*円[^"]*"\s*:', blob):
        findings.append(Finding("金額", "答案全体", "『円』を含む欄名がある"))
    return findings


def check_no_outside(blob: str) -> list[Finding]:
    """渡したフォルダの外を指していないか。"""
    return [
        Finding("外の場所", "答案全体", f"外の場所への言及: {term}")
        for term in OUTSIDE_TERMS
        if term in blob
    ]


def validate(answer: dict, total_pages: int) -> list[Finding]:
    """答案 1 つを検査して、見つかったものを全部返す。"""
    findings: list[Finding] = []
    for field in ("実行ID", "使ったモデル", "入力パッケージ", "開始時刻", "終了時刻"):
        if _empty(answer.get(field)):
            findings.append(Finding("欄の欠け", "答案", f"{field} が空"))

    findings += check_items(answer.get("工事項目") or [])
    findings += check_checks(answer.get("要確認") or [])
    findings += check_record(answer.get("作業記録") or {}, total_pages)

    blob = json.dumps(answer, ensure_ascii=False)
    findings += check_no_money(blob)
    findings += check_no_outside(blob)
    return findings


def summarize(answer: dict) -> dict[str, int]:
    """採点の前に、両方の条件で必ず並べて見る数。

    **出した行数そのものを、言い当ての割合の隣に必ず置く**（採点の決まり 7-2-2）。
    """
    items = answer.get("工事項目") or []
    return {
        "工事項目": len(items),
        "数量あり": sum(1 for i in items if not _empty(i.get("数量"))),
        "数量が空": sum(1 for i in items if _empty(i.get("数量"))),
        "要確認": len(answer.get("要確認") or []),
    }


def format_report(findings: Sequence[Finding], counts: dict[str, int]) -> str:
    lines = [
        "件数: " + "、".join(f"{k} {v}" for k, v in counts.items()),
    ]
    if not findings:
        lines.append("欠陥は見つかりませんでした。")
    else:
        lines.append(f"欠陥 {len(findings)} 件:")
        lines += [f"  {f}" for f in findings]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - 入口
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("answer", type=pathlib.Path, help="答案の JSON")
    parser.add_argument("--pages", type=int, default=34, help="材料の全ページ数")
    args = parser.parse_args(argv)

    answer = json.loads(args.answer.read_text(encoding="utf-8"))
    findings = validate(answer, args.pages)
    print(format_report(findings, summarize(answer)))
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
