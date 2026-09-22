"""整合性軸: Z3 による制約充足エンジン。

`6軸_実装詳細設計書.md` 軸4(整合性軸)の「OR-Tools / Z3 の使い方」と、
`ClaudeCode指示_段階B実装検証.md` に対応します。

------------------------------------------------------------------------------
なぜ Z3 か(OR-Tools との比較)
------------------------------------------------------------------------------
OR-Tools(CP-SAT)・Z3-solver はどちらも PyPI から問題なくインストールでき、
このリポジトリの依存関係(numpy 等)とも衝突しませんでした(段階Bステップ0で確認)。

この整合性軸に必要な制約はすべて整数の線形関係式(範囲・等式・不等式)で、
どちらのソルバーでも表現できます。採否を分けたのは、指示書が要求している

    「解が存在しなければ、どの制約同士が衝突しているかを特定して返す」

という要件です。Z3 は ``Solver.assert_and_track(expr, name)`` で制約に名前を
付けておくと、``unsat_core()`` が「矛盾の原因になった制約の名前の集合」を
直接返します。OR-Tools の CP-SAT にも assumption 変数を介して同等のことは
できますが、そのためには制約 1 つずつを reify する配線が別途必要で、
「宣言した制約にそのまま名前が付く」Z3 の方が実装・可読性の両面で単純でした。
そのため今回は **Z3 を採用**します。

------------------------------------------------------------------------------
設計の要点
------------------------------------------------------------------------------
1. 変数は `axes/image_axis/grounding_dino_adapter.py` の
   ``SymbolCountReading``(レンジ + 根拠 + 確信度)をそのまま受け取って宣言する
   (``add_variable_from_reading``)。単独の下限・上限を直接指定することもできる
   (``add_variable``)。
2. 制約は ``add_constraint`` で z3 の式を直接宣言するか、
   ``add_relation`` で「変数Aと変数Bの関係(==, <=, >= など)」を宣言する。
   どちらも v8 の「要素間の関係式」「レンジに基づく制約」の両方を表現できる。
3. v8 の「強い軸のみをハードな制約に使い、弱い軸は参考情報として別枠に置く」
   (3-2 節)を、変数登録時の ``strength`` 引数で反映する。
   弱い軸の読み取りは ``add_advisory_reading`` で登録し、**z3 の変数にはならない**
   (ハードな解を排除する権限を持たせないことを、型システムのレベルで保証する)。
4. ``solve()`` は次のいずれかを返す。
   - 解が存在する場合: 各変数について、制約を通した後の**絞り込まれたレンジ**
     (= v8 の「多軸レンジ収束」で言う積集合)。加えて、弱い軸の読み取りとの
     整合・不整合を参考情報(``AdvisoryNote``)として添える
   - 解が存在しない場合: 矛盾の原因になった制約名の集合(unsat core)
     (= v8 の「積集合が空集合になった場合の軸間矛盾」)
5. 確信度ステータスが ``abstained`` の読み取りは、レンジを主張せず棄権する
   という v8 の方針どおり、制約を追加しない(``add_variable_from_reading`` が
   自動的にスキップし、その旨を ``AbstainedReading`` として記録する)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence, Union

import z3

Strength = Literal["strong", "weak"]

#: 統合されたレンジ (下限, 上限)。両端を含む閉区間として扱う。
IntRange = tuple[int, int]

_RELATION_OPS: dict[str, Callable[[z3.ArithRef, z3.ArithRef], z3.BoolRef]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}

#: add_relation の rhs / lhs に渡せる値: 変数名、整数リテラル、
#: あるいは変数辞書から z3 の式を組み立てる関数。
Operand = Union[str, int, Callable[[dict], z3.ArithRef]]


class _AccessRecordingVariables(dict):
    """``referenced_variables()`` 用: ``__getitem__`` されたキーを記録する辞書。"""

    def __init__(self, data: dict[str, z3.ArithRef]) -> None:
        super().__init__(data)
        self.accessed: set[str] = set()

    def __getitem__(self, key: str) -> z3.ArithRef:
        self.accessed.add(key)
        return super().__getitem__(key)


@dataclass(frozen=True)
class Variable:
    """ハードな制約に使う変数 1 つ(強い軸から登録されたもの)。"""

    name: str
    lower: int
    upper: int
    axis: str
    strength: Strength
    evidence: dict[str, object] = field(default_factory=dict)

    #: レンジが何を数えているか("count" / "m" / "m2" / "yen" 等)。
    #: 空文字は「宣言されていない」。単位の違うレンジは比較しない(v8 3-2節)。
    unit: str = ""

    #: レンジ幅が 0 でも「まだ人の確認を得ていない」ことを表すフラグ。
    #: v8 3-3節の階層3(要確認)の要素に立てる。**幅0を「確定済み」と同一視
    #: すると、階層3の要素がキラークエスチョンの対象から外れ、確定済み金額に
    #: 計上されてしまう**(2026-09-21 に実測したバグ。
    #: `docs/top_priority_unit_safety_defect.md` 3-4節)。
    requires_confirmation: bool = False

    #: この値の**出どころの軸**("image" / "text" / "history" 等)。
    #:
    #: ``axis`` とは役割が違う。``killer_question/firewall_bridge.py`` を通すと
    #: ``axis`` には確信度階層のラベル(``firewall_provisional`` 等)が入るため、
    #: **元の軸が分からなくなる。** その結果、v8 4-3節ルール2の
    #: 「同じスコアなら誤り率の低いデータ源を優先する」(トライアル9)が
    #: **実運用の経路では一度も発火しない**という状態になっていた
    #: (2026-09-21 実測。`docs/trial789_reproduction_report.md` 4節)。
    #: 空文字のときは ``axis`` を出どころとして扱う。
    source_axis: str = ""

    #: **矛盾を見つけるための範囲**(省略すると ``lower``/``upper`` と同じ)。
    #:
    #: ``lower``/``upper`` は「確定に使う範囲」で、人に質問できるようにする
    #: ために広げられることがある(``firewall_bridge`` の精密モード)。
    #: 広げた範囲をそのまま矛盾検出に使うと、**広がった幅が群合計制約の中で
    #: 他の要素の誤りを吸収してしまい、unsat が sat に変わる**
    #: (2026-09-22 実測。`docs/group_total_masking_design.md` 2節)。
    #: そこで矛盾検出には、読み取り値に基づく狭いほうの範囲を使う。
    #: ``solve(use_detection_ranges=True)`` がこちらを参照する。
    detection_lower: int | None = None
    detection_upper: int | None = None

    @property
    def error_rate_axis(self) -> str:
        """データ源別誤り率を引くときに使う軸名。"""
        return self.source_axis or self.axis

    @property
    def detection_range(self) -> IntRange:
        """矛盾検出に使う範囲。宣言されていなければ確定用の範囲と同じ。"""
        lower = self.lower if self.detection_lower is None else self.detection_lower
        upper = self.upper if self.detection_upper is None else self.detection_upper
        return (lower, upper)


@dataclass(frozen=True)
class AbstainedReading:
    """棄権した読み取り(status == "abstained")。制約には反映されない。"""

    name: str
    axis: str


@dataclass(frozen=True)
class AdvisoryReading:
    """弱い軸からの参考情報。z3 の変数にはならず、ハードな解には影響しない。"""

    target: str
    axis: str
    lower: int
    upper: int
    evidence: dict[str, object] = field(default_factory=dict)
    unit: str = ""


@dataclass(frozen=True)
class Constraint:
    """宣言的な制約 1 つ。範囲制約(add_variable が内部生成)・関係制約の両方を含む。"""

    name: str
    build: Callable[[dict[str, z3.ArithRef]], z3.BoolRef]
    description: str = ""


@dataclass(frozen=True)
class VariableSolution:
    """解が存在した場合の、変数 1 つ分の結果。"""

    name: str
    axis: str
    original_range: IntRange
    solved_range: IntRange

    @property
    def was_tightened(self) -> bool:
        return self.solved_range != self.original_range


@dataclass(frozen=True)
class AdvisoryNote:
    """弱い軸の読み取りと、強い軸で確定した解との突き合わせ結果。"""

    target: str
    axis: str
    advisory_range: IntRange
    solved_range: IntRange | None
    agrees: bool | None  # 比較対象が無ければ None
    message: str


@dataclass(frozen=True)
class SolveResult:
    """``ConsistencySolver.solve()`` の結果。"""

    status: Literal["sat", "unsat"]
    variables: dict[str, VariableSolution]
    conflicting_constraints: tuple[str, ...] = ()
    advisories: tuple[AdvisoryNote, ...] = ()
    abstained: tuple[AbstainedReading, ...] = ()
    solve_seconds: float = 0.0

    @property
    def is_consistent(self) -> bool:
        return self.status == "sat"

    def describe(self) -> str:
        """人간が読める短い要約(ログ・報告書向け)。"""
        lines = [f"status={self.status} ({self.solve_seconds * 1000:.2f} ms)"]
        if self.status == "sat":
            for name, sol in self.variables.items():
                mark = " (絞り込み)" if sol.was_tightened else ""
                lines.append(
                    f"  {name}: {sol.original_range} -> {sol.solved_range}{mark}"
                )
            for note in self.advisories:
                lines.append(f"  [advisory] {note.message}")
        else:
            lines.append(f"  矛盾した制約: {', '.join(self.conflicting_constraints)}")
        for ab in self.abstained:
            lines.append(f"  [abstained] {ab.name} ({ab.axis}) は棄権")
        return "\n".join(lines)


class ConsistencySolver:
    """v8 設計の整合性軸を実装する内部・低レベルAPI。

    実運用入力から直接呼び出してはならない。実運用は必ず
    ``InferenceOrchestrator`` を入口にし、軸品質ファイアウォール通過後の
    証拠だけをこの層へ渡す。既存テスト・ベンチマーク互換のため公開名は維持する。
    """

    def __init__(self) -> None:
        self._variables: dict[str, Variable] = {}
        self._constraints: list[Constraint] = []
        self._advisories: list[AdvisoryReading] = []
        self._abstained: list[AbstainedReading] = []

    # -- 変数の登録 --------------------------------------------------------
    def add_variable(
        self,
        name: str,
        lower: int,
        upper: int,
        *,
        strength: Strength = "strong",
        axis: str = "",
        evidence: dict[str, object] | None = None,
        requires_confirmation: bool = False,
        unit: str = "",
        source_axis: str = "",
        detection_range: IntRange | None = None,
    ) -> None:
        """下限・上限を直接指定して、ハードな制約に使う変数を登録する。

        ``requires_confirmation=True`` は「レンジ幅が 0 でも、まだ人の確認を
        得ていない」ことを表す(v8 3-3節の階層3)。

        ``source_axis`` は値の出どころの軸。``axis`` に確信度階層のラベルを
        入れる呼び出し(``firewall_bridge``)でも、データ源別誤り率を引けるように
        するためのもの。省略すると ``axis`` を出どころとして扱う。

        ``detection_range`` は**矛盾を見つけるための範囲**。``lower``/``upper``
        を人への質問のために広げる場合に、読み取り値に基づく狭いほうの範囲を
        別に渡す(:attr:`Variable.detection_lower` を参照)。確定用の範囲より
        広くはできない。
        """
        if name in self._variables:
            raise ValueError(f"変数 '{name}' は既に登録されています")
        if lower > upper:
            raise ValueError(f"'{name}': lower({lower}) > upper({upper})")
        detection_lower = detection_upper = None
        if detection_range is not None:
            detection_lower, detection_upper = detection_range
            if detection_lower > detection_upper:
                raise ValueError(
                    f"'{name}': detection_range の下限({detection_lower})が"
                    f"上限({detection_upper})を超えています"
                )
            if detection_lower < lower or detection_upper > upper:
                # 確定用より広い検出用の範囲は、「狭いほうで矛盾を見る」という
                # この仕組みの前提を壊す(広い側で吸収が起きる)。
                raise ValueError(
                    f"'{name}': detection_range ({detection_lower}, {detection_upper}) が"
                    f"確定用の範囲 ({lower}, {upper}) からはみ出しています"
                )
        self._variables[name] = Variable(
            name, lower, upper, axis, strength, dict(evidence or {}),
            unit=unit, requires_confirmation=requires_confirmation,
            source_axis=source_axis,
            detection_lower=detection_lower, detection_upper=detection_upper,
        )

    def add_variable_from_reading(
        self,
        name: str,
        reading: "SymbolCountReading",  # noqa: F821 - 型ヒントは文字列で遅延評価
        *,
        axis: str,
        strength: Strength = "strong",
    ) -> None:
        """``SymbolCountReading``(レンジ + 根拠 + 確信度)から変数を登録する。

        ``reading.status == "abstained"`` の場合は、v8 の「分布外の軸はレンジを
        主張せず棄権する」に従い、**変数を追加せず** ``AbstainedReading`` として
        記録するだけにする。
        """
        if reading.status == "abstained":
            self._abstained.append(AbstainedReading(name, axis))
            return
        # 呼び出し側が誤って弱い軸や low_confidence をこのAPIへ渡しても、
        # ハード制約へ昇格させない。既存の confident/strong 呼び出しは不変。
        if strength == "weak" or reading.status != "confident":
            self.add_advisory_reading(name, reading, axis=axis)
            return
        lower, upper = reading.count_range
        self.add_variable(
            name,
            lower,
            upper,
            strength=strength,
            axis=axis,
            evidence={"status": reading.status, "category": reading.category, **reading.evidence},
        )

    def add_advisory_reading(
        self,
        target: str,
        reading: "SymbolCountReading",  # noqa: F821
        *,
        axis: str,
        unit: str = "",
    ) -> None:
        """弱い軸の読み取りを、参考情報として登録する。

        ``target`` は比較対象にする強い軸側の変数名。ここで登録した内容は
        z3 の変数にはならないため、**ハードな解を排除する権限を持たない**
        (v8 3-2 節)。``status == "abstained"`` の読み取りは、棄権として記録
        するのみで参考情報にもしない。
        """
        if reading.status == "abstained":
            self._abstained.append(AbstainedReading(target, axis))
            return
        lower, upper = reading.count_range
        self._advisories.append(
            AdvisoryReading(
                target,
                axis,
                lower,
                upper,
                evidence={"status": reading.status, "category": reading.category, **reading.evidence},
                unit=unit,
            )
        )

    def has_variable(self, name: str) -> bool:
        """変数が登録済みか(棄権により未登録の可能性があるため、呼び出し側の
        ガード用に公開している)。"""
        return name in self._variables

    def variable_names(self) -> frozenset[str]:
        """登録済みの(強い軸の)変数名の集合。

        ``killer_question`` パッケージが依存関係グラフを組み立てる際に使う。
        依存関係という"事実"はここ(整合性軸)に既に存在しているため、
        killer_question 側で制約を再宣言しない設計にするための公開 API。
        """
        return frozenset(self._variables)

    def variable_axis(self, name: str) -> str:
        """変数が属する軸の名前(登録時の ``axis`` 引数)。"""
        return self._variables[name].axis

    def variable_range(self, name: str) -> IntRange:
        """確定に使う範囲(登録時の ``lower`` / ``upper``)。"""
        variable = self._variables[name]
        return (variable.lower, variable.upper)

    def variable_detection_range(self, name: str) -> IntRange:
        """矛盾を見つけるための範囲(:attr:`Variable.detection_range`)。"""
        return self._variables[name].detection_range

    def variable_evidence(self, name: str) -> dict[str, object]:
        """変数に添えられた根拠(登録時の ``evidence``)の複製。

        ``firewall_bridge`` が確信度階層(``firewall_tier`` / ``firewall_action``)
        をここに入れるため、下流が階層を読み直せる。複製を返すので、
        受け取った側が書き換えても登録内容は壊れない。"""
        return dict(self._variables[name].evidence)

    def variable_error_rate_axis(self, name: str) -> str:
        """データ源別誤り率を引くときに使う軸名(``source_axis`` 優先)。

        ``firewall_bridge`` を通すと ``axis`` は確信度階層のラベルになるので、
        誤り率の参照にはこちらを使う(v8 4-3節ルール2)。
        """
        return self._variables[name].error_rate_axis

    def requires_confirmation(self, name: str) -> bool:
        """その変数が、レンジ幅に関わらず人の確認を要するか(階層3かどうか)。"""
        variable = self._variables.get(name)
        return bool(variable and variable.requires_confirmation)

    def names_requiring_confirmation(self) -> frozenset[str]:
        """人の確認を要する変数の集合。"""
        return frozenset(
            name for name, v in self._variables.items() if v.requires_confirmation
        )

    def mark_confirmed(self, name: str) -> None:
        """人の確認を得た変数のフラグを降ろす。

        ``killer_question`` エンジンが回答を反映したときに呼ぶ。これを忘れると
        その変数が永久に未確定のままになり、質問ループが終わらない。
        """
        variable = self._variables.get(name)
        if variable is None or not variable.requires_confirmation:
            return
        self._variables[name] = Variable(
            variable.name, variable.lower, variable.upper, variable.axis,
            variable.strength, dict(variable.evidence),
            unit=variable.unit, requires_confirmation=False,
            source_axis=variable.source_axis,
            detection_lower=variable.detection_lower,
            detection_upper=variable.detection_upper,
        )

    def constraint_names(self) -> tuple[str, ...]:
        """登録済みの制約名の一覧(``add_relation`` / ``add_constraint`` で
        宣言したもの。``solve()`` が内部生成する範囲制約は含まない)。"""
        return tuple(c.name for c in self._constraints)

    def referenced_variables(self, constraint_name: str) -> frozenset[str]:
        """指定した制約が参照している変数名の集合を調べる。

        ``Constraint.build`` は任意の Python 関数なので、静的解析はしない。
        代わりに、変数辞書のアクセスを記録するダミー辞書を渡して実際に
        ``build`` を1回実行し、``__getitem__`` されたキーを記録する
        (式そのものは使い捨てる)。``add_relation`` の文字列オペランドも、
        素の ``add_constraint`` に渡した関数も、辞書アクセス(``v["name"]``)
        でしか変数を参照できないため、この方法で網羅できる。
        """
        constraint = self._constraint_by_name(constraint_name)
        recorder = _AccessRecordingVariables(
            {name: z3.Int(name) for name in self._variables}
        )
        constraint.build(recorder)
        return frozenset(recorder.accessed)

    def remove_constraint(self, name: str) -> None:
        """登録済みの制約を1つ外す。

        「その制約が無かったら解はどうなるか」を調べるために使う
        (`arbitration/group_total.py` が、群合計が実際に何を絞り込んだのかを
        判定するのに使っている)。**元の solver を壊さないよう、
        ``clone()`` した複製に対して呼ぶこと。**
        """
        self._constraint_by_name(name)  # 未登録なら KeyError
        self._constraints = [c for c in self._constraints if c.name != name]

    def _constraint_by_name(self, name: str) -> Constraint:
        for constraint in self._constraints:
            if constraint.name == name:
                return constraint
        raise KeyError(f"制約 '{name}' は登録されていません")

    def clone(self) -> "ConsistencySolver":
        """変数・制約の登録内容をコピーした、独立した新しいインスタンスを返す。

        ``killer_question`` エンジンが「この値を仮に確定したら」という
        仮説を試すために使う。``Variable`` / ``Constraint`` 等はいずれも
        frozen dataclass なので、コンテナ(dict/list)だけを複製すれば安全。
        """
        clone = ConsistencySolver()
        clone._variables = dict(self._variables)
        clone._constraints = list(self._constraints)
        clone._advisories = list(self._advisories)
        clone._abstained = list(self._abstained)
        return clone

    # -- 制約の宣言 ----------------------------------------------------------
    def add_constraint(
        self,
        name: str,
        build: Callable[[dict[str, z3.ArithRef]], z3.BoolRef],
        *,
        description: str = "",
    ) -> None:
        """z3 の式を直接組み立てる関数で、任意の制約を宣言する。"""
        self._constraints.append(Constraint(name, build, description))

    def add_relation(
        self,
        name: str,
        lhs: Operand,
        op: Literal["==", "!=", "<=", ">=", "<", ">"],
        rhs: Operand,
        *,
        description: str = "",
    ) -> None:
        """要素間の関係式を宣言する簡易ヘルパー。

        例: ``door_count = room_count + 1`` は次のように書ける。

            solver.add_relation(
                "door_ge_room_plus_1", "door_count", "==",
                lambda v: v["room_count"] + 1,
            )

        ``lhs`` / ``rhs`` には、変数名(文字列)・整数リテラル・
        変数辞書を受け取って z3 の式を返す関数、のいずれかを渡せる。
        """
        if op not in _RELATION_OPS:
            raise ValueError(f"未対応の演算子です: {op}")

        def build(v: dict[str, z3.ArithRef]) -> z3.BoolRef:
            left = self._resolve_operand(lhs, v)
            right = self._resolve_operand(rhs, v)
            return _RELATION_OPS[op](left, right)

        self.add_constraint(name, build, description=description)

    @staticmethod
    def _resolve_operand(operand: Operand, variables: dict[str, z3.ArithRef]):
        if isinstance(operand, str):
            return variables[operand]
        if callable(operand):
            return operand(variables)
        return operand

    # -- 求解 ----------------------------------------------------------------
    def solve(self, *, use_detection_ranges: bool = False) -> SolveResult:
        """強い軸の変数・制約だけでハードに解き、弱い軸は参考情報として突き合わせる。

        ``use_detection_ranges=True`` にすると、各変数の定義域に
        :attr:`Variable.detection_range`(矛盾を見つけるための狭い範囲)を使う。
        **確定に使う範囲と矛盾を見つけるための範囲を分けて持つ**ためのもので、
        人への質問のために広げた範囲が、群合計制約の中で他の要素の誤りを
        吸収してしまうのを防ぐ(`docs/group_total_masking_design.md`)。
        """
        # 短いZ3呼び出しはWindowsのmonotonic時計では0 msに丸められることがある。
        # 効果測定に使える高分解能時計で計測する。
        start = time.perf_counter()

        strong_vars = {n: v for n, v in self._variables.items() if v.strength == "strong"}

        # **毎回まっさらな Z3 コンテキストで解く。**
        #
        # z3.Int()/z3.Solver() を引数なしで呼ぶと、プロセス全体で共有される
        # グローバルコンテキストを使う。共有コンテキストは呼び出しをまたいで
        # 内部状態を持ち越すため、**まったく同じ制約集合を解いても
        # unsat_core() が呼ぶたびに変わる**。矛盾の核が変われば、
        # 「人に確認してもらう要素」が変わり、監査の母集団も費用も変わる。
        # つまり効果測定の数値が再現しなくなる(2026-09-21 発見。実測では
        # 同一入力15回で核が5種類に割れ、コンテキストを毎回新しくすると
        # 15回とも同一になった)。
        #
        # どの核も「矛盾の説明」としては正しいので、これは正しさのバグでは
        # なく**再現性のバグ**である。だが本リポジトリは測った数値を根拠に
        # 設計を決めているので、再現しない測定は使えない。
        ctx = z3.Context()
        z3vars = {name: z3.Int(name, ctx) for name in strong_vars}

        domains = {
            name: (var.detection_range if use_detection_ranges else (var.lower, var.upper))
            for name, var in strong_vars.items()
        }

        items: list[tuple[str, z3.BoolRef]] = []
        for name in strong_vars:
            low, high = domains[name]
            items.append(
                (f"range::{name}", z3.And(z3vars[name] >= low, z3vars[name] <= high))
            )
        for constraint in self._constraints:
            items.append((constraint.name, constraint.build(z3vars)))

        core_solver = z3.Solver(ctx=ctx)
        for name, expr in items:
            core_solver.assert_and_track(expr, name)
        status = core_solver.check()

        if status == z3.unsat:
            elapsed = time.perf_counter() - start
            conflicting = tuple(str(c) for c in core_solver.unsat_core())
            variables = {
                name: VariableSolution(name, var.axis, domains[name], domains[name])
                for name, var in strong_vars.items()
            }
            return SolveResult(
                status="unsat",
                variables=variables,
                conflicting_constraints=conflicting,
                abstained=tuple(self._abstained),
                solve_seconds=elapsed,
            )

        solved_ranges = {
            name: self._tighten(items, z3vars[name]) for name in strong_vars
        }
        variables = {
            name: VariableSolution(
                name, var.axis, domains[name], solved_ranges[name]
            )
            for name, var in strong_vars.items()
        }
        advisories = tuple(self._build_advisories(variables))
        elapsed = time.perf_counter() - start
        return SolveResult(
            status="sat",
            variables=variables,
            advisories=advisories,
            abstained=tuple(self._abstained),
            solve_seconds=elapsed,
        )

    @staticmethod
    def _tighten(items: Sequence[tuple[str, z3.BoolRef]], var: z3.ArithRef) -> IntRange:
        """制約を通した後の、この変数の実現可能なレンジ(下限・上限)を求める。

        これが v8 の「多軸レンジ収束」の実体で、``[reading.lower, reading.upper]``
        (元のレンジ)と全ハード制約との**積集合**を、区間として計算している。

        NOTE: ``core_solver`` の assertions() は ``assert_and_track`` の内部実装上
        (Implies(tracker, expr) の形で保持され、tracker 自体は別枠で真とされる)
        再利用できない。そのため生の式(``items``)から毎回 Optimize を組み直す。
        """
        lower = ConsistencySolver._optimize(items, var, minimize=True)
        upper = ConsistencySolver._optimize(items, var, minimize=False)
        return (lower, upper)

    @staticmethod
    def _optimize(items: Sequence[tuple[str, z3.BoolRef]], var: z3.ArithRef, *, minimize: bool) -> int:
        # solve() が作ったコンテキストをそのまま使う(式はそれに属している)。
        opt = z3.Optimize(ctx=var.ctx)
        for _, expr in items:
            opt.add(expr)
        (opt.minimize if minimize else opt.maximize)(var)
        opt.check()
        model = opt.model()
        return model.eval(var).as_long()

    def _build_advisories(self, variables: dict[str, VariableSolution]) -> list[AdvisoryNote]:
        notes: list[AdvisoryNote] = []
        for advisory in self._advisories:
            match = variables.get(advisory.target)
            advisory_range = (advisory.lower, advisory.upper)
            declared = self._variables.get(advisory.target)
            match_unit = declared.unit if declared else ""
            if match is None:
                notes.append(
                    AdvisoryNote(
                        target=advisory.target,
                        axis=advisory.axis,
                        advisory_range=advisory_range,
                        solved_range=None,
                        agrees=None,
                        message=(
                            f"{advisory.axis} の参考情報: '{advisory.target}' は "
                            f"{advisory_range} だが、対応する強い軸の変数が無いため比較不可"
                        ),
                    )
                )
                continue
            lo, hi = match.solved_range

            # v8 3-2節: 単位が違うレンジは比較しない。数値が重なることは
            # 支持の証拠にならない。以前はここで単位を見ずに「整合」と
            # 人に報告していた(`docs/top_priority_unit_safety_defect.md` 3-2節)。
            if advisory.unit and match_unit and advisory.unit != match_unit:
                notes.append(
                    AdvisoryNote(
                        target=advisory.target,
                        axis=advisory.axis,
                        advisory_range=advisory_range,
                        solved_range=match.solved_range,
                        agrees=None,
                        message=(
                            f"{advisory.axis} の参考情報: '{advisory.target}' は "
                            f"{advisory_range}[{advisory.unit}] だが、強い軸の解は "
                            f"{match.solved_range}[{match_unit}] で"
                            "**単位が違うため比較不可**(数値が重なっても支持にはならない)"
                        ),
                    )
                )
                continue

            overlaps = not (hi < advisory.lower or lo > advisory.upper)
            if overlaps:
                message = (
                    f"{advisory.axis} の参考情報: '{advisory.target}' は "
                    f"{advisory_range} で、強い軸の解 {match.solved_range} と整合"
                )
            else:
                message = (
                    f"{advisory.axis} の参考情報: '{advisory.target}' は "
                    f"{advisory_range} だが、強い軸の解 {match.solved_range} と重ならない"
                    "(参考情報のため解の排除はしない。要注意)"
                )
            notes.append(
                AdvisoryNote(
                    target=advisory.target,
                    axis=advisory.axis,
                    advisory_range=advisory_range,
                    solved_range=match.solved_range,
                    agrees=overlaps,
                    message=message,
                )
            )
        return notes
