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

2026-09-22 の変更(群合計制約の矛盾検出)
----------------------------------------
上の「変数を作らない」という既定には、実測で分かった副作用があった。
**その要素を参照する群合計制約が書けなくなるため(``solve()`` が
``KeyError`` になる)、停止した1要素を守るために同じ群の他の要素の矛盾検出が
まとめて消えていた。** さらに精密モードの開き直しは、広げた範囲がそのまま
矛盾検出の定義域になるため、**同じデータに対して標準モードは unsat、
精密モードは sat** という逆転を起こしていた(`docs/group_total_masking_design.md`)。

→ **修正: 確定に使う範囲と、矛盾を見つけるための範囲を分けて持つ。**

- 停止した要素も**必ず変数として登録する**(単位・粒度が混在している場合を除く。
  混在した証拠からは意味のある範囲が作れないので、従来どおり登録しない)
- 矛盾検出には ``detection_range``(読み取り値が支持する狭い範囲)を使う。
  精密モードで開き直しても ``detection_range`` は確定範囲のまま据え置く
- ``requires_confirmation=True`` は従来どおり立つので、**確定済みとして
  扱われることはない**(バグ②の修正はそのまま効いている)
- 棄権した証拠は、どの経路でも範囲に混ぜない(バグ①の修正はそのまま効いている)

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


