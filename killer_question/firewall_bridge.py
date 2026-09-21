"""``AxisQualityFirewall`` の出力から、killer_question 用の結合 ``ConsistencySolver``
を組み立てる橋渡し層。

段階1(トライアル7〜9の再現)は統制された合成データの上で行われたが、
``AxisQualityFirewall.assess()`` は1要素(target)ずつしか処理しない。
複数要素にまたがる関係式(「開き戸の数 >= 部屋数」等)を使ってキラー
クエスチョンを選ぶには、各要素をそれぞれファイアウォールに通した後、
**1つの結合solverへ組み直す**必要がある。この組み直し方は、トライアル7〜9には
無かった、今回新たに必要になった設計判断である
(詳細は ``docs/killer_question_report.md`` 3節)。

2026-09-21 に修正した2つのバグ
--------------------------------
点検で、この関数に2つのバグが見つかった(実測結果は
``docs/top_priority_unit_safety_defect.md`` 3-3節・3-4節)。

**バグ①: 強い軸が1つも無いとき、全証拠のレンジの和集合を
`strength="strong"` のハード変数として登録していた。** 和集合には
``status="abstained"`` の証拠の番兵値 ``(0, 0)`` や、単位の違う弱い軸の
レンジも混ざるため、「レンジを主張せず棄権する」と宣言した軸が実際には
範囲を書き換えていた(実測: ``lower=0 upper=246``)。
→ **修正: 強い軸が1つも無い場合、代替のハード変数は作らず、
その要素を階層3(要確認)へフォールバックさせる**(既定の挙動)。和集合は作らない。

**この修正には副作用がある(要判断)。** ``docs/killer_question_report.md`` 3節が
実証した「Grounding DINO の読み取り単体(``calibrated=False``、階層3)を暫定候補
として結合solverに入れ、関係式で ``(2,6)`` から ``(4,5)`` まで絞り込み、1問で
解決する」という挙動は、**この和集合の上に成り立っていた。** 既定の挙動では
door_count / window_count は solver に入らないため、この絞り込みは起きず、
そのまま人の確認へ回る(安全側だが、自動化率は下がる)。
従来の挙動が必要な場合は ``allow_provisional_domain=True`` を明示的に渡す。
そのときも棄権した証拠は除外し、``requires_confirmation=True`` を立てるため、
**確定済み金額に計上されることも、質問対象から外れることもない**(バグ②の修正が
効いている)。どちらを既定にすべきかはおーちゃんの判断を仰ぐ。

**バグ②: 分岐条件が ``decision.confirmed_range is None`` になっており、
自身のdocstringと一致していなかった。** そのため階層3(``requires_review``)の
要素も、``confirmed_range`` が非Noneなら階層1・2と同じ経路を通り、
レンジ幅0の変数として登録されていた。エンジンは幅0を「確定済み」と見なすため、
**人の確認が必要な要素が質問対象から外れ、確定済み金額にも計上されていた。**
→ **修正: 分岐は ``decision.action`` で行い、階層3の要素には
``requires_confirmation=True`` を立てる。** 幅0でも未確定として扱われる。

精度モードとの連動
------------------
``PrecisionMode.PRECISE``(精密)では、階層2(仮採用+抜き取り監査)の要素も
標本監査だけで済ませず、直接確認の対象に含める。そのため精密モードでは
階層2の要素に ``requires_confirmation=True`` を立て、候補を絞り込む前の
読み取りレンジまで開き直す。**このとき棄権した証拠は必ず除外する**
(番兵値 ``(0, 0)`` が下限を 0 まで引き下げるため。バグ①と同根)。
標準・概算モードでは、階層2は従来どおり確定値として使う。
"""

from __future__ import annotations

from dataclasses import dataclass
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

#: 階層3(requires_review)の要素に付ける軸名。**確定していない。**
#: 以前はこの状態の要素にも FIREWALL_PROVISIONAL_AXIS を付けていたため、
#: 階層2と区別できなくなっていた(バグ②)。
FIREWALL_UNCONFIRMED_AXIS = "firewall_unconfirmed"


@dataclass(frozen=True)
class BridgeResult:
    """1要素分の組み込み結果。呼び出し側が「入ったのか」を判別できるようにする。"""

    target: str
    registered: bool
    #: 変数として登録されなかった理由(登録された場合は None)。
    skipped_reason: str | None = None
    requires_confirmation: bool = False

    @property
    def escalated_to_review(self) -> bool:
        """人の確認へ回るか(未登録、または確認待ちで登録された)。"""
        return not self.registered or self.requires_confirmation


