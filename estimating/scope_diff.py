"""工事内容(現況と計画の差分)の**要素 1 件**の形。

設計は `docs/principles/scope_of_work_diff.md` 6節にある。**この版で実装して
あるのは、その設計のうち「要素 1 件の入れ物」と「工事区分の 5 つの値」だけ**
である。設計にある残りは**まだ無い**。

まだ無いもの(設計にはあるが、このファイルには入っていない)
--------------------------------------------------------
- `ScopePairing` … 現況の図面と計画の図面の対応づけ(設計 2節)
- `ScopeDiffResult` … 対応づけから差分を作る層(設計 4-1節の突き合わせ表)
- `existing_quantity` / `planned_quantity` からの区分の決定

**なぜ入れ物だけ先に作るのか。** 内装仕上表は、現況と計画を 2 枚の図面で
突き合わせなくても、**1 つの表の中に「下地は既存、仕上は張り替え」と
書いてある**。つまり差分の突き合わせを通さずに工事区分が読める経路が
1 本だけ先にできた(K-05)。その経路の出口をここに合わせておけば、
後から突き合わせの層ができたときに、同じ形のまま合流できる。

**この層は数量を作らない。** 面積と長さは人の入力から来る決まりである
(原則 3-1・原則 4)。`value_range` と `unit` は、突き合わせの層ができて
から埋まる欄で、いまは常に None である。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 工事区分。**日本語で持つ**(設計 4-1節。読み手に英語の語を使わせない)。
WORK_REMOVAL = "撤去"
WORK_NEW = "新設"
WORK_AS_IS = "既存のまま"
WORK_ALTERED = "改修"
WORK_UNDECIDED = "区分未定"

#: 区分の全部。**ここに無い値は受け付けない。**
WORK_KINDS: tuple[str, ...] = (
    WORK_REMOVAL,
    WORK_NEW,
    WORK_AS_IS,
    WORK_ALTERED,
    WORK_UNDECIDED,
)


class ScopeError(ValueError):
    """要素の作り方が約束に反しているときに投げる。"""


@dataclass(frozen=True)
class ScopeEvidence:
    """**その区分がどこから来たか。**

    設計そのものには無い欄だが、おーちゃんの K-05 の 4 番
    「根拠の欄には『仕上表から読んだ』と、ページと行番号を必ず入れてください」
    がこれである。数量が無い要素でも、根拠だけは必ず付く。
    """

    kind: str
    """どこから読んだか。例: ``仕上表から読んだ``。**空にできない。**"""

    page_number: int
    """1 始まり。人に見せる番号に合わせる。"""

    row_index: int
    """表の中の行番号。見出しの行が 0。"""

    source_texts: dict[str, str] = field(default_factory=dict)
    """区分の決め手になった升目の、**図面に印字されたままの文字**。

    **この中身は図面の中身なので、リポジトリにも記憶にも書かない。**
    実行時に人へ見せるためだけに運ぶ。
    """

    def __post_init__(self) -> None:
        if not self.kind or not self.kind.strip():
            raise ScopeError("根拠の kind が空です")
        if self.page_number < 1:
            raise ScopeError(f"ページ番号は 1 以上です: {self.page_number}")
        if self.row_index < 0:
            raise ScopeError(f"行番号は 0 以上です: {self.row_index}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "page_number": self.page_number,
            "row_index": self.row_index,
            "source_texts": dict(self.source_texts),
        }


@dataclass(frozen=True)
class WorkScopeItem:
    """工事内容 1 件(設計 6節の `WorkScopeItem` の、いま埋められる欄だけ)。"""

    work_kind: str
    """`WORK_KINDS` のどれか。"""

    what: str
    """それは何か。仕上表から作る場合は部位(床・壁・天井・巾木…)。"""

    where: str
    """どこのものか。仕上表から作る場合は室名。**空にできない。**

    設計 3-3節は `Meaning.where` が `不明` でなければそれを使うと書いている。
    室名が引き継げなかった行では、この部品は要素を作らずに問いへ回す。
    """

    evidence: tuple[ScopeEvidence, ...]
    """**根拠。1 件以上必須。** 根拠の無い区分は置かない(原則・第1段階)。"""

    value_range: tuple[float, float] | None = None
    """工事の数量。**この層では常に None。** 引き算では作らない(設計 4-3節)。"""

    unit: str | None = None

    alternatives: tuple[str, ...] = ()
    """ほかにありえた区分。**「撤去して新設」のように 1 つの区分に畳むと
    片方が消える読みで、消えたほうをここに残す。**"""

    exclusive_group: str | None = None
    """排他の組の識別子。**同じ組の行は合計しない**(設計 4-3節)。"""

    site_survey_reason: str = ""
    """現地確認が要る理由。**根拠が無ければ空**(設計 6節)。"""

    notes: tuple[str, ...] = ()
    """限界の注記(設計 4-5節)。"""

    def __post_init__(self) -> None:
        if self.work_kind not in WORK_KINDS:
            raise ScopeError(
                f"知らない工事区分です: {self.work_kind!r}。"
                f"使えるのは {'/'.join(WORK_KINDS)} だけです"
            )
        if not self.what or not self.what.strip():
            raise ScopeError("what(それは何か)が空です")
        if not self.where or not self.where.strip():
            raise ScopeError("where(どこのものか)が空です")
        if not self.evidence:
            raise ScopeError(
                f"根拠の無い要素は作れません(区分 {self.work_kind!r})。"
                "根拠が無ければ区分不明にするのではなく、要素を作らないこと"
            )
        for kind in self.alternatives:
            if kind not in WORK_KINDS:
                raise ScopeError(f"知らない工事区分です: {kind!r}")
        if (self.value_range is None) != (self.unit is None):
            raise ScopeError("数量と単位は、両方あるか両方無いかのどちらかです")

    @property
    def target(self) -> str:
        """下流の `QuantityItem` に戻すときの対象名(設計 6節)。"""
        return f"工事内容::{self.what}::{self.where}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_kind": self.work_kind,
            "what": self.what,
            "where": self.where,
            "target": self.target,
            "value_range": list(self.value_range) if self.value_range else None,
            "unit": self.unit,
            "alternatives": list(self.alternatives),
            "exclusive_group": self.exclusive_group,
            "site_survey_reason": self.site_survey_reason,
            "notes": list(self.notes),
            "evidence": [e.as_dict() for e in self.evidence],
        }
