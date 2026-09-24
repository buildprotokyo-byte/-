"""図面の要素ごとに、どの専門知識で読むかを選んで、AI に専門家として読ませる段。

なぜこれを作るのか
------------------
本番の経路(`app.py`、K-27)には、**AI が図面を読む段が 1 つも無かった。**
台帳・表の読み取り・凡例の引き当ては、どれも決まった手順の図形処理と文字の照合である。
おーちゃんの指摘(2026-09-24): 図面は決まった書き方で描かれていて、専門家はその
書き方を知っているから読める。**知識を前提に渡さずに読ませても、読めないのは当たり前。**

この段は 3 つのことをする。

1. **要素ごとに知識を選ぶ。** 「室の面積を出す」なら、床面積の法の定義・製図の
   寸法の書き方・積算基準・設計製図の試験の描き方、を選ぶ。対応表は
   `knowledge/expertise/catalog.json`(出どころと要点だけ。本文は写さない)。
2. **選んだ知識を前提にして読ませる。** 前提の文は `build_premise` の 1 か所で作る。
3. **読んだ答えに「知識から出した」印を付け、図面自身と 1 か所で突き合わせる**
   (`check_against_drawing`)。K-21・K-22 で、知識で読んだ答えは
   「一般の記号としては正しいが、この図面の凡例とは違う」ことがあった。
   **図面の凡例・表・印字と食い違えば、図面を採る。**

この段がしないこと
------------------
- **何も確定させない。** 答えはいつも候補で、`confirmed` は常に False。
  判定のしかたにも基準値にも触らない。
- **読み手が無いときに答えを作らない。** 鍵や通信が無ければ「読み手が未接続」と
  記録して、答えは空のまま返す。
- **AI の計算を信じない。** 面積は AI が挙げた寸法からこちらで掛け算し直し、
  AI が言った面積と合わなければそう記録する。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from knowledge.table import KnowledgeError, Source, _parse_source

#: 既定の目録。**中身はコードに書かない。**差し替えるときはパスを渡す。
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

CATALOG_VERSION = 1

#: 知識で読んだ答えに必ず付ける印。
ORIGIN_KNOWLEDGE = "知識から出した"

#: 読み手が返せる状態。
STATUS_READ = "読めた"
STATUS_UNREADABLE = "読めない"
STATUS_QUERY = "質疑にあたる"
READER_STATUSES = (STATUS_READ, STATUS_UNREADABLE, STATUS_QUERY)
#: 読み手がそもそも居ないとき(こちらが付ける。読み手は返せない)。
STATUS_NOT_CONNECTED = "読み手が未接続"

#: 要点を原文と照らし合わせたかどうか。
POINT_UNCHECKED = "未照合"
POINT_CHECKED = "照合済み"
POINT_STATUSES = (POINT_UNCHECKED, POINT_CHECKED)

#: 図面との突き合わせの結果。
CHECK_AGREED = "図面と一致"
CHECK_CONFLICT = "図面と食い違い(図面を採る)"
CHECK_NOTHING_TO_CHECK = "図面に照合先が無い(知識だけの答え)"

#: 面積の基準。**芯々はおーちゃんの決め**(`docs/decision_area_basis.md`)。
AREA_BASIS_CENTER = "芯々"
AREA_BASIS_INNER = "内法"
AREA_BASIS_UNKNOWN = "不明"
AREA_BASES = (AREA_BASIS_CENTER, AREA_BASIS_INNER, AREA_BASIS_UNKNOWN)
DECIDED_AREA_BASIS = AREA_BASIS_CENTER

ELEMENT_ROOM_AREA = "室の面積"

_CATALOG_FIELDS = {"catalog_version", "catalog_id", "description", "roles", "elements", "knowledge"}
_ROLE_FIELDS = {"role_id", "covers"}
_ELEMENT_FIELDS = {"element_id", "description"}
_KNOWLEDGE_FIELDS = {
    "knowledge_id", "domain", "source", "points", "point_status",
    "elements", "roles", "exam_fields",
}


class ExpertiseError(Exception):
    """目録や答えがそのままでは使えない形だった。**黙って直さない。**"""


class ReaderUnavailable(Exception):
    """読み手(AI)を呼べない。理由を文にして持つ。"""


# ---------------------------------------------------------------------------
# 目録
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    role_id: str
    covers: str


@dataclass(frozen=True)
class Element:
    element_id: str
    description: str


@dataclass(frozen=True)
class ExpertKnowledge:
    """知識 1 件。**出どころと、こちらの言葉で書いた要点だけ。**"""

    knowledge_id: str
    domain: str
    source: Source
    points: tuple[str, ...]
    point_status: str
    elements: tuple[str, ...]
    roles: tuple[str, ...]
    exam_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpertiseCatalog:
    catalog_id: str
    roles: tuple[Role, ...]
    elements: tuple[Element, ...]
    knowledge: tuple[ExpertKnowledge, ...]
    description: str = ""

    def element(self, element_id: str) -> Element:
        for element in self.elements:
            if element.element_id == element_id:
                return element
        raise ExpertiseError(
            f"知らない要素です: {element_id}。"
            f"目録にあるのは {[e.element_id for e in self.elements]} です"
        )

    def uncovered_elements(self) -> tuple[str, ...]:
        """知識が 1 件も付いていない要素。"""
        covered = {e for k in self.knowledge for e in k.elements}
        return tuple(e.element_id for e in self.elements if e.element_id not in covered)


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], what: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ExpertiseError(
            f"{what} に知らない列があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた目録になるので受け付けません"
        )


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpertiseError(f"{what} が必要です")
    return value


def _texts(value: Any, what: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ExpertiseError(f"{what} は文字列の並びである必要があります")
    out = tuple(_text(item, f"{what} の要素") for item in value)
    if not out and not allow_empty:
        raise ExpertiseError(f"{what} が空です")
    return out


def parse_catalog(payload: Any) -> ExpertiseCatalog:
    """目録を検査して読む。"""
    if not isinstance(payload, Mapping):
        raise ExpertiseError("目録の中身が辞書ではありません")
    _reject_unknown(payload, _CATALOG_FIELDS, "目録")
    if payload.get("catalog_version") != CATALOG_VERSION:
        raise ExpertiseError(
            f"catalog_version が {payload.get('catalog_version')!r} です。"
            f"読めるのは {CATALOG_VERSION} だけです"
        )

    roles: list[Role] = []
    for raw in payload.get("roles") or ():
        if not isinstance(raw, Mapping):
            raise ExpertiseError("roles の要素が辞書ではありません")
        _reject_unknown(raw, _ROLE_FIELDS, "roles の要素")
        roles.append(Role(_text(raw.get("role_id"), "role_id"), _text(raw.get("covers"), "covers")))
    role_ids = {r.role_id for r in roles}
    if not roles or len(role_ids) != len(roles):
        raise ExpertiseError("roles が空か、role_id が重複しています")

    elements: list[Element] = []
    for raw in payload.get("elements") or ():
        if not isinstance(raw, Mapping):
            raise ExpertiseError("elements の要素が辞書ではありません")
        _reject_unknown(raw, _ELEMENT_FIELDS, "elements の要素")
        elements.append(
            Element(_text(raw.get("element_id"), "element_id"), _text(raw.get("description"), "description"))
        )
    element_ids = {e.element_id for e in elements}
    if not elements or len(element_ids) != len(elements):
        raise ExpertiseError("elements が空か、element_id が重複しています")

    knowledge: list[ExpertKnowledge] = []
    seen: set[str] = set()
    for raw in payload.get("knowledge") or ():
        if not isinstance(raw, Mapping):
            raise ExpertiseError("knowledge の要素が辞書ではありません")
        _reject_unknown(raw, _KNOWLEDGE_FIELDS, "knowledge の要素")
        knowledge_id = _text(raw.get("knowledge_id"), "knowledge_id")
        if knowledge_id in seen:
            raise ExpertiseError(f"knowledge_id が重複しています: {knowledge_id}")
        seen.add(knowledge_id)
        try:
            source = _parse_source(raw.get("source"))
        except KnowledgeError as exc:  # 出どころの検査は知識の表と同じもの
            raise ExpertiseError(f"{knowledge_id} の出どころ: {exc}") from exc
        point_status = raw.get("point_status")
        if point_status not in POINT_STATUSES:
            raise ExpertiseError(
                f"{knowledge_id} の point_status が {point_status!r} です。"
                f"書けるのは {list(POINT_STATUSES)} のどれかです"
            )
        if point_status == POINT_CHECKED and (not source.read_directly or source.checked_on is None):
            raise ExpertiseError(
                f"{knowledge_id} は 照合済み なのに、原文を直接読んでいないか確認日がありません。"
                "要点を原文と照らしたなら read_directly を true にし、確認日を書いてください"
            )
        used_elements = _texts(raw.get("elements"), f"{knowledge_id} の elements")
        missing = [e for e in used_elements if e not in element_ids]
        if missing:
            raise ExpertiseError(f"{knowledge_id} が目録に無い要素を指しています: {missing}")
        used_roles = _texts(raw.get("roles"), f"{knowledge_id} の roles")
        missing = [r for r in used_roles if r not in role_ids]
        if missing:
            raise ExpertiseError(f"{knowledge_id} が目録に無い専門家を指しています: {missing}")
        knowledge.append(
            ExpertKnowledge(
                knowledge_id=knowledge_id,
                domain=_text(raw.get("domain"), f"{knowledge_id} の domain"),
                source=source,
                points=_texts(raw.get("points"), f"{knowledge_id} の points"),
                point_status=point_status,
                elements=used_elements,
                roles=used_roles,
                exam_fields=_texts(raw.get("exam_fields", ()), f"{knowledge_id} の exam_fields", allow_empty=True),
            )
        )
    if not knowledge:
        raise ExpertiseError("knowledge が空です")

    description = payload.get("description", "")
    if not isinstance(description, str):
        raise ExpertiseError("description は文字列である必要があります")
    return ExpertiseCatalog(
        catalog_id=_text(payload.get("catalog_id"), "catalog_id"),
        roles=tuple(roles),
        elements=tuple(elements),
        knowledge=tuple(knowledge),
        description=description,
    )


def load_catalog(path: str | Path | None = None) -> ExpertiseCatalog:
    path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExpertiseError(f"目録が見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExpertiseError(f"目録が JSON として読めません: {path}: {exc}") from exc
    return parse_catalog(payload)


# ---------------------------------------------------------------------------
# 要素ごとに知識を選ぶ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeSelection:
    """1 つの要素を読むために選んだ知識と専門家。"""

    element: Element
    knowledge: tuple[ExpertKnowledge, ...]
    roles: tuple[Role, ...]

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(k.knowledge_id for k in self.knowledge)


def select_knowledge(catalog: ExpertiseCatalog, element_id: str) -> KnowledgeSelection:
    """その要素に付いた知識を、目録の並び順のまま全部選ぶ。**選り好みしない。**"""
    element = catalog.element(element_id)
    chosen = tuple(k for k in catalog.knowledge if element_id in k.elements)
    if not chosen:
        raise ExpertiseError(f"{element_id} に付いた知識が目録に 1 件もありません")
    wanted = {r for k in chosen for r in k.roles}
    roles = tuple(r for r in catalog.roles if r.role_id in wanted)
    return KnowledgeSelection(element=element, knowledge=chosen, roles=roles)


def build_premise(selection: KnowledgeSelection) -> str:
    """読み手に渡す前提の文。**前提の文はここ 1 か所でだけ作る。**"""
    roles = "\n".join(f"- {r.role_id}: {r.covers}" for r in selection.roles)
    blocks = []
    for k in selection.knowledge:
        s = k.source
        points = "\n".join(f"  - {p}" for p in k.points)
        blocks.append(
            f"[{k.knowledge_id}] {s.document} {s.clause}(発行: {s.publisher}、拘束力: {s.binding})\n{points}"
        )
    knowledge = "\n".join(blocks)
    return f"""あなたたちは次の専門家の集まりとして、1 枚の建築図面を読みます。
{roles}

