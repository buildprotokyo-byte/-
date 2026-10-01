"""図面を、土台の知識の上に要素ごとの専門知識を足して、AI に専門家として読ませる段。

なぜこれを作るのか
------------------
本番の経路(`app.py`、K-27)には、**AI が図面を読む段が 1 つも無かった。**
おーちゃんの指示(2026-09-24): 図面は決まった書き方で描かれていて、専門家はその
書き方を知っているから読める。知識を前提に渡さずに読ませても、読めないのは当たり前。

知識は三層で持つ(おーちゃんの指示、2026-09-24 15:02)
------------------------------------------------------
1. **土台** … いつも前提に置く。製図の決まりの網羅・建築基準法の骨格・
   一級建築士や一級施工管理技士の資格が前提とする知識。要素によって外さない。
2. **図の種類の書き方** … 平面図・断面図・天井伏図などが、それぞれどう描かれるか。
3. **要素の専門知識** … 寸法・室の面積・記号など、読みたい要素に当てる知識。

読む順番(おーちゃんの決め。`docs/reading_stance.md` と検証ループに揃える)
----------------------------------------------------------------------
1. **図面の構造** … 外枠・表題欄・図の領域に分け、表題欄から何の図面か・縮尺・
   どこを描いたか・描き手の意図を読む
2. **図の種類の書き方** … その種類の専門知識で図を見に行く
3. **表現の根拠から推論** … 線・記号・書き方の根拠を探し、「こう書く決まりだから、
   ここにはこういうものがある」と推論する

**この順番を逆にすると分からなくなる。**だから答えには 1 段目の結果(図の種類・縮尺・
どこを描いたか・意図)を必ず書かせ、書いていなければ記録する。

この段がしないこと
------------------
- **何も確定させない。** 答えはいつも候補で、`confirmed` は常に False。
- **読み手が無いときに答えを作らない。**
- **AI の計算を信じない。** 面積はこちらで掛け算し直す。
- 知識で出した答えは「知識から出した」印を付け、図面自身と 1 か所で突き合わせる
  (`check_against_drawing`)。食い違えば図面を採る。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from knowledge.table import KnowledgeError, Source, _parse_source

#: 既定の目録。**中身はコードに書かない。**差し替えるときはパスを渡す。
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

CATALOG_VERSION = 2

#: 知識で読んだ答えに必ず付ける印。
ORIGIN_KNOWLEDGE = "知識から出した"

#: 読み手が返せる状態。
STATUS_READ = "読めた"
STATUS_UNREADABLE = "読めない"
STATUS_QUERY = "質疑にあたる"
READER_STATUSES = (STATUS_READ, STATUS_UNREADABLE, STATUS_QUERY)
#: 読み手がそもそも居ないとき(こちらが付ける。読み手は返せない)。
STATUS_NOT_CONNECTED = "読み手が未接続"

#: 読めなかった理由の 3 つ(`docs/reading_stance.md` 5 節)。**混ぜない。**
UNREADABLE_NO_RULE = "決まりが無い"
UNREADABLE_NO_IMPLEMENTATION = "決まりはあるが実装が無い"
UNREADABLE_DID_NOT_WORK = "実装はあるが動かなかった"
UNREADABLE_KINDS = (UNREADABLE_NO_RULE, UNREADABLE_NO_IMPLEMENTATION, UNREADABLE_DID_NOT_WORK)

#: 要点の出どころ。
POINT_UNCHECKED = "未照合"  # こちらの一般の理解。原文と照らしていない
POINT_FROM_OWNER = "おーちゃんの原文から"  # docs/drafting_rules_reference.md から取った
POINT_CHECKED = "照合済み"  # 原文を直接読んで照らした
POINT_STATUSES = (POINT_UNCHECKED, POINT_FROM_OWNER, POINT_CHECKED)

#: 知識の層。
LAYER_FOUNDATION = "土台"
LAYER_DRAWING_KIND = "図の種類の書き方"
LAYER_ELEMENT = "要素の専門知識"

#: 1 段目で必ず書かせる欄。
PAGE_STRUCTURE_FIELDS = ("drawing_kind", "scale", "subject", "intent", "title_block_evidence")

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

_CATALOG_FIELDS = {
    "catalog_version", "catalog_id", "description", "reading_order", "foundation",
    "roles", "drawing_kinds", "elements", "knowledge",
}
_STEP_FIELDS = {"step", "what"}
_ROLE_FIELDS = {"role_id", "covers"}
_KIND_FIELDS = {"kind_id", "description"}
_ELEMENT_FIELDS = {"element_id", "description"}
_KNOWLEDGE_FIELDS = {
    "knowledge_id", "domain", "source", "points", "point_status", "taken_from",
    "elements", "drawing_kinds", "roles", "exam_fields",
}


class ExpertiseError(Exception):
    """目録や答えがそのままでは使えない形だった。**黙って直さない。**"""


class ReaderUnavailable(Exception):
    """読み手(AI)を呼べない。理由を文にして持つ。"""


# ---------------------------------------------------------------------------
# 目録
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadingStep:
    step: str
    what: str


@dataclass(frozen=True)
class Role:
    role_id: str
    covers: str


@dataclass(frozen=True)
class DrawingKind:
    kind_id: str
    description: str


@dataclass(frozen=True)
class Element:
    element_id: str
    description: str


@dataclass(frozen=True)
class ExpertKnowledge:
    """知識 1 件。**出どころと要点だけ。**"""

    knowledge_id: str
    domain: str
    source: Source
    points: tuple[str, ...]
    point_status: str
    elements: tuple[str, ...]
    roles: tuple[str, ...]
    drawing_kinds: tuple[str, ...] = ()
    exam_fields: tuple[str, ...] = ()
    #: 要点をリポジトリのどの文書から取ったか(`おーちゃんの原文から` のとき必須)。
    taken_from: str = ""


@dataclass(frozen=True)
class ExpertiseCatalog:
    catalog_id: str
    reading_order: tuple[ReadingStep, ...]
    foundation: tuple[str, ...]
    roles: tuple[Role, ...]
    drawing_kinds: tuple[DrawingKind, ...]
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

    def drawing_kind(self, kind_id: str) -> DrawingKind:
        for kind in self.drawing_kinds:
            if kind.kind_id == kind_id:
                return kind
        raise ExpertiseError(
            f"知らない図の種類です: {kind_id}。"
            f"目録にあるのは {[k.kind_id for k in self.drawing_kinds]} です"
        )

    def uncovered_elements(self) -> tuple[str, ...]:
        """要素の専門知識が 1 件も付いていない要素(土台だけの知識は数えない)。"""
        base = set(self.foundation)
        covered = {e for k in self.knowledge if k.knowledge_id not in base for e in k.elements}
        return tuple(e.element_id for e in self.elements if e.element_id not in covered)

    def uncovered_drawing_kinds(self) -> tuple[str, ...]:
        """書き方の知識が 1 件も付いていない図の種類(土台だけの知識は数えない)。"""
        base = set(self.foundation)
        covered = {d for k in self.knowledge if k.knowledge_id not in base for d in k.drawing_kinds}
        return tuple(d.kind_id for d in self.drawing_kinds if d.kind_id not in covered)


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


def _pairs(raw_list: Any, fields: set[str], key: str, value: str, what: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in raw_list or ():
        if not isinstance(raw, Mapping):
            raise ExpertiseError(f"{what} の要素が辞書ではありません")
        _reject_unknown(raw, fields, f"{what} の要素")
        out.append((_text(raw.get(key), key), _text(raw.get(value), value)))
    ids = [k for k, _ in out]
    if not out or len(set(ids)) != len(ids):
        raise ExpertiseError(f"{what} が空か、{key} が重複しています")
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

    steps = [ReadingStep(s, w) for s, w in _pairs(payload.get("reading_order"), _STEP_FIELDS, "step", "what", "reading_order")]
    roles = [Role(r, c) for r, c in _pairs(payload.get("roles"), _ROLE_FIELDS, "role_id", "covers", "roles")]
    kinds = [DrawingKind(k, d) for k, d in _pairs(payload.get("drawing_kinds"), _KIND_FIELDS, "kind_id", "description", "drawing_kinds")]
    elements = [Element(e, d) for e, d in _pairs(payload.get("elements"), _ELEMENT_FIELDS, "element_id", "description", "elements")]
    role_ids = {r.role_id for r in roles}
    kind_ids = {k.kind_id for k in kinds}
    element_ids = {e.element_id for e in elements}

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
        taken_from = raw.get("taken_from", "")
        if not isinstance(taken_from, str):
            raise ExpertiseError(f"{knowledge_id} の taken_from は文字列である必要があります")
        if point_status == POINT_FROM_OWNER and not taken_from.strip():
            raise ExpertiseError(
                f"{knowledge_id} は {POINT_FROM_OWNER} なのに、どの文書から取ったか(taken_from)がありません"
            )
        used_elements = _texts(raw.get("elements"), f"{knowledge_id} の elements")
        missing = [e for e in used_elements if e not in element_ids]
        if missing:
            raise ExpertiseError(f"{knowledge_id} が目録に無い要素を指しています: {missing}")
        used_kinds = _texts(raw.get("drawing_kinds", ()), f"{knowledge_id} の drawing_kinds", allow_empty=True)
        missing = [k for k in used_kinds if k not in kind_ids]
        if missing:
            raise ExpertiseError(f"{knowledge_id} が目録に無い図の種類を指しています: {missing}")
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
                drawing_kinds=used_kinds,
                exam_fields=_texts(raw.get("exam_fields", ()), f"{knowledge_id} の exam_fields", allow_empty=True),
                taken_from=taken_from,
            )
        )
    if not knowledge:
        raise ExpertiseError("knowledge が空です")

    foundation = _texts(payload.get("foundation"), "foundation")
    missing = [f for f in foundation if f not in seen]
    if missing:
        raise ExpertiseError(f"foundation(土台)が目録に無い知識を指しています: {missing}")
    if len(set(foundation)) != len(foundation):
        raise ExpertiseError("foundation(土台)に同じ知識が 2 回あります")

    description = payload.get("description", "")
    if not isinstance(description, str):
        raise ExpertiseError("description は文字列である必要があります")
    return ExpertiseCatalog(
        catalog_id=_text(payload.get("catalog_id"), "catalog_id"),
        reading_order=tuple(steps),
        foundation=foundation,
        roles=tuple(roles),
        drawing_kinds=tuple(kinds),
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
# 知識を選ぶ(土台 + 図の種類 + 要素)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeSelection:
    """1 つの要素を読むために選んだ知識。層ごとに分けて持つ。**1 件は 1 つの層にだけ入る。**"""

    element: Element
    #: 読み手に渡した図の種類。None は「1 段目で読み手が決める」。
    drawing_kind: DrawingKind | None
    foundation: tuple[ExpertKnowledge, ...]
    kind_knowledge: tuple[ExpertKnowledge, ...]
    element_knowledge: tuple[ExpertKnowledge, ...]
    roles: tuple[Role, ...]

    @property
    def knowledge(self) -> tuple[ExpertKnowledge, ...]:
        return self.foundation + self.kind_knowledge + self.element_knowledge

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(k.knowledge_id for k in self.knowledge)

    def layer_of(self, knowledge_id: str) -> str | None:
        for layer, group in (
            (LAYER_FOUNDATION, self.foundation),
            (LAYER_DRAWING_KIND, self.kind_knowledge),
            (LAYER_ELEMENT, self.element_knowledge),
        ):
            if any(k.knowledge_id == knowledge_id for k in group):
                return layer
        return None


def select_knowledge(
    catalog: ExpertiseCatalog, element_id: str, drawing_kind: str | None = None
) -> KnowledgeSelection:
    """**土台はいつも全部入れる。**その上に図の種類と要素の知識を足す(選り好みしない)。"""
    element = catalog.element(element_id)
    kind = catalog.drawing_kind(drawing_kind) if drawing_kind is not None else None
    by_id = {k.knowledge_id: k for k in catalog.knowledge}
    foundation = tuple(by_id[f] for f in catalog.foundation)
    taken = set(catalog.foundation)
    kind_knowledge: tuple[ExpertKnowledge, ...] = ()
    if kind is not None:
        kind_knowledge = tuple(
            k for k in catalog.knowledge if k.knowledge_id not in taken and kind.kind_id in k.drawing_kinds
        )
        taken |= {k.knowledge_id for k in kind_knowledge}
    element_knowledge = tuple(
        k for k in catalog.knowledge if k.knowledge_id not in taken and element_id in k.elements
    )
    if not element_knowledge and not kind_knowledge:
        raise ExpertiseError(f"{element_id} に付いた専門知識が土台のほかに 1 件もありません")
    wanted = {r for k in foundation + kind_knowledge + element_knowledge for r in k.roles}
    roles = tuple(r for r in catalog.roles if r.role_id in wanted)
    return KnowledgeSelection(
        element=element,
        drawing_kind=kind,
        foundation=foundation,
        kind_knowledge=kind_knowledge,
        element_knowledge=element_knowledge,
        roles=roles,
    )


def _knowledge_block(group: Sequence[ExpertKnowledge]) -> str:
    blocks = []
    for k in group:
        s = k.source
        points = "\n".join(f"  - {p}" for p in k.points)
        blocks.append(f"[{k.knowledge_id}] {s.document} {s.clause}(拘束力: {s.binding})\n{points}")
    return "\n".join(blocks) if blocks else "(なし)"


def build_premise(selection: KnowledgeSelection, catalog: ExpertiseCatalog) -> str:
    """読み手に渡す前提の文。**前提の文はここ 1 か所でだけ作る。**"""
    roles = "\n".join(f"- {r.role_id}: {r.covers}" for r in selection.roles)
    order = "\n".join(f"{i}. {s.step}: {s.what}" for i, s in enumerate(catalog.reading_order, start=1))
    kinds = "\n".join(f"- {k.kind_id}: {k.description}" for k in catalog.drawing_kinds)
    if selection.drawing_kind is not None:
        kind_line = f"この図は「{selection.drawing_kind.kind_id}」として渡されています。1 段目で表題欄から確かめ、違えばそう書いてください。"
    else:
        kind_line = "図の種類は渡されていません。1 段目で表題欄と描き方から決めてください。"
    return f"""あなたたちは次の専門家の集まりとして、建築図面を読みます。
{roles}

