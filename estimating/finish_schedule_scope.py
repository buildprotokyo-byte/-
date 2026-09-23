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

1 行が 1 件とは限らない(おーちゃんの回答)
-------------------------------------------
**「撤去して新設」と「下地からやり替え」は、見積の行としては撤去と新設の
2 行になる。** どちらの行も根拠は**同じ仕上表の同じページ・同じ行**である。

「下地からやり替え」を分ける理屈はおーちゃんの言葉で、**下地からやり替える
なら既存の下地を撤去する工事は必ず起きる。範囲が決まらないのは数量の話で、
工事があるかどうかとは別**というものである(K-08 1番)。撤去のほうには
「範囲は仕上表からは決まらない」と断りを残す。

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
    WORK_NEW,
    WORK_REMOVAL,
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

#: 部品の区分 → 工事区分(`estimating/scope_diff.py`)。**1 つの読みが
#: 複数の区分になることがある。**
#:
#: **「撤去して新設」と「下地からやり替え」は要素 2 つ(撤去・新設)になる。**
#: 1 件に畳むと片方が見えなくなるので、見積の行のほうを 2 本に分ける。
#: **どちらの要素も根拠は同じ仕上表の同じページ・同じ行**で、読み
#: (`FinishScopeAssignment.reading`)は 1 行分のまま残る。
#:
#: - 「撤去して新設」… おーちゃんの回答(札、2026-09-23 15:11「2行に分ける」)
#: - 「下地からやり替え」… おーちゃんの回答(K-08 1番)。**下地からやり替える
#:   なら、既存の下地を撤去する工事は必ず起きる。範囲が決まらないのは数量の
#:   話で、工事があるかどうかとは別である。** 数量は人の入力から来るので、
#:   ここで止める理由が無い。撤去のほうには
#:   `NOTE_EXTENT_UNKNOWN` を注記として残す。
#:
#: **「改修」1 つに畳まれるのは「仕上だけやり替え」だけになった。** 元の読みは
#: `FinishScopeAssignment.reading` に残したまま運ぶ(おーちゃんの回答、札、
#: 2026-09-23 15:01。この形でよい)。
READING_TO_WORK_KINDS: dict[str, tuple[str, ...]] = {
    READING_NO_WORK: (WORK_AS_IS,),
    READING_FINISH_ONLY: (WORK_ALTERED,),
    READING_REPLACE: (WORK_REMOVAL, WORK_NEW),
    READING_FROM_BASE: (WORK_REMOVAL, WORK_NEW),
    READING_QUESTION: (WORK_UNDECIDED,),
}

#: 「下地からやり替え」の**撤去のほう**に付ける断り。おーちゃんの指定
#: (K-08 1番)。工事があることは言えるが、**どこまで撤去するかは表に
#: 書いていない。**
NOTE_EXTENT_UNKNOWN = "範囲は仕上表からは決まらない"

#: 下地欄の「現況のまま」を表す書き方。
#: ``流用`` ``再使用`` はおーちゃんが足した(K-10 2番。住宅改修でよく使う)。
BASE_EXISTING: frozenset[str] = frozenset(
    {"既存", "既存のまま", "現況", "現状", "流用", "再使用"}
)

#: 下地欄の「取り替える」を表す書き方。
#: ``更新`` ``新替`` はおーちゃんが足した(K-10 2番)。
BASE_REPLACED: frozenset[str] = frozenset(
    {"交換", "取替", "取り替え", "取替え", "更新", "新替"}
)

#: 下地欄の「該当なし」を表す書き方。長音符・ダッシュ・ハイフンの区別は
#: 図面ごとに揺れるので、見た目が横棒 1 本のものはまとめて受ける。
BASE_NOT_APPLICABLE: frozenset[str] = frozenset(
    {"ー", "-", "－", "―", "‐", "–", "—", "‒", "─", "なし", "無し"}
)

#: **材料名として読んではいけない書き方。** 「まだ決まっていない」という
#: 意味の語で、材料名とみなすと工事が 1 件増える。
#:
#: 2026-09-23、おーちゃんが ``協議`` ``別途見積`` を足した(K-10 2番)。
#: P011 の 69 行にはこのどれも出てこないが、**住宅改修ではよく使う**とのこと。
#:
#: **``同上`` はここから外した**(K-10 1番)。「まだ決まっていない」ではなく
#: 「上の行と同じ」という意味なので、`BASE_SAME_AS_ABOVE` へ移した。
BASE_UNDETERMINED: frozenset[str] = frozenset(
    {
        "未定",
        "不明",
        "確認中",
        "別途",
        "要確認",
        "現地確認",
        "協議",
        "別途見積",
        "?",
        "？",
    }
)

