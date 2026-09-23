"""内装仕上表の 1 行から、**その部位に工事があるかどうか**を読む(K-05)。

なぜこれを作るのか
------------------
見積の行が当たらなかった原因のうち 2 つ目(面積・下地)に直接効く。面積そのもの
は人の入力から来るが、**「どの部屋のどの部位に工事があるか」が決まらないと、
面積を当てる先が決まらない。** 内装仕上表にはそれが文字で書いてある。

おーちゃんが決めた読み方(2026-09-23、K-05)
-------------------------------------------
**これは会社の読み方であって、こちらが推論したものではない。**

============ ============== ==========================
下地欄       仕上欄         読み
============ ============== ==========================
既存         既存           工事なし
既存         材料名あり     仕上だけやり替え
交換         何でも         既存を撤去して新設
ー           何でも         該当なし。工事なし
既存         空欄           工事なし
材料名あり   何でも         下地からやり替え
============ ============== ==========================

**下地欄が空欄の継続行(同じ部位で材料名だけが続く行)は、自動で判定しない。**
物によって意味が違うためである。

- アクセントクロス … 同じ壁の別の面。面積が分かれる
- 出隅コーナー材 … 巾木に付属する部材。長さの単位が違う

この 2 つを取り違えると、面積の行と長さの行が入れ替わる。**区分を当てずに
問いとして出す。**

この部品がしないこと
--------------------
1. **数量を出さない。** 面積と長さは人の入力から来る決まり(原則 3-1・4)。
   ここが出すのは「どの部屋のどの部位に、どういう工事があるか」までである。
2. **知らない書き方を材料名とみなさない。** ``未定`` ``要確認`` のような
   「まだ決まっていない」語を材料名として読むと、**ありもしない工事が
   1 件増える。** 表に無い書き方は問いへ回す。
3. **下地の列が無い表で工事の有無を名乗らない。** 「下地は既存だから仕上だけ」
   という読みは、下地欄があって初めて成り立つ。列そのものが無い表では、
   同じ結論に見えても根拠が無い。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from estimating.scope_diff import (
    WORK_ALTERED,
    WORK_AS_IS,
    WORK_UNDECIDED,
    ScopeEvidence,
    WorkScopeItem,
)

#: 部品の区分。**おーちゃんが書いた言葉をそのまま使う。**
READING_NO_WORK = "工事なし"
READING_FINISH_ONLY = "仕上だけやり替え"
READING_REPLACE = "撤去して新設"
READING_FROM_BASE = "下地からやり替え"
READING_QUESTION = "問い"

READINGS: tuple[str, ...] = (
    READING_NO_WORK,
    READING_FINISH_ONLY,
    READING_REPLACE,
    READING_FROM_BASE,
    READING_QUESTION,
)

#: 部品の区分 → 工事区分の 5 つ(`estimating/scope_diff.py`)。
#:
#: **3 つの読みが「改修」1 つに畳まれる。** 仕上だけやり替えるのと、下地から
#: やり替えるのでは、単価も工程も違う。畳んだ結果しか下流へ行かないと
#: その差が消えるので、**読みのほうも `FinishScopeAssignment.reading` に
#: 残したまま運ぶ。**
READING_TO_WORK_KIND: dict[str, str] = {
    READING_NO_WORK: WORK_AS_IS,
    READING_FINISH_ONLY: WORK_ALTERED,
    READING_REPLACE: WORK_ALTERED,
    READING_FROM_BASE: WORK_ALTERED,
    READING_QUESTION: WORK_UNDECIDED,
}

#: 下地欄の「現況のまま」を表す書き方。
BASE_EXISTING: frozenset[str] = frozenset({"既存", "既存のまま", "現況", "現状"})

#: 下地欄の「取り替える」を表す書き方。
BASE_REPLACED: frozenset[str] = frozenset({"交換", "取替", "取り替え", "取替え"})

#: 下地欄の「該当なし」を表す書き方。長音符・ダッシュ・ハイフンの区別は
#: 図面ごとに揺れるので、見た目が横棒 1 本のものはまとめて受ける。
BASE_NOT_APPLICABLE: frozenset[str] = frozenset(
    {"ー", "-", "－", "―", "‐", "–", "—", "‒", "─", "なし", "無し"}
)

#: **材料名として読んではいけない書き方。** 「まだ決まっていない」という
#: 意味の語で、材料名とみなすと工事が 1 件増える。
#:
#: **この一覧は校正していない。** P011 の 69 行にはこのどれも出てこないので、
#: 入れても外しても実案件の数は変わらない。**安全な向き(問いへ倒す)へ
#: 倒してあるだけ**である。
BASE_UNDETERMINED: frozenset[str] = frozenset(
    {"未定", "不明", "確認中", "別途", "要確認", "現地確認", "同上", "?", "？"}
)

PartSource = Literal["cell", "carried_forward", "unknown"]

#: 問いの原因。
CAUSE_BLANK_BASE = "下地欄が空欄の継続行"
CAUSE_NO_BASE_COLUMN = "下地の列が無い"
CAUSE_UNKNOWN_BASE_WORD = "下地欄が表に無い書き方"

EVIDENCE_KIND = "仕上表から読んだ"


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return "".join(unicodedata.normalize("NFKC", text).split())


# ---------------------------------------------------------------------------
# 出力の形
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeQuestion:
    """人に聞く 1 件。**数量にはしない。**"""

    question: str
    cause: str
    page_number: int
    row_index: int
    previous_room: str | None = None
    previous_part: str | None = None
    previous_finish: str | None = None
    this_row_texts: tuple[str, ...] = ()
    """この行に書かれていること。**列の役割を決めつけずに、空でない升目を
    左から並べる。** メーカー名や品番の欄に材料名が書かれている行がある。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "cause": self.cause,
            "page_number": self.page_number,
            "row_index": self.row_index,
            "previous_room": self.previous_room,
            "previous_part": self.previous_part,
            "previous_finish": self.previous_finish,
            "this_row_texts": list(self.this_row_texts),
        }


