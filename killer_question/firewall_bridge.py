"""``AxisQualityFirewall`` の出力から、killer_question 用の結合 ``ConsistencySolver``
を組み立てる橋渡し層。

段階1(トライアル7〜9の再現)は統制された合成データの上で行われたが、
``AxisQualityFirewall.assess()`` は1要素(target)ずつしか処理しない。
複数要素にまたがる関係式(「開き戸の数 >= 部屋数」等)を使ってキラー
クエスチョンを選ぶには、各要素をそれぞれファイアウォールに通した後、
**1つの結合solverへ組み直す**必要がある。この組み直し方(階層1は確定範囲、
それ以外は生の読み取りレンジを暫定候補として使う)は、トライアル7〜9には
無かった、今回新たに必要になった設計判断である
(詳細は ``docs/killer_question_report.md`` 3節)。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from arbitration.axis_quality_firewall import AxisEvidence, FirewallDecision
from arbitration.consistency_solver import ConsistencySolver

#: 階層1(auto_confirm)で確定した要素に付ける軸名。誤り率をほぼゼロと
#: 扱ってよい(独立した強い軸2つ以上の一致という、v8 3-3節の定義そのもの)。
FIREWALL_CONFIRMED_AXIS = "firewall_confirmed"


def add_target_to_joint_solver(
    solver: ConsistencySolver,
    target: str,
    decision: FirewallDecision,
    evidences: Sequence[AxisEvidence],
) -> None:
    """1要素分のファイアウォール結果を、結合solverへ変数として登録する。

    - 階層1(``action == "auto_confirm"``): ``decision.confirmed_range`` を
      そのまま使う。独立した強い軸2つ以上が一致した結果のため、
      ``FIREWALL_CONFIRMED_AXIS`` という信頼度の高い軸名を付ける
    - それ以外(階層2・3): まだ確定していないため、**生の読み取りレンジの
      和集合**(最小の下限〜最大の上限)を暫定候補として登録する。校正の
      有無に関わらず登録する点に注意。ハードな確定ではなく、
      「キラークエスチョンで絞り込む前の出発点」という位置づけである
    """
    if not evidences:
        raise ValueError(f"'{target}' の証拠が空です")

    if decision.confirmed_range is not None:
        lower, upper = decision.confirmed_range
        axis = FIREWALL_CONFIRMED_AXIS
    else:
        lower = min(e.count_range[0] for e in evidences)
        upper = max(e.count_range[1] for e in evidences)
        # 複数の軸が混ざる場合は、代表として最初の証拠の軸を使う。
        axis = evidences[0].axis_id

    solver.add_variable(target, lower, upper, axis=axis)