図面は絵ではなく、製図の決まりに従って書かれた文書です。
下の【土台】は、どの要素を読むときもいつも前提として持つ知識です。
その上に【図の種類の書き方】と【要素の専門知識】を足して読みます。

必ずこの順番で読んでください。順番を逆にすると、決まりから外れた所が読めなくなります。
{order}

【図の種類】
{kinds}
{kind_line}

【土台】
{_knowledge_block(selection.foundation)}

【図の種類の書き方】
{_knowledge_block(selection.kind_knowledge)}

【要素の専門知識】いま読むのは「{selection.element.element_id}」です({selection.element.description})。
{_knowledge_block(selection.element_knowledge)}

守ること:
1. 一般の決まりは既定値です。この図面の凡例・表・特記・印字された数字が違うことを言っていれば、図面を採ってください。どちらの根拠で読んだかを書いてください。
2. 答えを出すのに使った知識は、上の [ ] の名前で必ず挙げてください。図面のどのページのどこを見たか、なぜそれがその対象に当たると判断したかも書いてください。
3. 図面に書かれていない数字を作らないでください。平面図に無いことは別の図に書かれていることがあります。「図面に無い」は「存在しない」ではありません。
4. 1 つの記号や語が 2 つ以上の意味に取れて図面の中で決まらないときは、1 つに決めず「質疑にあたる」と答え、理由を書いてください。
5. 読めないときは「読めない」と答え、どの知識を使おうとしたか、その決まりではどう描かれているはずだったか、実際はどう違ったかを書いてください。
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
    #: 図の種類が分かっていれば渡す。None なら 1 段目で読み手が決める。
    drawing_kind: str | None = None

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
  "page_structure": {{
    "drawing_kind": "1 段目で決めた図の種類",
    "scale": "縮尺(例: 1:50)。書かれていなければ 不明",
    "subject": "どこを描いた図か(階・範囲)",
    "intent": "何のための図か(描き手の意図)",
    "title_block_evidence": "表題欄や図のタイトルのどこを見たか"
  }},
  "status": "{STATUS_READ} / {STATUS_UNREADABLE} / {STATUS_QUERY} のどれか",
  "answer": {request.answer_shape},
  "used_knowledge": ["使った知識の [ ] の名前"],
  "drawing_evidence": ["図面のどのページのどこを見たか"],
  "reason": "なぜそれがその対象に当たると判断したか(どの決まりから何を推論したか)",
  "unreadable": {{
    "kind": "{' / '.join(UNREADABLE_KINDS)} のどれか(読めないときだけ)",
    "tried_knowledge": ["使おうとした知識の名前"],
    "expected_by_rule": "その決まりではどう描かれているはずだったか",
    "actual": "実際の図面はどう違ったか"
  }}
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
    #: 1 段目(図面の構造)の結果。
    page_structure: Mapping[str, str]
    used_knowledge: tuple[str, ...]
    #: 目録で選んでいないのに読み手が挙げた名前(作った名前かもしれない)。
    unknown_knowledge: tuple[str, ...]
    drawing_evidence: tuple[str, ...]
    reason: str
    selected_knowledge: tuple[str, ...]
    reader_id: str
    #: 使った知識を層ごとに数えたもの(土台 / 図の種類の書き方 / 要素の専門知識)。
    used_by_layer: Mapping[str, int] = None  # type: ignore[assignment]
    #: 読めなかったときの中身。
    unreadable: Mapping[str, Any] = None  # type: ignore[assignment]
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
    raw_structure = payload.get("page_structure") or {}
    if not isinstance(raw_structure, Mapping):
        raise ExpertiseError("page_structure が辞書ではありません")
    structure = {f: str(raw_structure.get(f) or "") for f in PAGE_STRUCTURE_FIELDS}
    named = tuple(str(x) for x in payload.get("used_knowledge") or ())
    selected = set(selection.knowledge_ids)
    used = tuple(n for n in named if n in selected)
    evidence = tuple(str(x) for x in payload.get("drawing_evidence") or ())
    unreadable = payload.get("unreadable") or {}
    if not isinstance(unreadable, Mapping):
        raise ExpertiseError("unreadable が辞書ではありません")

    notes: list[str] = []
    missing = [f for f in ("drawing_kind", "scale", "subject", "intent") if not structure[f].strip()]
    if missing:
        notes.append(f"1 段目(図面の構造)を飛ばしている: {', '.join(missing)} が無い")
    if selection.drawing_kind is not None and structure["drawing_kind"].strip() and (
        structure["drawing_kind"].strip() != selection.drawing_kind.kind_id
    ):
        notes.append(
            f"渡した図の種類({selection.drawing_kind.kind_id})と、読み手が表題欄から決めた種類"
            f"({structure['drawing_kind']})が違う"
        )
    if status == STATUS_READ and not named:
        notes.append("使った知識を挙げていない")
    if status == STATUS_READ and not evidence:
        notes.append("図面のどこを見たかを挙げていない")
    if status == STATUS_UNREADABLE and unreadable.get("kind") not in UNREADABLE_KINDS:
        notes.append("読めなかった理由が 3 つ(決まりが無い / 決まりはあるが実装が無い / 実装はあるが動かなかった)のどれでもない")

    by_layer = {LAYER_FOUNDATION: 0, LAYER_DRAWING_KIND: 0, LAYER_ELEMENT: 0}
    for knowledge_id in used:
        layer = selection.layer_of(knowledge_id)
        if layer is not None:
            by_layer[layer] += 1
    return ExpertReading(
        element_id=selection.element.element_id,
        status=status,
        answer=dict(answer) if status == STATUS_READ else {},
        page_structure=structure,
        used_knowledge=used,
        unknown_knowledge=tuple(n for n in named if n not in selected),
        drawing_evidence=evidence,
        reason=str(payload.get("reason") or ""),
        selected_knowledge=selection.knowledge_ids,
        reader_id=reader_id,
        used_by_layer=by_layer,
        unreadable=dict(unreadable) if status == STATUS_UNREADABLE else {},
        notes=tuple(notes),
    )