def _reading_based_range(evidences: Sequence[AxisEvidence]) -> tuple[int, int] | None:
    """棄権していない読みが支持する、いちばん狭い範囲。

    積集合が空でなければ積集合を、空なら和集合を返す。読みが1つも無ければ
    ``None``。**棄権した証拠は必ず除く**(番兵値 ``(0, 0)`` が下限を 0 まで
    引き下げるのがバグ①の実害だった)。

    これは「確定に使う範囲」ではなく、**矛盾を見つけるための範囲**である。
    積集合を採るので、実測校正を通っていない読みや弱い軸の読みも範囲を
    狭める側に働く。v8 3-2節は弱い軸に「ハードな解を排除する権限」を
    与えない方針だが、ここで起きうるのは **unsat 側への倒れ込み**、つまり
    「人が確認する」方向にしか外れない。弱い軸が強い軸とまったく重ならない
    場合は積集合が空になるので、その場合は和集合に落として拒否権を与えない。
    """
    speaking = [item for item in evidences if item.status != "abstained"]
    if not speaking:
        return None
    lower = max(item.count_range[0] for item in speaking)
    upper = min(item.count_range[1] for item in speaking)
    if lower <= upper:
        return (lower, upper)
    return (
        min(item.count_range[0] for item in speaking),
        max(item.count_range[1] for item in speaking),
    )


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
      開き直し ``requires_confirmation=True`` を立てる。** ただし開き直すのは
      確定に使う範囲だけで、``detection_range``(矛盾を見つけるための範囲)は
      ``confirmed_range`` のまま据え置く
    - 階層3(``requires_review``): ``FIREWALL_UNCONFIRMED_AXIS`` で
      ``requires_confirmation=True`` を立てて登録する。**レンジ幅が0でも
      確定済みとして扱われない。** 強い軸が1つも無く ``confirmed_range`` が
      ``None`` の場合も、読み取り値が支持する狭い範囲
      (:func:`_reading_based_range`)で**変数として登録する**

    変数を作らないのは次の2つだけで、どちらも「意味のある範囲が存在しない」場合:

    - 単位・粒度が混在している(違う物差しの数字は重ねられない。v8 3-2節)
    - 棄権していない証拠が1つも無い

    :param allow_provisional_domain: 強い軸が1つも無い階層3の要素について、
        棄権していない証拠のレンジの**和集合**を確定用の定義域にするか。
        **既定は False**(v8 10章14項。既定では読み取り値が支持する狭い範囲を
        使う)。True にすると ``docs/killer_question_report.md`` 3節の絞り込みが
        再現するが、校正されていない読み取りが定義域を決めることになる。
        **どちらの場合も ``detection_range`` は狭い範囲のままで、矛盾検出は
        緩まない。** また ``requires_confirmation=True`` が立つため、
        確定済みとして扱われることもない。

    戻り値の ``BridgeResult`` で、登録されたか・確認待ちかが分かる。
    """
    if not evidences:
        raise ValueError(f"'{target}' の証拠が空です")

    if decision.escalation is not None and decision.escalation.failure_type == "unit_mismatch":
        # **単位・粒度が混ざった証拠からは、意味のある範囲を作れない。**
        # 積集合も和集合も「違う物差しの数字を重ねた値」にしかならず、それを
        # ハード制約にするのがバグ①の実害そのものだった(実測 lower=0 upper=246)。
        # 入力の取り違えは人が直すべきなので、変数を作らず階層3へ落とす。
        return BridgeResult(
            target=target,
            registered=False,
            skipped_reason=(
                "単位・粒度が混在しているため、範囲を作らず階層3(要確認)へ"
                "フォールバックした(v8 3-2節)"
            ),
        )

    reading_range = _reading_based_range(evidences)

    if decision.action == "requires_review":
        requires_confirmation = True
        axis = FIREWALL_UNCONFIRMED_AXIS
        if decision.confirmed_range is not None:
            lower, upper = decision.confirmed_range
            detection_range = decision.confirmed_range
        elif reading_range is None:
            return BridgeResult(
                target=target,
                registered=False,
                skipped_reason=(
                    "棄権していない証拠が1つも無いため、範囲を作らず"
                    "階層3(要確認)へフォールバックした"
                ),
            )
        else:
            # **2026-09-22 変更。以前はここで変数を作らず登録を見送っていた。**
            # 安全側の判断だったが、この要素を参照する群合計制約が書けなくなり、
            # **停止した1要素を守るために同じ群の他の要素の矛盾検出が
            # まとめて消えていた**(`docs/group_total_masking_design.md` 2節)。
            # 変数は作るが、矛盾検出に使うのは読み取り値が支持する狭い範囲で、
            # ``requires_confirmation=True`` なので確定済みとしては扱われない。
            detection_range = reading_range
            if allow_provisional_domain:
                speaking = [e for e in evidences if e.status != "abstained"]
                lower = min(e.count_range[0] for e in speaking)
                upper = max(e.count_range[1] for e in speaking)
                axis = speaking[0].axis_id
            else:
                lower, upper = reading_range
    elif decision.action == "auto_confirm":
        if decision.confirmed_range is None:
            raise ValueError(
                f"'{target}': action={decision.action} なのに confirmed_range が None"
            )
        lower, upper = decision.confirmed_range
        detection_range = decision.confirmed_range
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
        detection_range = decision.confirmed_range
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
            # **開き直すのは「人に質問できるようにするため」であって、
            # 矛盾検出を緩めるためではない。** 広げた範囲をそのまま矛盾検出に
            # 使うと、広がった幅が群合計制約の中で他の要素の誤りを吸収し、
            # **同じデータに対して精密モードのほうが誤りを見逃す**という逆転が
            # 起きていた(2026-09-22 実測。標準モードは unsat、精密モードは sat)。
            # detection_range は確定範囲のまま据え置く。
            # 軸名は開き直した読み取りの軸のまま残す。4-3節のデータ源別
            # 誤り率による同点崩しが、この変数にも効くようにするため。
            axis = speaking[0].axis_id
        else:
            lower, upper = decision.confirmed_range

    # 値の出どころの軸を、確信度階層のラベルとは別に渡す。
    # ``axis`` には firewall_* のラベルが入るため、これが無いと
    # v8 4-3節ルール2(同じスコアなら誤り率の低いデータ源を優先する)が
    # **実運用の経路では一度も発火しない**
    # (2026-09-21 実測。`docs/trial789_reproduction_report.md` 4節)。
    #
    # **留保: 証拠が複数の軸から来ている場合、先頭の軸を採っている。** 上の
    # 精密モードの分岐が既に ``speaking[0].axis_id`` を使っているのと同じ規約に
    # 合わせた。誤り率がいちばん低い軸を選ぶほうが 4-3節ルール2の意図には近いが、
    # bridge は誤り率表を持っていないため、ここでは決められない。
    speaking_axes = [e.axis_id for e in evidences if e.status != "abstained"]
    solver.add_variable(
        target,
        lower,
        upper,
        axis=axis,
        source_axis=speaking_axes[0] if speaking_axes else "",
        requires_confirmation=requires_confirmation,
        detection_range=detection_range,
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
