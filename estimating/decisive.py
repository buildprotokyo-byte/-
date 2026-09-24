"""**その行が出た決め手**と、**取れなかった理由**。

おーちゃんの指示(2026-09-23、優先順1番)。

> 今は「何行当たった」しか分からず、「なぜ当たったか」が分かりません。
> 以後のすべての判断がここに依存するので、技術を足すより先にこれを作ってください。

基準は `docs/d_decisive_reason_criteria.md`(測る前にコミット済み)。

ここが守ること
--------------
1. **証拠が無ければ決め手を名乗れない。** ルールIDの無い「知識のルール」、
   経路が 1 本だけの「経路の一致」、どの問いか分からない「人の回答」は作らせない。
   空欄でそれらしい札を貼ると、**理由別の集計がそのまま嘘になる。**
2. **種類に合わない証拠を持たせない。** 「観測だけで出た」にルールIDは付かない。
3. **決め手が無い行は、空のまま残す。** 既定値で埋めない
   (`lines_without_reason()` が名指しする)。原則5の read/assumed と同じ考え方で、
   **「決め手が無い」と「観測だけで出た」は別である。**
4. **この層は判定をやり直さない。** 階層も許容差も見ない。**記録だけ。**

**この仕組みは `estimating/` の他のどれも import しない。** 数量(`quantities.py`)が
こちらを import するので、向きを逆にすると循環する。行や数量は**属性の有無だけ**で
受け取る(`getattr`)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# 決め手の 5 種類(おーちゃんの指示の 5 つ。**勝手に増やさない。**)
# ---------------------------------------------------------------------------

#: 図面を読んで素直に出た。**下の 4 つがどれも無いときだけ名乗れる。**
REASON_OBSERVED = "観測"
#: 知識のルールが効いて出た(図面に現れない行の決まり・基準・ガイドライン・波及のルール)。
REASON_KNOWLEDGE_RULE = "知識のルール"
#: 複数の経路・技術が一致して確度が上がった。
REASON_PATHS_AGREED = "経路の一致"
#: 人の回答で確定した。
REASON_HUMAN_ANSWER = "人の回答"
#: 要約資料(メニュー)から探しに行って見つかった。
REASON_SUMMARY_SEARCH = "要約資料"

REASON_KINDS: tuple[str, ...] = (
    REASON_OBSERVED,
    REASON_KNOWLEDGE_RULE,
    REASON_PATHS_AGREED,
    REASON_HUMAN_ANSWER,
    REASON_SUMMARY_SEARCH,
)

# ---------------------------------------------------------------------------
# 取れなかった理由(おーちゃんの指示の 4 つ + 説明つきの「その他」)
# ---------------------------------------------------------------------------

NOT_OBTAINED_SYMBOL = "記号が読めない"
NOT_OBTAINED_AREA = "面積が出せない"
NOT_OBTAINED_NO_RULE = "ルールが無い"
NOT_OBTAINED_SITE_SURVEY = "現地確認が必要"
#: 上の 4 つに入らないもの。**説明が無ければ作れない。**
#: 「その他」で束ねて中身が消えるのを防ぐ。
NOT_OBTAINED_OTHER = "その他"

NOT_OBTAINED_REASONS: tuple[str, ...] = (
    NOT_OBTAINED_SYMBOL,
    NOT_OBTAINED_AREA,
    NOT_OBTAINED_NO_RULE,
    NOT_OBTAINED_SITE_SURVEY,
    NOT_OBTAINED_OTHER,
)


class DecisiveError(Exception):
    """決め手として受け付けられなかった。"""


@dataclass(frozen=True)
class DecisiveReason:
    """**その行(その数量)が出た決め手 1 つ。**

    種類ごとに要る証拠が違い、**足りなければ作れない。**
    """

    kind: str

    knowledge_rule_ids: tuple[str, ...] = ()
    """どの知識のルールが効いたか。**`知識のルール` では 1 つ以上が要る。**"""

    agreeing_paths: tuple[str, ...] = ()
    """一致した経路(手法)。**`経路の一致` では、異なるものが 2 つ以上要る。**"""

    paths_independent: bool | None = None
    """その経路が独立しているか。**`経路の一致` では書かないと作れない。**

    同じ PDF から出た 2 つは独立していない(`docs/` の多経路の報告)。
    **「一致した」とだけ書いて独立性を落とすと、裏を取った数字に見えてしまう。**
    """

    question_id: str = ""
    """どの問いへの回答か。**`人の回答` では要る。**"""

    summary_item: str = ""
    """要約資料(メニュー)のどの項目から見つけたか。**`要約資料` では要る。**"""

    detail: str = ""
    """人が読むための補足。**判定には使わない。**"""

    def __post_init__(self) -> None:
        if self.kind not in REASON_KINDS:
            raise DecisiveError(
                f"知らない決め手です: {self.kind!r}"
                f"({'、'.join(REASON_KINDS)} のどれか)"
            )

        required = {
            REASON_KNOWLEDGE_RULE: bool(self.knowledge_rule_ids),
            REASON_PATHS_AGREED: len(set(self.agreeing_paths)) >= 2
            and self.paths_independent is not None,
            REASON_HUMAN_ANSWER: bool(self.question_id),
            REASON_SUMMARY_SEARCH: bool(self.summary_item),
        }
        if self.kind in required and not required[self.kind]:
            raise DecisiveError(
                f"{self.kind} と言うには証拠が足りません"
                f"(ルールID {len(self.knowledge_rule_ids)} 件 / "
                f"経路 {len(set(self.agreeing_paths))} 本 / "
                f"独立性 {self.paths_independent} / "
                f"問い {self.question_id or 'なし'} / "
                f"要約資料の項目 {self.summary_item or 'なし'})"
            )

        # **種類に合わない証拠は持たせない。** 付いていると、集計のときに
        # 「どの証拠で言っているのか」が種類から読めなくなる。
        foreign = {
            "knowledge_rule_ids": (bool(self.knowledge_rule_ids), REASON_KNOWLEDGE_RULE),
            "agreeing_paths": (bool(self.agreeing_paths), REASON_PATHS_AGREED),
            "question_id": (bool(self.question_id), REASON_HUMAN_ANSWER),
            "summary_item": (bool(self.summary_item), REASON_SUMMARY_SEARCH),
        }
        for name, (present, owner) in foreign.items():
            if present and self.kind != owner:
                raise DecisiveError(
                    f"{self.kind} に {name} は付きません({owner} の証拠です)"
                )
        if self.paths_independent is not None and self.kind != REASON_PATHS_AGREED:
            raise DecisiveError(
                f"{self.kind} に独立性は付きません({REASON_PATHS_AGREED} の証拠です)"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "knowledge_rule_ids": list(self.knowledge_rule_ids),
            "agreeing_paths": list(self.agreeing_paths),
            "paths_independent": self.paths_independent,
            "question_id": self.question_id,
            "summary_item": self.summary_item,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class NotObtained:
    """**取れなかったもの 1 件と、その理由。**"""

    reason: str
    target: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.reason not in NOT_OBTAINED_REASONS:
            raise DecisiveError(
                f"知らない理由です: {self.reason!r}"
                f"({'、'.join(NOT_OBTAINED_REASONS)} のどれか)"
            )
        if self.reason == NOT_OBTAINED_OTHER and not self.detail:
            raise DecisiveError(
                "「その他」には説明が要ります(束ねて中身を消さないため)"
            )

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "target": self.target, "detail": self.detail}


# ---------------------------------------------------------------------------
# 決め手の決め方
# ---------------------------------------------------------------------------


def decisive_reasons_for(
    *,
    effective_derivation: str,
    source_kind: str = "drawing",
    knowledge_rule_ids: Sequence[str] = (),
    agreeing_paths: Sequence[str] = (),
    paths_independent: bool | None = None,
    question_id: str = "",
    summary_item: str = "",
) -> tuple[DecisiveReason, ...]:
    """手元の証拠から、名乗れる決め手だけを作る。

    **証拠が無いものは作らない。** どれも作れなければ空を返す
    (`一般則で補った値` がこれになる。**「観測」に化けさせない。**)。

    `effective_derivation` は根拠まで遡った由来(`estimating/quantities.py` の
    `QuantityItem.effective_derivation`)。**計算した値でも、根拠に「一般則で
    補った」が 1 つでもあれば `assumed` で渡ってくる。**
    """
    reasons: list[DecisiveReason] = []

    if knowledge_rule_ids:
        reasons.append(
            DecisiveReason(
                kind=REASON_KNOWLEDGE_RULE,
                knowledge_rule_ids=tuple(knowledge_rule_ids),
            )
        )
    # **同じ経路を 2 回数えて「一致」にしない。** 独立性が分からないときも
    # 作らない(`paths_independent=None` のままでは `DecisiveReason` が断る)。
    if len(set(agreeing_paths)) >= 2 and paths_independent is not None:
        reasons.append(
            DecisiveReason(
                kind=REASON_PATHS_AGREED,
                agreeing_paths=tuple(agreeing_paths),
                paths_independent=paths_independent,
            )
        )
    if question_id:
        reasons.append(
            DecisiveReason(kind=REASON_HUMAN_ANSWER, question_id=question_id)
        )
    if summary_item:
        reasons.append(
            DecisiveReason(kind=REASON_SUMMARY_SEARCH, summary_item=summary_item)
        )

    if reasons:
        return tuple(reasons)

    # **「観測だけで出た」の「だけ」。** 上が 1 つでもあればこちらは名乗らない。
    #
    # **読んだ値から計算した値も観測に入れる。** 図面以外の根拠が 1 つも
    # 入っていないからである。**ただし計算したことは書き添える**(素直に
    # 読めた値と、計算で出した値を、報告で見分けられるようにするため)。
    # **一般則で補った値(`assumed`)はここに入らない。** 根拠が図面の外にあり、
    # それを指せるルールIDも無いので、決め手は空のままにする。
    if source_kind == "drawing" and effective_derivation in ("read", "derived"):
        return (
            DecisiveReason(
                kind=REASON_OBSERVED,
                detail="読んだ値から計算した" if effective_derivation == "derived" else "",
            ),
        )
    return ()


# ---------------------------------------------------------------------------
# 理由別の集計(**既存の指標は消さない。足すだけ。**)
# ---------------------------------------------------------------------------


def _reasons_of(line: Any) -> tuple[DecisiveReason, ...]:
    return tuple(getattr(line, "decisive", ()) or ())


def counts_by_reason(lines: Iterable[Any]) -> dict[str, int]:
    """行を決め手の種類ごとに数える。

    **1 行が 2 つの決め手を持つことがある**ので、合計は行数と一致しない。
    行数と突き合わせたいときは `lines_without_reason()` と併せて見る。
    """
    counts: dict[str, int] = {}
    for line in lines:
        for reason in _reasons_of(line):
            counts[reason.kind] = counts.get(reason.kind, 0) + 1
    return counts


def lines_without_reason(lines: Iterable[Any]) -> tuple[Any, ...]:
    """**決め手が 1 つも無い行。** 埋めずに名指しするための入口。"""
    return tuple(line for line in lines if not _reasons_of(line))


@dataclass(frozen=True)
class ReasonScore:
    """決め手 1 種類ぶんの採点。"""

    reason: str
    hit: int
    miss: int

    @property
    def total(self) -> int:
        return self.hit + self.miss

    @property
    def hit_rate(self) -> float | None:
        """**突き合わせるものが無ければ None。** 0.0 と区別する
        (`arbitration/provisional_audit.py` と同じ約束)。
        """
        if not self.total:
            return None
        return self.hit / self.total

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "hit": self.hit,
            "miss": self.miss,
            "total": self.total,
            "hit_rate": self.hit_rate,
        }


def score_by_reason(
    hits: Iterable[Any], misses: Iterable[Any]
) -> tuple[ReasonScore, ...]:
    """**当たった行と外した行を、決め手の理由別に集計する。**

    採点のときに呼ぶ。**当たり外れの判定はこの関数の外でする**(正解との
    突き合わせは採点側の仕事で、ここは数えるだけ)。抽出と採点を分ける
    ベンチマークの運用規則を、この層でも崩さない。
    """
    hit_counts = counts_by_reason(hits)
    miss_counts = counts_by_reason(misses)
    return tuple(
        ReasonScore(
            reason=kind,
            hit=hit_counts.get(kind, 0),
            miss=miss_counts.get(kind, 0),
        )
        for kind in REASON_KINDS
        if hit_counts.get(kind) or miss_counts.get(kind)
    )


def not_obtained_counts(items: Iterable[NotObtained]) -> dict[str, int]:
    """取れなかったものを理由別に数える。"""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    return counts


def counts_text(counts: dict[str, int]) -> str:
    """報告にそのまま貼れる 1 行。**件数だけ。**"""
    if not counts:
        return "なし"
    return "、".join(f"{name} {counts[name]} 件" for name in sorted(counts))
