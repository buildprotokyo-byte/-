"""図面の PDF を 1 つ渡すと、見積の行の候補の一覧が出る入口(K-27)。

使い方::

    python app.py <図面PDF> --case-id P011 --out 結果.json \\
        [--legend-table 対照表.json] [--human-input 人の入力.json] [--rules 規則.json] \\
        [--plan-note-pages 8] [--demolition-pages 33] \\
        [--reader {ai-file,ai-api,machine}] [--ai-reading 答案.json] \\
        [--machine-output 機械の出力.json | --no-machine-check]

読み手(K-49)
-------------
おーちゃんの決定(2026-09-26): 部分ごとに AI に替えるのをやめ、**AI が全ページを読んだ答案を
そのまま本番の入力にする。機械は測る・数える・検算するに回る。**基準は
`docs/k49_ai_reads_all_criteria.md`。

- ``--reader ai-file``(**既定**)… ``--ai-reading`` の答案(`intake/ai_reading.py` の形)の行を
  見積の行にする。数量は答案のまま、空は空のまま。決め手は「AI の読み」。確かさは上げない。
  答案が渡されなければ「読んでいない」(行 0 件、理由つき)。
- ``--reader ai-api`` … AI をその場で呼ぶ(鍵 ``ANTHROPIC_API_KEY`` と部品 ``anthropic`` が要る。
  モデルは ``AI_READING_MODEL``)。鍵が無ければ止めずに「読んでいない」。
- ``--reader machine`` … いままでの形(下の 7 つの段と線引き)。比べるために残す。

AI の側では、機械の読み(下の 7 つの段の出力)は**見積の行を作らず**、同じ工事・場所の AI の行に
「機械の検算」として並ぶ。食い違えば行を要確認にして理由を書く。**機械は答案を直さない。**

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

道(行の出どころ)
------------------
仕上表・凡例の記号・入口の図形・人の入力に加えて、K-42 で 2 つ足した
(測ったのは K-41 周 6・周 9・周 11)。どちらも**候補で、確定させない。**

- 改装平面の注記(`intake/plan_colour_notes.py`)… 表題が「改装平面…図」のページの
  赤・青の文字の行。数量は注記に数が刷られているときだけ。
- 撤去の網(`intake/demolition_hatch.py`)… 表題に「撤去」がある図のページの青い網を
  面にして、面ごとに床組の撤去・天井組の撤去の 2 行。縮尺が無ければ面積は空。
  K-45(おーちゃん 2026-09-26)から、同じ面ごとに「床組 新設」「天井組 新設」
  「天井 石膏ボード 張」の 3 行も**要確認**として出す(撤去の範囲を新設の範囲と
  推し量ったもの。確定しない)。

許容の少し外にあったものは捨てずに「候補(近いが外れ)」に理由つきで残す。

線引き(K-46)
-------------
行が「見積に載せる工事の行か」の線引きは `estimating/line_judge.py` の役 1 つで行う。
**本番の既定は AI の判定**(`--line-judgments` で AI が出した判定のファイルを渡す)。
判定が渡されていない行・判定が無い行は**落とさずに「要確認」**にする。
「工事の行ではない」とされた行は見積の行から外し、「工事の行ではないと判定した行」に
理由つきで残す。どの役が判定したか(AI/語の一覧/判定なし)は行の根拠に残る。
語の一覧(案 B)は `--line-judge words` で比べるために残してある。

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
PATH_PLAN_NOTES = "改装平面の注記"
PATH_DEMOLITION_HATCH = "撤去の網"
#: K-49: AI が図面を読んだ答案から出た行。**本番の既定。**
PATH_AI_READING = "AI の読み"

#: 読み手(K-49)。**既定は AI の答案のファイル。**機械は比べるために残す。
READER_AI_FILE = "ai-file"
READER_AI_API = "ai-api"
READER_MACHINE = "machine"
READERS = (READER_AI_FILE, READER_AI_API, READER_MACHINE)

#: 行の確かさ(`EstimateLine.certainty`)。**どちらも確定ではない。**
CERTAINTY_CANDIDATE = "候補"
CERTAINTY_NEEDS_CHECK = "要確認"

#: K-45: 撤去の網の面ごとに、要確認として出す新設の 3 行。
HATCH_INFERRED_NEW_WORKS = ("床組 新設", "天井組 新設", "天井 石膏ボード 張")
#: その 3 行の根拠。**面積は撤去の網の面積で、新設に使うのは推し量り。**
HATCH_INFERRED_BASIS = (
    "面積は撤去の網の面積。撤去の範囲を新設の範囲と推し量った。確定しない"
)

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
    extra: dict[str, Any] = field(default_factory=dict)
    """その道だけが持つ欄(K-42。例: 注記の色・同じ注記の数)。**空なら出力に何も足さない。**"""
    certainty: str = CERTAINTY_CANDIDATE
    """確かさ。既定は「候補」。推し量った行(K-45 の撤去の網から出す新設)は「要確認」。
    **どちらも確定ではない。** 確定させる口はこの行には無い。"""
    decisive: tuple[Any, ...] = ()
    """決め手(`estimating.decisive`)。**札は decisive が証拠から作る。**空なら出力に何も足さない。"""

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
            "確かさ": self.certainty,
            "出どころ": _origin_of(self.path),
            "道": self.path,
            "符号": self.code,
            "数量の幅": list(self.quantity_range) if self.quantity_range else None,
            "人の入力待ち": self.waits_for_human,
            "摘要": self.spec,
            "備考": " / ".join(self.notes),
            **({"決め手": [r.as_dict() for r in self.decisive]} if self.decisive else {}),
            **self.extra,
        }


def _origin_of(path: str) -> str:
    if path == PATH_HUMAN:
        return "人の入力"
    if path == PATH_AI_READING:
        return "AI が読んだ"
    return "直接読んだ"


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

    checks: dict[str, list[str]] = field(default_factory=dict)
    """機械の検算(K-40)。**知らせるだけで、数量は変えていない。**"""

    near_misses: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    """許容の少し外にあったもの(K-42)。**見積の行ではない。確定もしない。**理由つきで残す。"""

    not_work_lines: list[EstimateLine] = field(default_factory=list)
    """線引き(K-46)で「工事の行ではない」とされた行。**消さずに理由つきで残す。**"""

    line_judge_summary: dict[str, Any] = field(default_factory=dict)
    """線引きの役と、判定ごとの件数(K-46)。"""

    reader: str = READER_MACHINE
    """誰が読んだか(K-49)。"""

    ai_reading: dict[str, Any] | None = None
    """AI の答案のまとめ(K-49)。機械が読んだときは ``None``。"""

    machine_check: dict[str, Any] | None = None
    """機械の検算(K-49)。**機械は答案を直さない。**並べて、食い違いを要確認の理由に書くだけ。"""

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
            "読み手": self.reader,
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
            "候補(近いが外れ)": self.near_misses,
            "線引き": self.line_judge_summary,
            "工事の行ではないと判定した行": [
                line.as_answer_row(i) for i, line in enumerate(self.not_work_lines, 1)
            ],
            "検算": self.checks,
            "AI の答案": self.ai_reading,
            "機械の検算": self.machine_check,
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
    from intake.drawing_room_dimensions import (
        dimension_ids,
        load_assignments,
        load_rulers,
        other_room_names_inside,
        rooms_from_drawing,
    )

    payload = json.loads(assignments_path.read_text(encoding="utf-8"))
    assignments = load_assignments(payload)
    rulers = {r.page: r for r in load_rulers(payload)}
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
    ruler_gaps: list[str] = []

    def read(page: int):  # noqa: ANN202
        plain = read_dimensions(pdf_path, page - 1)
        ruler = rulers.get(page)
        if ruler is None:
            return plain
        reference = dimension_ids([plain]).get(ruler.reference_id)
        if reference is None or not reference.paper_distance_pt:
            ruler_gaps.append(
                f"[基準の寸法が読みに無い] ページ {page}: {ruler.reference_id} が無いので縮尺なしで読んだ"
            )
            return plain
        return read_dimensions(
            pdf_path,
            page - 1,
            ruler_mm_per_point=float(reference.value_mm) / float(reference.paper_distance_pt),
            ruler_tolerance=ruler.tolerance,
        )

    readings = dimension_ids(read(p) for p in sorted(pages))
    result = rooms_from_drawing(assignments, readings, texts)
    if ruler_gaps:
        from dataclasses import replace

        result = replace(result, gaps=tuple(ruler_gaps) + result.gaps)
    labels = _room_label_positions(pdf_path, sorted(pages), [a.room_name for a in assignments])
    return result, other_room_names_inside(assignments, readings, labels)


def _room_label_positions(
    pdf_path: Path, pages: Sequence[int], names: Sequence[str]
) -> dict[str, list[tuple[int, float, float]]]:
    """室名の文字の中心(ページ・x・y)。**図面に刷られた語と、NFKC で正規化した室名の各行が一致したものだけ。**

    仕上表の室名(例: 「キッチン/ダイニング/リビング」は 3 行)は、行ごとに探す。
    平面図の書き方(「洋室(1)」など)と一致しないものは見つからないまま(検算から漏れる)。
    """
    import pymupdf

    wanted = {name: {_nfkc(part) for part in name.split("\n") if _nfkc(part)} for name in names}
    out: dict[str, list[tuple[int, float, float]]] = {name: [] for name in names}
    with pymupdf.open(pdf_path) as doc:
        for page in pages:
            if not 1 <= page <= doc.page_count:
                continue
            for x0, y0, x1, y1, word, *_ in doc.load_page(page - 1).get_text("words"):
                text = _nfkc(word)
                for name, parts in wanted.items():
                    if text in parts:
                        out[name].append((page, (x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return out



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
    # K-45: 問いの中身も残す。**「下地は既存・仕上の欄が空欄」の行は、元は
    # 工事なしとして黙って落ちていた。** いまは推奨なしの問いとしてここに出る。
    question_texts: list[dict[str, Any]] = []
    for page_number in pages:
        for schedule in read_finish_schedules(pdf_path, page_number - 1):
            result = assign_finish_schedule_scope(schedule)
            questions += len(result.questions)
            question_texts.extend(
                {
                    "ページ": q.page_number,
                    "行": q.row_index,
                    "問い": q.question,
                    "原因": q.cause,
                    "推奨": q.recommended_answer,
                }
                for q in result.questions
            )
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
    return lines, counts, {
        "仕上表の読み": dict(readings),
        "仕上表の問い": questions,
        "仕上表の問いの中身": question_texts,
    }


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
# K-42. 改装平面の注記 → 行
# ---------------------------------------------------------------------------


def _plan_note_lines(
    pdf_path: Path, pages: Sequence[int] | None, room_names: Sequence[str]
) -> tuple[list[EstimateLine], dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    """改装平面の赤・青の注記を行にする。**数量は注記に刷られた数だけ。**

    ``pages`` が ``None`` なら、表題欄に「改装平面…図」とあるページを探す。
    """
    from intake.plan_colour_notes import (
        pages_with_title,
        read_colour_notes,
        room_label_positions,
    )

    counts = dict.fromkeys(STAGES, 0)
    chosen = list(pages) if pages is not None else pages_with_title(pdf_path, "改装平面")
    lines: list[EstimateLine] = []
    near: list[dict[str, Any]] = []
    with_count = 0
    for page_number in chosen:
        labels = room_label_positions(pdf_path, page_number, room_names)
        result = read_colour_notes(pdf_path, page_number, labels)
        counts[STAGE_RECOGNIZE] += result.recognized
        counts[STAGE_READ] += len(result.notes)
        # 理解: 色の意味(凡例が名乗る工事の別)が決まった行。許容の外の色は入らない。
        counts[STAGE_UNDERSTAND] += len(result.notes)
        near.extend(
            {
                "ページ": m.page,
                "読んだ文字": m.text,
                "色": m.colour_value,
                "位置_pt": [round(v, 1) for v in m.bbox],
                "理由": m.reason,
            }
            for m in result.near_misses
        )
        for note in result.notes:
            notes = [
                f"色 {note.colour}({note.colour_value}): 凡例では{note.kind}を表す"
                "(凡例の意味は 1 案件でしか確かめていない候補)",
            ]
            if note.count is not None:
                with_count += 1
                notes.append(f"数量は注記に刷られた数「{note.count_text}」")
            elif note.count_text:
                notes.append(
                    f"注記に数が 2 つ以上刷られている({note.count_text})ので、数量は空のまま"
                )
            else:
                notes.append("注記に数が刷られていないので、数量は空のまま")
            if not note.place:
                notes.append("近くに仕上表の室名が見つからないので、場所は空のまま")
            notes.append(
                f"同じ注記がこのページに {note.same_text_count} 行ある。**数量ではない**"
            )
            lines.append(
                EstimateLine(
                    work_item=note.text,
                    place=note.place,
                    quantity=note.count,
                    unit=note.count_unit or "",
                    path=PATH_PLAN_NOTES,
                    evidence=[
                        {
                            "ページ": note.page,
                            "読んだ文字": note.text,
                            "位置_pt": list(note.bbox),
                            "いちばん近い室名までの距離_pt": note.place_distance_pt,
                        }
                    ],
                    notes=notes,
                    extra={
                        "色": note.colour,
                        "色の値": note.colour_value,
                        "別": note.kind,
                        "同じ注記の数": {
                            "行": note.same_text_count,
                            "場所": list(note.same_text_places),
                            "注意": "数量ではない(採点する側が見るための知らせ)",
                        },
                    },
                )
            )
    counts[STAGE_ASSEMBLE] = len(lines)
    return lines, counts, {
        "改装平面の注記のページ": chosen,
        "改装平面の注記": len(lines),
        "改装平面の注記(数が刷られていた)": with_count,
        "改装平面の注記(近いが外れ)": len(near),
    }, near


# ---------------------------------------------------------------------------
# K-42. 既存撤去図の青い網 → 行
# ---------------------------------------------------------------------------


def _demolition_hatch_lines(
    pdf_path: Path,
    pages: Sequence[int] | None,
    room_names: Sequence[str],
    page_scales: Mapping[int, Any],
) -> tuple[list[EstimateLine], dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    """既存撤去図の青い網を面にして、面ごとに床組・天井組の撤去の 2 行を出す。

    ``page_scales`` はページ番号 → 入口が決めた縮尺(`DrawingScale`)。
    **無いページでは面積を出さない(数量は空)。**
    """
    from intake.demolition_hatch import (
        LEGEND_BASIS,
        LEGEND_MISSING,
        WORK_CEILING,
        WORK_FLOOR,
        find_legend_text,
        measure_demolition_hatch,
    )
    from intake.plan_colour_notes import pages_with_title, room_label_positions

    counts = dict.fromkeys(STAGES, 0)
    chosen = list(pages) if pages is not None else pages_with_title(pdf_path, "撤去")
    lines: list[EstimateLine] = []
    near: list[dict[str, Any]] = []
    areas: list[dict[str, Any]] = []
    legend_text = find_legend_text(pdf_path) if chosen else None
    for page_number in chosen:
        scale = page_scales.get(page_number)
        mm_per_point = getattr(scale, "mm_per_point", None)
        result = measure_demolition_hatch(
            pdf_path,
            page_number,
            mm_per_point=mm_per_point,
            scale_text=getattr(scale, "source_text", None),
            room_labels=room_label_positions(pdf_path, page_number, room_names),
            legend_text=legend_text,
        )
        near.extend(result.near_misses)
        counts[STAGE_RECOGNIZE] += len(result.regions)
        for region in result.regions:
            if region.area_sqm is not None:
                counts[STAGE_READ] += 1
            if result.legend_found:
                counts[STAGE_UNDERSTAND] += 1
            areas.append(
                {"ページ": page_number, "面積_㎡": region.area_sqm, "場所": list(region.places)}
            )
            basis = (
                f"{LEGEND_BASIS}(図面の文字: {legend_text})"
                if result.legend_found
                else LEGEND_MISSING
            )
            notes = ["面積は候補。床組と天井組で同じ面の面積を 2 行に出している"]
            if region.area_sqm is None:
                notes.append("このページの縮尺が決まらないので面積を出していない(推測しない)")
            else:
                notes.append(
                    f"縮尺: 1pt = {mm_per_point:.4g}mm({getattr(scale, 'source_text', '')})"
                )
            if not region.places:
                notes.append("面の中に仕上表の室名が無いので、場所は空のまま")
            for work in (WORK_FLOOR, WORK_CEILING):
                lines.append(
                    EstimateLine(
                        work_item=work,
                        place="・".join(region.places),
                        quantity=region.area_sqm,
                        unit="㎡",
                        path=PATH_DEMOLITION_HATCH,
                        evidence=[
                            {
                                "ページ": page_number,
                                "読んだ図形": "青い斜めの線の網を 40pt の四角で閉じた面",
                                "外接_pt": list(region.bbox_pt),
                                "紙の上の面積_pt2": region.area_pt2,
                                "根拠": basis,
                            }
                        ],
                        notes=list(notes),
                        # 撤去は解体・撤去工事に置く(仕上表の行と同じ仮の判断。
                        # `docs/provisional_decisions.md` 7 節)。
                        category=FINISH_KAMOKU_REMOVAL,
                    )
                )
            # K-45(おーちゃん 2026-09-26「木工の数量について」): 床組・天井組・天井
            # ボードの新設は、正解が撤去の網の面積と同じだった。**要確認として数量も
            # 出す。確定はしない。** 網は撤去の範囲であって、新設の範囲とは書いていない。
            for work in HATCH_INFERRED_NEW_WORKS:
                lines.append(
                    EstimateLine(
                        work_item=work,
                        place="・".join(region.places),
                        quantity=region.area_sqm,
                        unit="㎡",
                        path=PATH_DEMOLITION_HATCH,
                        evidence=[
                            {
                                "ページ": page_number,
                                "読んだ図形": "青い斜めの線の網を 40pt の四角で閉じた面",
                                "外接_pt": list(region.bbox_pt),
                                "紙の上の面積_pt2": region.area_pt2,
                                "根拠": f"{HATCH_INFERRED_BASIS} / {basis}",
                            }
                        ],
                        notes=[HATCH_INFERRED_BASIS, *notes[1:]],
                        # 新設は内装仕上工事に置く(仮の判断。`docs/provisional_decisions.md`
                        # 7 節。木工・大工工事に分けるかはまだ決めていない)。
                        category=FINISH_KAMOKU_OTHER,
                        certainty=CERTAINTY_NEEDS_CHECK,
                    )
                )
    counts[STAGE_ASSEMBLE] = len(lines)
    return lines, counts, {
        "撤去の網のページ": chosen,
        "撤去の網の面": areas,
        "撤去の網の凡例": legend_text if legend_text is not None else LEGEND_MISSING,
        "撤去の網(近いが外れ)": len(near),
    }, near


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
    plan_note_pages: Sequence[int] | None = None,
    demolition_pages: Sequence[int] | None = None,
    line_judge: Any = None,
    line_judgments: str | Path | None = None,
) -> OnePassResult:
    """7 つの段を順に動かす。**途中の段が空でも止めずに最後まで通す。**

    ``plan_note_pages`` / ``demolition_pages`` を渡さなければ、表題欄の語から
    改装平面・撤去の図のページを探す(K-42)。

    ``line_judge`` は線引きの役(`estimating.line_judge`)。渡さなければ AI の判定の
    ファイル ``line_judgments`` を読む役になる(K-46 の本番の既定)。ファイルも
    渡さなければ、全部の行が「要確認」になる(落とさない)。
    """
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
    rectangle_checks: tuple[str, ...] = ()
    if drawing_rooms is not None:
        drawing_result, rectangle_checks = _drawing_room_result(pdf, Path(drawing_rooms))
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

    # K-42. 改装平面の注記と、既存撤去図の青い網。場所は仕上表の室名から探す。
    room_names = list(
        dict.fromkeys(row.room for row in intake.finish_schedule_rows if row.room)
    )
    started = time.perf_counter()
    note_lines, stages[PATH_PLAN_NOTES], note_extra, note_near = _plan_note_lines(
        pdf, plan_note_pages, room_names
    )
    timings["K-42 改装平面の注記"] = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    page_scales = {page.page_number: page.scale for page in intake.pages if page.scale is not None}
    hatch_lines, stages[PATH_DEMOLITION_HATCH], hatch_extra, hatch_near = _demolition_hatch_lines(
        pdf, demolition_pages, room_names, page_scales
    )
    timings["K-42 撤去の網"] = round(time.perf_counter() - started, 1)

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
    lines = finish_lines + legend_lines + note_lines + hatch_lines + mapped
    lines, not_work, judge_summary = _draw_the_line(lines, line_judge, line_judgments)
    for line in not_work:
        if line.path in stages:
            stages[line.path][STAGE_ASSEMBLE] -= 1
    from estimating.cross_checks import same_surface_counted_twice

    checks = {
        "長方形の中の別の室名": list(rectangle_checks),
        "同じ面を2回以上": list(
            same_surface_counted_twice(
                {"場所": line.place, "工事項目": line.work_item, "数量": line.quantity, "単位": line.unit}
                for line in lines
            )
        ),
    }
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
        checks=checks,
        near_misses={PATH_PLAN_NOTES: note_near, PATH_DEMOLITION_HATCH: hatch_near},
        not_work_lines=not_work,
        line_judge_summary=judge_summary,
        extras={
            **finish_extra,
            **legend_extra,
            **note_extra,
            **hatch_extra,
            "入口の読み": len(intake.findings),
            "入口の数量": len(quantities),
            "入口の判定": len(intake.decisions),
            "建具表の行": len(intake.door_schedule_rows),
            "仕上表の行(入口)": len(intake.finish_schedule_rows),
            "人の入力待ちの行": sum(1 for line in lines if line.waits_for_human),
        },
    )


def _draw_the_line(
    lines: list[EstimateLine], line_judge: Any, line_judgments: str | Path | None
) -> tuple[list[EstimateLine], list[EstimateLine], dict[str, Any]]:
    """線引き(K-46)。**判定の無い行は要確認にして残す。何も確定させない。**

    返すのは (見積の行、工事の行ではないとされた行、件数のまとめ)。
    """
    from estimating.line_judge import (
        VERDICT_NEEDS_CHECK,
        VERDICT_NOT_WORK,
        LineText,
        make_line_judge,
    )

    judge = line_judge if line_judge is not None else make_line_judge(judgments=line_judgments)
    verdicts = judge.judge(
        [LineText(i, line.work_item, line.place) for i, line in enumerate(lines, 1)]
    )
    if len(verdicts) != len(lines):
        raise ValueError(f"線引きの判定の数 {len(verdicts)} が行の数 {len(lines)} と合いません")
    kept: list[EstimateLine] = []
    not_work: list[EstimateLine] = []
    for line, verdict in zip(lines, verdicts):
        line.evidence = [*line.evidence, verdict.as_evidence()]
        line.extra = {**line.extra, "線引き": verdict.as_evidence()}
        if verdict.verdict == VERDICT_NOT_WORK:
            not_work.append(line)
            continue
        if verdict.verdict == VERDICT_NEEDS_CHECK:
            line.certainty = CERTAINTY_NEEDS_CHECK
        kept.append(line)
    summary = {
        "役": getattr(judge, "name", type(judge).__name__),
        "判定ごと": dict(Counter(v.verdict for v in verdicts)),
        "判定した役ごと": dict(Counter(v.judge for v in verdicts)),
    }
    return kept, not_work, summary


# ---------------------------------------------------------------------------
# K-49. AI が読んだ答案を本番の入力にする。機械は検算に回る
# ---------------------------------------------------------------------------

#: 機械の検算で食い違いとみなさない差(数の丸めの差だけ)。
MACHINE_CHECK_EPSILON = 1e-6


def ai_reading_lines(reading) -> list[EstimateLine]:  # noqa: ANN001
    """答案の行を見積の行にする。**数量は答案のまま。null は null のまま(0 にしない)。**

    決め手は「AI の読み」。札は `estimating.decisive` が読み手(証拠)から作る。
    **確かさは上げない**(候補のまま)。
    """
    from estimating.decisive import decisive_reasons_for

    lines: list[EstimateLine] = []
    for row in reading.rows:
        decisive = decisive_reasons_for(
            effective_derivation="read", ai_reader=reading.reader or "AI(読み手不明)"
        )
        notes = []
        if row.formula:
            notes.append(f"式: {row.formula}")
        if row.quantity is None:
            notes.append("AI が数量を出していない(空のまま。0 にしていない)")
        lines.append(
            EstimateLine(
                work_item=row.work,
                place=row.place,
                quantity=row.quantity,
                unit=row.unit,
                path=PATH_AI_READING,
                evidence=[
                    {
                        "読み": PATH_AI_READING,
                        "読み手": reading.reader,
                        "答案の番号": row.number,
                        "根拠": row.basis,
                        "式": row.formula,
                    }
                ],
                notes=notes,
                decisive=decisive,
                extra={"式": row.formula},
            )
        )
    return lines


def _same_number(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= MACHINE_CHECK_EPSILON * max(1.0, abs(float(b)))


def check_with_machine(
    lines: list[EstimateLine], machine_rows: Sequence[Mapping[str, Any]] | None, source: str = ""
) -> dict[str, Any]:
    """機械の読みを AI の行に「機械の検算」として並べる。**機械は答案を直さない。**

    引き方は工事と場所の文字(NFKC にして空白を除く)が両方とも同じものだけ。引けないものは引かない。
    機械が数を持っていれば行の横に並べ、食い違えば行を要確認にして理由を書く。
    **数量は変えない。**AI の数量が空で機械が数を持っていても、埋めない(理由に書くだけ)。
    """
    if machine_rows is None:
        return {"動かした": False, "理由": source or "機械の読みを渡していない"}
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in machine_rows:
        key = (_nfkc(row.get("工事項目")), _nfkc(row.get("場所")))
        by_key.setdefault(key, []).append(row)
    matched_machine: set[int] = set()
    matched_lines = 0
    disagreements = 0
    for line in lines:
        found = by_key.get((_nfkc(line.work_item), _nfkc(line.place)), [])
        with_number = [r for r in found if isinstance(r.get("数量"), (int, float)) and not isinstance(r.get("数量"), bool)]
        for r in found:
            matched_machine.add(id(r))
        if not with_number:
            continue
        matched_lines += 1
        line.extra = {
            **line.extra,
            "機械の検算": [
                {
                    "機械の番号": r.get("番号"),
                    "数量": r.get("数量"),
                    "単位": r.get("単位"),
                    "道": r.get("道"),
                }
                for r in with_number
            ],
        }
        reasons: list[str] = []
        numbers = [float(r["数量"]) for r in with_number]
        units = {_nfkc(r.get("単位")) for r in with_number}
        if line.quantity is None:
            reasons.append(
                "AI は数量を出していない。機械は "
                + "、".join(f"{r['数量']}{r.get('単位') or ''}" for r in with_number)
                + "(埋めていない)"
            )
        else:
            if not (
                any(_same_number(line.quantity, n) for n in numbers)
                or _same_number(line.quantity, sum(numbers))
            ):
                reasons.append(
                    f"数量が機械の検算と食い違う: AI {line.quantity}{line.unit} / 機械 "
                    + "、".join(f"{r['数量']}{r.get('単位') or ''}" for r in with_number)
                )
            if units and _nfkc(line.unit) not in units:
                reasons.append(
                    f"単位が機械の検算と食い違う: AI {line.unit or '(空)'} / 機械 "
                    + "、".join(sorted(str(r.get("単位") or "(空)") for r in with_number))
                )
        if reasons:
            disagreements += 1
            line.certainty = CERTAINTY_NEEDS_CHECK
            line.notes.extend(reasons)
            line.extra = {**line.extra, "要確認の理由": reasons}
    unmatched = [dict(r) for r in machine_rows if id(r) not in matched_machine]
    return {
        "動かした": True,
        "出どころ": source,
        "機械の行": len(machine_rows),
        "機械の行のうち数量のある行": sum(
            1 for r in machine_rows
            if isinstance(r.get("数量"), (int, float)) and not isinstance(r.get("数量"), bool)
        ),
        "機械の数を並べた AI の行": matched_lines,
        "食い違って要確認にした AI の行": disagreements,
        "AI の行に引けなかった機械の行": len(unmatched),
        "引けなかった機械の行": unmatched,
    }


def run_ai_reading(
    reading,  # noqa: ANN001
    *,
    case_id: str,
    reader: str = READER_AI_FILE,
    machine_rows: Sequence[Mapping[str, Any]] | None = None,
    machine_source: str = "",
    machine_auto_confirmed: Mapping[str, int] | None = None,
    timings: Mapping[str, float] | None = None,
) -> OnePassResult:
    """AI の答案を本番の行にし、機械の読みを検算として並べる(K-49)。**何も確定させない。**"""
    from estimating.cross_checks import same_surface_counted_twice

    lines = ai_reading_lines(reading)
    machine_check = check_with_machine(lines, machine_rows, machine_source)
    gaps: list[str] = []
    if reading.status != "読んだ" or reading.reason:
        gaps.append(f"[AI の読み] {reading.status}: {reading.reason}")
    if reading.dropped:
        gaps.append(
            f"[AI の答案の形] 形が崩れた行 {len(reading.dropped)} 件を理由つきで落とした"
            "(「AI の答案」の「落とした行」)"
        )
    auto_confirmed = {"AI の読みで確定": 0}
    for name, count in (machine_auto_confirmed or {}).items():
        if name != "合計":
            auto_confirmed[f"機械の検算の側: {name}"] = int(count)
    received = len(reading.rows)
    stages = {
        PATH_AI_READING: {
            STAGE_RECOGNIZE: received + len(reading.dropped),
            STAGE_READ: received,
            STAGE_UNDERSTAND: received,
            STAGE_ASSEMBLE: len(lines),
        }
    }
    checks = {
        "同じ面を2回以上": list(
            same_surface_counted_twice(
                {"場所": line.place, "工事項目": line.work_item, "数量": line.quantity, "単位": line.unit}
                for line in lines
            )
        ),
        "機械の検算と食い違う行": [
            f"{line.work_item} / {line.place}: " + " / ".join(line.extra.get("要確認の理由", []))
            for line in lines
            if line.extra.get("要確認の理由")
        ],
    }
    return OnePassResult(
        case_id=case_id,
        lines=lines,
        stages=stages,
        ledger={"動かした": False},
        auto_confirmed=auto_confirmed,
        gaps=gaps,
        not_connected=[{"部品": name, "理由": why} for name, why in NOT_CONNECTED],
        timings=dict(timings or {}),
        checks=checks,
        line_judge_summary={"役": "AI の読み(答案の行をそのまま使う。線引きは掛けない)"},
        reader=reader,
        ai_reading=reading.summary(),
        machine_check=machine_check,
        extras={"数量のある行": sum(1 for line in lines if line.quantity is not None)},
    )


def run_production(
    pdf_path: str | Path,
    *,
    case_id: str,
    answers_path: str | Path,
    reader: str = READER_AI_FILE,
    ai_reading: str | Path | None = None,
    machine_output: str | Path | None = None,
    machine_check: bool = True,
    ai_client: Any = None,
    **machine_kwargs: Any,
) -> OnePassResult:
    """本番の入口(K-49)。**既定は AI の答案のファイル。**

    - ``reader="machine"`` … いままでの形(機械が読み、線引きを掛ける)。比べるために残す。
    - ``reader="ai-file"`` … ``ai_reading`` の答案を行にする。無ければ「読んでいない」(行 0 件)。
    - ``reader="ai-api"`` … AI をその場で呼ぶ。鍵が無ければ止めずに「読んでいない」。

    AI の側では、機械の読み(``run`` の出力の行)を検算として並べる。``machine_output`` に
    前に出した機械の出力(JSON)を渡せば、機械を動かし直さずにそれを使う。
    """
    if reader not in READERS:
        raise ValueError(f"知らない読み手です: {reader!r}(選べるのは {', '.join(READERS)})")
    if reader == READER_MACHINE:
        result = run(pdf_path, case_id=case_id, answers_path=answers_path, **machine_kwargs)
        result.reader = READER_MACHINE
        return result

    from intake.ai_reading import load_ai_reading, read_with_api

    timings: dict[str, float] = {}
    started = time.perf_counter()
    if reader == READER_AI_API:
        reading = read_with_api(pdf_path, client=ai_client)
    else:
        reading = load_ai_reading(ai_reading)
    timings["AI の読み(受け取り)"] = round(time.perf_counter() - started, 1)
    if reading.seconds is not None:
        timings["AI が読んだ秒数"] = reading.seconds

    machine_rows: list[dict[str, Any]] | None = None
    machine_auto: dict[str, int] | None = None
    source = ""
    if machine_output is not None:
        payload = json.loads(Path(machine_output).read_text(encoding="utf-8"))
        machine_rows = list(payload.get("工事項目", []))
        machine_auto = {
            k: int(v) for k, v in (payload.get("自動確定") or {}).items() if k != "合計"
        }
        source = f"前に出した機械の出力: {Path(machine_output).name}"
    elif machine_check:
        started = time.perf_counter()
        machine = run(pdf_path, case_id=case_id, answers_path=answers_path, **machine_kwargs)
        timings["機械の検算"] = round(time.perf_counter() - started, 1)
        machine_rows = [line.as_answer_row(i) for i, line in enumerate(machine.lines, 1)]
        machine_auto = dict(machine.auto_confirmed)
        source = "この場で機械が読んだ(app.run)"
    else:
        source = "機械の検算を止めた(--no-machine-check)"
    return run_ai_reading(
        reading,
        case_id=case_id,
        reader=reader,
        machine_rows=machine_rows,
        machine_source=source,
        machine_auto_confirmed=machine_auto,
        timings=timings,
    )


def _page_list(text: str | None) -> list[int] | None:
    if text is None:
        return None
    return [int(part) for part in text.replace("、", ",").split(",") if part.strip()]


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
    parser.add_argument(
        "--plan-note-pages",
        default=None,
        help="改装平面の注記を読むページ(1 始まり、カンマ区切り)。無ければ表題欄から探す",
    )
    parser.add_argument(
        "--demolition-pages",
        default=None,
        help="撤去の網を測るページ(1 始まり、カンマ区切り)。無ければ表題欄から探す",
    )
    parser.add_argument(
        "--line-judge",
        default="ai",
        choices=("ai", "ai-api", "words"),
        help="工事の行かの線引きの役(K-46)。既定は AI の判定のファイル(--line-judgments)",
    )
    parser.add_argument(
        "--line-judgments",
        default=None,
        help="AI が出した線引きの判定(JSON)。無ければ全部の行を要確認として残す",
    )
    parser.add_argument(
        "--reader",
        default=READER_AI_FILE,
        choices=READERS,
        help="誰が読むか(K-49)。既定は AI の答案のファイル(--ai-reading)。"
        "ai-api は AI をその場で呼ぶ(鍵 ANTHROPIC_API_KEY が要る)。machine はいままでの形",
    )
    parser.add_argument(
        "--ai-reading", default=None, help="AI が図面を読んだ答案(JSON)。--reader ai-file で使う"
    )
    parser.add_argument(
        "--machine-output",
        default=None,
        help="機械の検算に、前に --reader machine で出した出力(JSON)を使う(機械を動かし直さない)",
    )
    parser.add_argument(
        "--no-machine-check", action="store_true", help="AI の側で機械の検算を動かさない"
    )
    args = parser.parse_args(argv)

    from estimating.line_judge import make_line_judge

    out = Path(args.out)
    result = run_production(
        args.pdf,
        case_id=args.case_id,
        answers_path=args.answers or out.with_suffix(".answers.json"),
        reader=args.reader,
        ai_reading=args.ai_reading,
        machine_output=args.machine_output,
        machine_check=not args.no_machine_check,
        legend_table=args.legend_table,
        human_input=args.human_input,
        drawing_rooms=args.drawing_rooms,
        rules=args.rules,
        build_ledger_stage=not args.no_ledger,
        plan_note_pages=_page_list(args.plan_note_pages),
        demolition_pages=_page_list(args.demolition_pages),
        line_judge=make_line_judge(args.line_judge, args.line_judgments),
    )
    out.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    totals = result.stage_totals()
    print(
        f"読み手 {result.reader} / 行 {len(result.lines)} / 自動確定 {result.auto_confirmed_total} / "
        + " / ".join(f"{k} {v}" for k, v in totals.items())
    )
    if result.ai_reading is not None and result.ai_reading.get("状態") != "読んだ":
        print(f"AI は読んでいない: {result.ai_reading.get('理由')}", file=sys.stderr)
    if result.auto_confirmed_total:
        print("**自動確定が出た。K-27 の止める条件に当たる。**", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
