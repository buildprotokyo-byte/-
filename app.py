"""図面の PDF を 1 つ渡すと、見積の行の候補の一覧が出る入口(K-27)。

使い方::

    python app.py <図面PDF> --case-id P011 --out 結果.json \\
        [--legend-table 対照表.json] [--human-input 人の入力.json] [--rules 規則.json]

なぜこれを作るのか
------------------
部品はそろっていたが、**図面から見積の行まで一度も端から端まで通していなかった。**
68 周まわってテストは通るのに、自動で出た見積の行は 0 行のままで、どこで
落ちているのかを誰も数えられなかった。ここは精度を上げるためのものではなく、
**いまどこに立っているかを数えるための 1 本**である。基準は
`docs/k27_one_pass_criteria.md`(通す前にコミット)。

7 つの段
--------
1. 台帳を作る(`candidate_ledger`)… 線と閉領域の候補。**下の段へ繋がる道はまだ無い。**
2. 文字と表を読む(`read_drawing`)… 今までの本番の入口そのもの
3. 仕上表から工事の有無(`finish_schedule_scope`)
4. 凡例から記号(`legend_lookup`)… 対照表はリポジトリの外から渡す
5. 人の入力(室の寸法・記号の個数)
6. 数量を作る
7. 見積の行に対応づける(規則ファイルがあれば `map_quantities`)

この入口がしないこと
--------------------
- **何も確定させない。** 入口の判定と当てはめの確定行を数え、1 件でもあれば
  `auto_confirmed` に出す(K-27 の止める条件)。判定のしかたには触らない。
- **数字を作らない。** 数量が決まらない行は数量を空のまま出す。
- **正解を読まない。** 採点は別のスクリプトで、この出力と正解だけを見て行う。
- **段を飛ばさない。** 撤去か新設かが決まらなかったもの(理解を通らなかったもの)は
  見積の行にしない。落ちた数は段ごとに数えて出す。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from estimating.from_room_dimensions import KIND_FLOOR_AREA, KIND_PERIMETER, KIND_WALL_AREA
from intake.drawing_room_dimensions import METHOD_DRAWING_ROOM_DIMENSIONS

# 段の名前。K-27 の言葉をそのまま使う。
STAGE_RECOGNIZE = "認識"
STAGE_READ = "読む"
STAGE_UNDERSTAND = "理解"
STAGE_ASSEMBLE = "組み立て"
STAGES = (STAGE_RECOGNIZE, STAGE_READ, STAGE_UNDERSTAND, STAGE_ASSEMBLE)

PATH_FINISH = "仕上表"
PATH_LEGEND = "凡例の記号"
PATH_INTAKE = "入口の図形"
PATH_HUMAN = "人の入力"

#: 仕上表の部位 → 人の入力から作る数量の種類。**ここに無い部位には数量を付けない。**
#: 天井は床と同じ広さとみなす(平らな天井の一般則)。そうしたことを行に注記する。
#:
#: **名前は作る側(`estimating.from_room_dimensions`)の定数から取る。写さない。**
#: 2026-09-25 まで巾木は `周長` と書き写してあり、作る側の `室の周長` と
#: 食い違っていたので、人が寸法を入れても巾木の行は永久に空だった(周28、K-36)。
FINISH_PART_QUANTITY: dict[str, tuple[str, str, str | None]] = {
    "床": (KIND_FLOOR_AREA, "㎡", None),
    "天井": (KIND_FLOOR_AREA, "㎡", "天井の面積は床面積と同じとみなした(平らな天井の一般則)"),
    "壁": (KIND_WALL_AREA, "㎡", None),
    "巾木": (KIND_PERIMETER, "m", None),
}

#: 行にしなかった数量に付ける印(K-36)。**捨てずに残し、なぜ行でないかを書く。**
MARK_NO_RULES = "規則なし"
"""規則ファイルが渡されなかったので当てはめていない。"""
MARK_NO_MATCHING_RULE = "規則が当たらない"
"""規則ファイルはあったが、この数量に当たる規則が無かった。"""

#: 仕上表の行の科目(K-36 改訂版、仮の判断。`docs/provisional_decisions.md` 7 節)。
#: 撤去は解体・撤去工事、それ以外(新設・改修)は内装仕上工事に置く。
#: 下地からのやり替えを木工・大工工事に分けるかは、まだ決めていない。
FINISH_KAMOKU_REMOVAL = "解体・撤去工事"
FINISH_KAMOKU_OTHER = "内装仕上工事"

#: 行にしない工事区分(工事が無い)。
#: 「区分不明」は撤去か新設かが決まっていない(理解を通っていない)ので行にしない。
NO_WORK_KINDS = frozenset({"既存のまま"})
UNDECIDED_KINDS = frozenset({"区分不明"})

#: 凡例の色が名乗る意味のうち、工事が無いもの。**凡例の文をそのまま比べる。**
NO_WORK_COLOUR_MEANINGS = frozenset({"既存のまま"})

#: 繋がなかった部品と、その理由。**繋げなかったことを黙らせない。**
NOT_CONNECTED: tuple[tuple[str, str], ...] = (
    (
        "知識の道(axes/reading・knowledge)",
        "K-27 で知識 26 件の採否が止まっているため繋いでいない"
        "(入口の候補は docs/k22_knowledge_route_report.md)",
    ),
    (
        "キラークエスチョン(killer_question)",
        "問いを選ぶ経路で、数量も行も作らない。一本通すのに要らないので繋いでいない",
    ),
    (
        "台帳 → 下の段",
        "台帳の線と閉領域を名前や数量に変える部品がまだ無い。台帳は数えるだけ",
    ),
)


def _nfkc(text: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


# ---------------------------------------------------------------------------
# 出力の形
# ---------------------------------------------------------------------------


@dataclass
class EstimateLine:
    """見積の行の候補 1 つ。**確定ではない。**"""

    work_item: str
    place: str
    quantity: float | None
    unit: str
    path: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    code: str | None = None
    category: str | None = None
    quantity_range: tuple[float, float] | None = None
    waits_for_human: bool = False
    """数量が人の入力を待っている(入力があれば埋まる)行か。"""
    spec: str = ""
    """摘要。材種・材質・工法など、単価に対応する条件(K-36 改訂版)。**読めたものだけ。**"""

    def as_answer_row(self, number: int) -> dict[str, Any]:
        """読み方の比較実験と同じ出力の形の 1 行(採点をそのまま当てるため)。"""
        return {
            "番号": number,
            "工事項目": self.work_item,
            "区分": self.category or "",
            "場所": self.place,
            "数量": self.quantity,
            "単位": self.unit,
            "根拠": self.evidence,
            "確かさ": "候補",
            "出どころ": "直接読んだ" if self.path != PATH_HUMAN else "人の入力",
            "道": self.path,
            "符号": self.code,
            "数量の幅": list(self.quantity_range) if self.quantity_range else None,
            "人の入力待ち": self.waits_for_human,
            "摘要": self.spec,
            "備考": " / ".join(self.notes),
        }


@dataclass
class OnePassResult:
    case_id: str
    lines: list[EstimateLine]
    stages: dict[str, dict[str, int]]
    ledger: dict[str, Any]
    auto_confirmed: dict[str, int]
    gaps: list[str]
    not_connected: list[dict[str, str]]
    timings: dict[str, float]
    extras: dict[str, Any] = field(default_factory=dict)
    drawing_rooms: dict[str, Any] | None = None
    """図面の寸法から組んだ室の状態(K-37)。渡されなかったら ``None``。"""

    kept_quantities: list[dict[str, Any]] = field(default_factory=list)
    """行にしなかった入口の数量。**捨てずに印を付けて残す**(K-36)。

    2026-09-25 までは、規則ファイルが無いと入口の数量は丸ごと落ち、
    `gaps` に 1 行残るだけだった(周31。P011 では 2,887 件)。
    ここに残すのは数量そのもので、**見積の行ではない。確定もしない。**
    """

    def breakdown(self):  # noqa: ANN201
        """行を種目・科目・中科目・細目に組んだもの(K-36 改訂版 3 節)。

        **科目は行にあるものだけ。**科目の無い行は「科目未定」に集まる。
        単価はまだ無いので、金額は空のまま出る。
        同じ工事の行は 1 つの細目にまとまり、場所ごとの内訳を持つ。数量の無い場所は
        「未取得」で、未取得があるうちは細目の合計を出さない(K-38 原則11)。
        """
        from estimating.breakdown import build_breakdown

        return build_breakdown(
            {
                "科目": line.category,
                "工事項目": line.work_item,
                "場所": line.place,
                "摘要": line.spec,
                "数量": line.quantity,
                "単位": line.unit,
            }
            for line in self.lines
        )

    @property
    def auto_confirmed_total(self) -> int:
        return sum(self.auto_confirmed.values())

    def stage_totals(self) -> dict[str, int]:
        return {
            stage: sum(counts.get(stage, 0) for counts in self.stages.values())
            for stage in STAGES
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "案件": self.case_id,
            "工事項目": [line.as_answer_row(i) for i, line in enumerate(self.lines, 1)],
            "段ごとの件数": {"道ごと": self.stages, "合計": self.stage_totals()},
            "台帳": self.ledger,
            "自動確定": {**self.auto_confirmed, "合計": self.auto_confirmed_total},
            "足りないもの": self.gaps,
            "繋げなかった部品": self.not_connected,
            "内訳書": self.breakdown().as_dict(),
            "行にしなかった数量": {
                "件数": len(self.kept_quantities),
                "印ごと": dict(Counter(row["印"] for row in self.kept_quantities)),
                "数量": self.kept_quantities,
            },
            "図面の寸法から組んだ室": self.drawing_rooms,
            "かかった秒数": self.timings,
            "そのほか": self.extras,
        }


# ---------------------------------------------------------------------------
# 5. 人の入力
# ---------------------------------------------------------------------------


def _drawing_room_result(pdf_path: Path, assignments_path: Path):  # noqa: ANN202
    """AI の対応づけ(寸法の id だけ)と、機械が読んだ寸法から室を組む(K-37)。

    読むのは、対応づけが指しているページと天井高のページだけ。
    """
    import pymupdf

    from axes.image_axis.pdf_dimensions import read_dimensions
    from intake.drawing_room_dimensions import dimension_ids, load_assignments, rooms_from_drawing

    assignments = load_assignments(json.loads(assignments_path.read_text(encoding="utf-8")))
    pages: set[int] = set()
    for a in assignments:
        for dim_id in a.width_ids + a.length_ids:
            head = dim_id.split("-", 1)[0]
            if head.startswith("P") and head[1:].isdigit():
                pages.add(int(head[1:]))
        if a.ceiling_page is not None:
            pages.add(int(a.ceiling_page))
    with pymupdf.open(pdf_path) as doc:
        pages = {p for p in pages if 1 <= p <= doc.page_count}
        texts = {p: doc.load_page(p - 1).get_text("text") for p in sorted(pages)}
    readings = dimension_ids(read_dimensions(pdf_path, p - 1) for p in sorted(pages))
    return rooms_from_drawing(assignments, readings, texts)



@dataclass(frozen=True)
class HumanInput:
    rooms: tuple[Any, ...] = ()
    symbols: tuple[Any, ...] = ()


def load_human_input(path: str | Path | None) -> HumanInput:
    """人の入力を読む。**無ければ空。既定値で埋めない。**

    形::

        {"rooms": [{"room_name": "洋室1", "length_mm": 3600, "width_mm": 2700,
                    "ceiling_height_mm": 2400, "area_basis": "...", "entered_by": "..."}],
         "symbols": [{"symbol_name": "コンセント", "count": 12, "unit": "箇所"}]}
    """
    from intake.room_dimensions import RoomDimension
    from intake.symbol_counts import SymbolCount

    if path is None:
        return HumanInput()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rooms = tuple(RoomDimension(**row) for row in payload.get("rooms", ()))
    symbols = tuple(SymbolCount(**row) for row in payload.get("symbols", ()))
    return HumanInput(rooms=rooms, symbols=symbols)


# ---------------------------------------------------------------------------
# 3. 仕上表 → 工事の有無 → 行
# ---------------------------------------------------------------------------


def _finish_lines(
    pdf_path: Path, pages: Sequence[int], room_quantities: Mapping[str, Any]
) -> tuple[list[EstimateLine], dict[str, int], dict[str, Any]]:
    from axes.image_axis.schedule_tables import read_finish_schedules
    from estimating.finish_schedule_scope import READING_QUESTION, assign_finish_schedule_scope

    counts = dict.fromkeys(STAGES, 0)
    lines: list[EstimateLine] = []
    readings: Counter[str] = Counter()
    questions = 0
    for page_number in pages:
        for schedule in read_finish_schedules(pdf_path, page_number - 1):
            result = assign_finish_schedule_scope(schedule)
            questions += len(result.questions)
            # 認識: 文字のある行。**読めた行 + 割り当てられなかった行。**
            counts[STAGE_RECOGNIZE] += len(result.assignments) + len(result.unassigned)
            for assignment in result.assignments:
                readings[assignment.reading] += 1
                if assignment.room and assignment.part:
                    counts[STAGE_READ] += 1
                else:
                    continue
                if assignment.reading == READING_QUESTION:
                    continue
                counts[STAGE_UNDERSTAND] += 1
                for item in assignment.items:
                    if item.work_kind in NO_WORK_KINDS or item.work_kind in UNDECIDED_KINDS:
                        continue
                    lines.append(
                        _finish_line(assignment, item, schedule.page_number, room_quantities)
                    )
    counts[STAGE_ASSEMBLE] = len(lines)
    return lines, counts, {"仕上表の読み": dict(readings), "仕上表の問い": questions}


def _finish_line(assignment, item, page_number: int, room_quantities) -> EstimateLine:  # noqa: ANN001
    part = assignment.part or ""
    words = [part]
    # 撤去の行に仕上の名前を書かない。**仕上の欄は新しい仕上の名前である。**
    if item.work_kind != "撤去" and assignment.finish:
        words.append(assignment.finish)
    words.append(item.work_kind)
    notes = [assignment.reason]
    quantity = None
    quantity_range = None
    unit = "㎡"
    waits = False
    spec = FINISH_PART_QUANTITY.get(_nfkc(part))
    if spec is not None:
        kind, unit, note = spec
        found = room_quantities.get((kind, _nfkc(assignment.room)))
        if found is None:
            waits = True
            notes.append("数量は人の入力(室の寸法)を待っている")
        else:
            low, high = found.value_range
            quantity_range = (low, high)
            quantity = round((low + high) / 2, 2)
            if note:
                notes.append(note)
            if found.method_id == METHOD_DRAWING_ROOM_DIMENSIONS:
                notes.append(
                    "数量は図面の寸法(AIが室に対応づけ)から: "
                    + str(found.provenance.get("entered_by", ""))
                )
    else:
        notes.append("この部位の数量の作り方をまだ持っていない")
    return EstimateLine(
        work_item=" ".join(w for w in words if w),
        place=assignment.room or "",
        quantity=quantity,
        unit=unit,
        path=PATH_FINISH,
        evidence=[
            {
                "ページ": page_number,
                "表": f"仕上表の {assignment.row_index} 行目",
                "読んだ文字": "／".join(
                    x for x in (assignment.room, part, assignment.base, assignment.finish) if x
                ),
            }
        ],
        notes=notes,
        quantity_range=quantity_range,
        waits_for_human=waits,
        category=FINISH_KAMOKU_REMOVAL if item.work_kind == "撤去" else FINISH_KAMOKU_OTHER,
        spec=" / ".join(
            f"{label}: {value}"
            for label, value in (
                ("仕上", assignment.finish if item.work_kind != "撤去" else None),
                ("下地", assignment.base),
            )
            if value
        ),
    )


# ---------------------------------------------------------------------------
# 4. 凡例 → 記号 → 行
# ---------------------------------------------------------------------------


def _colour_of(packed: int) -> tuple[float, float, float]:
    return (
        round(((packed >> 16) & 0xFF) / 255.0, 4),
        round(((packed >> 8) & 0xFF) / 255.0, 4),
        round((packed & 0xFF) / 255.0, 4),
    )


def _drawing_words(page) -> list[tuple[str, tuple[float, ...] | None]]:  # noqa: ANN001
    """表題欄の外の語と、その語が刷られた色。

    表題欄の線引きは `benchmarks/page_geometry.py` の 1 か所から読む(K-26 2 番。
    写すと直し漏れが残る)。
    """
    from axes.image_axis.legend_lookup import normalize
    from benchmarks import page_geometry

    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                spans.append((span["bbox"], int(span["color"])))
    out: list[tuple[str, tuple[float, ...] | None]] = []
    for w in page.get_text("words"):
        if not normalize(w[4]) or not page_geometry.in_drawing(page, w[:4]):
            continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        colour = None
        for (x0, y0, x1, y1), packed in spans:
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                colour = _colour_of(packed)
                break
        out.append((w[4], colour))
    return out


def _legend_lines(
    pdf_path: Path, table_path: Path | None
) -> tuple[list[EstimateLine], dict[str, int], dict[str, Any]]:
    counts = dict.fromkeys(STAGES, 0)
    if table_path is None:
        return [], counts, {"凡例": "対照表が渡されていないので、この段は動かしていない"}

    import pymupdf

    from axes.image_axis.legend_lookup import (
        KIND_EQUIPMENT,
        KIND_WORK,
        LegendTable,
        match_line_colors,
        match_marks,
        summarize,
    )

    table = LegendTable.load(table_path)
    legend_pages = {
        int(row["source_page"])
        for rows in (table.work_marks, table.symbols, table.line_colors)
        for row in rows
        if "source_page" in row
    }
    words: list[tuple[int, str, tuple[float, ...] | None]] = []
    with pymupdf.open(pdf_path) as doc:
        for number in range(1, doc.page_count + 1):
            if number in legend_pages:
                continue
            words.extend((number, text, colour) for text, colour in _drawing_words(doc[number - 1]))

    counts[STAGE_RECOGNIZE] = len(words)
    matches = match_marks([text for _, text, _ in words], table)
    counts[STAGE_READ] = sum(1 for m in matches if m.matched)

    grouped: dict[tuple[str, str], list[int]] = {}
    work_marks = 0
    colour_undecided = 0
    for (page_number, _, colour), match in zip(words, matches):
        if not match.matched:
            continue
        if match.kind == KIND_WORK:
            # 工事の印そのもの。**何を撤去するかは決まらない**ので行にはしない。
            work_marks += 1
            counts[STAGE_UNDERSTAND] += 1
            continue
        if match.kind != KIND_EQUIPMENT or colour is None:
            colour_undecided += 1
            continue
        (colour_match,) = match_line_colors([colour], table)
        if not colour_match.matched:
            colour_undecided += 1
            continue
        counts[STAGE_UNDERSTAND] += 1
        meaning = colour_match.meaning or ""
        if meaning in NO_WORK_COLOUR_MEANINGS:
            continue
        grouped.setdefault((match.display_name, meaning), []).append(page_number)

    lines: list[EstimateLine] = []
    for (name, meaning), pages in sorted(grouped.items()):
        label = meaning[:-3] if meaning.endswith("を表す") else meaning
        lines.append(
            EstimateLine(
                work_item=f"{name} {label}",
                place="",
                quantity=float(len(pages)),
                unit="箇所",
                path=PATH_LEGEND,
                evidence=[
                    {"ページ": p, "読んだ文字": "凡例の対照表と完全一致した記号"}
                    for p in sorted(set(pages))
                ],
                notes=[
                    f"図面の側で同じ記号が {len(pages)} 回出た(同じ記号を 2 枚の図に描いていれば 2 回数える)",
                    "色の意味は凡例が名乗っているもの",
                ],
            )
        )
    counts[STAGE_ASSEMBLE] = len(lines)
    summary = summarize(matches)
    return lines, counts, {
        "凡例のページ": sorted(legend_pages),
        "名前が付いた語": summary.named,
        "名前が付かなかった語": summary.unknown,
        "名前が付かなかった理由": summary.by_reason,
        "工事の印": work_marks,
        "色の意味が決まらなかった記号": colour_undecided,
        "行に入った記号の語": sum(len(p) for p in grouped.values()),
    }


# ---------------------------------------------------------------------------
# 2・6・7. 今の入口 → 数量 → 規則で行へ
# ---------------------------------------------------------------------------


def _intake_counts(result) -> dict[str, int]:  # noqa: ANN001
    counts = dict.fromkeys(STAGES, 0)
    counts[STAGE_RECOGNIZE] = len(result.findings)
    for finding in result.findings:
        kind = finding.target.split("::", 1)[0]
        # 名前の無い記号群(「記号::群N」)は「何かある」までで、何であるかは言えていない。
        if kind == "記号":
            continue
        counts[STAGE_READ] += 1
        meaning = getattr(finding, "meaning", None)
        if meaning is not None and meaning.phase != "不明":
            counts[STAGE_UNDERSTAND] += 1
    return counts


def _kept_quantity(quantity, mark: str) -> dict[str, Any]:  # noqa: ANN001
    """行にしなかった数量 1 件を、印つきで残す形にする。**値は作り直さない。**"""
    low, high = quantity.value_range
    return {
        "印": mark,
        "対象": quantity.target,
        "数量の範囲": [low, high],
        "単位": quantity.unit,
        "手法": quantity.method_id,
        "由来": quantity.derivation,
    }


def _mapped_lines(draft) -> list[EstimateLine]:  # noqa: ANN001
    lines: list[EstimateLine] = []
    for mapping in draft.mapping.mappings:
        for line in mapping.lines:
            value = line.value_range
            quantity = round((value[0] + value[1]) / 2, 2)
            lines.append(
                EstimateLine(
                    work_item=line.work_item,
                    place="",
                    quantity=quantity,
                    unit=line.unit,
                    path=PATH_HUMAN if line.axis_id == "human" else PATH_INTAKE,
                    code=line.code,
                    category=line.major_category,
                    quantity_range=tuple(value),
                    notes=[f"規則 {line.rule_id} で当てはめた"],
                )
            )
    return lines


def run(
    pdf_path: str | Path,
    *,
    case_id: str,
    answers_path: str | Path,
    legend_table: str | Path | None = None,
    human_input: str | Path | None = None,
    rules: str | Path | None = None,
    build_ledger_stage: bool = True,
    drawing_rooms: str | Path | None = None,
) -> OnePassResult:
    """7 つの段を順に動かす。**途中の段が空でも止めずに最後まで通す。**"""
    from estimating.from_intake import quantities_from_intake
    from estimating.from_room_dimensions import ORIGIN_DRAWING, quantities_from_room_dimensions
    from estimating.from_symbol_counts import quantities_from_symbol_counts
    from intake.drawing_intake import IntakeConfig, read_drawing

    pdf = Path(pdf_path)
    timings: dict[str, float] = {}
    gaps: list[str] = []
    stages: dict[str, dict[str, int]] = {}

    # 1. 台帳
    ledger_summary: dict[str, Any] = {"動かした": False}
    if build_ledger_stage:
        from axes.image_axis.candidate_ledger import build_ledger

        started = time.perf_counter()
        ledger = build_ledger(pdf)
        timings["1 台帳"] = round(time.perf_counter() - started, 1)
        ledger_summary = {
            "動かした": True,
            "ページ": len(ledger.pages),
            "線の候補": sum(len(p.lines) for p in ledger.pages),
            "閉領域の候補": sum(len(p.regions) for p in ledger.pages),
            "小さな輪郭": sum(len(p.small) for p in ledger.pages),
            "ページの関門": ledger.gate_summary(),
            "下の段へ繋がる道": "無い(数えるだけ)",
        }

    # 2. 文字と表を読む(今の本番の入口)
    started = time.perf_counter()
    intake = read_drawing(
        IntakeConfig(case_id=case_id, pdf_path=pdf, answers_path=Path(answers_path))
    )
    timings["2 文字と表"] = round(time.perf_counter() - started, 1)

    # 5. 人の入力
    human = load_human_input(human_input)
    room_result = quantities_from_room_dimensions(human.rooms)
    symbol_result = quantities_from_symbol_counts(human.symbols)
    gaps.extend(room_result.gaps)
    gaps.extend(symbol_result.gaps)
    room_quantities: dict[tuple[str, str], Any] = {}
    for q in room_result.quantities:
        kind, _, room = q.target.partition("::")
        room_quantities[(kind, _nfkc(room))] = q

    # 5'. 図面の寸法から組んだ室(K-37)。**人の入力がある室は人の入力のまま**(混ぜない)。
    drawing_summary: dict[str, Any] | None = None
    if drawing_rooms is not None:
        drawing_result = _drawing_room_result(pdf, Path(drawing_rooms))
        drawing_summary = drawing_result.summary()
        drawn = quantities_from_room_dimensions(drawing_result.rooms, origin=ORIGIN_DRAWING)
        gaps.extend(drawing_result.gaps)
        gaps.extend(drawn.gaps)
        for q in drawn.quantities:
            kind, _, room = q.target.partition("::")
            room_quantities.setdefault((kind, _nfkc(room)), q)

    # 3. 仕上表
    started = time.perf_counter()
    finish_pages = sorted({row.page_number for row in intake.finish_schedule_rows})
    finish_lines, stages[PATH_FINISH], finish_extra = _finish_lines(pdf, finish_pages, room_quantities)
    timings["3 仕上表"] = round(time.perf_counter() - started, 1)

    # 4. 凡例
    started = time.perf_counter()
    legend_lines, stages[PATH_LEGEND], legend_extra = _legend_lines(
        pdf, Path(legend_table) if legend_table else None
    )
    timings["4 凡例"] = round(time.perf_counter() - started, 1)

    # 6. 数量 と 7. 規則で行へ
    stages[PATH_INTAKE] = _intake_counts(intake)
    quantities = list(quantities_from_intake(intake)) + list(symbol_result.quantities)
    mapped: list[EstimateLine] = []
    kept: list[dict[str, Any]] = []
    settled_lines = 0
    if rules is not None:
        from estimating.pipeline import build_estimate_draft_from_quantities
        from estimating.rules import load_rules

        draft = build_estimate_draft_from_quantities(quantities, load_rules(rules))
        settled_lines = len(draft.settled_lines)
        mapped = _mapped_lines(draft)
        gaps.extend(draft.gaps())
        kept = [
            _kept_quantity(m.quantity, MARK_NO_MATCHING_RULE) for m in draft.mapping.unmapped()
        ]
    else:
        gaps.append(
            f"[規則ファイルが無い] 入口の数量 {len(quantities)} 件は見積の行に当てはめていない"
            "(この案件の規則ファイルがまだ無い。合成の見本は実案件に使わない)。"
            "**数量は捨てずに「行にしなかった数量」に「規則なし」の印を付けて残した**"
        )
        kept = [_kept_quantity(q, MARK_NO_RULES) for q in quantities]
    stages[PATH_INTAKE][STAGE_ASSEMBLE] = sum(1 for line in mapped if line.path == PATH_INTAKE)
    human_lines = [line for line in mapped if line.path == PATH_HUMAN]
    stages[PATH_HUMAN] = {
        STAGE_RECOGNIZE: len(human.rooms) + len(human.symbols),
        STAGE_READ: len(human.rooms) + len(human.symbols),
        STAGE_UNDERSTAND: 0,
        STAGE_ASSEMBLE: len(human_lines),
    }
    if human.symbols and rules is None:
        gaps.append("[規則ファイルが無い] 人が数えた記号は見積の行に当てはめていない")

    auto_confirmed = {
        "入口の判定で確定": sum(1 for d in intake.decisions if d.confirmed),
        "当てはめで確定した行": settled_lines,
    }
    lines = finish_lines + legend_lines + mapped
    return OnePassResult(
        case_id=case_id,
        lines=lines,
        stages=stages,
        ledger=ledger_summary,
        auto_confirmed=auto_confirmed,
        gaps=gaps,
        not_connected=[{"部品": name, "理由": why} for name, why in NOT_CONNECTED],
        timings=timings,
        kept_quantities=kept,
        drawing_rooms=drawing_summary,
        extras={
            **finish_extra,
            **legend_extra,
            "入口の読み": len(intake.findings),
            "入口の数量": len(quantities),
            "入口の判定": len(intake.decisions),
            "建具表の行": len(intake.door_schedule_rows),
            "仕上表の行(入口)": len(intake.finish_schedule_rows),
            "人の入力待ちの行": sum(1 for line in lines if line.waits_for_human),
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="図面 PDF から見積の行の候補の一覧を出す(K-27)")
    parser.add_argument("pdf", help="図面の PDF")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--out", required=True, help="結果の JSON を書く先")
    parser.add_argument("--answers", default=None, help="問いの答えを置く JSON(無ければ作る)")
    parser.add_argument("--legend-table", default=None, help="凡例の対照表(リポジトリの外)")
    parser.add_argument("--human-input", default=None, help="人の入力(室の寸法・記号の個数)")
    parser.add_argument(
        "--drawing-rooms", default=None, help="図面の寸法の id を室に対応づけたもの(K-37)"
    )
    parser.add_argument("--rules", default=None, help="当てはめの規則ファイル(リポジトリの外)")
    parser.add_argument("--no-ledger", action="store_true", help="台帳の段を飛ばす")
    args = parser.parse_args(argv)

    out = Path(args.out)
    result = run(
        args.pdf,
        case_id=args.case_id,
        answers_path=args.answers or out.with_suffix(".answers.json"),
        legend_table=args.legend_table,
        human_input=args.human_input,
        drawing_rooms=args.drawing_rooms,
        rules=args.rules,
        build_ledger_stage=not args.no_ledger,
    )
    out.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    totals = result.stage_totals()
    print(
        f"行 {len(result.lines)} / 自動確定 {result.auto_confirmed_total} / "
        + " / ".join(f"{k} {v}" for k, v in totals.items())
    )
    if result.auto_confirmed_total:
        print("**自動確定が出た。K-27 の止める条件に当たる。**", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