def add_target_to_joint_solver(
    solver: ConsistencySolver,
    target: str,
    decision: FirewallDecision,
    evidences: Sequence[AxisEvidence],
    *,
    mode: PrecisionMode = PrecisionMode.STANDARD,
    allow_provisional_domain: bool = False,
) -> BridgeResult:
    """1要素分のファイアウォール結果を、結合solverへ変数として登録する。

    分岐は **``decision.action`` で行う**(``confirmed_range`` の有無ではない)。

    - 階層1(``auto_confirm``): ``confirmed_range`` を確定値として登録する。
      独立した強い軸2つ以上が一致した結果のため、``FIREWALL_CONFIRMED_AXIS``
    - 階層2(``provisional_audit``): 標準・概算モードでは ``confirmed_range`` を
      確定値として登録する(``FIREWALL_PROVISIONAL_AXIS``)。
      **精密モードでは直接確認の対象にするため、棄権していない証拠のレンジまで
      開き直し ``requires_confirmation=True`` を立てる**
    - 階層3(``requires_review``): ``FIREWALL_UNCONFIRMED_AXIS`` で
      ``requires_confirmation=True`` を立てて登録する。**レンジ幅が0でも
      確定済みとして扱われない。** 強い軸が1つも無く ``confirmed_range`` が
      ``None`` の場合は、**和集合による代替のハード変数を作らず、変数として
      登録しない**(その要素はそのまま人の確認へ回る)

    :param allow_provisional_domain: 強い軸が1つも無い階層3の要素について、
        棄権していない証拠のレンジの和集合を「暫定候補の定義域」として
        登録するか。**既定は False**(モジュール冒頭のバグ①)。True にすると
        ``docs/killer_question_report.md`` 3節の絞り込みが再現するが、
        校正されていない読み取りが定義域を決めることになる。どちらの場合も
        ``requires_confirmation=True`` が立つため、確定済みとして扱われることは
        ない。

    戻り値の ``BridgeResult`` で、登録されたか・確認待ちかが分かる。
    """
    if not evidences:
        raise ValueError(f"'{target}' の証拠が空です")

    if decision.action == "requires_review":
        requires_confirmation = True
        if decision.confirmed_range is not None:
            lower, upper = decision.confirmed_range
            axis = FIREWALL_UNCONFIRMED_AXIS
        elif not allow_provisional_domain:
            # バグ①の修正(既定)。ここで和集合を作ってはならない。棄権した
            # 証拠の番兵値や単位の違うレンジが、ハード制約の範囲を書き換える。
            return BridgeResult(
                target=target,
                registered=False,
                skipped_reason=(
                    "実測校正済みの強い軸が1つも無いため、暫定のハード変数を作らず"
                    "階層3(要確認)へフォールバックした"
                ),
            )
        else:
            # 明示的に許可された場合のみ、暫定候補の定義域として和集合を使う。
            # **棄権した証拠は必ず除外する**(番兵値 (0,0) が下限を 0 まで
            # 引き下げるのがバグ①の実害だった)。
            speaking = [e for e in evidences if e.status != "abstained"]
            if not speaking:
                return BridgeResult(
                    target=target,
                    registered=False,
                    skipped_reason=(
                        "棄権していない証拠が1つも無いため、暫定の定義域を作らず"
                        "階層3(要確認)へフォールバックした"
                    ),
                )
            lower = min(e.count_range[0] for e in speaking)
            upper = max(e.count_range[1] for e in speaking)
            axis = speaking[0].axis_id
    elif decision.action == "auto_confirm":
        if decision.confirmed_range is None:
            raise ValueError(
                f"'{target}': action={decision.action} なのに confirmed_range が None"
            )
        lower, upper = decision.confirmed_range
        axis = FIREWALL_CONFIRMED_AXIS
        requires_confirmation = False
    else:
        # 階層2(provisional_audit)。
        if decision.confirmed_range is None:
            raise ValueError(
                f"'{target}': action={decision.action} なのに confirmed_range が None"
            )
        requires_confirmation = mode is PrecisionMode.PRECISE
        axis = FIREWALL_PROVISIONAL_AXIS
        if requires_confirmation:
            # 精密モードは標本監査で済ませず直接確認する。確定範囲に絞り込む前の
            # 読み取りレンジまで戻して候補を開き直す。
            # **棄権した証拠は必ず除外する。** status="abstained" の証拠は
            # count_range に番兵値 (0, 0) を持つため、混ぜると下限が 0 まで
            # 引き下げられる(バグ①と同根の欠陥)。
            speaking = [e for e in evidences if e.status != "abstained"]
            if not speaking:
                return BridgeResult(
                    target=target,
                    registered=False,
                    skipped_reason=(
                        "精密モードで開き直そうとしたが、棄権していない証拠が"
                        "1つも無いため階層3(要確認)へフォールバックした"
                    ),
                )
            lower = min(e.count_range[0] for e in speaking)
            upper = max(e.count_range[1] for e in speaking)
            # 軸名は開き直した読み取りの軸のまま残す。4-3節のデータ源別
            # 誤り率による同点崩しが、この変数にも効くようにするため。
            axis = speaking[0].axis_id
        else:
            lower, upper = decision.confirmed_range

    solver.add_variable(
        target,
        lower,
        upper,
        axis=axis,
        requires_confirmation=requires_confirmation,
        evidence={
            "firewall_tier": decision.tier,
            "firewall_action": decision.action,
        },
    )
    return BridgeResult(
        target=target,
        registered=True,
        requires_confirmation=requires_confirmation,
    )