def not_connected(selection: KnowledgeSelection, why: str) -> ExpertReading:
    return ExpertReading(
        element_id=selection.element.element_id,
        status=STATUS_NOT_CONNECTED,
        answer={},
        page_structure={},
        used_knowledge=(),
        unknown_knowledge=(),
        drawing_evidence=(),
        reason=why,
        selected_knowledge=selection.knowledge_ids,
        reader_id="",
        used_by_layer={},
        unreadable={},
    )


def read_element(
    catalog: ExpertiseCatalog,
    request: ElementReadingRequest,
    reader: Reader | None,
    *,
    reader_id: str = "",
    unavailable_reason: str = "読み手が渡されていません",
) -> ExpertReading:
    """土台と、図の種類・要素に合う知識を選び、前提にして読ませる。読み手が無ければ答えを作らない。"""
    selection = select_knowledge(catalog, request.element_id, request.drawing_kind)
    if reader is None:
        return not_connected(selection, unavailable_reason)
    premise = build_premise(selection, catalog)
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
    drawing_kind: str | None = "平面図",
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
        drawing_kind=drawing_kind,
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
    parser.add_argument("--kind", default=None, help="図の種類(平面図・断面図など)")
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--list", action="store_true", help="要素と、付いている知識の一覧を出す")
    args = parser.parse_args(argv)
    catalog = load_catalog(args.catalog)
    if args.list:
        print(f"土台(いつも入る): {', '.join(catalog.foundation)}")
        for kind in catalog.drawing_kinds:
            sel = select_knowledge(catalog, "寸法", kind.kind_id)
            print(f"図の種類 {kind.kind_id}: {', '.join(k.knowledge_id for k in sel.kind_knowledge)}")
        for element in catalog.elements:
            sel = select_knowledge(catalog, element.element_id)
            print(f"要素 {element.element_id}: {', '.join(k.knowledge_id for k in sel.element_knowledge)}")
        return 0
    print(build_premise(select_knowledge(catalog, args.element, args.kind), catalog))
    _, why = connect_reader()
    print(f"\n読み手: {'つながる' if not why else why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
