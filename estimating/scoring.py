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

K-66(2026-10-02): 文字の比較をやめた
------------------------------------
上の 2.「似ている語で寄せない」は**捨てていない**。捨てたのは「文字が一致するか」で、
代わりに「**構造(工事の種類・部位・状態・材料)が同じか**」を見る(`sameness`)。
`大工工事` と `木工事` は同じ科目なので当たりになり、`外壁` と `内壁`、`撤去` と `新設` は
別のまま外れになる(囮で毎回測る。`benchmarks/measure_sameness.py`、線は囮の通過 0%)。

**旧規則は消していない。**``score_lines(..., rule="旧")`` で呼べる。比べるために要る。
既定は ``rule="新"``(K-66 5 節: 採点はこの部品を通す)。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from estimating.decisive import ReasonScore, lines_without_reason, score_by_reason
from sameness import SAME, Verdict, compare_keys, structure_key
from sameness.normalize import canonical_unit, flatten, same_unit


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

    rule: str = "新"
    """どの規則で突き合わせたか(`新` = K-66 の部品、`旧` = 文字の完全一致)。"""

    matches: tuple[dict[str, Any], ...] = ()
    """当たった組 1 つずつの理由。**画面で「なぜ同じと見たか」を見るため**(K-66 2 節 e)。"""

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
            "突き合わせの規則": self.rule,
            "単位も同じ": sum(1 for m in self.matches if m.get("単位が同じ")),
            "対応づけの内訳": _verdict_counts(self.matches),
        }


def _verdict_counts(matches: Sequence[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for match in matches:
        value = str(match.get("判定") or "")
        out[value] = out.get(value, 0) + 1
    return out


def _line_fields(line: Any) -> dict[str, Any]:
    """行から構造のキーを作るための欄を集める。**行の型に依らない。**"""
    if isinstance(line, dict):
        return dict(line)
    return {
        "工事項目": getattr(line, "work_item", "") or "",
        "摘要": getattr(line, "note", "") or getattr(line, "spec", "") or "",
        "単位": getattr(line, "unit", "") or "",
        "科目": getattr(line, "major_category", "") or "",
        "場所": getattr(line, "place", "") or "",
    }


def _item_fields(item: GoldenItem) -> dict[str, Any]:
    return {"工事項目": item.work_item, "単位": item.unit, "科目": item.major_category or ""}


def score_lines_new_rule(
    lines: Iterable[Any], golden_items: Sequence[GoldenItem]
) -> ScoreResult:
    """**K-66 の新規則**で突き合わせる。構造が同じなら当たり。品名の文字は見ない。

    順: ①符号が一致する組(符号はいちばん強い証拠なので先に取る)②構造のキーが `○` の組。
    `△` は当たりにしない(**格上げは合計と位置が合うことを呼ぶ側が示したときだけ**で、
    この層は合計を持たないため)。`△` と `比較不能` は `matches` に理由付きで残す。

    **1 行が当てられるのは 1 項目まで。**同じ行で 2 項目を言い当てたことにしない。
    """
    items = list(golden_items)
    keys = [structure_key(_item_fields(item)) for item in items]
    taken: set[int] = set()
    hits: list[Any] = []
    extras: list[Any] = []
    matched: list[GoldenItem] = []
    matches: list[dict[str, Any]] = []

    rows = list(lines)
    done: set[int] = set()
    """符号で当たった行の番号。**`line in hits` では同じ中身の行を取り違えるので番号で持つ。**"""

    # ① 符号
    for position, line in enumerate(rows):
        code = _norm(getattr(line, "code", "") or "")
        if not code:
            continue
        for index, item in enumerate(items):
            if index in taken or not item.code or _norm(item.code) != code:
                continue
            taken.add(index)
            done.add(position)
            hits.append(line)
            matched.append(item)
            matches.append({"判定": SAME, "規則": "符号", "理由": f"符号 {code} が一致",
                            "単位が同じ": same_unit(getattr(line, "unit", ""), item.unit)})
            break

    # ② 構造のキー
    for position, line in enumerate(rows):
        if position in done:
            continue
        line_key = structure_key(_line_fields(line))
        best: tuple[int, Verdict] | None = None
        others: list[Verdict] = []
        for index, item_key in enumerate(keys):
            if index in taken:
                continue
            verdict = compare_keys(line_key, item_key)
            if verdict.value == SAME:
                best = (index, verdict)
                break
            if verdict.value.startswith("△"):
                others.append(verdict)
        if best is None:
            extras.append(line)
            if others:
                matches.append({"判定": others[0].value, "規則": others[0].rule,
                                "理由": others[0].reason + "(当たりにしていない)", "単位が同じ": None})
            continue
        index, verdict = best
        taken.add(index)
        hits.append(line)
        matched.append(items[index])
        matches.append({"判定": verdict.value, "規則": verdict.rule, "理由": verdict.reason,
                        "単位が同じ": same_unit(getattr(line, "unit", "") or
                                             (line.get("単位") if isinstance(line, dict) else ""),
                                             items[index].unit)})

    # ③ 規則 8: 構造が取れなかった行は、**揃えた(品名, 単位)が丸ごと同じ**ときだけ当てる。
    #    部分一致は使わない。これを切ると、語彙に無い工事は字まで同じでも外れになる。
    still: list[Any] = []
    for line in extras:
        fields = _line_fields(line)
        flat = (flatten(fields.get("工事項目")), canonical_unit(fields.get("単位")))
        if not flat[0]:
            still.append(line)
            continue
        for index, item in enumerate(items):
            if index in taken or (flatten(item.work_item), canonical_unit(item.unit)) != flat:
                continue
            taken.add(index)
            hits.append(line)
            matched.append(item)
            matches.append({"判定": SAME, "規則": "細目8",
                            "理由": f"構造は取れていないが、揃えた品名と単位が丸ごと同じ({flat[0]})",
                            "単位が同じ": True})
            break
        else:
            still.append(line)

    missed = [item for index, item in enumerate(items) if index not in taken]
    return ScoreResult(
        hit_lines=tuple(hits), extra_lines=tuple(still),
        matched_items=tuple(matched), missed_items=tuple(missed),
        rule="新", matches=tuple(matches),
    )


def score_lines(
    lines: Iterable[Any], golden_items: Sequence[GoldenItem], *, rule: str = "新"
) -> ScoreResult:
    """出した行を正解に突き合わせる。**行と正解だけを受け取る。**

    `rule="新"`(既定、K-66 5 節)= 構造が同じなら当たり。
    `rule="旧"` = 文字の完全一致(**消していない。比べるために残す**)。

    同じ鍵の正解が複数あるときは、**1 行が当てられるのは 1 項目まで**にする
    (同じ行で 2 項目を言い当てたことにしない)。
    """
    if rule == "新":
        return score_lines_new_rule(lines, golden_items)
    if rule != "旧":
        raise ScoringError(f"規則は '新' か '旧': {rule!r}")
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
        rule="旧",
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
