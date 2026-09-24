"""規則が引用した知識の ID を、知識の表と突き合わせる。

**なぜこの層が別に要るのか**

`estimating/decisive.py` には「どの知識のルールで数えたか」の欄があるのに、
**値を入れる側が 0 か所だった**(K-09 の報告 2 節)。K-11 の 1 番でそこを繋いだが、
繋ぎ方には守るべき向きがある。

- `estimating/` は知識の表を**読まない。**引用は文字列のまま運ぶだけである。
  判定の側から `knowledge/` を import しないという約束
  (`tests/test_knowledge_table.py` の「判定の側から読み込まれていない」)を崩さない。
- 代わりに、**引用が本物かどうかはこの層が調べる。**ここは `knowledge/` の側なので、
  規則も知識の表も両方読める。

**ここが守ること**

1. **表に無い ID を引いた規則は断る。**証拠の無い札を作らせないのと同じ考え方で、
   指し先の無い引用を通さない(`estimating/decisive.py` 1 節)。
2. **`不採用` の知識は引用できない。**おーちゃんが落とした知識で行を立てない。
3. **`候補` は引用できるが、別に数える。**採否を決めるのはおーちゃんだけで
   (K-04 6 番)、コードが `候補` を `採用` と同じ扱いにしてはいけない。
   **混ぜて数えると、採否の判断が済んでいるように見えてしまう。**
   採否は `knowledge/table.py` の**読むだけの窓**(`is_adopted` など)を通して読む。
   この層は採否の列の名前を書かない。**読み手が増えても、書き換える口は増えない。**
4. **確認日が空欄(不明)の知識は名指しする。**確認日が `null` の知識は採用の判断に
   進めない(K-07 5 番)。引用そのものは止めないが、**どれが空欄かを返す。**
5. **この層は判定に何も足さない。**呼ぶかどうかは呼ぶ側が決める。階層・許容差・
   自動確定には触らない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from knowledge.table import ADOPTION_STATUSES, KnowledgeTable


class LinkageError(Exception):
    """引用が受け付けられなかった。**黙って直さない。**"""


@dataclass(frozen=True)
class LinkageReport:
    """引用の突き合わせの結果。**件数ではなく ID を返す。**

    報告で「何件」と書く前に、**どの ID かを見られるようにしておく。**
    """

    table_id: str
    cited_adopted: tuple[str, ...] = ()
    """`採用` の知識を引いているもの。"""

    cited_candidate: tuple[str, ...] = ()
    """`候補` の知識を引いているもの。**採用と混ぜて数えない。**"""

    cited_without_checked_on: tuple[str, ...] = ()
    """確認日が空欄(不明)の知識を引いているもの(K-07 5 番)。"""

    citing_rule_ids: tuple[str, ...] = ()
    """引用を持っていた規則・行の ID(どこから引かれたか)。"""

    @property
    def cited(self) -> tuple[str, ...]:
        """引かれた知識の ID すべて。"""
        return tuple(sorted(set(self.cited_adopted) | set(self.cited_candidate)))

    def summary(self) -> str:
        """人が読む 1 行。**採否の状態ごとに分けて数える。**"""
        return (
            f"知識の表: {self.table_id} / "
            f"引用を持つ規則 {len(self.citing_rule_ids)} 件 / "
            f"引かれた知識 採用 {len(self.cited_adopted)} 件・"
            f"候補 {len(self.cited_candidate)} 件 / "
            f"うち確認日が空欄 {len(self.cited_without_checked_on)} 件"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "cited_adopted": list(self.cited_adopted),
            "cited_candidate": list(self.cited_candidate),
            "cited_without_checked_on": list(self.cited_without_checked_on),
            "citing_rule_ids": list(self.citing_rule_ids),
        }


def _citations(ruleset: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """規則と、図面からは決まらない行の両方から引用を集める。

    `ruleset` の型を注釈で縛らないのは、この層が `estimating/` に依存しない
    ためである(向きを固定しておくと、あとで循環しない)。必要なのは
    `rules` と `standing_lines` の中の `rule_id` / `standing_id` /
    `knowledge_rule_ids` だけである。
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    for rule in getattr(ruleset, "rules", ()) or ():
        ids = tuple(getattr(rule, "knowledge_rule_ids", ()) or ())
        if ids:
            out.append((getattr(rule, "rule_id", "(名前なし)"), ids))
    for standing in getattr(ruleset, "standing_lines", ()) or ():
        ids = tuple(getattr(standing, "knowledge_rule_ids", ()) or ())
        if ids:
            out.append((getattr(standing, "standing_id", "(名前なし)"), ids))
    return tuple(out)


def check_knowledge_links(ruleset: Any, table: KnowledgeTable) -> LinkageReport:
    """規則の引用を知識の表と突き合わせる。**通らなければ例外。**

    返すのは「どの知識が引かれたか」で、**採否の状態ごとに分けてある。**
    """
    entries = {entry.entry_id: entry for entry in table.entries}

    missing: list[str] = []
    rejected: list[str] = []
    adopted: list[str] = []
    candidate: list[str] = []
    unknown_status: list[str] = []
    without_checked_on: list[str] = []
    citing: list[str] = []

    for owner, ids in _citations(ruleset):
        citing.append(owner)
        for entry_id in ids:
            entry = entries.get(entry_id)
            if entry is None:
                missing.append(f"{owner} → {entry_id}")
                continue
            # **読むだけ。**採否は `knowledge/table.py` の窓を通して読み、
            # 列の名前をこの層に書かない(K-04 6 番を崩さないため)。
            if entry.is_rejected:
                rejected.append(f"{owner} → {entry_id}")
                continue
            if entry.is_adopted:
                adopted.append(entry_id)
            elif entry.is_candidate:
                candidate.append(entry_id)
            else:
                unknown_status.append(f"{owner} → {entry_id}({entry.adoption})")
                continue
            if entry.source.checked_on is None:
                without_checked_on.append(entry_id)

    problems: list[str] = []
    if missing:
        problems.append(
            "知識の表に無い ID を引いています(指し先の無い引用は通しません): "
            + "、".join(missing)
        )
    if rejected:
        problems.append(
            "落とした知識(不採用)を引いています。その知識で行を立てません: "
            + "、".join(rejected)
        )
    if unknown_status:
        problems.append(
            "採否の状態が知らない値です"
            f"(書けるのは {list(ADOPTION_STATUSES)}): " + "、".join(unknown_status)
        )
    if problems:
        raise LinkageError(
            f"知識の表 {table.table_id} との突き合わせで断りました。" + " / ".join(problems)
        )

    return LinkageReport(
        table_id=table.table_id,
        cited_adopted=_unique(adopted),
        cited_candidate=_unique(candidate),
        cited_without_checked_on=_unique(without_checked_on),
        citing_rule_ids=tuple(citing),
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """**出てきた順**を保って重複を落とす(並べ替えると読みにくい)。"""
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return tuple(out)


__all__ = ["LinkageError", "LinkageReport", "check_knowledge_links"]