いま読むのは「{selection.element.element_id}」です({selection.element.description})。

この要素を読むときに使う知識は次のとおりです。設計者はこうした決まりに沿って図面を描きます。
まず決まりどおりに描かれていると考えて読み、決まりから外れる所は図面の中の手がかりから推し量ってください。
{knowledge}

守ること:
1. この図面の凡例・表・特記・印字された数字は、上の一般の決まりより優先します。一般の決まりと図面が違えば、図面を採ってください。
2. 答えを出すのに使った知識は、上の [ ] の名前で必ず挙げてください。図面のどこを見たかも挙げてください。
3. 図面に書かれていない数字を作らないでください。
4. 1 つの記号や語が 2 つ以上の意味に取れて図面の中で決まらないときは、1 つに決めず「質疑にあたる」と答え、理由を書いてください。
5. 読めないときは「読めない」と答えてください。読めないことは間違いではありません。
"""


# ---------------------------------------------------------------------------
# 読ませる
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementReadingRequest:
    """1 つの要素を読ませる依頼。"""

    element_id: str
    question: str
    #: 文字で渡す資料(その辺りの文字と位置など)。
    materials: tuple[str, ...] = ()
    #: 画像で渡す資料(メディア型, 中身)。
    images: tuple[tuple[str, bytes], ...] = ()
    #: 答えの `answer` に入れてほしい形の説明。
    answer_shape: str = '{"name": "<名前>"}'

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ExpertiseError("question が空です")
        if not self.materials and not self.images:
            raise ExpertiseError("資料が 1 つもありません。図面を渡さずに読ませない")


def build_request_text(request: ElementReadingRequest) -> str:
    materials = "\n\n".join(request.materials) if request.materials else "(画像だけ)"
    return f"""問い: {request.question}

