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
- `phase`(版 3)… 現況/計画/解体の条件。**意味の4欄からだけ読む。**
  意味が付いていない数量にも、`不明` のままの数量にも当たらない。
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
from axes.reading.meaning import PHASE_UNKNOWN, PHASES
from estimating.quantities import QuantityItem, normalise_text

#: この読み込みが解釈できる規則ファイルの版。
#:
#: Codex 側の仕様が届いたら、この数字を上げた新しい読み込みを足す。
#: **古い版の読み込みで新しいファイルを読ませない**ので、書式が変わった
#: ことに気づかないまま半分だけ当たる、という事故が起きない。
RULES_FORMAT_VERSION = 3

#: 読める版。**古い版のファイルはそのまま読める**(図面からは決まらない行が
#: 無いだけ)。新しい項目を書いたのに版を上げていないファイルは断る。
#: 半分だけ効いた規則がいちばん危ないという約束は変えていない。
SUPPORTED_RULES_FORMAT_VERSIONS: tuple[int, ...] = (1, 2, 3)

#: 版 2 で足した項目。版 1 のファイルにこれがあれば断る。
_VERSION2_ONLY_FIELDS = frozenset({"standing_lines"})

#: 版 3 で足した、**規則 1 つの中の**項目。版 2 以下のファイルにあれば断る。
#:
#: `phase` は現況/計画/解体の条件である。それまで `phase` は対象名の中
#: (`開き戸::現況::ページ1`)にしか無く、`kind` が `::` の手前しか見ないので
#: **規則からは見えなかった**(`docs/principles/scope_of_work_diff.md` 3-2)。
#: 意味の4欄(`Meaning.phase`)に移したことで条件にできるようになった。
_VERSION3_ONLY_RULE_FIELDS = frozenset({"phase"})

#: 図面からは決まらない行の基準の種類。
STANDING_BASIS_KINDS: tuple[str, ...] = ("一式", "数量参照")

#: 「一式」を表す単位。**測れる数量を持たない。**
#:
#: `arbitration/units.py` はこれらを知らない。知らないのが正しい。
#: 仲裁層が扱うのは測れる量(個数・長さ・面積・金額)だけで、
#: 「式」は**量ではなく数え方の宣言**だからである。採点でも「式・一式」の行は
#: 数量の判定から外されている。ここでは行の単位としてだけ受け、
#: 数量として正規化しない。
LUMP_SUM_UNITS: tuple[str, ...] = ("式", "一式")

