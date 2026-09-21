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

精度モードとの連動
------------------
``PrecisionMode.PRECISE``(精密)では、階層2(仮採用+抜き取り監査)の要素も
標本監査だけで済ませず、直接確認の対象に含める。そのため精密モードでは
階層2の要素を階層3と同じ扱い(生の読み取りレンジを暫定候補として登録)にする。
標準・概算モードでは、階層2は従来どおり ``confirmed_range`` を確定値として使う。
"""

from __future__ import annotations

from typing import Sequence

from arbitration.axis_quality_firewall import AxisEvidence, FirewallDecision
from arbitration.consistency_solver import ConsistencySolver
from killer_question.precision_mode import PrecisionMode

#: 階層1(auto_confirm)で確定した要素に付ける軸名。誤り率をほぼゼロと
#: 扱ってよい(独立した強い軸2つ以上の一致という、v8 3-3節の定義そのもの)。
FIREWALL_CONFIRMED_AXIS = "firewall_confirmed"

#: 階層2(provisional_audit)で暫定採用した要素に付ける軸名。階層1ほどではないが、
#: 強い軸1つ+複数の弱い軸の支持がある状態(v8 3-3節)。
FIREWALL_PROVISIONAL_AXIS = "firewall_provisional"


def add_target_to_joint_solver(
    solver: ConsistencySolver,
    target: str,
    decision: FirewallDecision,
    evidences: Sequence[AxisEvidence],
    *,
    mode: PrecisionMode = PrecisionMode.STANDARD,
) -> None:
    """1要素分のファイアウォール結果を、結合solverへ変数として登録する。

    - 階層1(``action == "auto_confirm"``): ``decision.confirmed_range`` を
      そのまま使う。独立した強い軸2つ以上が一致した結果のため、
      ``FIREWALL_CONFIRMED_AXIS`` という信頼度の高い軸名を付ける
    - 階層2(``action == "provisional_audit"``): 標準・概算モードでは階層1と
      同様に ``confirmed_range`` を使う(``FIREWALL_PROVISIONAL_AXIS``)。
      **精密モードでは、標本監査だけで済ませず直接確認の対象にするため、
      階層3と同じ扱い(生の読み取りレンジ)にする**
    - 階層3(``action == "requires_review"``、または精密モードの階層2):
      まだ確定していないため、**生の読み取りレンジの和集合**
      (最小の下限〜最大の上限)を暫定候補として登録する。校正の有無に
      関わらず登録する点に注意。ハードな確定ではなく、「キラークエスチョンで
      絞り込む前の出発点」という位置づけである
    """
    if not evidences:
        raise ValueError(f"'{target}' の証拠が空です")

    treat_as_needs_confirmation = decision.confirmed_range is None or (
        mode is PrecisionMode.PRECISE and decision.action == "provisional_audit"
    )

    if not treat_as_needs_confirmation:
        lower, upper = decision.confirmed_range  # type: ignore[misc]
        axis = FIREWALL_CONFIRMED_AXIS if decision.action == "auto_confirm" else FIREWALL_PROVISIONAL_AXIS
    else:
        lower = min(e.count_range[0] for e in evidences)
        upper = max(e.count_range[1] for e in evidences)
        # 複数の軸が混ざる場合は、代表として最初の証拠の軸を使う。
        axis = evidences[0].axis_id

    solver.add_variable(target, lower, upper, axis=axis)