@dataclass(frozen=True)
class FinishScopeAssignment:
    """仕上表の 1 行の割り当て。"""

    reading: str
    reason: str
    room: str | None
    part: str | None
    part_source: PartSource
    base: str | None
    finish: str | None
    page_number: int
    row_index: int
    item: WorkScopeItem
    question: ScopeQuestion | None = None
    """問いになった行では、**要素(区分不明)と問いの両方が出る。**

    要素を出すのは、工事が無いことと見落としたことを区別するため
    (`docs/principles/scope_of_work_diff.md` 4-2節)。**同じ行なので、
    要素の数と問いの数を足さないこと。**
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "reading": self.reading,
            "reason": self.reason,
            "room": self.room,
            "part": self.part,
            "part_source": self.part_source,
            "page_number": self.page_number,
            "row_index": self.row_index,
            "item": self.item.as_dict(),
            "question": self.question.as_dict() if self.question else None,
        }


@dataclass(frozen=True)
class UnassignedRow:
    """どの区分にも割り当てられなかった行。**捨てた事実を残す。**"""

    page_number: int
    row_index: int
    reason: str
    texts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "row_index": self.row_index,
            "reason": self.reason,
            "texts": list(self.texts),
        }


@dataclass(frozen=True)
class FinishScopeResult:
    assignments: tuple[FinishScopeAssignment, ...]
    questions: tuple[ScopeQuestion, ...]
    unassigned: tuple[UnassignedRow, ...]
    no_base_column: bool
    row_count: int

    def counts_by_reading(self) -> dict[str, int]:
        counts = {reading: 0 for reading in READINGS}
        for assignment in self.assignments:
            counts[assignment.reading] += 1
        return counts

    def counts_by_work_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for assignment in self.assignments:
            kind = assignment.item.work_kind
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def counts_text(self) -> str:
        parts = [f"{name} {count}件" for name, count in self.counts_by_reading().items()]
        parts.append(f"割り当てられなかった行 {len(self.unassigned)}件")
        return " / ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "no_base_column": self.no_base_column,
            "counts_by_reading": self.counts_by_reading(),
            "counts_by_work_kind": self.counts_by_work_kind(),
            "question_count": len(self.questions),
            "unassigned": [row.as_dict() for row in self.unassigned],
        }


# ---------------------------------------------------------------------------
# 1 行を見るための、読み取り結果によらない形
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RowView:
    row_index: int
    room: str | None
    part: str | None
    base: str | None
    finish: str | None
    texts: tuple[str, ...]
    from_skipped: bool


def _views(schedule: Any) -> list[_RowView]:
    """読めた行と、読み取り側が落とした行を、**行番号の順に 1 本に並べる。**

    落とした行(部位も仕上も空)は、おーちゃんの言う「下地が空欄の継続行」
    そのものなので、ここで拾い直さないと問いが丸ごと消える。
    """
    columns: dict[str, int] = dict(getattr(schedule, "columns", {}) or {})
    views: list[_RowView] = []

    for row in schedule.rows:
        views.append(
            _RowView(
                row_index=row.row_index,
                room=row.room,
                part=row.part,
                base=getattr(row, "base", None),
                finish=row.finish,
                texts=tuple(getattr(row, "row_texts", ()) or ()),
                from_skipped=False,
            )
        )

    base_index = columns.get("下地")
    for skipped in getattr(schedule, "skipped_rows", ()) or ():
        texts = tuple(text.strip() for text in skipped.texts)
        if not any(texts):
            # **文字が 1 つも無い行は継続行ではない。** 罫線だけ引いてある空の行で、
            # 実図面の仕上表にはこれが行数の半分近くある。問いにすると、
            # 人に見せる問いが 16 件から 67 件へ膨らむ。
            continue
        base = None
        if base_index is not None and base_index < len(texts):
            base = texts[base_index] or None
        views.append(
            _RowView(
                row_index=skipped.row_index,
                room=None,
                part=None,
                base=base,
                finish=None,
                texts=tuple(text for text in texts if text),
                from_skipped=True,
            )
        )

    views.sort(key=lambda view: view.row_index)
    return views


# ---------------------------------------------------------------------------
# 読み方の表
# ---------------------------------------------------------------------------


def _reading_of(base: str | None, finish: str | None) -> tuple[str, str]:
    """下地欄と仕上欄から、読みとその理由を返す。"""
    key = _normalize(base)
    if not key:
        return READING_QUESTION, f"{CAUSE_BLANK_BASE}のため、自動では判定しない"
    if key in BASE_UNDETERMINED:
        return (
            READING_QUESTION,
            f"{CAUSE_UNKNOWN_BASE_WORD}({base!r})なので、材料名とみなさない",
        )
    if key in BASE_NOT_APPLICABLE:
        return READING_NO_WORK, f"該当なし(下地欄が {base!r})"
    if key in BASE_REPLACED:
        return READING_REPLACE, f"下地欄が {base!r} なので、既存を撤去して新設"
    if key in BASE_EXISTING:
        if _normalize(finish) in BASE_EXISTING:
            return READING_NO_WORK, "下地も仕上も既存"
        if not _normalize(finish):
            return READING_NO_WORK, "下地は既存で、仕上の欄が空欄"
        return READING_FINISH_ONLY, "下地は既存で、仕上に材料名がある"
    return READING_FROM_BASE, f"下地欄に材料名({base!r})がある"


def _item_for(
    reading: str,
    *,
    room: str,
    part: str,
    page_number: int,
    row_index: int,
    source_texts: dict[str, str],
    notes: tuple[str, ...],
) -> WorkScopeItem:
    alternatives: tuple[str, ...] = ()
    if reading == READING_REPLACE:
        alternatives = ("撤去", "新設")
        notes = notes + (
            "この 1 件は撤去と新設の両方を含む。"
            "工事区分を「改修」1 つに畳むと片方が見えなくなる",
        )
    elif reading == READING_FROM_BASE:
        alternatives = ("撤去", "新設")
        notes = notes + (
            "下地から替わるので、撤去と新設の両方を含みうる。"
            "どこまで撤去するかは仕上表からは決まらない",
        )
    return WorkScopeItem(
        work_kind=READING_TO_WORK_KIND[reading],
        what=part,
        where=room,
        evidence=(
            ScopeEvidence(
                kind=EVIDENCE_KIND,
                page_number=page_number,
                row_index=row_index,
                source_texts=source_texts,
            ),
        ),
        alternatives=alternatives,
        notes=notes,
    )


def assign_finish_schedule_scope(schedule: Any) -> FinishScopeResult:
    """内装仕上表 1 つを、行ごとの区分に割り当てる。

    :param schedule: `axes.image_axis.schedule_tables.FinishSchedule`
    """
    page_number = schedule.page_number
    columns: dict[str, int] = dict(getattr(schedule, "columns", {}) or {})
    no_base_column = "下地" not in columns

    assignments: list[FinishScopeAssignment] = []
    questions: list[ScopeQuestion] = []
    unassigned: list[UnassignedRow] = []

    room_carried: str | None = None
    part_carried: str | None = None
    previous: FinishScopeAssignment | None = None

    views = _views(schedule)
    for view in views:
        room = view.room or room_carried
        if view.room:
            room_carried = view.room

        part_source: PartSource = "cell"
        part = view.part
        if part:
            part_carried = part
        elif part_carried:
            part, part_source = part_carried, "carried_forward"
        else:
            part_source = "unknown"

        if not room or not part:
            unassigned.append(
                UnassignedRow(
                    page_number=page_number,
                    row_index=view.row_index,
                    reason=(
                        "室名が読めず、引き継げる室名も無かった"
                        if not room
                        else "部位が読めず、引き継げる部位も無かった"
                    ),
                    texts=view.texts,
                )
            )
            continue

        if no_base_column:
            reading = READING_QUESTION
            reason = f"{CAUSE_NO_BASE_COLUMN}ので、工事の有無を名乗らない"
            cause = CAUSE_NO_BASE_COLUMN
        else:
            reading, reason = _reading_of(view.base, view.finish)
            cause = (
                CAUSE_UNKNOWN_BASE_WORD
                if reading == READING_QUESTION and _normalize(view.base)
                else CAUSE_BLANK_BASE
            )

        notes: tuple[str, ...] = ()
        if part_source == "carried_forward":
            notes = notes + (f"部位の欄が空欄だったので、上の行の {part!r} を引き継いだ",)
        if view.from_skipped:
            notes = notes + (
                "部位も仕上も空の行なので、読み取り側は行として読んでいない。"
                "継続行として拾い直した",
            )

        source_texts = {
            key: value
            for key, value in (
                ("室名", view.room or ""),
                ("部位", view.part or ""),
                ("下地", view.base or ""),
                ("仕上", view.finish or ""),
            )
            if value
        }

        question: ScopeQuestion | None = None
        if reading == READING_QUESTION:
            question = ScopeQuestion(
                question=(
                    "この行は前の行と同じ工事の材料違いですか、別の工事ですか"
                    if cause != CAUSE_NO_BASE_COLUMN
                    else "この表には下地の欄がありません。"
                    "この部位に工事はありますか"
                ),
                cause=cause,
                page_number=page_number,
                row_index=view.row_index,
                previous_room=previous.room if previous else None,
                previous_part=previous.part if previous else None,
                previous_finish=previous.finish if previous else None,
                this_row_texts=view.texts,
            )
            questions.append(question)

        assignment = FinishScopeAssignment(
            reading=reading,
            reason=reason,
            room=room,
            part=part,
            part_source=part_source,
            base=view.base,
            finish=view.finish,
            page_number=page_number,
            row_index=view.row_index,
            item=_item_for(
                reading,
                room=room,
                part=part,
                page_number=page_number,
                row_index=view.row_index,
                source_texts=source_texts,
                notes=notes,
            ),
            question=question,
        )
        assignments.append(assignment)
        previous = assignment

    return FinishScopeResult(
        assignments=tuple(assignments),
        questions=tuple(questions),
        unassigned=tuple(unassigned),
        no_base_column=no_base_column,
        row_count=len(views),
    )