_RULESET_FIELDS = frozenset(
    {"format_version", "ruleset_id", "description", "rules", "standing_lines"}
)
_STANDING_FIELDS = frozenset(
    {"standing_id", "work_item", "major_category", "unit", "basis", "note"}
)
_STANDING_BASIS_FIELDS = frozenset({"kind", "quantity", "target_kind", "per_unit"})
_RULE_FIELDS = frozenset(
    {
        "rule_id",
        "kind",
        "unit_dimension",
        "attributes",
        "phase",
        "line_items",
        "description",
    }
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
    phase: tuple[str, ...] = ()
    """この規則が当たる現況/計画/解体(版 3 で追加)。**空なら問わない。**

    **`不明` は書けない。** 「現況か計画か決まっていない数量に当てる規則」は、
    決まっていないことを根拠に行を立てることになる(原則2)。
    """

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

        # 現況/計画は**意味の4欄からだけ**読む(`QuantityItem.phase`)。
        # 対象名から切り出さない。**意味が付いていない数量には当てない。**
        # 読めなかった属性で行を決めない約束(上の `attributes`)と同じ向きで、
        # 「まだ付けていない」を「現況だろう」で埋めない。
        if self.phase:
            actual_phase = quantity.phase
            if actual_phase is None:
                reasons.append(
                    f"規則 {self.rule_id} が現況/計画を条件にしているが、"
                    "この数量には意味の4欄が付いていない"
                    "(付いていないことを「不明」と読み替えない)"
                )
            elif actual_phase == PHASE_UNKNOWN:
                reasons.append(
                    f"規則 {self.rule_id} が現況/計画を条件にしているが、"
                    "この数量の現況/計画は「不明」のままである"
                )
            elif actual_phase not in self.phase:
                reasons.append(
                    f"この数量は「{actual_phase}」で、"
                    f"規則 {self.rule_id} の条件 {list(self.phase)} に無い"
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
class StandingLineSpec:
    """**図面からは決まらない**見積の行 1 つの規則(版 2 で追加)。

    仮設・運搬・墨出し・清掃のように、図面のどこにも書かれていないが
    会社のルールがあれば立つ行を表す。**率や式の中身はここに書かない。**
    リポジトリに置く見本は架空のものだけで、実際の値は外部ファイルで渡す。
    """

    standing_id: str
    work_item: str
    major_category: str
    unit: str
    basis_kind: str
    """``一式`` か ``数量参照``。"""

    lump_sum_quantity: float = 1.0
    """``一式`` のときの数量。"""

    target_kind: str | None = None
    """``数量参照`` のとき、もとにする対象名の種類(``::`` より前)。"""

    per_unit: float | None = None
    """``数量参照`` のとき、もとの数量 1 単位あたりの値。

    **None は「まだ決まっていない」という意味で、1 ではない。**
    None のままなら数量を入れず、足りないものとして報告する。
    """

    note: str | None = None


@dataclass(frozen=True)
class RuleSet:
    """差し替えの単位になる規則の束。"""

    ruleset_id: str
    format_version: int
    rules: tuple[MappingRule, ...]
    standing_lines: tuple[StandingLineSpec, ...] = ()
    """図面からは決まらない行の規則。**版 1 のファイルでは常に空。**

    空であることは「その行が無い」ではなく「**ルールを持っていない**」である。
    その 2 つを見分けるのが `estimating/standing_lines.py` の仕事。
    """

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
    if version not in SUPPORTED_RULES_FORMAT_VERSIONS:
        raise RuleError(
            f"format_version が {version!r} です。"
            f"この読み込みが解釈できるのは {list(SUPPORTED_RULES_FORMAT_VERSIONS)} だけです"
        )
    too_new = _VERSION2_ONLY_FIELDS & set(payload)
    if version < 2 and too_new:
        raise RuleError(
            f"format_version {version} のファイルに版 2 の項目があります: {sorted(too_new)}。"
            "版を上げずに新しい項目を書くと、半分だけ効いた規則になるので受け付けません"
        )

    ruleset_id = payload.get("ruleset_id")
    if not isinstance(ruleset_id, str) or not ruleset_id:
        raise RuleError("ruleset_id が必要です")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)):
        raise RuleError("rules は規則の並びである必要があります")
    if not raw_rules and "standing_lines" not in payload:
        raise RuleError(
            "rules が空です"
            "(図面からは決まらない行だけの規則なら standing_lines を書いてください。"
            "**ルールを持っていないことを表したいなら standing_lines を空で書く**)"
        )

    rules: list[MappingRule] = []
    seen: set[str] = set()
    for raw in raw_rules:
        rule = _parse_rule(raw, version=version)
        if rule.rule_id in seen:
            raise RuleError(f"rule_id が重複しています: {rule.rule_id}")
        seen.add(rule.rule_id)
        rules.append(rule)

    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise RuleError("description は文字列である必要があります")

    standing = _parse_standing_lines(payload.get("standing_lines"))

    return RuleSet(
        ruleset_id=ruleset_id,
        format_version=version,
        rules=tuple(rules),
        standing_lines=standing,
        description=description,
        source_path=source_path,
    )


