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
4. **`format_version` が違えば読まない。**版 1 の表には、足す列を名指しして断る。
5. **採否の状態(`adoption_status`)は読むだけ。**無ければ断り、既定値で埋めない。
   `候補` から `採用` / `不採用` に変えるのはおーちゃんだけで、コードは書き換えない
   (K-04 6 番)。この列も `confidence` も、採否を決める根拠に使わない。
6. **`数え方` の単位は 1 つだけ。**複合(`個・組` など)は断り、単位ごとに 1 件へ
   分けてもらう(K-06 1 番)。単位の書き方は自由な言葉のままで、
   `AxisEvidence.unit` の正規形(`count` / `mm` / `cm2` / `yen`)には直さない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date
from typing import Any, Mapping, Sequence

#: この読み込みが解釈できる表の版。
KNOWLEDGE_FORMAT_VERSION = 2

KIND_COUNTING = "数え方"
KIND_PROPAGATION = "波及"
KIND_QUESTION = "問い"
KINDS = (KIND_COUNTING, KIND_PROPAGATION, KIND_QUESTION)

CONFIDENCES = ("一次資料", "慣行", "見立て")
SCOPES = ("公共工事", "住宅改修", "自社")

#: 出典の拘束力(`source.binding`)。強いものから順に並べる。
#: **値を足すときはここだけを変える**(読み込みの検査・断るときの案内・テストはこの一覧に付いてくる)。
#: `社内見立て` は、公開の基準でも社外の資料でもなく、社内で案件の突き合わせなどから
#: 起こした見立て(K-10 5 番)。会社として決めたもの(社内規程)が出てきたら、値を足して分ける。
BINDINGS = ("法令", "行政基準", "業界指針", "任意資料", "社内見立て")

#: 採否の状態。**読める値の一覧であって、コードが書き込む値ではない。**
#: 新しく書く知識は `候補`。`採用` / `不採用` に変えるのはおーちゃんだけ。
ADOPTION_STATUSES = ("候補", "採用", "不採用")

_TABLE_FIELDS = {"format_version", "table_id", "description", "synthetic", "entries"}
_ENTRY_FIELDS = {
    "entry_id", "kind", "statement", "source", "confidence", "scope",
    "applies_to", "overridden_by", "needs_human_check", "detail", "note",
    "adoption_status",
}
_SOURCE_FIELDS = {
    "document", "clause", "url", "read_directly",
    "publisher", "edition", "checked_on", "binding",
}

#: 版 1 から版 2 で足した列(版 1 の表を断るときに名指しする)。
_ADDED_IN_V2 = (
    "source.publisher(発行元)",
    "source.edition(出典の版・年)",
    "source.checked_on(確認日、YYYY-MM-DD。記録が無ければ null で 不明)",
    f"source.binding(拘束力: {' / '.join(BINDINGS)})",
    "adoption_status(採否の状態。新しく書くときは 候補)",
)

_CHECKED_ON = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: 単位に入っていたら「2 つ以上の単位」とみなす文字。
_UNIT_SEPARATORS = ("・", "、", ",", ",", "/", "/", "等")
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
    publisher: str
    edition: str
    #: 確認日(YYYY-MM-DD)。None は 不明(確認した日の記録が無い。推して埋めない)。
    checked_on: str | None
    binding: str
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
    adoption_status: str
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


