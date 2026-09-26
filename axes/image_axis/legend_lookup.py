"""凡例から写した**対照表**で、図面の文字と線を照合する(K-20)。

この部品がしないこと
--------------------
**名前を作らない。**近いものを探して当てにいかない。11 周目は凡例のページから
名前と形の対を作らせ、1 つの名前に 474 通りの形が付いた。原因は「当てにいった」
ことなので、ここは**完全一致だけを一致**とし、決まらないものは「不明」と言う。

「不明」は失敗ではなく**答え**である。名前が付かなかった件数は、名前が付いた件数と
同じだけ大事なので、`summarize` が両方とも数える。

対照表の出どころ
----------------
対照表は `benchmarks/build_legend_lookup.py` が凡例のページから写したもので、
**その案件の図面が自分で名乗っている意味**である。ほかの案件には使えないので、
`binding` は `案件の凡例` でなければ読み込まない。**表の中身はここに書かない。**
読み込む先は引数で渡す。

線の扱い(おーちゃんの決め、2026-09-24 01:12 に置き換わった)
------------------------------------------------------------
はじめの決め(K-20 4 番)は「**刻みの比率**が凡例の見本と合うものだけを一致とし、
太さと色は参考にとどめる」だった。**これは、この図面が色で描き分けていると分かる前の
決めである。**凡例が自分で「配線・シンボル色」として色の意味を書いていることが分かり、
おーちゃんが札で「**色で見てよい**」を選んだので、そちらに置き換える。

**凡例が名乗っている色と図面の線の色が合うものだけに意味を付け、合わない色は「不明」。**
色の値は凡例から読む。**こちらで「赤はふつう撤去だろう」と決めない。**
刻みの比率で引き当てる道(`match_line_styles`)は残してあるが、この図面には
線種の見本が無いので 0 件である。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 名前が付かなかったときに出す語。**推し量った名前を入れない。**
UNKNOWN = "不明"

REASON_NOT_IN_TABLE = "対照表に無い"
REASON_AMBIGUOUS = "対照表で1つに決まらない"
REASON_NO_SAMPLE = "凡例に見本が無い"
REASON_UNDISTINGUISHABLE = "記号として見分けが付かない形"

#: 名前の**出どころ**。K-22 の判断 1 で、おーちゃんが「知識から出した名前には必ず印を
#: 付け、人には別扱いで見せる」と決めた。`summarize` が出どころごとに数える。
SOURCE_TABLE = "対照表"
SOURCE_KNOWLEDGE = "知識"

KIND_WORK = "工事の区分"
KIND_EQUIPMENT = "設備"
KIND_LINE_STYLE = "線種"
KIND_LINE_COLOR = "線の色"

#: 読み込んでよい拘束力。**この案件限りの知識**であることを表す。
BINDING_CASE_LEGEND = "案件の凡例"

#: 刻みの比率が合っているとみなす相対のずれ。
RATIO_TOLERANCE = 0.1

#: 色が同じとみなすずれ(0〜1 の各成分)。
COLOR_TOLERANCE = 0.02

#: **仮の判断(K-20、おーちゃんの判断待ち)。**設備の記号は図面では「描かれた形」で、
#: 文字はその付け札にすぎない。1 文字の語は室番号や符号としても、数字だけの語は寸法としても
#: 出るので、**文字だけでは記号と見分けが付かない。**そういう語は「不明」にする。
#: **工事の区分は事情が違う**(凡例が「語をそのまま書く」と決めている印)ので落とさない。
#: `strict_equipment_codes=False` で外して測り直せる。
MIN_EQUIPMENT_CODE_LENGTH = 2

_HAS_NON_DIGIT = re.compile(r"[^0-9]")


def distinguishable(code: str) -> bool:
    """その語が、図面の中で記号として見分けが付く形か。"""
    body = normalize(code)
    return len(body) >= MIN_EQUIPMENT_CODE_LENGTH and bool(_HAS_NON_DIGIT.search(body))


def normalize(text: str) -> str:
    """全角・半角と空白のゆれだけを均す。**語の中身は変えない。**"""
    return "".join(unicodedata.normalize("NFKC", text or "").split())


@dataclass(frozen=True)
class LegendMatch:
    """1 つの文字(または線)を照合した結果。"""

    text: str
    kind: str
    name: str | None = None
    meaning: str | None = None
    group: str | None = None
    source_pages: tuple[int, ...] = ()
    reason: str | None = None
    #: この名前がどこから出たか。**知識の道から出したものは印が付く**(K-22 判断 1)。
    source: str = SOURCE_TABLE
    #: **決めてはいけない**箇所か。名前が 2 つ出た行がこれになる(K-22 判断 3)。
    #: ここが True の箇所は、知識の道へも回さない。**選ばずに人へ聞く。**
    to_question: bool = False

    @property
    def matched(self) -> bool:
        return self.name is not None

    @property
    def display_name(self) -> str:
        return self.name if self.name is not None else UNKNOWN

    @property
    def source_page(self) -> int | None:
        return self.source_pages[0] if self.source_pages else None


@dataclass(frozen=True)
class LegendCounts:
    total: int
    named: int
    unknown: int
    by_reason: dict[str, int] = field(default_factory=dict)
    #: 名前が付いた件数の**出どころごとの内訳**(K-22 判断 1)。
    by_source: dict[str, int] = field(default_factory=dict)
    #: 質疑へ回す件数(K-22 判断 3)。
    questions: int = 0


def _entries(payload: Any, keys: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    rows = payload if isinstance(payload, list) else []
    return tuple(row for row in rows if isinstance(row, dict) and all(k in row for k in keys))


@dataclass(frozen=True)
class LegendTable:
    binding: str
    work_marks: tuple[dict[str, Any], ...] = ()
    symbols: tuple[dict[str, Any], ...] = ()
    line_colors: tuple[dict[str, Any], ...] = ()
    line_styles: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LegendTable":
        binding = payload.get("binding")
        if binding != BINDING_CASE_LEGEND:
            raise ValueError(
                f"この対照表は凡例から写したものなので、binding は "
                f"{BINDING_CASE_LEGEND!r} でなければなりません(今は {binding!r})"
            )
        return cls(
            binding=binding,
            work_marks=_entries(payload.get("work_marks"), ("code", "meaning")),
            symbols=_entries(payload.get("symbols"), ("code", "name")),
            line_colors=_entries(payload.get("line_colors"), ("color", "meaning")),
            line_styles=_entries(payload.get("line_styles"), ("label", "dashes")),
        )

    @classmethod
    def load(cls, path: str | Path) -> "LegendTable":
        return cls.from_payload(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def mark_count(self) -> int:
        return len(self.work_marks) + len(self.symbols)


def _lookup(
    text: str, rows: Iterable[dict[str, Any]], name_key: str
) -> list[dict[str, Any]]:
    key = normalize(text)
    return [row for row in rows if normalize(str(row.get("code", ""))) == key and row.get(name_key)]


def _collapse(hits: list[dict[str, Any]], name_key: str) -> tuple[str | None, tuple[int, ...]]:
    """**同じ表の同じ対は 1 つに畳む。**別の名前が並んだら決められない。"""
    names = {str(hit[name_key]) for hit in hits}
    if len(names) != 1:
        return None, ()
    pages = tuple(sorted({int(hit["source_page"]) for hit in hits if "source_page" in hit}))
    return names.pop(), pages


def match_marks(
    texts: Iterable[str],
    table: LegendTable,
    *,
    strict_equipment_codes: bool = True,
) -> tuple[LegendMatch, ...]:
    """図面から拾った文字を、対照表に**完全一致で**引き当てる。

    `strict_equipment_codes` は上の `MIN_EQUIPMENT_CODE_LENGTH` の**仮の判断**を
    効かせるかどうか。既定は効かせる。
    """
    out: list[LegendMatch] = []
    for text in texts:
        for rows, name_key, kind in (
            (table.work_marks, "meaning", KIND_WORK),
            (table.symbols, "name", KIND_EQUIPMENT),
        ):
            hits = _lookup(text, rows, name_key)
            if not hits:
                continue
            if (
                kind == KIND_EQUIPMENT
                and strict_equipment_codes
                and not distinguishable(text)
            ):
                out.append(
                    LegendMatch(
                        text=text, kind=kind, reason=REASON_UNDISTINGUISHABLE
                    )
                )
                break
            value, pages = _collapse(hits, name_key)
            if value is None:
                # **判断 3(K-22)。**名前が 2 つ出た行は、どちらかを選ばずに質疑へ回す。
                out.append(
                    LegendMatch(
                        text=text,
                        kind=kind,
                        reason=REASON_AMBIGUOUS,
                        to_question=True,
                    )
                )
                break
            groups = {str(hit.get("group", "")) for hit in hits}
            out.append(
                LegendMatch(
                    text=text,
                    kind=kind,
                    name=normalize(text) if kind == KIND_WORK else value,
                    meaning=value if kind == KIND_WORK else None,
                    group=groups.pop() if len(groups) == 1 else None,
                    source_pages=pages,
                )
            )
            break
        else:
            out.append(
                LegendMatch(text=text, kind=KIND_WORK, reason=REASON_NOT_IN_TABLE)
            )
    return tuple(out)


def _ratio(dashes: Sequence[float]) -> tuple[float, ...]:
    values = [float(v) for v in dashes if float(v) > 0]
    if not values:
        return ()
    head = values[0]
    return tuple(round(v / head, 4) for v in values)


def match_line_styles(
    patterns: Iterable[Sequence[float]], table: LegendTable
) -> tuple[LegendMatch, ...]:
    """線の**刻みの比率**だけで引き当てる。太さと色は見ない(K-20 4 番)。"""
    samples = [(str(row["label"]), _ratio(row["dashes"])) for row in table.line_styles]
    out: list[LegendMatch] = []
    for pattern in patterns:
        text = ",".join(str(v) for v in pattern)
        if not samples:
            out.append(
                LegendMatch(text=text, kind=KIND_LINE_STYLE, reason=REASON_NO_SAMPLE)
            )
            continue
        wanted = _ratio(pattern)
        hits = [
            label
            for label, ratio in samples
            if len(ratio) == len(wanted)
            and all(abs(a - b) <= RATIO_TOLERANCE * max(1.0, b) for a, b in zip(wanted, ratio))
        ]
        if len(set(hits)) == 1:
            out.append(LegendMatch(text=text, kind=KIND_LINE_STYLE, name=hits[0]))
        else:
            out.append(
                LegendMatch(
                    text=text,
                    kind=KIND_LINE_STYLE,
                    reason=REASON_AMBIGUOUS if hits else REASON_NOT_IN_TABLE,
                    to_question=bool(hits),
                )
            )
    return tuple(out)


def match_line_colors(
    colors: Iterable[Sequence[float]], table: LegendTable
) -> tuple[LegendMatch, ...]:
    """線の色を、凡例が名乗っている色に引き当てる。

    **合わない色は「不明」。**近い色に寄せない(`COLOR_TOLERANCE` は、同じ色が
    刷りの都合でわずかにずれる分だけ)。
    """
    out: list[LegendMatch] = []
    for colour in colors:
        text = ",".join(str(round(float(c), 4)) for c in colour)
        hits = [
            row
            for row in table.line_colors
            if len(row["color"]) == len(colour)
            and all(
                abs(float(a) - float(b)) <= COLOR_TOLERANCE
                for a, b in zip(row["color"], colour)
            )
        ]
        meanings = {str(hit["meaning"]) for hit in hits}
        labels = {str(hit.get("label", "")) for hit in hits}
        if not hits:
            out.append(
                LegendMatch(
                    text=text, kind=KIND_LINE_COLOR, reason=REASON_NOT_IN_TABLE
                )
            )
            continue
        if len(meanings) != 1 or len(labels) != 1:
            out.append(
                LegendMatch(
                    text=text,
                    kind=KIND_LINE_COLOR,
                    reason=REASON_AMBIGUOUS,
                    to_question=True,
                )
            )
            continue
        pages = tuple(
            sorted({int(hit["source_page"]) for hit in hits if "source_page" in hit})
        )
        out.append(
            LegendMatch(
                text=text,
                kind=KIND_LINE_COLOR,
                name=labels.pop(),
                meaning=meanings.pop(),
                source_pages=pages,
            )
        )
    return tuple(out)


#: 色の突き合わせの結果の呼び名。
COLOUR_AGREES = "凡例と同じ色"
COLOUR_DIFFERS = "凡例と違う色"
COLOUR_NOT_IN_LEGEND = "凡例にその記号の色が無い"
COLOUR_CODE_NOT_IN_TABLE = "対照表に無い記号"


def mark_colour_agreement(
    items: Iterable[tuple[str, Sequence[float]]], table: LegendTable
) -> dict[str, int]:
    """図面でその記号が刷られている色が、**凡例が同じ記号を刷っている色**と同じか。

    凡例の 2 つの表(記号の表と色の表)を突き合わせるのではなく、**同じ記号の色どうし**を
    比べる。**こちらの推し量りが 1 つも入らない**のが要点である。

    独立した 2 つ目のデータ源にはならない(同じ PDF の同じ文字)。**手がかりが 2 つ
    揃っているだけ**で、証言が 2 つになったわけではない。
    """
    counts = {
        COLOUR_AGREES: 0,
        COLOUR_DIFFERS: 0,
        COLOUR_NOT_IN_LEGEND: 0,
        COLOUR_CODE_NOT_IN_TABLE: 0,
    }
    for text, colour in items:
        rows = _lookup(text, table.work_marks, "meaning")
        if not rows:
            counts[COLOUR_CODE_NOT_IN_TABLE] += 1
            continue
        wanted = [row["color"] for row in rows if row.get("color")]
        if not wanted:
            counts[COLOUR_NOT_IN_LEGEND] += 1
            continue
        same = any(
            len(value) == len(colour)
            and all(
                abs(float(a) - float(b)) <= COLOR_TOLERANCE
                for a, b in zip(value, colour)
            )
            for value in wanted
        )
        counts[COLOUR_AGREES if same else COLOUR_DIFFERS] += 1
    return counts


def questions(matches: Iterable[LegendMatch]) -> tuple[LegendMatch, ...]:
    """**決めてはいけない**箇所だけを取り出す(K-22 判断 3)。

    名前が 2 つ出た行がここに入る。**ここに入った箇所は、知識の道へも回さない。**
    2 つのうちどちらかを機械が選んだ時点で、選んだ根拠は「選んだ」以外に無くなる。
    """
    return tuple(m for m in matches if m.to_question)


def needs_knowledge(match: LegendMatch) -> bool:
    """その箇所を知識の道へ回してよいか(K-22 判断 1・2)。

    回してよいのは、**対照表が「不明」と言った箇所だけ**である。

    - 名前が付いた箇所は回さない。対照表はこの案件の図面が**自分で名乗っている**
      意味なので、知識より強い。
    - 質疑へ回す箇所(名前が 2 つ)は回さない。**判断 3 のほうが強い。**
    - **仮の判断(1 文字・数字だけ)で落ちた箇所は回す**(判断 2)。落とすのは
      落としたままにして、行き先だけを作る。
    """
    return not match.matched and not match.to_question


def apply_knowledge(
    matches: Iterable[LegendMatch], answers: Mapping[str, str]
) -> tuple[LegendMatch, ...]:
    """知識の道が出した名前を、**不明と言った箇所にだけ**入れる(K-22 判断 1)。

    入れた名前には `source=SOURCE_KNOWLEDGE` の印が必ず付く。**印の付かない道で
    名前が入ることはない。**対照表が黙った `reason` はそのまま残すので、人が見るとき
    「なぜ対照表が言えなかったのか」も一緒に見える。

    `answers` は語から名前への対応。**「不明」と空は入れない。**
    """
    out: list[LegendMatch] = []
    for match in matches:
        if not needs_knowledge(match):
            out.append(match)
            continue
        value = normalize(str(answers.get(match.text, "")))
        if not value or value == UNKNOWN:
            out.append(match)
            continue
        out.append(
            replace(match, name=str(answers[match.text]), source=SOURCE_KNOWLEDGE)
        )
    return tuple(out)


def summarize(matches: Iterable[LegendMatch]) -> LegendCounts:
    """報告に要る数だけを返す。**名前が付かなかった数も同じだけ大事。**"""
    rows = list(matches)
    named = sum(1 for m in rows if m.matched)
    reasons = Counter(m.reason for m in rows if m.reason)
    sources = Counter(m.source for m in rows if m.matched)
    return LegendCounts(
        total=len(rows),
        named=named,
        unknown=len(rows) - named,
        by_reason=dict(reasons),
        by_source=dict(sources),
        questions=sum(1 for m in rows if m.to_question),
    )