資料:
{materials}

次の形の JSON オブジェクトだけを出してください。前後に説明文を書かないでください。
{{
  "status": "{STATUS_READ} / {STATUS_UNREADABLE} / {STATUS_QUERY} のどれか",
  "answer": {request.answer_shape},
  "used_knowledge": ["使った知識の [ ] の名前"],
  "drawing_evidence": ["図面のどこを見たか"],
  "reason": "そう読んだ理由"
}}
"""


#: 読み手の形。前提の文と依頼を受け取り、返ってきた文字をそのまま返す。
Reader = Callable[[str, ElementReadingRequest], str]


@dataclass(frozen=True)
class ExpertReading:
    """知識で読んだ答え 1 件。**いつも候補で、確定しない。**"""

    element_id: str
    status: str
    answer: Mapping[str, Any]
    used_knowledge: tuple[str, ...]
    #: 目録で選んでいないのに読み手が挙げた名前(作った名前かもしれない)。
    unknown_knowledge: tuple[str, ...]
    drawing_evidence: tuple[str, ...]
    reason: str
    selected_knowledge: tuple[str, ...]
    reader_id: str
    origin: str = ORIGIN_KNOWLEDGE
    notes: tuple[str, ...] = ()

    @property
    def confirmed(self) -> bool:
        """**この段は何も確定させない。**"""
        return False


def _extract_json(text: str) -> Mapping[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ExpertiseError("読み手の答えに JSON がありません")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExpertiseError(f"読み手の答えが JSON として読めません: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ExpertiseError("読み手の答えが JSON オブジェクトではありません")
    return payload


def parse_reader_answer(
    raw_text: str, selection: KnowledgeSelection, *, reader_id: str
) -> ExpertReading:
    payload = _extract_json(raw_text)
    status = payload.get("status")
    if status not in READER_STATUSES:
        raise ExpertiseError(f"status が {status!r} です。返せるのは {list(READER_STATUSES)} だけです")
    answer = payload.get("answer") or {}
    if not isinstance(answer, Mapping):
        raise ExpertiseError("answer が辞書ではありません")
    named = tuple(str(x) for x in payload.get("used_knowledge") or ())
    selected = set(selection.knowledge_ids)
    evidence = tuple(str(x) for x in payload.get("drawing_evidence") or ())
    notes: list[str] = []
    if status == STATUS_READ and not named:
        notes.append("使った知識を挙げていない")
    if status == STATUS_READ and not evidence:
        notes.append("図面のどこを見たかを挙げていない")
    return ExpertReading(
        element_id=selection.element.element_id,
        status=status,
        answer=dict(answer) if status == STATUS_READ else {},
        used_knowledge=tuple(n for n in named if n in selected),
        unknown_knowledge=tuple(n for n in named if n not in selected),
        drawing_evidence=evidence,
        reason=str(payload.get("reason") or ""),
        selected_knowledge=selection.knowledge_ids,
        reader_id=reader_id,
        notes=tuple(notes),
    )


def not_connected(selection: KnowledgeSelection, why: str) -> ExpertReading:
    return ExpertReading(
        element_id=selection.element.element_id,
        status=STATUS_NOT_CONNECTED,
        answer={},
        used_knowledge=(),
        unknown_knowledge=(),
        drawing_evidence=(),
        reason=why,
        selected_knowledge=selection.knowledge_ids,
        reader_id="",
    )


def read_element(
    catalog: ExpertiseCatalog,
    request: ElementReadingRequest,
    reader: Reader | None,
    *,
    reader_id: str = "",
    unavailable_reason: str = "読み手が渡されていません",
) -> ExpertReading:
    """要素に合う知識を選び、前提にして読ませる。読み手が無ければ答えを作らない。"""
    selection = select_knowledge(catalog, request.element_id)
    if reader is None:
        return not_connected(selection, unavailable_reason)
    premise = build_premise(selection)
    raw = reader(premise, request)
    reading = parse_reader_answer(raw, selection, reader_id=reader_id or getattr(reader, "reader_id", "読み手"))
    if request.element_id == ELEMENT_ROOM_AREA and reading.status == STATUS_READ:
        reading = _check_room_area(reading)
    return reading


# ---------------------------------------------------------------------------
# 室の面積(最初の対象)
# ---------------------------------------------------------------------------

ROOM_AREA_ANSWER_SHAPE = (
    '{"basis": "芯々 / 内法 / 不明", '
    '"rectangles": [{"width_mm": <数>, "depth_mm": <数>, "sign": 1 または -1, '
    '"width_from": "<その寸法を図面のどこで読んだか>", "depth_from": "<同じく>"}], '
    '"area_m2": <数>}'
)


def room_area_request(
    room_name: str,
    materials: Sequence[str] = (),
    images: Sequence[tuple[str, bytes]] = (),
) -> ElementReadingRequest:
    """室の面積を、図面の寸法から出させる依頼。

    L 字などの室は長方形の足し引きで表させる(`sign` が -1 の長方形は引く)。
    **面積の掛け算はこちらでやり直す**ので、寸法はどこで読んだかと一緒に出させる。
    """
    return ElementReadingRequest(
        element_id=ELEMENT_ROOM_AREA,
        question=(
            f"「{room_name}」の面積を、図面に印字された寸法から出してください。"
            "室を囲む通り芯・壁芯のあいだの寸法を使い、どの線で測ったか(芯々か内法か)を basis に書いてください。"
            "室が長方形でなければ、長方形の足し引きに分けてください。"
            "寸法が印字されていない辺があれば、推して埋めずに「読めない」と答えてください。"
        ),
        materials=tuple(materials),
        images=tuple(images),
        answer_shape=ROOM_AREA_ANSWER_SHAPE,
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def area_from_rectangles(rectangles: Any) -> Decimal | None:
    """長方形の並びから面積(㎡)を出す。形が崩れていれば None。"""
    if not isinstance(rectangles, Sequence) or isinstance(rectangles, (str, bytes)) or not rectangles:
        return None
    total = Decimal(0)
    for rect in rectangles:
        if not isinstance(rect, Mapping):
            return None
        width, depth = _decimal(rect.get("width_mm")), _decimal(rect.get("depth_mm"))
        sign = rect.get("sign", 1)
        if width is None or depth is None or width <= 0 or depth <= 0 or sign not in (1, -1):
            return None
        total += sign * width * depth
    return total / Decimal(1_000_000)


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _check_room_area(reading: ExpertReading) -> ExpertReading:
    answer = dict(reading.answer)
    notes = list(reading.notes)
    computed = area_from_rectangles(answer.get("rectangles"))
    if computed is None:
        notes.append("寸法の並びが崩れていて面積を出し直せない")
    else:
        answer["area_m2_recomputed"] = str(_round2(computed))
        claimed = _decimal(answer.get("area_m2"))
        if claimed is None or _round2(claimed) != _round2(computed):
            notes.append(
                f"読み手の言った面積({answer.get('area_m2')})が、挙げた寸法の掛け算"
                f"({_round2(computed)})と合わない"
            )
    basis = answer.get("basis")
    if basis not in AREA_BASES:
        notes.append(f"面積の基準が {basis!r} で、芯々・内法・不明のどれでもない")
    elif basis != DECIDED_AREA_BASIS:
        notes.append(f"面積の基準が {basis}。会社の決め({DECIDED_AREA_BASIS})とは違う")
    return ExpertReading(**{**reading.__dict__, "answer": answer, "notes": tuple(notes)})


# ---------------------------------------------------------------------------
# 図面との突き合わせ(ここ 1 か所)
# ---------------------------------------------------------------------------

FACT_NAME = "名前"
FACT_AREA = "面積"


@dataclass(frozen=True)
class DrawingFact:
    """図面が自分で書いていること(凡例の名前、印字された面積など)。"""

    kind: str
    value: Any
    where: str


@dataclass(frozen=True)
class DrawingCheck:
    result: str
    reading_value: Any
    drawing_value: Any
    where: str

    @property
    def adopted_value(self) -> Any:
        """**食い違えば図面を採る。**照合先が無ければ知識の答えのまま(印は残る)。"""
        if self.result == CHECK_NOTHING_TO_CHECK:
            return self.reading_value
        return self.drawing_value


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


def check_against_drawing(reading: ExpertReading, fact: DrawingFact | None) -> DrawingCheck:
    """知識で読んだ答えを、図面自身が書いていることと突き合わせる。**ここ 1 か所だけで行う。**"""
    if fact is None:
        value = reading.answer.get("area_m2_recomputed") if reading.element_id == ELEMENT_ROOM_AREA else reading.answer.get("name")
        return DrawingCheck(CHECK_NOTHING_TO_CHECK, value, None, "")
    if fact.kind == FACT_AREA:
        mine = _decimal(reading.answer.get("area_m2_recomputed"))
        theirs = _decimal(fact.value)
        if theirs is None:
            raise ExpertiseError(f"図面の面積が数として読めません: {fact.value!r}")
        agreed = mine is not None and _round2(mine) == _round2(theirs)
        return DrawingCheck(CHECK_AGREED if agreed else CHECK_CONFLICT, None if mine is None else str(_round2(mine)), str(_round2(theirs)), fact.where)
    if fact.kind == FACT_NAME:
        mine = reading.answer.get("name")
        agreed = mine is not None and _norm(mine) == _norm(fact.value)
        return DrawingCheck(CHECK_AGREED if agreed else CHECK_CONFLICT, mine, fact.value, fact.where)
    raise ExpertiseError(f"知らない種類の照合先です: {fact.kind}")


# ---------------------------------------------------------------------------
# 読み手(AI)をつなぐ
# ---------------------------------------------------------------------------

#: 既定のモデル。環境変数 `EXPERT_READER_MODEL` で差し替える。
DEFAULT_MODEL = "claude-opus-5"


class AnthropicReader:
    """Claude に読ませる読み手。**鍵が無ければ作れない**(`ReaderUnavailable`)。"""

    def __init__(self, model: str | None = None, client: Any = None) -> None:
        self.model = model or os.environ.get("EXPERT_READER_MODEL") or DEFAULT_MODEL
        self.reader_id = f"anthropic:{self.model}"
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise ReaderUnavailable(
                    "AI を呼ぶ鍵がありません(環境変数 ANTHROPIC_API_KEY が設定されていない)"
                )
            try:
                import anthropic  # noqa: PLC0415  入れていなくてもリポジトリは動く
            except ImportError as exc:
                raise ReaderUnavailable(
                    "AI を呼ぶ部品(anthropic)が入っていません。pip install anthropic で入ります"
                ) from exc
            client = anthropic.Anthropic()
        self._client = client

    def __call__(self, premise: str, request: ElementReadingRequest) -> str:
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
            for media_type, data in request.images
        ]
        content.append({"type": "text", "text": build_request_text(request)})
        response = self._client.messages.create(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=premise,
            messages=[{"role": "user", "content": content}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            return json.dumps({"status": STATUS_UNREADABLE, "reason": "読み手が答えを断った"}, ensure_ascii=False)
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def connect_reader() -> tuple[Reader | None, str]:
    """読み手をつなぐ。つなげなければ (None, 理由) を返す。"""
    try:
        return AnthropicReader(), ""
    except ReaderUnavailable as exc:
        return None, str(exc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="要素ごとに選ばれる知識と前提の文を見る")
    parser.add_argument("--element", default=ELEMENT_ROOM_AREA)
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--list", action="store_true", help="要素と、付いている知識の一覧を出す")
    args = parser.parse_args(argv)
    catalog = load_catalog(args.catalog)
    if args.list:
        for element in catalog.elements:
            ids = select_knowledge(catalog, element.element_id).knowledge_ids
            print(f"{element.element_id}: {', '.join(ids)}")
        return 0
    print(build_premise(select_knowledge(catalog, args.element)))
    _, why = connect_reader()
    print(f"\n読み手: {'つながる' if not why else why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