def _checked_on(raw: Mapping[str, Any]) -> str | None:
    """確認日を読む。**null は 不明。**列そのものは必須(消したら断る)。

    不明 の書き方は null の 1 つだけ。空文字や「不明」と書いた文字は断る。
    """
    what = "source.checked_on(確認日)"
    if "checked_on" not in raw:
        raise KnowledgeError(
            f"{what} が必要です。確認した日の記録が無ければ null(不明)と書いてください"
        )
    value = raw["checked_on"]
    if value is None:
        return None
    if not isinstance(value, str) or not _CHECKED_ON.fullmatch(value):
        raise KnowledgeError(
            f"{what} は YYYY-MM-DD で書いてください(例: 2026-09-23)。"
            f"確認した日の記録が無ければ null(不明)と書きます。いまは {value!r} です"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise KnowledgeError(f"{what} が実在しない日付です: {value}") from exc
    return value


def _single_unit(value: Any, what: str) -> str:
    """単位を 1 つだけ受け取る。**書き方は自由な言葉のまま。正規形には直さない。**"""
    unit = _require_text(value, what)
    stripped = unit.strip()
    if any(sep in stripped for sep in _UNIT_SEPARATORS) or re.search(r"\s", stripped):
        raise KnowledgeError(
            f"{what} が {unit!r} です。単位は 1 つだけ書いてください。"
            "2 つ以上の単位にまたがるなら、単位ごとに 1 件へ分けてください"
            "(format.md 5節「1 件に 2 つのことを書かない」)"
        )
    return unit


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
    binding = raw.get("binding")
    if binding not in BINDINGS:
        raise KnowledgeError(
            f"source.binding(拘束力)が {binding!r} です。"
            f"書けるのは {list(BINDINGS)} のどれかです"
        )
    return Source(
        document=_require_text(raw.get("document"), "source.document"),
        clause=_require_text(raw.get("clause"), "source.clause"),
        publisher=_require_text(raw.get("publisher"), "source.publisher(発行元)"),
        edition=_require_text(raw.get("edition"), "source.edition(出典の版・年)"),
        checked_on=_checked_on(raw),
        binding=binding,
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
    if kind == KIND_COUNTING:
        _single_unit(raw.get("unit"), "detail.unit")
        threshold = raw.get("threshold")
        if isinstance(threshold, Mapping) and "unit" in threshold:
            _single_unit(threshold["unit"], "detail.threshold.unit")
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
        extent = raw.get("extent")
        if isinstance(extent, Mapping) and "unit" in extent:
            _single_unit(extent["unit"], "detail.extent.unit")
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

    # 読むだけ。無ければ断り、既定値では埋めない(書く側が 候補 と書く)。
    adoption_status = raw.get("adoption_status")
    if adoption_status not in ADOPTION_STATUSES:
        raise KnowledgeError(
            f"adoption_status(採否の状態)が {adoption_status!r} です。"
            f"書けるのは {list(ADOPTION_STATUSES)} のどれかで、新しく書く知識は 候補 です"
        )

    # 確認日が空欄(null)の知識は、採用の判断に進めない(おーちゃんの K-07 5 番)。
    # 候補としては読めるが、候補から先の状態は断る。
    source = _parse_source(raw.get("source"))
    if source.checked_on is None and adoption_status != "候補":
        raise KnowledgeError(
            f"source.checked_on(確認日)が null(不明)なのに adoption_status が "
            f"{adoption_status!r} です。確認日が分からない知識は採用の判断に進めません。"
            "候補 のままにするか、確認した日を書いてください"
        )

    return KnowledgeEntry(
        entry_id=_require_text(raw.get("entry_id"), "entry_id"),
        kind=kind,
        statement=_require_text(raw.get("statement"), "statement"),
        source=source,
        confidence=confidence,
        scope=scope,
        applies_to=_parse_applies_to(raw.get("applies_to")),
        detail=_parse_detail(kind, raw.get("detail")),
        needs_human_check=needs_human_check,
        adoption_status=adoption_status,
        overridden_by=_string_tuple(raw.get("overridden_by"), "overridden_by"),
        note=note,
    )


def parse_knowledge(payload: Any, *, source_path: Path | None = None) -> KnowledgeTable:
    """読み込んだ内容を検査して `KnowledgeTable` にする。"""
    if not isinstance(payload, Mapping):
        raise KnowledgeError("知識の表の中身が辞書ではありません")
    _reject_unknown(payload, _TABLE_FIELDS, "表")

    version = payload.get("format_version")
    if version == 1:
        raise KnowledgeError(
            f"format_version が 1 です。この読み込みが解釈できるのは "
            f"{KNOWLEDGE_FORMAT_VERSION} だけです。版 1 の表は、各知識に "
            + "、".join(_ADDED_IN_V2)
            + f" を足し、format_version を {KNOWLEDGE_FORMAT_VERSION} にしてください"
        )
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
