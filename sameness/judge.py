"""K-66 1 節: 判定の 5 値。**ここだけが「同じか」を決める。**

5 値: `○`(同じ)/ `△上位・下位` / `△粒度`(合計と位置が合えば `○` に格上げ)/ `×`(違う)/ `比較不能`。

細目の決め方は `docs/k66_sameness_criteria.md` 1 節に測る前に書いた 7 つの規則そのままである。
**規則の番号を理由に書く**ので、どの規則で決まったかが画面から辿れる。

格上げは呼ぶ側の仕事: `△粒度` を `○` にするのは `promote()` に
「合計が許容差内で合い、位置(科目・部位)も合う」を渡したときだけ。**部品が勝手に格上げしない。**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from sameness.keys import StructureKey, structure_key
from sameness.normalize import flatten
from sameness.terms import Terms, default_terms

SAME = "○"
HIGHER_LOWER = "△上位・下位"
GRAIN = "△粒度"
NOT_SAME = "×"
INCOMPARABLE = "比較不能"
VALUES = (SAME, HIGHER_LOWER, GRAIN, NOT_SAME, INCOMPARABLE)

LEVELS = ("科目", "中科目", "細目")


@dataclass(frozen=True)
class Verdict:
    """1 対の判定。**理由を必ず持つ**(K-66 2 節 e)。"""

    value: str
    reason: str
    rule: str
    level: str
    left: StructureKey | None = None
    right: StructureKey | None = None
    judges: tuple[str, ...] = ()
    """判定役(AI)の答え。空なら AI を呼んでいない。"""

    @property
    def hit(self) -> bool:
        """当たりとして数えるか。**`△` は格上げされるまで当たりにしない。**"""
        return self.value == SAME

    def promote(self, *, totals_match: bool, place_match: bool) -> "Verdict":
        """`△粒度` を `○` に格上げする。**合計と位置の両方が合ったときだけ。**"""
        if self.value != GRAIN or not (totals_match and place_match):
            return self
        return Verdict(
            value=SAME, level=self.level, rule=self.rule + "+格上げ",
            reason=self.reason + " / 粒度は違うが合計が許容差内で合い位置も合うので格上げ",
            left=self.left, right=self.right, judges=self.judges,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "判定": self.value, "理由": self.reason, "規則": self.rule, "段": self.level,
            "左": self.left.as_dict() if self.left else None,
            "右": self.right.as_dict() if self.right else None,
            "判定役": list(self.judges),
        }


def _name(terms: Terms, slot: str, gid: str | None) -> str:
    group = terms.group(slot, gid) if gid else None
    return group.代表 if group else (gid or "不明")


def _category_verdict(level: str, a: StructureKey, b: StructureKey, terms: Terms) -> Verdict:
    """科目・中科目の段。**辞書で同じ分類かだけを見る。**"""
    slot = level
    left = a.科目 if level == "科目" else a.中科目
    right = b.科目 if level == "科目" else b.中科目
    rel = terms.relation(slot, left, right)
    ln, rn = _name(terms, slot, left), _name(terms, slot, right)
    if rel == "不明":
        return Verdict(INCOMPARABLE, f"{level}が片方(または両方)取れていない({ln} / {rn})", "科目1", level, a, b)
    if rel == "同じ":
        return Verdict(SAME, f"{level}はどちらも {ln}(辞書で同じ組)", "科目2", level, a, b)
    if rel in ("上位", "下位"):
        return Verdict(HIGHER_LOWER, f"{level}は {ln} と {rn} で上位・下位の関係", "科目3", level, a, b)
    return Verdict(NOT_SAME, f"{level}が {ln} と {rn} で別の組", "科目4", level, a, b)


def compare_keys(a: StructureKey, b: StructureKey, *, level: str = "細目", terms: Terms | None = None) -> Verdict:
    """構造のキー 2 つを比べる。**品名は見ない。**"""
    terms = terms or default_terms()
    if level not in LEVELS:
        raise ValueError(f"段は {LEVELS} のどれか: {level!r}")
    if level in ("科目", "中科目"):
        return _category_verdict(level, a, b, terms)

    k = lambda gid: _name(terms, "工事の種類", gid)
    # 規則 7 / 6: 工事の種類が取れていない
    if not a.工事の種類 or not b.工事の種類:
        known, unknown = (a, b) if a.工事の種類 else (b, a)
        if known.工事の種類 and unknown.科目 and known.科目 == unknown.科目:
            return Verdict(
                HIGHER_LOWER,
                f"片方は細目({known.工事の種類})、もう片方はその科目({_name(terms,'科目',unknown.科目)})だけ",
                "細目6", level, a, b,
            )
        return Verdict(INCOMPARABLE, "工事の種類が片方(または両方)取れていない", "細目7", level, a, b)

    # 規則 1: 工事の種類が違う
    if a.工事の種類 != b.工事の種類:
        return Verdict(NOT_SAME, f"工事の種類が {a.工事の種類} と {b.工事の種類} で違う", "細目1", level, a, b)

    # 規則 1: 状態
    if a.状態 and b.状態 and a.状態 != b.状態:
        return Verdict(
            NOT_SAME,
            f"状態が {_name(terms,'状態',a.状態)} と {_name(terms,'状態',b.状態)} で違う",
            "細目1", level, a, b,
        )

    # 規則 1 / 5: 部位
    if a.部位 and b.部位 and a.部位 != b.部位:
        rel = terms.relation("部位", a.部位, b.部位)
        detail = f"部位が {_name(terms,'部位',a.部位)} と {_name(terms,'部位',b.部位)}"
        if rel in ("上位", "下位"):
            return Verdict(GRAIN, detail + " で上位・下位(粒度が違う)", "細目5", level, a, b)
        return Verdict(NOT_SAME, detail + " で別の部位", "細目1", level, a, b)
    if bool(a.部位) != bool(b.部位):
        return Verdict(GRAIN, "片方だけ部位が取れている(粒度が違う)", "細目5", level, a, b)
    if bool(a.状態) != bool(b.状態):
        return Verdict(GRAIN, "片方だけ状態が取れている(粒度が違う)", "細目5", level, a, b)

    # 規則 2: 材料が両方取れていて違う
    if a.材料 and b.材料 and a.材料 != b.材料:
        return Verdict(
            NOT_SAME,
            f"材料が {_name(terms,'材料',a.材料)} と {_name(terms,'材料',b.材料)} で違う",
            "細目2", level, a, b,
        )

    # 規則 3 / 4
    shared = [f"細目 {k(a.工事の種類)}"]
    if a.部位:
        shared.append(f"部位 {_name(terms,'部位',a.部位)}")
    if a.状態:
        shared.append(f"状態 {_name(terms,'状態',a.状態)}")
    if a.材料 and b.材料:
        shared.append(f"材料 {_name(terms,'材料',a.材料)}")
        return Verdict(SAME, "・".join(shared) + " がどちらも同じ", "細目4", level, a, b)
    if a.材料 or b.材料:
        return Verdict(SAME, "・".join(shared) + " が同じ(材料は片方だけ)", "細目3", level, a, b)
    return Verdict(SAME, "・".join(shared) + " が同じ(材料はどちらも未取得)", "細目3", level, a, b)


def compare(
    left: Mapping[str, Any] | str,
    right: Mapping[str, Any] | str,
    *,
    level: str = "細目",
    terms: Terms | None = None,
    identical_names: bool = True,
    **key_options: Any,
) -> Verdict:
    """行(または品名)2 つを比べる。構造のキーを作ってから `compare_keys` に渡す。

    `identical_names`(規則 8、**1 回目の測定のあとに足した**。基準の追記 1 を参照):
    構造が取れずに `比較不能` になったとき、**平らにした品名が丸ごと同じなら** `○` にする。
    同じ文字が同じものを指さないことは無いので、これは「似ている語で寄せる」ではない。
    **部分一致は使わない**(`床` と `床タイル` は一致にしない)。これを切ると、
    語彙に無い工事の行は、名前が字まで同じでも当たりにならない。
    """
    terms = terms or default_terms()
    a = structure_key(left, terms=terms, **key_options)
    b = structure_key(right, terms=terms, **key_options)
    verdict = compare_keys(a, b, level=level, terms=terms)
    if identical_names and verdict.value == INCOMPARABLE:
        from sameness.keys import _name_text

        la, lb = flatten(_name_text(left)), flatten(_name_text(right))
        if la and la == lb:
            return Verdict(SAME, f"構造は取れていないが、揃えた品名が丸ごと同じ({la})",
                           "細目8", level, a, b)
    return verdict


def judge_with_ai(
    verdict: Verdict,
    left: Any,
    right: Any,
    *,
    judges: Sequence[Callable[[Any, Any], str]] = (),
) -> Verdict:
    """K-66 2 節(d): **迷う対だけ**判定役に比べさせる。

    迷う対 = `比較不能` と `△`。`○`・`×` は機械が決めているので聞かない。
    **3 人のうち 2 人以上が「同じ」のときだけ `○`。2 人未満は `比較不能`**
    (`×` にはしない。判定役が割れたことを「違う」と言い切らない)。
    判定役が 3 人そろわなければ何もしない(**未取得を 0 にしない**)。
    """
    if verdict.value in (SAME, NOT_SAME) or len(judges) < 3:
        return verdict
    answers = tuple(str(ask(left, right)) for ask in judges[:3])
    same = sum(1 for a in answers if a.strip() in (SAME, "同じ", "same"))
    if same >= 2:
        return Verdict(
            SAME, verdict.reason + f" / 判定役 3 人のうち {same} 人が「同じ」",
            verdict.rule + "+判定役", verdict.level, verdict.left, verdict.right, answers,
        )
    return Verdict(
        INCOMPARABLE, verdict.reason + f" / 判定役 3 人のうち「同じ」は {same} 人(2 人未満)",
        verdict.rule + "+判定役", verdict.level, verdict.left, verdict.right, answers,
    )