def _parse_standing_lines(raw: Any) -> tuple[StandingLineSpec, ...]:
    """図面からは決まらない行の規則を読む。**無ければ空**(それ自体が結果)。"""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RuleError("standing_lines は行の並びである必要があります")

    out: list[StandingLineSpec] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise RuleError("standing_lines の要素が辞書ではありません")
        unknown = set(item) - _STANDING_FIELDS
        if unknown:
            raise RuleError(
                f"standing_lines に知らない項目があります: {sorted(unknown)}"
            )
        standing_id = item.get("standing_id")
        if not isinstance(standing_id, str) or not standing_id:
            raise RuleError("standing_id が必要です")
        if standing_id in seen:
            raise RuleError(f"standing_id が重複しています: {standing_id}")
        seen.add(standing_id)
        for field_name in ("work_item", "major_category", "unit"):
            value = item.get(field_name)
            if not isinstance(value, str) or not value:
                raise RuleError(f"{standing_id}: {field_name} が必要です")
        unit = item["unit"]
        is_lump_sum_unit = unit in LUMP_SUM_UNITS
        if not is_lump_sum_unit:
            try:
                canonical_unit(unit)
            except UnitError as error:
                raise RuleError(
                    f"{standing_id}: 単位 {unit!r} は解釈できません"
                ) from error

        basis = item.get("basis")
        if not isinstance(basis, Mapping):
            raise RuleError(f"{standing_id}: basis が必要です")
        unknown_basis = set(basis) - _STANDING_BASIS_FIELDS
        if unknown_basis:
            raise RuleError(
                f"{standing_id}: basis に知らない項目があります: {sorted(unknown_basis)}"
            )
        kind = basis.get("kind")
        if is_lump_sum_unit and kind == "数量参照":
            raise RuleError(
                f"{standing_id}: 単位が {unit!r} なのに数量参照になっています。"
                "「式」は測れる量ではないので、ほかの数量から計算できません"
            )
        if kind not in STANDING_BASIS_KINDS:
            raise RuleError(
                f"{standing_id}: basis の kind が {kind!r} です。"
                f"使えるのは {list(STANDING_BASIS_KINDS)} だけです"
            )

        lump_sum = 1.0
        target_kind = None
        per_unit = None
        if kind == "一式":
            quantity = basis.get("quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                raise RuleError(f"{standing_id}: 一式の quantity が数ではありません")
            lump_sum = float(quantity)
        else:
            target_kind = basis.get("target_kind")
            if not isinstance(target_kind, str) or not target_kind:
                raise RuleError(f"{standing_id}: 数量参照には target_kind が必要です")
            raw_per_unit = basis.get("per_unit", None)
            if raw_per_unit is not None:
                if isinstance(raw_per_unit, bool) or not isinstance(raw_per_unit, (int, float)):
                    raise RuleError(f"{standing_id}: per_unit が数ではありません")
                per_unit = float(raw_per_unit)

        note = item.get("note")
        if note is not None and not isinstance(note, str):
            raise RuleError(f"{standing_id}: note は文字列である必要があります")

        out.append(
            StandingLineSpec(
                standing_id=standing_id,
                work_item=item["work_item"],
                major_category=item["major_category"],
                unit=item["unit"],
                basis_kind=kind,
                lump_sum_quantity=lump_sum,
                target_kind=target_kind,
                per_unit=per_unit,
                note=note,
            )
        )
    return tuple(out)


def _parse_rule(raw: Any, *, version: int) -> MappingRule:
    if not isinstance(raw, Mapping):
        raise RuleError("規則が辞書ではありません")
    unknown = set(raw) - _RULE_FIELDS
    if unknown:
        raise RuleError(
            f"規則に知らない項目があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた規則になるので受け付けません"
        )
    too_new = _VERSION3_ONLY_RULE_FIELDS & set(raw)
    if version < 3 and too_new:
        raise RuleError(
            f"format_version {version} の規則に版 3 の項目があります: {sorted(too_new)}。"
            "版を上げずに新しい項目を書くと、半分だけ効いた規則になるので受け付けません"
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
    phase = _parse_phase(rule_id, raw.get("phase"))
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
        phase=phase,
        description=description,
    )


def _parse_phase(rule_id: str, raw: Any) -> tuple[str, ...]:
    """規則の現況/計画の条件を読む。**`不明` は書かせない。**"""
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence) or not raw:
        raise RuleError(
            f"規則 {rule_id} の phase は、許す値の並び(空でないリスト)"
            "である必要があります"
        )
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in PHASES:
            raise RuleError(
                f"規則 {rule_id} の phase に知らない値があります: {value!r}"
                f"(使えるのは {sorted(PHASES - {PHASE_UNKNOWN})})"
            )
        if value == PHASE_UNKNOWN:
            raise RuleError(
                f"規則 {rule_id} の phase に「{PHASE_UNKNOWN}」は書けません。"
                "決まっていないことを根拠に見積の行を立てることになります"
            )
        out.append(value)
    return tuple(out)


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
