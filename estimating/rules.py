"""当てはめの規則(会社の積算ルール)の読み込みと検査。

**規則はコードに書かない。** 会社ごと・案件の種類ごとに違うものなので、
差し替えられる外部ファイルとして読む。リポジトリに置くのは
`estimating/examples/synthetic_rules.json`(**合成の見本**)だけで、実際に
使う規則はリポジトリの外に置き、パスを設定で渡す。実案件の見積明細から
作った規則はコミットしない。

書式(format_version 1)
----------------------
```json
{
  "format_version": 1,
  "ruleset_id": "example",
  "description": "...",
  "rules": [
    {
      "rule_id": "door-install",
      "kind": "建具数量",
      "unit_dimension": "count",
      "attributes": {"種別": ["引戸", "片引戸"]},
      "line_items": [
        {"code": "...", "work_item": "...", "major_category": "...", "unit": "箇所"}
      ]
    }
  ]
}
```

- `kind` … 対象名の `::` より前(`建具数量`)。**全文には当てない。**
- `attributes` … 属性名 → 許す値の並び。**書いた属性が数量に無ければ
  当たらない。** 読めなかった属性で行を決めてしまわないため。
- `line_items` … 1 つの規則が当たったときに**同時に**作る行。取付費と
  材料費のように、1 つの数量が複数の行になるのはあいまいさではない。
- 複数の**規則**が同じ数量に当たったときが「一意に決まらない」場合で、
  `estimating/mapping.py` がそれを候補として出し、確定させない。

この読み込みが守ること
----------------------
1. **知らない項目があったら断る。** Codex 側の詳しい仕様が届いたとき、
   新しい項目が黙って読み飛ばされて「半分だけ効いた規則」になるのが
   いちばん危ない。`format_version` が違えば、そもそも読まない。
2. **行の単位は読み込みの時点で検査する。** `人工` のような未知の単位を
   書いた規則は、当てはめを走らせる前に落とす。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from arbitration.units import UNIT_SPECS, UnitError, canonical_unit
from estimating.quantities import QuantityItem, normalise_text

#: この読み込みが解釈できる規則ファイルの版。
#:
#: Codex 側の仕様が届いたら、この数字を上げた新しい読み込みを足す。
#: **古い版の読み込みで新しいファイルを読ませない**ので、書式が変わった
#: ことに気づかないまま半分だけ当たる、という事故が起きない。
RULES_FORMAT_VERSION = 1

_RULESET_FIELDS = frozenset({"format_version", "ruleset_id", "description", "rules"})
_RULE_FIELDS = frozenset(
    {"rule_id", "kind", "unit_dimension", "attributes", "line_items", "description"}
)
_LINE_FIELDS = frozenset({"code", "work_item", "major_category", "unit", "note"})


class RuleError(Exception):
    """規則ファイルが受け付けられなかった。"""


@dataclass(frozen=True)
class EstimateLineSpec:
    """見積の行 1 つの雛形。**数量も金額もまだ入っていない。**

    項目名は `benchmarks/run_golden_eval.py` がゴールデンベンチマークで
    使っている語彙(code / work_item / major_category / unit)に合わせてある。
    別の名前を作ると、将来の突き合わせで対応表が要る。

    **単価・金額はここにも下流にも無い。** この層は数量を行に当てはめる
    ところまでで、値付けは別の話である。
    """

    work_item: str
    unit: str
    code: str | None = None
    major_category: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class MatchResult:
    """1 つの規則をあてがってみた結果。**当たらなかった理由も残す。**"""

    matched: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingRule:
    """数量 1 件を見積の行に当てはめる規則 1 つ。"""

    rule_id: str
    kind: str
    line_items: tuple[EstimateLineSpec, ...]
    unit_dimension: str | None = None
    """数量の単位がこの次元(`count` `area` `length` `money`)であることを要求する。"""

    attributes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    description: str | None = None

    def match(self, quantity: QuantityItem) -> MatchResult:
        """この規則が当たるか。**当たらなかった理由を必ず返す。**"""
        if quantity.kind != self.kind:
            return MatchResult(False, (f"種類が {self.kind} ではない",))

        reasons: list[str] = []
        if self.unit_dimension is not None:
            spec = UNIT_SPECS[quantity.canonical_unit]
            if spec.dimension != self.unit_dimension:
                reasons.append(
                    f"単位 {quantity.unit} の次元が {self.unit_dimension} ではない"
                )

        for name, allowed in self.attributes.items():
            actual = quantity.attribute(name)
            if actual is None:
                reasons.append(
                    f"規則 {self.rule_id} が条件にしている属性 {name} が"
                    "この数量には無い(読めなかったものを当てない)"
                )
                continue
            if normalise_text(actual) not in {normalise_text(v) for v in allowed}:
                reasons.append(
                    f"属性 {name} の値 {actual} が規則 {self.rule_id} の条件に無い"
                )

        # 行の単位は、数量の単位と**同じ正規形**でなければならない。
        # 次元が同じでも表記が違うだけなら通す(㎡ と m²)。違う次元へは
        # 勝手に換算しない(箇所 → ㎡ は数量からは導けない)。
        for line in self.line_items:
            if canonical_unit(line.unit) != quantity.canonical_unit:
                reasons.append(
                    f"行「{line.work_item}」の単位 {line.unit} は"
                    f"数量の単位 {quantity.unit} と揃わない(換算しない)"
                )

        if reasons:
            return MatchResult(False, tuple(reasons))
        return MatchResult(True)


@dataclass(frozen=True)
class RuleSet:
    """差し替えの単位になる規則の束。"""

    ruleset_id: str
    format_version: int
    rules: tuple[MappingRule, ...]
    description: str | None = None
    source_path: Path | None = None
    """どのファイルから読んだか。報告に残すため。"""


def load_rules(path: str | Path) -> RuleSet:
    """規則ファイルを読む。**パスは常に外から渡す。**"""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuleError(f"規則ファイルが見つかりません: {source}") from error
    except json.JSONDecodeError as error:
        raise RuleError(f"規則ファイルが JSON として読めません: {source}: {error}") from error
    return parse_rules(payload, source_path=source)


def parse_rules(payload: Any, *, source_path: Path | None = None) -> RuleSet:
    """読み込んだ内容を検査して `RuleSet` にする。"""
    if not isinstance(payload, Mapping):
        raise RuleError("規則ファイルの中身が辞書ではありません")

    unknown = set(payload) - _RULESET_FIELDS
    if unknown:
        raise RuleError(
            f"知らない項目があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた規則になるので受け付けません"
        )

    version = payload.get("format_version")
    if version != RULES_FORMAT_VERSION:
        raise RuleError(
            f"format_version が {version!r} です。"
            f"この読み込みが解釈できるのは {RULES_FORMAT_VERSION} だけです"
        )

    ruleset_id = payload.get("ruleset_id")
    if not isinstance(ruleset_id, str) or not ruleset_id:
        raise RuleError("ruleset_id が必要です")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)):
        raise RuleError("rules は規則の並びである必要があります")
    if not raw_rules:
        raise RuleError("rules が空です")

    rules: list[MappingRule] = []
    seen: set[str] = set()
    for raw in raw_rules:
        rule = _parse_rule(raw)
        if rule.rule_id in seen:
            raise RuleError(f"rule_id が重複しています: {rule.rule_id}")
        seen.add(rule.rule_id)
        rules.append(rule)

    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise RuleError("description は文字列である必要があります")

    return RuleSet(
        ruleset_id=ruleset_id,
        format_version=RULES_FORMAT_VERSION,
        rules=tuple(rules),
        description=description,
        source_path=source_path,
    )


def _parse_rule(raw: Any) -> MappingRule:
    if not isinstance(raw, Mapping):
        raise RuleError("規則が辞書ではありません")
    unknown = set(raw) - _RULE_FIELDS
    if unknown:
        raise RuleError(
            f"規則に知らない項目があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた規則になるので受け付けません"
        )

    rule_id = raw.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise RuleError("rule_id が必要です")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        raise RuleError(f"規則 {rule_id} に kind が必要です")

    unit_dimension = raw.get("unit_dimension")
    if unit_dimension is not None:
        known = {spec.dimension for spec in UNIT_SPECS.values()}
        if unit_dimension not in known:
            raise RuleError(
                f"規則 {rule_id} の unit_dimension {unit_dimension!r} は知らない次元です"
                f"(使えるのは {sorted(known)})"
            )

    attributes = _parse_attributes(rule_id, raw.get("attributes"))
    line_items = _parse_line_items(rule_id, raw.get("line_items"))

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise RuleError(f"規則 {rule_id} の description は文字列である必要があります")

    return MappingRule(
        rule_id=rule_id,
        kind=kind,
        line_items=line_items,
        unit_dimension=unit_dimension,
        attributes=attributes,
        description=description,
    )


def _parse_attributes(rule_id: str, raw: Any) -> Mapping[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise RuleError(f"規則 {rule_id} の attributes は辞書である必要があります")
    out: dict[str, tuple[str, ...]] = {}
    for name, values in raw.items():
        if not isinstance(name, str) or not name:
            raise RuleError(f"規則 {rule_id} の属性名が文字列ではありません")
        if isinstance(values, str) or not isinstance(values, Sequence) or not values:
            raise RuleError(
                f"規則 {rule_id} の属性 {name} は、許す値の並び(空でないリスト)"
                "である必要があります"
            )
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise RuleError(f"規則 {rule_id} の属性 {name} に空の値があります")
        out[name] = tuple(values)
    return out


def _parse_line_items(rule_id: str, raw: Any) -> tuple[EstimateLineSpec, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise RuleError(
            f"規則 {rule_id} の line_items は、空でない行の並びである必要があります"
        )
    lines: list[EstimateLineSpec] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise RuleError(f"規則 {rule_id} の line_items に辞書でないものがあります")
        unknown = set(item) - _LINE_FIELDS
        if unknown:
            raise RuleError(
                f"規則 {rule_id} の行に知らない項目があります: {sorted(unknown)}"
            )
        work_item = item.get("work_item")
        if not isinstance(work_item, str) or not work_item:
            raise RuleError(f"規則 {rule_id} の行に work_item が必要です")
        unit = item.get("unit")
        if not isinstance(unit, str) or not unit:
            raise RuleError(f"規則 {rule_id} の行 {work_item} に unit が必要です")
        try:
            canonical_unit(unit)
        except UnitError as error:
            raise RuleError(
                f"規則 {rule_id} の行 {work_item} の単位 {unit!r} を解釈できません"
                f"({error})"
            ) from error
        for optional in ("code", "major_category", "note"):
            value = item.get(optional)
            if value is not None and not isinstance(value, str):
                raise RuleError(
                    f"規則 {rule_id} の行 {work_item} の {optional} は文字列である必要があります"
                )
        lines.append(
            EstimateLineSpec(
                work_item=work_item,
                unit=unit,
                code=item.get("code"),
                major_category=item.get("major_category"),
                note=item.get("note"),
            )
        )
    return tuple(lines)
