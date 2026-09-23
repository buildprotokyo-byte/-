"""知識の表の読み込みと検査。

**この層は判定に一切つながっていない。**読み込むだけである
(`docs/d_knowledge_format_criteria.md` 5節)。知識を判定に効かせるのは別の周。

書式は `docs/knowledge/format.md`。リポジトリに置くのは
`knowledge/examples/synthetic_knowledge.json`(**架空の見本**)だけで、
実在の会社のルールや実案件から作った表は置かない。パスを設定で渡す。

守ること
--------
1. **知らない列があったら断る。**黙って読み飛ばすと、半分だけ効いた表になる。
2. **出どころが無ければ断る。**後から「なぜそう決めたか」を辿れなくなる。
3. **知らない `kind` は断る。**
4. **`format_version` が違えば読まない。**
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

#: この読み込みが解釈できる表の版。
KNOWLEDGE_FORMAT_VERSION = 1

KIND_COUNTING = "数え方"
KIND_PROPAGATION = "波及"
KIND_QUESTION = "問い"
KINDS = (KIND_COUNTING, KIND_PROPAGATION, KIND_QUESTION)

CONFIDENCES = ("一次資料", "慣行", "見立て")
SCOPES = ("公共工事", "住宅改修", "自社")

_TABLE_FIELDS = {"format_version", "table_id", "description", "synthetic", "entries"}
_ENTRY_FIELDS = {
    "entry_id", "kind", "statement", "source", "confidence", "scope",
    "applies_to", "overridden_by", "needs_human_check", "detail", "note",
}
_SOURCE_FIELDS = {"document", "clause", "url", "read_directly"}
_APPLIES_FIELDS = {"work_kinds", "parts", "axes"}
_DETAIL_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    # kind: (必須, 任意)
    KIND_COUNTING: ({"unit", "method"}, {"threshold", "excluded"}),
    KIND_PROPAGATION: ({"trigger", "affected", "extent", "symmetric"}, set()),
    KIND_QUESTION: ({"question", "answer_options", "what_changes"}, set()),
}


class KnowledgeError(Exception):
    """表がそのままでは使えない形だった。**黙って直さない。**"""


@dataclass(frozen=True)
class Source:
    """出どころ。**空を許さない。**"""

    document: str
    clause: str
    url: str = ""
    read_directly: bool = False


@dataclass(frozen=True)
class AppliesTo:
    """どこに掛かるか。**3 つとも空は許さない。**"""

    work_kinds: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()
    axes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.work_kinds or self.parts or self.axes)


@dataclass(frozen=True)
class KnowledgeEntry:
    """知識 1 件。"""

    entry_id: str
    kind: str
    statement: str
    source: Source
    confidence: str
    scope: str
    applies_to: AppliesTo
    detail: Mapping[str, Any]
    needs_human_check: bool
    overridden_by: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class KnowledgeTable:
    """表 1 つ。"""

    table_id: str
    entries: tuple[KnowledgeEntry, ...]
    synthetic: bool
    description: str = ""
    source_path: Path | None = None

    def of_kind(self, kind: str) -> tuple[KnowledgeEntry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == kind)


def _require_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{what} が必要です")
    return value


def _string_tuple(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise KnowledgeError(f"{what} は文字列の並びである必要があります")
    out: list[str] = []
    for item in value:
        out.append(_require_text(item, f"{what} の要素"))
    return tuple(out)


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str], what: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise KnowledgeError(
            f"{what} に知らない列があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた表になるので受け付けません"
        )


def _parse_source(raw: Any) -> Source:
    if not isinstance(raw, Mapping):
        raise KnowledgeError("source が辞書ではありません。出どころは必ず要ります")
    _reject_unknown(raw, _SOURCE_FIELDS, "source")
    read_directly = raw.get("read_directly")
    if not isinstance(read_directly, bool):
        raise KnowledgeError("source.read_directly は true か false で書いてください")
    url = raw.get("url", "")
    if not isinstance(url, str):
        raise KnowledgeError("source.url は文字列である必要があります")
    return Source(
        document=_require_text(raw.get("document"), "source.document"),
        clause=_require_text(raw.get("clause"), "source.clause"),
        url=url,
        read_directly=read_directly,
    )


def _parse_applies_to(raw: Any) -> AppliesTo:
    if not isinstance(raw, Mapping):
        raise KnowledgeError("applies_to が辞書ではありません")
    _reject_unknown(raw, _APPLIES_FIELDS, "applies_to")
    applies = AppliesTo(
        work_kinds=_string_tuple(raw.get("work_kinds"), "applies_to.work_kinds"),
        parts=_string_tuple(raw.get("parts"), "applies_to.parts"),
        axes=_string_tuple(raw.get("axes"), "applies_to.axes"),
    )
    if applies.is_empty:
        raise KnowledgeError(
            "applies_to が空です。どこにも掛からない知識は使いようがありません"
        )
    return applies


def _parse_detail(kind: str, raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise KnowledgeError("detail が辞書ではありません")
    required, optional = _DETAIL_FIELDS[kind]
    _reject_unknown(raw, required | optional, f"detail({kind})")
    missing = required - set(raw)
    if missing:
        raise KnowledgeError(f"detail({kind}) に {sorted(missing)} がありません")
    if kind == KIND_QUESTION:
        options = _string_tuple(raw.get("answer_options"), "detail.answer_options")
        if len(options) < 2:
            raise KnowledgeError(
                "detail.answer_options は 2 つ以上必要です。"
                "答えが 1 つしかない問いは、聞く意味がありません"
            )
    if kind == KIND_PROPAGATION:
        if not isinstance(raw.get("symmetric"), bool):
            raise KnowledgeError("detail.symmetric は true か false で書いてください")
        _string_tuple(raw.get("affected"), "detail.affected")
    return dict(raw)


def _parse_entry(raw: Any) -> KnowledgeEntry:
    if not isinstance(raw, Mapping):
        raise KnowledgeError("entries の要素が辞書ではありません")
    _reject_unknown(raw, _ENTRY_FIELDS, "entries の要素")

    kind = _require_text(raw.get("kind"), "kind")
    if kind not in KINDS:
        raise KnowledgeError(
            f"知らない kind です: {kind}。読めるのは {list(KINDS)} だけです"
        )

    confidence = _require_text(raw.get("confidence"), "confidence")
    if confidence not in CONFIDENCES:
        raise KnowledgeError(f"知らない confidence です: {confidence}")

    scope = _require_text(raw.get("scope"), "scope")
    if scope not in SCOPES:
        raise KnowledgeError(f"知らない scope です: {scope}")

    needs_human_check = raw.get("needs_human_check")
    if not isinstance(needs_human_check, bool):
        raise KnowledgeError("needs_human_check は true か false で書いてください")

    note = raw.get("note", "")
    if not isinstance(note, str):
        raise KnowledgeError("note は文字列である必要があります")

    return KnowledgeEntry(
        entry_id=_require_text(raw.get("entry_id"), "entry_id"),
        kind=kind,
        statement=_require_text(raw.get("statement"), "statement"),
        source=_parse_source(raw.get("source")),
        confidence=confidence,
        scope=scope,
        applies_to=_parse_applies_to(raw.get("applies_to")),
        detail=_parse_detail(kind, raw.get("detail")),
        needs_human_check=needs_human_check,
        overridden_by=_string_tuple(raw.get("overridden_by"), "overridden_by"),
        note=note,
    )


def parse_knowledge(payload: Any, *, source_path: Path | None = None) -> KnowledgeTable:
    """読み込んだ内容を検査して `KnowledgeTable` にする。"""
    if not isinstance(payload, Mapping):
        raise KnowledgeError("知識の表の中身が辞書ではありません")
    _reject_unknown(payload, _TABLE_FIELDS, "表")

    version = payload.get("format_version")
    if version != KNOWLEDGE_FORMAT_VERSION:
        raise KnowledgeError(
            f"format_version が {version!r} です。"
            f"この読み込みが解釈できるのは {KNOWLEDGE_FORMAT_VERSION} だけです"
        )

    table_id = _require_text(payload.get("table_id"), "table_id")

    synthetic = payload.get("synthetic")
    if not isinstance(synthetic, bool):
        raise KnowledgeError(
            "synthetic は true か false で書いてください。"
            "表自身が架空かどうかを名乗ること"
        )

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
        raise KnowledgeError("entries は知識の並びである必要があります")
    if not raw_entries:
        raise KnowledgeError("entries が空です")

    entries: list[KnowledgeEntry] = []
    seen: set[str] = set()
    for raw in raw_entries:
        entry = _parse_entry(raw)
        if entry.entry_id in seen:
            raise KnowledgeError(f"entry_id が重複しています: {entry.entry_id}")
        seen.add(entry.entry_id)
        entries.append(entry)

    description = payload.get("description", "")
    if not isinstance(description, str):
        raise KnowledgeError("description は文字列である必要があります")

    return KnowledgeTable(
        table_id=table_id,
        entries=tuple(entries),
        synthetic=synthetic,
        description=description,
        source_path=source_path,
    )


def load_knowledge(path: Path | str) -> KnowledgeTable:
    """ファイルから読む。**パスは設定で渡す。コードに埋め込まない。**"""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeError(f"知識の表が見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"知識の表が JSON として読めません: {path}: {exc}") from exc
    return parse_knowledge(payload, source_path=path)
