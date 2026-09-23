"""画像軸: 図面の表を**建具表**・**内装仕上表**として読む。

なぜこの 2 つなのか
-------------------
実案件 P011 の評価で分かったこと(`docs/real_drawing_eval_report.md`)。

- 建具を図形から拾う `find_door_arcs()` は**円弧の幾何**で判定するので、
  **引戸と折戸は原理的に拾えない。** P011 の新設建具は全部引戸か折戸で、
  だから新設分の検出は 0 件だった。これは不具合ではなく手法の範囲外である。
  **建具表の文字が読めれば、図形では拾えない建具が拾える。** それがこの
  モジュールの一番の目的である。
- 面積 25 項目のうち 22 項目が「室の輪郭を取る実装が無い」ところで
  止まっている。内装仕上表は**室名と仕上げの対応**を与えるので、輪郭が
  取れるようになったときに、その面積が何の仕上げの数量なのかを決められる。
  **輪郭が取れるまでは、この表だけでは数量にならない。**

このモジュールがやらないこと
----------------------------
1. **数量を確定させない。** 読んだ値は根拠つきの証拠として
   `intake/drawing_intake.py` が仲裁層へ渡すだけで、階層1(自動確定)には
   届かない(手法は `arbitration/method_policies.py` で未校正として登録)。
2. **単位が表に書かれていない寸法を mm と決めない。** 住宅の建具表の
   `1650` はまず mm だが、「まず mm だろう」で埋めるのは、2026-09-21 に
   直した単位の取り違え(`docs/top_priority_unit_safety_defect.md`)と
   同じ形である。単位が読めないときは印字された文字列だけを残し、
   `width_mm` は None にして理由を書く。
3. **ページをまたいで足さない。** 同じ建具番号が別のページにあっても
   合計しない。呼び出し側(入口)が値の食い違いとして扱う。
4. **室名の引き継ぎを黙ってやらない。** 縦に結合されたセルは室名を下の行へ
   引き継ぐが、引き継いだことを `room_source` に残す。結合(`merged_cell`)と
   単なる空欄(`blank_carried_forward`)も区別する。前者は図面がそう描いている
   事実、後者はこちらの解釈である。

列の役割は**見出しの文字**から決める。列の順番は事務所ごとに違うので
決め打ちできない。見出しが揃わない表は、建具表とも内装仕上表とも名乗らない
(図面には凡例表・面積表・記事欄など、罫線で組まれた別の表がいくらでもある)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from axes.image_axis.pdf_tables import TableCell, TableRegion, find_tables

#: 手法ID。`arbitration/method_policies.py` の登録簿がこの名前を鍵にする。
#: 変えると登録簿の上限が効かなくなる(未登録手法として weak に落ちるので
#: 安全側ではあるが、黙って別物になる)ので、変えるときは登録簿も一緒に直すこと。
METHOD_DOOR_SCHEDULE = "pdf_table_door_schedule"
METHOD_FINISH_SCHEDULE = "pdf_table_finish_schedule"

#: 建具表の列の見出し。左が正式な役割名で、右がその列を指す書き方。
#: **網羅はしていない。** 知らない書き方は「その列が無い」として扱われるので、
#: 誤って別の列に割り当たるより安全な向きに倒れる。
DOOR_COLUMN_SYNONYMS: dict[str, frozenset[str]] = {
    "建具番号": frozenset(
        {"建具番号", "建具記号", "建具符号", "記号", "符号", "番号", "no.", "no",
         "建具no.", "建具no"}
    ),
    "種別": frozenset({"種別", "種類", "形式", "建具種別", "形状"}),
    "幅": frozenset({"幅", "巾", "w", "有効幅", "開口幅", "内法幅"}),
    "高さ": frozenset({"高さ", "高", "h", "有効高さ", "開口高", "内法高さ"}),
    "数量": frozenset({"数量", "員数", "個数", "数", "箇所数", "ヶ所数"}),
}

#: 建具表と認めるのに、建具番号のほかに最低いくつの列が要るか。
#:
#: 1 にすると「記号 / 備考」の 2 列しかない凡例表まで建具表になる。
#: 3 にすると数量の無い簡略な建具表を落とす。2 はその間で、
#: **実測で校正した値ではない**(合成の表でしか確かめていない)。
DOOR_MIN_SUPPORTING_COLUMNS = 2

#: 内装仕上表の列の見出し。
FINISH_COLUMN_SYNONYMS: dict[str, frozenset[str]] = {
    "室名": frozenset({"室名", "部屋名", "室", "部屋"}),
    "部位": frozenset({"部位", "箇所", "部分"}),
    "仕上": frozenset({"仕上", "仕上げ", "仕上材", "仕上げ材", "仕様"}),
    "下地": frozenset({"下地", "下地類", "下地材", "下地種別", "下地仕様", "素地"}),
}

#: 部位が列になっている書き方(こちらのほうが図面では多い)のときの、
#: 部位そのものを指す見出し。
FINISH_PART_HEADINGS: frozenset[str] = frozenset(
    {
        "床", "壁", "天井", "幅木", "巾木", "廻縁", "回り縁", "腰壁",
        "床仕上", "壁仕上", "天井仕上", "床仕上げ", "壁仕上げ", "天井仕上げ",
    }
)

#: 見出しの行を探す範囲。表の上に図面名の行が入ることがあるので 1 行目に
#: 決め打ちしない。ただし深追いもしない(データ行を見出しと取り違えるため)。
HEADER_SEARCH_ROWS = 3

#: 単位の書き方 → mm への倍率。NFKC 正規化後の文字で照合する
#: (``㎜`` は ``mm`` に、``ｍ`` は ``m`` になる)。
_UNIT_TO_MM: dict[str, float] = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

#: ``単位:mm`` のように、単位だと明示して書かれている形。
_UNIT_DECLARATION_RE = re.compile(r"単位[:：]?(mm|cm|m)(?![a-z0-9])", re.IGNORECASE)

#: 括弧の中が単位そのものである形(``幅(mm)``)。**括弧の中が単位だけのときに
#: 限る。** 文字列のどこかに ``m`` があれば単位とみなす作りにすると、
#: ``room`` のような無関係な文字で寸法が 1000 倍になる。
_UNIT_TOKEN_RE = re.compile(r"^(mm|cm|m)$", re.IGNORECASE)
_PAREN_RE = re.compile(r"[(（]([^)）]*)[)）]")
_NUMBER_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)$")
_QUANTITY_RE = re.compile(r"^([0-9]+)\s*(?:箇所|ヶ所|ケ所|か所|カ所|個|枚|本|組|台)?$")

RoomSource = Literal["cell", "merged_cell", "blank_carried_forward"]
UnitSource = Literal["heading", "caption"]


# ---------------------------------------------------------------------------
# 根拠
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleCell:
    """表から読んだ値 1 つと、その出どころ。"""

    source_text: str
    """図面に印字されたまま。**加工した値はここに入れない。**"""

    rect_pt: tuple[float, float, float, float]
    """ページ座標(ポイント)。結合セルでは文字が書いてある親セルを指す。"""

    page_number: int
    """1 始まり。人に見せる番号に合わせる。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "rect_pt": list(self.rect_pt),
            "page_number": self.page_number,
        }


