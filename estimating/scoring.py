"""**出した見積の行を、正解の項目に突き合わせる。**

おーちゃんの指示(66周目の続き): 採点のときに、当たった行・外した行を
**決め手の理由別に**集計できるようにする。基準は
`docs/d_reason_scoring_criteria.md`(測る前にコミット済み)。

ここが守ること
--------------
1. **抽出と採点を分ける。** この層は**行と正解だけ**を受け取る。読み取りも
   当てはめもしない。正解を読むのは採点の時点だけ、という
   ゴールデンの運用規則(`benchmarks/run_golden_eval.py` の冒頭)を崩さない。
2. **対応づけを緩めない。** 突き合わせの鍵は
   「正解に符号があれば符号」「無ければ(工事内容, 単位)の完全一致」だけ。
   **部分一致も、似ている語で寄せることもしない。** 緩めると、当たりの数だけが
   増えて中身が薄まる。
3. **数量の値は見ない。** ここが数えるのは「その行が出せたか」だけである。
   値の当たり外れと混ぜると、「出たけれど値が違う」が見えなくなる。
4. **正解からは名前・単位・符号しか読まない。** 数量も単価もこの層に入れない。

表記のゆれについて
------------------
突き合わせの前に NFKC で正規化する。`㎡` と `m²` は同じ単位なので、
**表記が違うだけで外れにしない。** 意味は変えない(`箇所` と `㎡` は別のまま)。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from estimating.decisive import ReasonScore, lines_without_reason, score_by_reason


class ScoringError(Exception):
    """採点の入力として受け付けられなかった。"""


def _norm(value: str) -> str:
    """突き合わせのための正規化。**表記をそろえるだけ。**"""
    return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True)
class GoldenItem:
    """正解の見積項目 1 つ。**数量も単価も持たない。**"""

    work_item: str
    unit: str
    code: str | None = None
    major_category: str | None = None

    @property
    def match_key(self) -> tuple[str, ...]:
        """突き合わせの鍵。**符号があれば符号だけ。**"""
        if self.code:
            return ("code", _norm(self.code))
        return ("name", _norm(self.work_item), _norm(self.unit))


def _line_key_for(line: Any, *, by_code: bool) -> tuple[str, ...]:
    if by_code:
        return ("code", _norm(getattr(line, "code", "") or ""))
    return (
        "name",
        _norm(getattr(line, "work_item", "") or ""),
        _norm(getattr(line, "unit", "") or ""),
    )


@dataclass(frozen=True)
class ScoreResult:
    """突き合わせの結果。**既存の指標(言い当て数と分母)も残す。**"""

    hit_lines: tuple[Any, ...]
    """正解の項目に当たった行。"""

    extra_lines: tuple[Any, ...]
    """出したのに正解に無い行。**「外した行」はこれである。**"""

    matched_items: tuple[GoldenItem, ...]
    missed_items: tuple[GoldenItem, ...]
    """正解にあるのに 1 行も出せなかった項目。"""

    @property
    def total_items(self) -> int:
        return len(self.matched_items) + len(self.missed_items)

    @property
    def coverage(self) -> float | None:
        """言い当てた項目数 ÷ 正解の項目数。**正解が無ければ None。**"""
        if not self.total_items:
            return None
        return len(self.matched_items) / self.total_items

    def by_reason(self) -> tuple[ReasonScore, ...]:
        """**当たった行と外した行を、決め手の理由別に数える。**"""
        return score_by_reason(self.hit_lines, self.extra_lines)

    def lines_without_reason(self) -> tuple[Any, ...]:
        """決め手の無い行。**理由別の合計に入らない行を隠さない。**"""
        return lines_without_reason(tuple(self.hit_lines) + tuple(self.extra_lines))

    def as_dict(self) -> dict[str, Any]:
        return {
            "言い当てた項目数": len(self.matched_items),
            "正解の項目数": self.total_items,
            "言い当てた割合": self.coverage,
            "出せなかった項目数": len(self.missed_items),
            "当たった行": len(self.hit_lines),
            "外した行": len(self.extra_lines),
            "理由別": [score.as_dict() for score in self.by_reason()],
            "決め手の無い行": len(self.lines_without_reason()),
        }


def score_lines(
    lines: Iterable[Any], golden_items: Sequence[GoldenItem]
) -> ScoreResult:
    """出した行を正解に突き合わせる。**行と正解だけを受け取る。**

    同じ鍵の正解が複数あるときは、**1 行が当てられるのは 1 項目まで**にする
    (同じ行で 2 項目を言い当てたことにしない)。
    """
    remaining: dict[tuple[str, ...], list[GoldenItem]] = {}
    for item in golden_items:
        remaining.setdefault(item.match_key, []).append(item)

    hits: list[Any] = []
    extras: list[Any] = []
    matched: list[GoldenItem] = []
    for line in lines:
        for by_code in (True, False):
            key = _line_key_for(line, by_code=by_code)
            if by_code and not (getattr(line, "code", "") or ""):
                continue
            bucket = remaining.get(key)
            if bucket:
                matched.append(bucket.pop(0))
                hits.append(line)
                break
        else:
            extras.append(line)

    missed = [item for bucket in remaining.values() for item in bucket]
    return ScoreResult(
        hit_lines=tuple(hits),
        extra_lines=tuple(extras),
        matched_items=tuple(matched),
        missed_items=tuple(missed),
    )


def load_golden_items(path: str | Path) -> tuple[GoldenItem, ...]:
    """正解ファイルから**項目の名前・単位・符号だけ**を読む。

    **数量も単価も読まない。** 採点の層に値を持ち込まないための入口である。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload.get("expected_items")
    if not isinstance(items, list):
        raise ScoringError("expected_items がありません(正解ファイルの形式が違います)")

    out: list[GoldenItem] = []
    for row in items:
        work_item = str(row.get("work_item", "")).strip()
        unit = str(row.get("unit", "")).strip()
        if not work_item or not unit:
            raise ScoringError(
                f"工事内容か単位が空の項目があります: {row.get('code') or work_item!r}"
            )
        out.append(
            GoldenItem(
                work_item=work_item,
                unit=unit,
                code=(str(row["code"]).strip() if row.get("code") else None),
                major_category=(
                    str(row["major_category"]).strip()
                    if row.get("major_category")
                    else None
                ),
            )
        )
    return tuple(out)
