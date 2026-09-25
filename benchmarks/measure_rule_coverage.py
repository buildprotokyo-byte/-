"""周31(目的から数える): **行にならずに捨てられている入口の数量を、種類ごとに数える。**

**この道具は数えるだけで、本番の経路には繋いでいない。**
`app.py` にも `intake/` にも `estimating/` にも手を入れていない(K-29)。

**着手前にコードを読んで分かったこと(取り決め①)**

`app.py` の 547 行目は、**規則ファイルが渡されなかったとき、入口の数量を
1 件も見積の行に当てはめず、`gaps` に 1 行書いて捨てている。**

    [規則ファイルが無い] 入口の数量 N 件は見積の行に当てはめていない

**周27 の「一本通すと 67 行」は、この状態で出た行である。**
だから周27〜30 が追っていた面積待ちの 62 行と、この N 件は別の話だった。
**「#139 は入口まで届くのに行は 1 つも変わらない」の理由もここにある**——
**繋がっていないのではなく、規則が無いので当てはめの段が丸ごと飛んでいる。**

**規則は対象の「種類」に当てる**(`estimating/quantities.py` の `split_target`)。
**だから種類の数が、書かねばならない規則の本数の下限になる。**それを数える。

基準は `docs/loop_round31_what_rules_would_cover_criteria.md`(測る前にコミット済み)。

**対象名そのものは実案件の図面の中身なので、この道具は種類名を出力しない。**
**出すのは件数と種類数と順位だけである。**
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estimating.from_intake import quantities_from_intake  # noqa: E402
from estimating.quantities import QuantityItem  # noqa: E402
from estimating.rules import RuleSet, load_rules  # noqa: E402

#: 線1 の合格。**種類が 100 以下なら人が書ける量。**
LINE1_MAX_KINDS = 100

#: 線3 の合格。**上位 10 種類で 8 割**。
LINE3_TOP = 10
LINE3_SHARE = 0.8

#: 線3 の囮に勝つ差(ポイント)。**これを下回れば順位を付けた値打ちが無い。**
LINE3_MARGIN = 0.20

#: 囮を引く回数。
DECOY_DRAWS = 10


def kinds_of(quantities: Sequence[QuantityItem]) -> Counter:
    """対象の種類ごとの件数。**種類名は鍵にしか使わず、外へは出さない。**"""
    return Counter(item.kind for item in quantities)


def coverage_of(counts: Counter, chosen: Sequence[str]) -> float:
    """選んだ種類が全体の何割を覆うか。"""
    total = sum(counts.values())
    if not total:
        return 0.0
    return sum(counts[kind] for kind in chosen) / total


def scatter_kinds(counts: Counter, how_many: int, seed: int) -> float:
    """囮: **同じ本数だけ種類を無作為に選ぶ。**中央値を返す。

    **上位を選ぶことに値打ちがあるのかを試す囮であって、
    「種類が集まっていない」ことを試す囮ではない。**
    """
    rng = random.Random(seed)
    kinds = list(counts)
    if len(kinds) <= how_many:
        # **選ぶ余地が無いなら、囮は本物と同じになる。**そう書いて返す。
        return coverage_of(counts, kinds)
    drawn = []
    for _ in range(DECOY_DRAWS):
        drawn.append(coverage_of(counts, rng.sample(kinds, how_many)))
    drawn.sort()
    return drawn[len(drawn) // 2]


def hits_of(quantities: Sequence[QuantityItem], ruleset: RuleSet) -> int:
    """いま在る規則が何件に当たるか。**線2(対照)。判定には使わない。**"""
    hit = 0
    for item in quantities:
        if any(rule.match(item).matched for rule in ruleset.rules):
            hit += 1
    return hit


def settled_with(quantities: Sequence[QuantityItem], ruleset: RuleSet) -> int:
    """架空の規則を渡したとき、**自動で確定した行**が何件出るか(線4)。

    **0 でなければ裏返しの危険である。**
    """
    from estimating.pipeline import build_estimate_draft_from_quantities

    draft = build_estimate_draft_from_quantities(list(quantities), ruleset)
    return len(draft.settled_lines)


def measure(pdf: Path, answers: Path, rules: Path, seed: int) -> dict:
    from intake.drawing_intake import IntakeConfig, read_drawing

    intake = read_drawing(
        IntakeConfig(case_id="round31", pdf_path=pdf, answers_path=answers)
    )
    quantities = list(quantities_from_intake(intake))
    counts = kinds_of(quantities)
    total = sum(counts.values())

    ordered = [kind for kind, _ in counts.most_common()]
    top = ordered[:LINE3_TOP]
    real_share = coverage_of(counts, top)
    decoy_share = scatter_kinds(counts, LINE3_TOP, seed)

    ruleset = load_rules(rules)
    hits = hits_of(quantities, ruleset)
    settled = settled_with(quantities, ruleset)

    return {
        "入口の数量": total,
        "線1_種類の数": {
            "本物": len(counts),
            "合格": f"{LINE1_MAX_KINDS} 以下",
            "通過": len(counts) <= LINE1_MAX_KINDS,
            "意味": "規則は種類に当てるので、これが書く規則の本数の下限",
        },
        "線2_いま在る規則が当たる件数": {
            "規則の本数": len(ruleset.rules),
            "当たった件数": hits,
            "割合": round(hits / total, 4) if total else 0.0,
            "断り": "架空の見本。対照であって判定には使わない",
        },
        "線3_多い順に書けば何本で8割か": {
            "上位": LINE3_TOP,
            "上位の被覆": round(real_share, 4),
            "囮の被覆(無作為に同数、中央値)": round(decoy_share, 4),
            "差": round(real_share - decoy_share, 4),
            "合格": f"被覆 {LINE3_SHARE:.0%} 以上、かつ囮との差 {LINE3_MARGIN:.0%} 以上",
            "通過": real_share >= LINE3_SHARE
            and (real_share - decoy_share) >= LINE3_MARGIN,
        },
        "線4_架空の規則で自動確定が起きるか": {
            "当てはめで確定した行": settled,
            "合格": 0,
            "通過": settled == 0,
            "意味": "0 でなければ裏返しの危険。その場で止めて報告する",
        },
        "参考_順位ごとの件数": [count for _, count in counts.most_common()],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("estimating/examples/synthetic_rules.json"),
        help="対照に使う架空の見本",
    )
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()
    print(
        json.dumps(
            measure(args.pdf, args.answers, args.rules, args.seed),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
