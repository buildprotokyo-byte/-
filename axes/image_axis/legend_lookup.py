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

色の扱い(K-20 4 番、おーちゃんの決め)
-------------------------------------
線の太さと色は印刷の都合で変わるので、**一致の判断には使わず参考にとどめる。**
線種は**刻みの比率**が凡例の見本と合うものだけを一致とする。
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 名前が付かなかったときに出す語。**推し量った名前を入れない。**
UNKNOWN = "不明"

REASON_NOT_IN_TABLE = "対照表に無い"
REASON_AMBIGUOUS = "対照表で1つに決まらない"
REASON_NO_SAMPLE = "凡例に見本が無い"

KIND_WORK = "工事の区分"
KIND_EQUIPMENT = "設備"
KIND_LINE_STYLE = "線種"

#: 読み込んでよい拘束力。**この案件限りの知識**であることを表す。
BINDING_CASE_LEGEND = "案件の凡例"

#: 刻みの比率が合っているとみなす相対のずれ。
RATIO_TOLERANCE = 0.1

#: 色が同じとみなすずれ(0〜1 の各成分)。
COLOR_TOLERANCE = 0.02


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
class ColourNote:
    """線の色から分かること。**参考であって、名前ではない。**"""

    label: str
    meaning: str
    source_pages: tuple[int, ...] = ()
    advisory: bool = True


@dataclass(frozen=True)
class LegendCounts:
    total: int
    named: int
    unknown: int
    by_reason: dict[str, int] = field(default_factory=dict)


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


def match_marks(texts: Iterable[str], table: LegendTable) -> tuple[LegendMatch, ...]:
    """図面から拾った文字を、対照表に**完全一致で**引き当てる。"""
    out: list[LegendMatch] = []
    for text in texts:
        for rows, name_key, kind in (
            (table.work_marks, "meaning", KIND_WORK),
            (table.symbols, "name", KIND_EQUIPMENT),
        ):
            hits = _lookup(text, rows, name_key)
            if not hits:
                continue
            value, pages = _collapse(hits, name_key)
            if value is None:
                out.append(
                    LegendMatch(text=text, kind=kind, reason=REASON_AMBIGUOUS)
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
                )
            )
    return tuple(out)


def advise_line_colors(
    colors: Iterable[Sequence[float]], table: LegendTable
) -> tuple[ColourNote, ...]:
    """色から分かることを**参考として**返す。名前は決めない(K-20 4 番)。"""
    out: list[ColourNote] = []
    for colour in colors:
        hits = [
            row
            for row in table.line_colors
            if len(row["color"]) == len(colour)
            and all(abs(float(a) - float(b)) <= COLOR_TOLERANCE for a, b in zip(row["color"], colour))
        ]
        meanings = {str(hit["meaning"]) for hit in hits}
        labels = {str(hit.get("label", "")) for hit in hits}
        if len(meanings) != 1 or len(labels) != 1:
            continue
        pages = tuple(sorted({int(hit["source_page"]) for hit in hits if "source_page" in hit}))
        out.append(
            ColourNote(label=labels.pop(), meaning=meanings.pop(), source_pages=pages)
        )
    return tuple(out)


def summarize(matches: Iterable[LegendMatch]) -> LegendCounts:
    """報告に要る数だけを返す。**名前が付かなかった数も同じだけ大事。**"""
    rows = list(matches)
    named = sum(1 for m in rows if m.matched)
    reasons = Counter(m.reason for m in rows if m.reason)
    return LegendCounts(
        total=len(rows),
        named=named,
        unknown=len(rows) - named,
        by_reason=dict(reasons),
    )
