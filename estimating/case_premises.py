"""案件の前提。**3層のうち、いままで層として無かったもの。**

おーちゃんの原則(`docs/principles/start_kit.md`)で、数量は3つの層から作られる。

1. 読み取った数量 … `estimating/quantities.py`
2. **案件の前提 … ここ**
3. 会社のルール … `estimating/rules.py`

2 が層として無かったので、人が入れた前提は `intake/` の中で値に溶けてしまい、
見積の行から「この数量は、どの前提を置いたからこの値なのか」を遡れなかった
(`docs/principles/principle_conformance_review.md` の 8・15、D-14)。

前提は**値ではなく前提そのもの**を持ち、数量は識別子で参照する。

ファイルを分ける理由(おーちゃんの回答13)
----------------------------------------
**規則のファイルは会社で1つを基本にし、案件ごとの前提は別のファイルに分ける。**
規則は会社が決めるもので案件をまたいで同じ、前提は案件ごとに違うからである。
1つのファイルに混ぜると、案件の都合で会社のルールを書き換えることになる。

ここが守ること
--------------
1. **仮説には「何が分かれば要らなくなるか」を必ず書く。** これが無いと、
   原則5の「仮説が変わったら連動して見直す」が動かせないし、置いた仮説が
   誰にも気づかれないまま残る。
2. **人が入れた前提には、入れた人を必ず残す。** 誰が入れたか分からない
   前提は、後から確かめようがない。
3. **知らない項目と知らない版は断る。** 半分だけ効いた前提がいちばん危ない
   (`estimating/rules.py` と同じ約束)。
4. **前提そのものは、値を確定させない。** ここにあるのは前提だけで、
   確定させるのは仲裁層である。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from estimating.quantities import QuantityItem

#: この読み込みが解釈できる前提ファイルの版。
PREMISES_FORMAT_VERSION = 1

#: 人が入れた前提。**それだけを根拠に自動確定させない**のは今までどおり。
SOURCE_HUMAN = "human"
#: AI が図面から推論した前提。
SOURCE_INFERRED = "inferred"
#: AI が置いた仮説。原則3-3「分からない部分は、AI が図面から推論するか、
#: 仮説を置いて進む」の後半。
SOURCE_HYPOTHESIS = "hypothesis"

PREMISE_SOURCES: tuple[str, ...] = (SOURCE_HUMAN, SOURCE_INFERRED, SOURCE_HYPOTHESIS)

_PREMISESET_FIELDS = frozenset(
    {"format_version", "premise_set_id", "case_id", "description", "premises"}
)
_PREMISE_FIELDS = frozenset(
    {
        "premise_id",
        "kind",
        "statement",
        "source",
        "entered_by",
        "resolved_by",
        "alternatives",
        "scope",
    }
)
_SCOPE_FIELDS = frozenset({"target_kinds", "targets"})


class PremiseError(Exception):
    """前提が、そのままでは使えない形だった。"""


@dataclass(frozen=True)
class PremiseScope:
    """この前提がどこに効くか。**どちらも空なら案件全体に効く。**

    **ページ単位の範囲はまだ持てない。** 数量の側が「どこのものか」を
    独立した欄として持っていないためで(原則2の意味の4欄)、そこが入った
    ときに足す。いまページ番号を根拠に当てると、面積のように複数ページの
    記載をまとめた数量に当たらず、**効いているつもりで効かない**ことになる。
    """

    target_kinds: tuple[str, ...] = ()
    """対象の種類(`建具数量` など)。対象名の `::` より前。"""

    targets: tuple[str, ...] = ()
    """対象名そのもの(`建具数量::AW-1`)。"""

    @property
    def is_case_wide(self) -> bool:
        return not (self.target_kinds or self.targets)

    def covers(self, quantity: QuantityItem) -> bool:
        if self.is_case_wide:
            return True
        return quantity.target in self.targets or quantity.kind in self.target_kinds


@dataclass(frozen=True)
class CasePremise:
    """案件の前提 1 つ。**値ではなく前提そのもの。**"""

    premise_id: str
    kind: str
    """何についての前提か(`面積の基準` `寸法の単位` `天井高` など)。"""

    statement: str
    """前提の中身。**人が読んで分かる文で書く。**"""

    source: str
    scope: PremiseScope = field(default_factory=PremiseScope)

    entered_by: str = ""
    """人が入れた前提のときだけ。**誰が入れたかを必ず残す。**"""

    resolved_by: str = ""
    """**仮説のときだけ。** 何が分かればこの仮説が要らなくなるか。"""

    alternatives: tuple[str, ...] = ()
    """仮説のとき、ほかにありえた置き方。**人が選び直すときの材料。**"""

    def __post_init__(self) -> None:
        if not self.premise_id:
            raise PremiseError("premise_id は空にできません")
        if not self.kind:
            raise PremiseError(f"{self.premise_id}: kind が必要です")
        if not self.statement.strip():
            raise PremiseError(
                f"{self.premise_id}: 前提の中身を人が読める文で書いてください"
            )
        if self.source not in PREMISE_SOURCES:
            raise PremiseError(
                f"{self.premise_id}: source {self.source!r} は知らない値です"
                f"(使えるのは {list(PREMISE_SOURCES)})"
            )
        if self.source == SOURCE_HUMAN and not self.entered_by.strip():
            raise PremiseError(
                f"{self.premise_id}: 人が入れた前提には、入れた人を残してください"
            )
        if self.source == SOURCE_HYPOTHESIS and not self.resolved_by.strip():
            raise PremiseError(
                f"{self.premise_id}: 仮説には「何が分かれば要らなくなるか」を"
                "書いてください(書けない仮説は、置いたことに誰も気づけません)"
            )
        if self.source != SOURCE_HYPOTHESIS and (self.resolved_by or self.alternatives):
            raise PremiseError(
                f"{self.premise_id}: resolved_by と alternatives は仮説のときだけです"
            )

    @property
    def is_hypothesis(self) -> bool:
        return self.source == SOURCE_HYPOTHESIS


@dataclass(frozen=True)
class PremiseSet:
    """1 案件ぶんの前提。**差し替えの単位。**"""

    premise_set_id: str
    format_version: int
    premises: tuple[CasePremise, ...] = ()
    case_id: str | None = None
    description: str | None = None
    source_path: Path | None = None

    def get(self, premise_id: str) -> CasePremise | None:
        for premise in self.premises:
            if premise.premise_id == premise_id:
                return premise
        return None

    def hypotheses(self) -> tuple[CasePremise, ...]:
        """置いてある仮説。**人に見せる一覧。**"""
        return tuple(p for p in self.premises if p.is_hypothesis)

    def covering(self, quantity: QuantityItem) -> tuple[CasePremise, ...]:
        return tuple(p for p in self.premises if p.scope.covers(quantity))


def apply_premises(
    quantities: Iterable[QuantityItem], premise_set: PremiseSet
) -> tuple[QuantityItem, ...]:
    """数量に、寄りかかっている前提を結びつける。

    **値は1つも変えない。** 変えるのは `premise_ids` と
    `hypothesis_premise_ids` だけで、そこから `basis` が決まる。
    **確定させるかどうかはここでは決めない**(仲裁層の判定をそのまま運ぶ)。

    すでに前提が結びついている数量には**足す**。入口で付いた前提を
    ここで消さないためである。
    """
    out: list[QuantityItem] = []
    for quantity in quantities:
        covering = premise_set.covering(quantity)
        if not covering:
            out.append(quantity)
            continue
        premise_ids = tuple(
            dict.fromkeys(
                tuple(quantity.premise_ids) + tuple(p.premise_id for p in covering)
            )
        )
        hypothesis_ids = tuple(
            dict.fromkeys(
                tuple(quantity.hypothesis_premise_ids)
                + tuple(p.premise_id for p in covering if p.is_hypothesis)
            )
        )
        out.append(
            replace_premises(
                quantity, premise_ids=premise_ids, hypothesis_premise_ids=hypothesis_ids
            )
        )
    return tuple(out)


def replace_premises(
    quantity: QuantityItem,
    *,
    premise_ids: tuple[str, ...],
    hypothesis_premise_ids: tuple[str, ...],
) -> QuantityItem:
    """前提の結びつきだけを差し替えた数量を返す。**ほかの欄は写すだけ。**"""
    from dataclasses import replace

    return replace(
        quantity,
        premise_ids=premise_ids,
        hypothesis_premise_ids=hypothesis_premise_ids,
    )


def load_premises(path: str | Path) -> PremiseSet:
    """案件の前提のファイルを読む。**パスは常に外から渡す。**

    実案件の前提はリポジトリに置かない。同梱するのは
    `estimating/examples/synthetic_premises.json`(**合成の見本**)だけである。
    """
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PremiseError(f"前提のファイルが見つかりません: {source}") from error
    except json.JSONDecodeError as error:
        raise PremiseError(
            f"前提のファイルが JSON として読めません: {source}: {error}"
        ) from error
    return parse_premises(payload, source_path=source)


def parse_premises(payload: Any, *, source_path: Path | None = None) -> PremiseSet:
    """読み込んだ内容を検査して `PremiseSet` にする。"""
    if not isinstance(payload, Mapping):
        raise PremiseError("前提のファイルの中身が辞書ではありません")

    unknown = set(payload) - _PREMISESET_FIELDS
    if unknown:
        raise PremiseError(
            f"知らない項目があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた前提になるので受け付けません"
        )

    version = payload.get("format_version")
    if version != PREMISES_FORMAT_VERSION:
        raise PremiseError(
            f"format_version が {version!r} です。"
            f"この読み込みが解釈できるのは {PREMISES_FORMAT_VERSION} だけです"
        )

    premise_set_id = payload.get("premise_set_id")
    if not isinstance(premise_set_id, str) or not premise_set_id:
        raise PremiseError("premise_set_id が必要です")

    raw_premises = payload.get("premises")
    if not isinstance(raw_premises, Sequence) or isinstance(raw_premises, (str, bytes)):
        raise PremiseError("premises は前提の並びである必要があります")

    premises: list[CasePremise] = []
    seen: set[str] = set()
    for raw in raw_premises:
        premise = _parse_premise(raw)
        if premise.premise_id in seen:
            raise PremiseError(f"premise_id が重複しています: {premise.premise_id}")
        seen.add(premise.premise_id)
        premises.append(premise)

    for name in ("case_id", "description"):
        value = payload.get(name)
        if value is not None and not isinstance(value, str):
            raise PremiseError(f"{name} は文字列である必要があります")

    return PremiseSet(
        premise_set_id=premise_set_id,
        format_version=PREMISES_FORMAT_VERSION,
        premises=tuple(premises),
        case_id=payload.get("case_id"),
        description=payload.get("description"),
        source_path=source_path,
    )


def _parse_premise(raw: Any) -> CasePremise:
    if not isinstance(raw, Mapping):
        raise PremiseError("前提が辞書ではありません")
    unknown = set(raw) - _PREMISE_FIELDS
    if unknown:
        raise PremiseError(
            f"前提に知らない項目があります: {sorted(unknown)}。"
            "読み飛ばすと半分だけ効いた前提になるので受け付けません"
        )

    for name in ("premise_id", "kind", "statement", "source"):
        if not isinstance(raw.get(name), str) or not raw.get(name):
            raise PremiseError(f"前提に {name} が必要です: {dict(raw)!r}")

    for name in ("entered_by", "resolved_by"):
        value = raw.get(name)
        if value is not None and not isinstance(value, str):
            raise PremiseError(
                f"{raw['premise_id']}: {name} は文字列である必要があります"
            )

    alternatives = raw.get("alternatives")
    if alternatives is None:
        alternatives = ()
    elif isinstance(alternatives, str) or not isinstance(alternatives, Sequence):
        raise PremiseError(
            f"{raw['premise_id']}: alternatives は文字列の並びである必要があります"
        )
    else:
        for value in alternatives:
            if not isinstance(value, str) or not value.strip():
                raise PremiseError(
                    f"{raw['premise_id']}: alternatives に空の値があります"
                )
        alternatives = tuple(alternatives)

    return CasePremise(
        premise_id=raw["premise_id"],
        kind=raw["kind"],
        statement=raw["statement"],
        source=raw["source"],
        scope=_parse_scope(raw["premise_id"], raw.get("scope")),
        entered_by=raw.get("entered_by") or "",
        resolved_by=raw.get("resolved_by") or "",
        alternatives=alternatives,
    )


def _parse_scope(premise_id: str, raw: Any) -> PremiseScope:
    if raw is None:
        return PremiseScope()
    if not isinstance(raw, Mapping):
        raise PremiseError(f"{premise_id}: scope は辞書である必要があります")
    unknown = set(raw) - _SCOPE_FIELDS
    if unknown:
        raise PremiseError(f"{premise_id}: scope に知らない項目があります: {sorted(unknown)}")
    out: dict[str, tuple[str, ...]] = {}
    for name in _SCOPE_FIELDS:
        values = raw.get(name)
        if values is None:
            continue
        if isinstance(values, str) or not isinstance(values, Sequence):
            raise PremiseError(
                f"{premise_id}: scope の {name} は文字列の並びである必要があります"
            )
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise PremiseError(f"{premise_id}: scope の {name} に空の値があります")
        out[name] = tuple(values)
    return PremiseScope(**out)