#: 下地欄の「上の行と同じ」を表す書き方(K-10 1番)。
#:
#: **上の行の下地を自動で引き継がない。** 継続行と同じで、問いに回す
#: (おーちゃんの指示)。引き継ぐと、上の行が「軸組新設」だったときに
#: 下の行まで勝手に「下地からやり替え」になる。
#:
#: ``〃`` はおーちゃんの回答(札、2026-09-23 23:47「同じく問いに」)で足した。
#: **表で「同上」と書くか「〃」と書くかは書き手の癖でしかない。** 片方だけ
#: 問いに回すと、同じ意味の行が図面によって別の扱いになる。
BASE_SAME_AS_ABOVE: frozenset[str] = frozenset({"同上", "〃"})

#: 下地欄の「施主支給」(K-10 2番)。
#:
#: **工事が無いのではない。材料の出どころが違うだけである**(おーちゃんの言葉)。
#: **材料費が落ちる一方、手間は残る。** どちらに寄せるかは人が決めるので、
#: 問いに回す。
BASE_OWNER_SUPPLIED: frozenset[str] = frozenset({"施主支給"})

PartSource = Literal["cell", "carried_forward", "unknown"]

#: 問いの原因。
CAUSE_BLANK_BASE = "下地欄が空欄の継続行"
CAUSE_NO_BASE_COLUMN = "下地の列が無い"
CAUSE_UNKNOWN_BASE_WORD = "下地欄が表に無い書き方"
CAUSE_SAME_AS_ABOVE = "下地欄が「同上」"
CAUSE_OWNER_SUPPLIED = "下地欄が「施主支給」"

#: 問いの文面。**おーちゃんが指定した形をそのまま持つ。**
QUESTION_CONTINUATION = "この行は前の行と同じ工事の材料違いですか、別の工事ですか"
QUESTION_SAME_AS_ABOVE = "この行の下地は、上の行と同じですか"
#: **見積の項目の名前(「材料費」など)をこの文面に書かない。**
#: `tests/test_estimate_line_mapping.py` が `estimating/` のコードに当てはめ先の
#: 名前が埋め込まれていないことを見張っている(当てはめの規則は差し替えられる
#: 設定として持つ、という決まり)。問いの文面でも同じ扱いになる。
QUESTION_OWNER_SUPPLIED = (
    "この行は施主支給です。材料は施主が用意する前提で、手間だけを見積もりますか"
)
QUESTION_NO_BASE_COLUMN = (
    "この表には下地の欄がありません。この部位に工事はありますか"
)

#: 原因 → 問いの文面。**ここに無い原因は継続行の文面になる。**
QUESTION_BY_CAUSE: dict[str, str] = {
    CAUSE_BLANK_BASE: QUESTION_CONTINUATION,
    CAUSE_UNKNOWN_BASE_WORD: QUESTION_CONTINUATION,
    CAUSE_SAME_AS_ABOVE: QUESTION_SAME_AS_ABOVE,
    CAUSE_OWNER_SUPPLIED: QUESTION_OWNER_SUPPLIED,
    CAUSE_NO_BASE_COLUMN: QUESTION_NO_BASE_COLUMN,
}

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
    previous_base: str | None = None
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
            "previous_base": self.previous_base,
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
    items: tuple[WorkScopeItem, ...]
    """この行から出た要素。**1 行が 1 要素とは限らない。**

    「撤去して新設」の行は撤去と新設の 2 要素になる(おーちゃんの回答、
    札、2026-09-23 15:11)。**行の数を数えるときは `assignments` を、
    見積の行の数を数えるときは要素を数えること。**
    """
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
            "items": [item.as_dict() for item in self.items],
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

    @property
    def item_count(self) -> int:
        """作った要素の数。**行の数とは違う**(「撤去して新設」が 2 つになる)。"""
        return sum(len(assignment.items) for assignment in self.assignments)

    def counts_by_work_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for assignment in self.assignments:
            for item in assignment.items:
                counts[item.work_kind] = counts.get(item.work_kind, 0) + 1
        return counts

    def counts_text(self) -> str:
        parts = [f"{name} {count}件" for name, count in self.counts_by_reading().items()]
        parts.append(f"要素 {self.item_count}件")
        parts.append(f"割り当てられなかった行 {len(self.unassigned)}件")
        return " / ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "item_count": self.item_count,
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