@dataclass(frozen=True)
class SkippedRow:
    """読まなかった行。**捨てた事実を残すためだけの入れ物。**"""

    row_index: int
    texts: tuple[str, ...]
    reason: str


# ---------------------------------------------------------------------------
# 建具表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoorScheduleRow:
    """建具表の 1 行。"""

    mark: str
    """建具番号。**空の行はそもそも行にしない。**"""

    kind: str | None = None
    """種別(引戸・折戸・開き戸など)。この文字が、図形では拾えない建具を
    拾えたことの根拠になる。"""

    width_text: str | None = None
    """幅の欄に印字されていた文字列そのまま。"""

    height_text: str | None = None
    quantity: int | None = None
    """整数として読めたときだけ。**読めなかったら 0 ではなく None。**"""

    width_mm: float | None = None
    """**単位が表に書かれていたときだけ** mm に直した値。"""

    height_mm: float | None = None
    page_number: int = 1
    cells: dict[str, ScheduleCell] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    """読めなかったもの・こちらが解釈したものの記録。"""

    table_rect_pt: tuple[float, float, float, float] | None = None
    caption: str | None = None
    row_index: int = 0

    def provenance(self) -> dict[str, Any]:
        """仲裁層まで一緒に運ぶ根拠。"""
        return {
            "page_number": self.page_number,
            "table_rect_pt": list(self.table_rect_pt) if self.table_rect_pt else None,
            "caption": self.caption,
            "row_index": self.row_index,
            "cells": {role: cell.as_dict() for role, cell in self.cells.items()},
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DoorSchedule:
    """1 ページの中の建具表 1 つ。"""

    page_index: int
    rect_pt: tuple[float, float, float, float]
    columns: dict[str, int]
    """役割 → 列番号。**読めた列だけが入る。**"""

    rows: tuple[DoorScheduleRow, ...]
    skipped_rows: tuple[SkippedRow, ...] = ()
    caption: str | None = None
    unit_source: UnitSource | None = None
    """寸法の単位をどこから読んだか。None なら**単位が書かれていなかった**。"""

    @property
    def page_number(self) -> int:
        return self.page_index + 1


def read_door_schedules(pdf_path: str | Path, page_index: int) -> list[DoorSchedule]:
    """ページの中の建具表を読む。建具表が無ければ空のリスト。

    **空であることは「建具が無い」ではない。** 罫線で組まれていない表、
    スキャンされたページ、知らない見出しの表はどれも空になる。
    """
    out: list[DoorSchedule] = []
    for table in find_tables(pdf_path, page_index):
        schedule = _as_door_schedule(table)
        if schedule is not None:
            out.append(schedule)
    return out


def _as_door_schedule(table: TableRegion) -> DoorSchedule | None:
    header = _find_header(table, DOOR_COLUMN_SYNONYMS, required="建具番号")
    if header is None:
        return None
    header_row, columns = header
    supporting = [role for role in columns if role != "建具番号"]
    if len(supporting) < DOOR_MIN_SUPPORTING_COLUMNS:
        # 建具番号の列があるだけの表(凡例・記事欄)は建具表ではない。
        return None

    unit_mm, unit_source = _dimension_unit(table, columns, header_row)

    rows: list[DoorScheduleRow] = []
    skipped: list[SkippedRow] = []
    for row_index in range(header_row + 1, table.row_count):
        cells = table.rows[row_index]
        mark_cell = cells[columns["建具番号"]]
        mark = mark_cell.text.strip()
        if not mark:
            skipped.append(
                SkippedRow(
                    row_index=row_index,
                    texts=tuple(cell.text for cell in cells),
                    reason="建具番号の欄が空のため行として読まなかった",
                )
            )
            continue
        rows.append(
            _door_row(
                table=table,
                cells=cells,
                columns=columns,
                row_index=row_index,
                mark=mark,
                mark_cell=mark_cell,
                unit_mm=unit_mm,
            )
        )

    return DoorSchedule(
        page_index=table.page_index,
        rect_pt=table.rect_pt,
        columns=dict(columns),
        rows=tuple(rows),
        skipped_rows=tuple(skipped),
        caption=table.caption,
        unit_source=unit_source,
    )


def _door_row(
    *,
    table: TableRegion,
    cells: tuple[TableCell, ...],
    columns: dict[str, int],
    row_index: int,
    mark: str,
    mark_cell: TableCell,
    unit_mm: float | None,
) -> DoorScheduleRow:
    page_number = table.page_index + 1
    refs: dict[str, ScheduleCell] = {
        "建具番号": _ref(mark_cell, page_number, mark)
    }
    notes: list[str] = []

    kind = None
    if "種別" in columns:
        cell = cells[columns["種別"]]
        kind = cell.text.strip() or None
        if kind is not None:
            refs["種別"] = _ref(cell, page_number, kind)

    width_text, width_mm, width_notes = _dimension(
        cells, columns, "幅", page_number, unit_mm, refs
    )
    height_text, height_mm, height_notes = _dimension(
        cells, columns, "高さ", page_number, unit_mm, refs
    )
    notes.extend(width_notes)
    notes.extend(height_notes)

    quantity = None
    if "数量" in columns:
        cell = cells[columns["数量"]]
        raw = cell.text.strip()
        if raw:
            refs["数量"] = _ref(cell, page_number, raw)
            match = _QUANTITY_RE.match(_normalize(raw))
            if match is not None:
                quantity = int(match.group(1))
            else:
                notes.append(
                    f"数量の欄 {raw!r} を整数として読めなかったので None にした"
                )
        else:
            notes.append("数量の欄が空だった")

    return DoorScheduleRow(
        mark=mark,
        kind=kind,
        width_text=width_text,
        height_text=height_text,
        quantity=quantity,
        width_mm=width_mm,
        height_mm=height_mm,
        page_number=page_number,
        cells=refs,
        notes=tuple(notes),
        table_rect_pt=table.rect_pt,
        caption=table.caption,
        row_index=row_index,
    )


def _dimension(
    cells: tuple[TableCell, ...],
    columns: dict[str, int],
    role: str,
    page_number: int,
    unit_mm: float | None,
    refs: dict[str, ScheduleCell],
) -> tuple[str | None, float | None, list[str]]:
    """寸法の欄を読む。**単位が分からなければ mm の値を作らない。**"""
    if role not in columns:
        return None, None, []
    cell = cells[columns[role]]
    raw = cell.text.strip()
    if not raw:
        return None, None, []
    refs[role] = _ref(cell, page_number, raw)

    match = _NUMBER_RE.match(_normalize(raw))
    if match is None:
        return raw, None, [f"{role}の欄 {raw!r} を数値として読めなかった"]
    if unit_mm is None:
        return (
            raw,
            None,
            [
                f"{role}の欄 {raw!r} は単位が表に書かれていないため、"
                "mm に直していない"
            ],
        )
    return raw, float(match.group(1)) * unit_mm, []


def _ref(cell: TableCell, page_number: int, source_text: str) -> ScheduleCell:
    return ScheduleCell(
        source_text=source_text, rect_pt=cell.rect_pt, page_number=page_number
    )


def _dimension_unit(
    table: TableRegion, columns: dict[str, int], header_row: int
) -> tuple[float | None, UnitSource | None]:
    """寸法の単位を、列見出し → キャプションの順に探す。

    **どこにも書かれていなければ None を返し、推測しない。**
    """
    for role in ("幅", "高さ"):
        if role not in columns:
            continue
        heading = table.rows[header_row][columns[role]].text
        factor = _unit_in_parentheses(heading)
        if factor is not None:
            return factor, "heading"
    if table.caption:
        factor = _unit_in_caption(table.caption)
        if factor is not None:
            return factor, "caption"
    return None, None


def _unit_in_parentheses(text: str) -> float | None:
    """``幅(mm)`` のように、括弧の中が単位そのものである場合だけ読む。"""
    for match in _PAREN_RE.finditer(_normalize(text)):
        content = match.group(1).strip()
        declared = _UNIT_DECLARATION_RE.search(content)
        if declared is not None:
            return _UNIT_TO_MM[declared.group(1).lower()]
        token = _UNIT_TOKEN_RE.match(content)
        if token is not None:
            return _UNIT_TO_MM[token.group(1).lower()]
    return None


def _unit_in_caption(text: str) -> float | None:
    """キャプションからは、**単位だと明示されているときだけ**読む。

    ``建具表（単位:mm）`` は読む。``建具表`` や ``建具表(S=1/50)`` は読まない。
    """
    declared = _UNIT_DECLARATION_RE.search(_normalize(text))
    if declared is not None:
        return _UNIT_TO_MM[declared.group(1).lower()]
    return _unit_in_parentheses(text)


# ---------------------------------------------------------------------------
# 内装仕上表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinishScheduleRow:
    """内装仕上表の 1 件(室名・部位・仕上の 3 つ組)。"""

    room: str | None
    part: str | None
    finish: str | None
    base: str | None = None
    """下地の欄。**表に下地の列が無ければ None。**

    「下地類」が複数の列にまたがって書かれている表(見出しが横に結合されて
    いて、右側の列の見出しが空)では、**いちばん右の列**をこの欄にする。
    左側の列は `base_extra` に入る。工法(``軸組新設`` など)が左、材料や
    状態(``既存`` ``交換`` ``合板12mm``)が右に来る書き方が実図面にあった。
    **この左右の決め方は 1 つの案件でしか確かめていない。**
    """

    base_extra: tuple[str, ...] = ()
    """下地の欄が複数列にまたがるときの、**いちばん右より左の列**の文字。"""

    row_texts: tuple[str, ...] = ()
    """その行の升目の文字を、**左から順に、空でないものだけ**並べたもの。

    役割を決めた列(室名・部位・下地・仕上)だけでは足りない場面があるため。
    実図面の内装仕上表では、同じ部位の続きの行の材料名が、メーカー名や
    品番の欄にだけ書かれていることがある。**列の役割を増やして推測する
    かわりに、印字されたまま並べて人に見せる。**
    """

    room_source: RoomSource = "cell"
    """室名をどこから取ったか。

    - ``cell`` … その行のセルに書かれていた
    - ``merged_cell`` … 上のセルと縦に結合されていた(図面がそう描いている)
    - ``blank_carried_forward`` … 罫線はあるが空で、上の行から引き継いだ
      (**こちらの解釈**。図面がそう描いているとは限らない)
    """

    page_number: int = 1
    cells: dict[str, ScheduleCell] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    table_rect_pt: tuple[float, float, float, float] | None = None
    caption: str | None = None
    row_index: int = 0

    def provenance(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "table_rect_pt": list(self.table_rect_pt) if self.table_rect_pt else None,
            "caption": self.caption,
            "row_index": self.row_index,
            "room_source": self.room_source,
            "base": self.base,
            "base_extra": list(self.base_extra),
            "cells": {role: cell.as_dict() for role, cell in self.cells.items()},
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class FinishSchedule:
    """1 ページの中の内装仕上表 1 つ。"""

    page_index: int
    rect_pt: tuple[float, float, float, float]
    layout: Literal["long", "wide"]
    """``long`` … 部位と仕上が列になっている。``wide`` … 部位そのものが列。"""

    columns: dict[str, int]
    rows: tuple[FinishScheduleRow, ...]
    skipped_rows: tuple[SkippedRow, ...] = ()
    caption: str | None = None
    base_columns: tuple[int, ...] = ()
    """下地の欄に当たる列の番号を、左から右の順で全部。

    見出しが横に結合されている表では 2 つ以上になる。`columns["下地"]` は
    そのいちばん右で、**区分の判定に使うのはそちら**(`FinishScheduleRow.base`)。
    """

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    def rooms(self) -> tuple[str, ...]:
        """出てきた室名を、出た順のまま重複を除いて返す。"""
        seen: list[str] = []
        for row in self.rows:
            if row.room and row.room not in seen:
                seen.append(row.room)
        return tuple(seen)


def read_finish_schedules(
    pdf_path: str | Path, page_index: int
) -> list[FinishSchedule]:
    """ページの中の内装仕上表を読む。無ければ空のリスト。

    **この表だけでは数量にならない。** 室の輪郭を取る実装がこのリポジトリに
    無いので、面積は出せない(モジュール冒頭)。ここで作るのは
    「室名 → 部位 → 仕上」の対応だけである。
    """
    out: list[FinishSchedule] = []
    for table in find_tables(pdf_path, page_index):
        schedule = _as_finish_schedule(table)
        if schedule is not None:
            out.append(schedule)
    return out


def _as_finish_schedule(table: TableRegion) -> FinishSchedule | None:
    header = _find_header(table, FINISH_COLUMN_SYNONYMS, required="室名")
    if header is None:
        return None
    header_row, columns = header

    if "部位" in columns and "仕上" in columns:
        return _long_finish_schedule(table, header_row, columns)

    part_columns = {
        index: table.rows[header_row][index].text.strip()
        for index in range(table.col_count)
        if index != columns["室名"]
        and _match_role(table.rows[header_row][index].text) in FINISH_PART_HEADINGS
    }
    if len(part_columns) >= 2:
        return _wide_finish_schedule(table, header_row, columns, part_columns)
    return None


def _base_column_group(
    table: TableRegion, header_row: int, columns: dict[str, int]
) -> tuple[int, ...]:
    """下地の欄に当たる列を、左から右の順で返す。無ければ空。

    **見出しが空の列を、すぐ左の「下地」の続きとみなす。** 実図面の内装仕上表
    には「下 地 類」の見出しが 2 列にまたがって書かれているものがあり、
    そこを 1 列だけ読むと、区分の決め手になる ``既存`` ``交換`` の欄を
    丸ごと落とす。**広げるのは見出しが空の列だけ**で、ほかの役割に当たった
    列や、文字のある見出しの列では止める。
    """
    if "下地" not in columns:
        return ()
    taken = set(columns.values())
    group = [columns["下地"]]
    index = columns["下地"] + 1
    while index < table.col_count:
        if index in taken:
            break
        if _normalize(table.rows[header_row][index].text):
            break
        group.append(index)
        index += 1
    return tuple(group)


def _long_finish_schedule(
    table: TableRegion, header_row: int, columns: dict[str, int]
) -> FinishSchedule:
    rows: list[FinishScheduleRow] = []
    skipped: list[SkippedRow] = []
    carried: ScheduleCell | None = None
    page_number = table.page_index + 1

    base_columns = _base_column_group(table, header_row, columns)
    if base_columns:
        # **判定に使うのはいちばん右。** 工法が左、材料や状態が右に来る。
        columns = dict(columns) | {"下地": base_columns[-1]}

    for row_index in range(header_row + 1, table.row_count):
        cells = table.rows[row_index]
        part = cells[columns["部位"]].text.strip() or None
        finish = cells[columns["仕上"]].text.strip() or None
        base = cells[base_columns[-1]].text.strip() or None if base_columns else None
        base_extra = tuple(
            cells[index].text.strip() for index in base_columns[:-1]
        ) if base_columns else ()
        room_cell = cells[columns["室名"]]
        room, room_source, carried, notes = _resolve_room(
            room_cell, carried, page_number
        )

        if part is None and finish is None:
            skipped.append(
                SkippedRow(
                    row_index=row_index,
                    texts=tuple(cell.text for cell in cells),
                    reason="部位も仕上も空のため行として読まなかった",
                )
            )
            continue

        refs: dict[str, ScheduleCell] = {}
        if carried is not None:
            refs["室名"] = carried
        if part is not None:
            refs["部位"] = _ref(cells[columns["部位"]], page_number, part)
        if finish is not None:
            refs["仕上"] = _ref(cells[columns["仕上"]], page_number, finish)
        if base is not None:
            refs["下地"] = _ref(cells[columns["下地"]], page_number, base)

        rows.append(
            FinishScheduleRow(
                room=room,
                part=part,
                finish=finish,
                base=base,
                base_extra=base_extra,
                row_texts=tuple(
                    cell.text.strip() for cell in cells if cell.text.strip()
                ),
                room_source=room_source,
                page_number=page_number,
                cells=refs,
                notes=notes,
                table_rect_pt=table.rect_pt,
                caption=table.caption,
                row_index=row_index,
            )
        )

    return FinishSchedule(
        page_index=table.page_index,
        rect_pt=table.rect_pt,
        layout="long",
        columns=dict(columns),
        rows=tuple(rows),
        skipped_rows=tuple(skipped),
        caption=table.caption,
        base_columns=base_columns,
    )


def _wide_finish_schedule(
    table: TableRegion,
    header_row: int,
    columns: dict[str, int],
    part_columns: dict[int, str],
) -> FinishSchedule:
    rows: list[FinishScheduleRow] = []
    skipped: list[SkippedRow] = []
    carried: ScheduleCell | None = None
    page_number = table.page_index + 1

    for row_index in range(header_row + 1, table.row_count):
        cells = table.rows[row_index]
        room_cell = cells[columns["室名"]]
        room, room_source, carried, notes = _resolve_room(
            room_cell, carried, page_number
        )

        produced = 0
        for column_index in sorted(part_columns):
            finish = cells[column_index].text.strip()
            if not finish:
                # 仕上げが書かれていない部位を「仕上げ無し」として作らない。
                continue
            refs: dict[str, ScheduleCell] = {}
            if carried is not None:
                refs["室名"] = carried
            refs["部位"] = _ref(
                table.rows[header_row][column_index], page_number, part_columns[column_index]
            )
            refs["仕上"] = _ref(cells[column_index], page_number, finish)
            rows.append(
                FinishScheduleRow(
                    room=room,
                    part=part_columns[column_index],
                    finish=finish,
                    row_texts=tuple(
                        cell.text.strip() for cell in cells if cell.text.strip()
                    ),
                    room_source=room_source,
                    page_number=page_number,
                    cells=refs,
                    notes=notes,
                    table_rect_pt=table.rect_pt,
                    caption=table.caption,
                    row_index=row_index,
                )
            )
            produced += 1
        if produced == 0:
            skipped.append(
                SkippedRow(
                    row_index=row_index,
                    texts=tuple(cell.text for cell in cells),
                    reason="どの部位の欄にも仕上げが書かれていなかった",
                )
            )

    return FinishSchedule(
        page_index=table.page_index,
        rect_pt=table.rect_pt,
        layout="wide",
        columns=dict(columns) | {part: index for index, part in part_columns.items()},
        rows=tuple(rows),
        skipped_rows=tuple(skipped),
        caption=table.caption,
    )


def _resolve_room(
    room_cell: TableCell, carried: ScheduleCell | None, page_number: int
) -> tuple[str | None, RoomSource, ScheduleCell | None, tuple[str, ...]]:
    """室名の欄を読む。空なら上の行から引き継ぎ、**引き継いだことを残す。**"""
    text = room_cell.text.strip()
    if text:
        ref = _ref(room_cell, page_number, text)
        return text, "cell", ref, ()
    if carried is None:
        return None, "cell", None, ("室名が読めず、引き継げる室名も無かった",)
    if room_cell.merged_with_above:
        return carried.source_text, "merged_cell", carried, ()
    return (
        carried.source_text,
        "blank_carried_forward",
        carried,
        (
            f"室名の欄が空欄(罫線では区切られている)だったため、"
            f"上の行の {carried.source_text!r} を引き継いだ",
        ),
    )


# ---------------------------------------------------------------------------
# 見出しの照合
# ---------------------------------------------------------------------------


def _find_header(
    table: TableRegion, synonyms: dict[str, frozenset[str]], *, required: str
) -> tuple[int, dict[str, int]] | None:
    """見出しの行と、役割 → 列番号の対応を探す。

    **`required` の列が無ければ None。** 上から `HEADER_SEARCH_ROWS` 行まで
    見て、役割が一番多く当たった行を見出しとする。
    """
    best: tuple[int, int, dict[str, int]] | None = None
    for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
        columns: dict[str, int] = {}
        for col_index, cell in enumerate(table.rows[row_index]):
            role = _role_of(cell.text, synonyms)
            # 同じ役割が 2 列に当たったら、左の列を採る。右を上書きすると
            # 「幅」と「有効幅」が並ぶ表で後ろが勝ってしまう。
            if role is not None and role not in columns:
                columns[role] = col_index
        if required not in columns:
            continue
        if best is None or len(columns) > best[1]:
            best = (row_index, len(columns), columns)
    if best is None:
        return None
    return best[0], best[2]


def _role_of(text: str, synonyms: dict[str, frozenset[str]]) -> str | None:
    key = _match_role(text)
    if not key:
        return None
    for role, names in synonyms.items():
        if key in names:
            return role
    return None


def _match_role(text: str) -> str:
    """見出しを照合用の形にする。括弧の中(単位など)は落とす。"""
    normalized = _normalize(text)
    normalized = _PAREN_RE.sub("", normalized)
    return normalized.strip().lower()


def _normalize(text: str) -> str:
    """NFKC 正規化して空白を落とす。``ｍｍ`` → ``mm``、``１`` → ``1``。

    **正規化した文字列は照合にしか使わない。** 根拠として残す
    `source_text` は図面に印字されたままである。
    """
    return "".join(unicodedata.normalize("NFKC", text).split())



# ---------------------------------------------------------------------------
# 円弧では拾えない建具
# ---------------------------------------------------------------------------

#: 円弧を描かないので `find_door_arcs()` では**原理的に**拾えない種別の書き方。
#:
#: P011 の新設建具はすべて引戸か折戸で、だから図形からの検出は 0 件だった。
#: 建具表が読めれば、この種別の建具は文字から拾える。**それがこのモジュールの
#: 一番の目的である。**
ARC_BLIND_KIND_MARKERS: tuple[str, ...] = (
    "引戸", "引き戸", "引違", "引き違", "片引", "両引", "引込", "引き込",
    "折戸", "折れ戸", "折り戸", "雨戸", "シャッター", "アコーディオン",
)

#: 円弧を描くので図形からも拾える種別の書き方。
ARC_VISIBLE_KIND_MARKERS: tuple[str, ...] = (
    "開き", "開戸", "片開", "両開", "ドア", "扉",
)


def is_arc_blind(kind: str | None) -> bool | None:
    """その種別が `find_door_arcs()` の範囲外かどうか。

    - ``True``  … 円弧を描かないので図形からは拾えない(引戸・折戸など)
    - ``False`` … 円弧を描くので図形からも拾える(開き戸など)
    - ``None``  … **種別が読めていない、または知らない書き方。**

    知らない種別を ``False``(図形で拾える)に倒さないこと。倒すと
    「図形で拾えているはず」という前提で見落としが隠れる。
    """
    if not kind:
        return None
    normalized = _normalize(kind)
    if any(marker in normalized for marker in ARC_BLIND_KIND_MARKERS):
        return True
    if any(marker in normalized for marker in ARC_VISIBLE_KIND_MARKERS):
        return False
    return None