def _reading_of(base: str | None, finish: str | None) -> tuple[str, str, str]:
    """下地欄と仕上欄から、読みと理由と**問いの原因**を返す。

    問いにならない読みでは、原因は空文字である。**原因は当てた場所で決める。**
    後から「問いだったなら原因はこれだろう」と推し量ると、読みを 1 つ足す
    たびに取り違える(`同上` と `施主支給` は、どちらも「表に無い書き方」
    ではない)。
    """
    key = _normalize(base)
    if not key:
        return (
            READING_QUESTION,
            f"{CAUSE_BLANK_BASE}のため、自動では判定しない",
            CAUSE_BLANK_BASE,
        )
    if key in BASE_SAME_AS_ABOVE:
        return (
            READING_QUESTION,
            f"下地欄が {base!r} なので、上の行と同じかどうかを人に聞く",
            CAUSE_SAME_AS_ABOVE,
        )
    if key in BASE_OWNER_SUPPLIED:
        return (
            READING_QUESTION,
            f"下地欄が {base!r}。工事が無いのではなく、材料の出どころが違う",
            CAUSE_OWNER_SUPPLIED,
        )
    if key in BASE_UNDETERMINED:
        return (
            READING_QUESTION,
            f"{CAUSE_UNKNOWN_BASE_WORD}({base!r})なので、材料名とみなさない",
            CAUSE_UNKNOWN_BASE_WORD,
        )
    if key in BASE_NOT_APPLICABLE:
        return READING_NO_WORK, f"該当なし(下地欄が {base!r})", ""
    if key in BASE_REPLACED:
        return READING_REPLACE, f"下地欄が {base!r} なので、既存を撤去して新設", ""
    if key in BASE_EXISTING:
        if _normalize(finish) in BASE_EXISTING:
            return READING_NO_WORK, "下地も仕上も既存", ""
        if not _normalize(finish):
            return READING_NO_WORK, "下地は既存で、仕上の欄が空欄", ""
        return READING_FINISH_ONLY, "下地は既存で、仕上に材料名がある", ""
    return READING_FROM_BASE, f"下地欄に材料名({base!r})がある", ""


def _items_for(
    reading: str,
    *,
    room: str,
    part: str,
    page_number: int,
    row_index: int,
    source_texts: dict[str, str],
    notes: tuple[str, ...],
) -> tuple[WorkScopeItem, ...]:
    """1 行から要素を作る。**「撤去して新設」と「下地からやり替え」は 2 つになる。**

    分けた 2 つは**同じ根拠**(同じページ・同じ行)を持つ。別々の根拠に
    すると、見積の 2 行が別の証拠から出てきたように見えてしまう。
    """
    kinds = READING_TO_WORK_KINDS[reading]

    items: list[WorkScopeItem] = []
    for kind in kinds:
        item_notes = notes
        if len(kinds) > 1:
            item_notes = item_notes + (
                f"仕上表の 1 行(読みは「{reading}」)を撤去と新設の 2 件に"
                f"分けたうちの「{kind}」。もう一方と根拠は同じ行である",
            )
            if reading == READING_FROM_BASE and kind == WORK_REMOVAL:
                # **工事があることは言えるが、どこまで撤去するかは表に無い。**
                # 数量は人の入力から来るので、ここで止める理由は無い(K-08 1番)。
                item_notes = item_notes + (NOTE_EXTENT_UNKNOWN,)
        items.append(
            WorkScopeItem(
                work_kind=kind,
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
                notes=item_notes,
            )
        )
    return tuple(items)


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
            reading, reason, cause = _reading_of(view.base, view.finish)

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
                question=QUESTION_BY_CAUSE.get(cause, QUESTION_CONTINUATION),
                cause=cause,
                page_number=page_number,
                row_index=view.row_index,
                previous_room=previous.room if previous else None,
                previous_part=previous.part if previous else None,
                previous_base=previous.base if previous else None,
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
            items=_items_for(
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
