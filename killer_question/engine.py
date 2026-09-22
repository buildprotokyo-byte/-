"""キラークエスチョンエンジン: 依存関係上、最も影響力の大きい要素を選んで質問する。

`自動積算AI_統合設計書v8.md` 4章(トライアル7・8・9で確立)の手順をそのまま
実装する。

役割分担
--------
依存関係の"事実"(要素間の関係式)は ``arbitration/consistency_solver.py`` の
変数・制約宣言に存在する。このモジュールは ``killer_question/dependency_graph.py``
経由でそれを読み取るだけで、**質問選定の"戦略"だけ**を担当する
(``dependency_graph.py`` の冒頭を参照)。

影響度スコアの算出(4-2節)
----------------------------
1. 確信度が低い(候補が複数残っている)要素それぞれについて、候補値を
   1つずつ仮確定してみる(``ConsistencySolver.clone()`` で複製した上で試すため、
   本体の状態は変えない)
2. 仮確定するたびに、依存関係でつながっている下流の要素の候補数(レンジの幅)が
   それぞれいくつ減るかを計算し、下流要素すべてについて合計する
3. その合計を、候補値ごとの結果で平均する(どの値が正解かは分からないため、
   候補値それぞれが正解である可能性を均等に見て平均を取る)。これを
   ``reduction_score`` と呼ぶ
4. ``reduction_score`` に、要素ごとのインパクトスコア(数量×単価。単価が
   未登録なら1.0で重み付けなし)を掛けたものを最終的な ``score`` とする
   (``score = reduction_score × impact``)。単価を一切登録しなければ
   ``impact`` は常に1.0になるため、既存の(価格を考慮しない)動作と完全に
   一致する

運用上のルール(4-3節)
------------------------
- スコアが同点の場合は、誤り率が低いデータ源(軸)から取得できる要素を優先する
  (トライアル9)。``axis_error_rates`` にデータ源ごとの誤り率を渡すことで有効になる
- 標準モードでは、スコアが0以下(これ以上質問しても候補が減らない)に
  なった時点で打ち切る。精密・概算モードの挙動は ``PrecisionMode`` を参照

精度モード
----------
``mode`` に ``PrecisionMode`` を渡すことで、「どこまで質問するか」を
切り替えられる。詳細は ``killer_question/precision_mode.py`` および
``docs/killer_question_precision_report.md`` を参照。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from arbitration.consistency_solver import ConsistencySolver, SolveResult
from killer_question.dependency_graph import DependencyGraph, build_dependency_graph
from killer_question.precision_mode import DEFAULT_TARGET_COVERAGE, PrecisionMode

#: 仮確定・回答の確定に使う制約名の接頭辞(名前の衝突を避けるための予約語)。
_HYPOTHESIS_PREFIX = "__killer_question_hypothesis__"
_ANSWER_PREFIX = "__killer_question_answer__"

#: 未知の軸(誤り率が登録されていない)に割り当てる、最も信頼しない扱いの誤り率。
_UNKNOWN_AXIS_ERROR_RATE = 1.0

#: 単価が未登録の要素に割り当てるインパクトスコア(重み付けなしと等価)。
_DEFAULT_IMPACT = 1.0

#: 1つの要素について列挙してよい候補値の上限。
#:
#: スコア計算は候補値1つごとに ``solver.clone().solve()`` を1回呼ぶため、
#: 候補数にそのまま比例して時間がかかる。離散カウントでは候補が数個〜数十個
#: なので問題にならないが、**固定小数点で表した連続量では容易に数千〜数万に
#: なる**(実測: 配管延長 12.0〜13.0m = 1001候補で4.8秒。床面積 100㎡ を
#: cm² で ±1% 見ると 20001候補)。
#:
#: 上限を超えた要素は「キラークエスチョンでは扱わない」として扱う。
#: **未確定のまま残す**(``remaining_unresolved`` に入れる)のが要点で、
#: 列挙できないことを理由に確定済みへ回してはならない。これは v8 9.5節
#: 「設計への反映2」の「独立した合算にキラークエスチョンを無理に適用しない」
#: と同じ結論である。
_MAX_CANDIDATE_ENUMERATION = 256

#: 反復選定ループの安全上限。1回の反復で必ず1つの変数が解決するか、ループが
#: 終了するため理論上は不要だが、想定外のバグで無限ループになることを防ぐ
#: 最終防波堤として置いている。
_MAX_ITERATIONS_PER_VARIABLE = 4


@dataclass(frozen=True)
class CandidateScore:
    """1つの未確定要素について計算した、キラークエスチョンスコア。

    ``score`` は ``reduction_score * impact``。``unit_prices`` を一切
    渡さない場合、``impact`` は常に1.0になるため ``score == reduction_score``
    となり、価格を考慮しない従来の挙動と完全に一致する。
    """

    variable: str
    score: float
    reduction_score: float
    impact: float
    candidate_values: tuple[int, ...]
    downstream: frozenset[str]
    #: 候補値が多すぎて列挙を諦めた場合 True。``candidate_values`` は空になる。
    #: このとき質問は作れないが、要素は未確定のまま残る。
    too_many_candidates: bool = False

    @property
    def is_askable(self) -> bool:
        """この要素を人への質問にできるか。

        候補値が無い要素を質問にすると、``run()`` の「回答は候補に含まれて
        いなければならない」という検査を必ず落ちるため、選定の対象から外す。
        """
        return bool(self.candidate_values)


@dataclass(frozen=True)
class Question:
    """次に人へ確認すべき質問1件。"""

    variable: str
    score: float
    candidate_values: tuple[int, ...]
    tie_broken_by_axis: bool = False
    mode: PrecisionMode = PrecisionMode.STANDARD
    selected_by: str = "reduction"  # "reduction" | "impact_fallback"


@dataclass(frozen=True)
class AnsweredQuestion:
    """実際に質問し、回答を得た記録。"""

    variable: str
    answer: int
    score_at_selection: float


@dataclass(frozen=True)
class SessionResult:
    """反復選定ループ全体の結果。"""

    answered: tuple[AnsweredQuestion, ...]
    remaining_unresolved: tuple[str, ...]
    final_solver: ConsistencySolver
    final_result: SolveResult
    stopped_reason: str  # "all_resolved" | "no_further_reduction" | "unsat"
    #                      | "coverage_reached" | "candidates_not_enumerable"
    mode: PrecisionMode = PrecisionMode.STANDARD
    final_coverage: float | None = None

    @property
    def question_count(self) -> int:
        return len(self.answered)


class KillerQuestionEngine:
    """v8 4章のキラークエスチョンエンジン本体。"""

    def __init__(
        self,
        solver: ConsistencySolver,
        *,
        axis_error_rates: dict[str, float] | None = None,
        unit_prices: dict[str, float] | None = None,
        mode: PrecisionMode = PrecisionMode.STANDARD,
        target_coverage: float = DEFAULT_TARGET_COVERAGE,
    ) -> None:
        if not 0.0 < target_coverage <= 1.0:
            raise ValueError(f"target_coverage は 0 より大きく1以下で指定してください: {target_coverage}")
        # 呼び出し側の solver は変更しない(内部で複製してから使う)。
        self._solver = solver.clone()
        self._axis_error_rates = dict(axis_error_rates or {})
        self._unit_prices = dict(unit_prices or {})
        self._mode = mode
        self._target_coverage = target_coverage
        self._answered: list[AnsweredQuestion] = []

    @property
    def solver(self) -> ConsistencySolver:
        """現在の(質問への回答を反映した)solverの状態。"""
        return self._solver

    @property
    def answered(self) -> tuple[AnsweredQuestion, ...]:
        return tuple(self._answered)

    @property
    def mode(self) -> PrecisionMode:
        return self._mode

    # -- 金額インパクト・カバレッジ ------------------------------------------------

    def _representative_quantity(self, variable: str, result: SolveResult) -> float:
        lower, upper = result.variables[variable].solved_range
        return (lower + upper) / 2.0

    def _impact(self, variable: str, result: SolveResult) -> float:
        """要素1つ分のインパクトスコア(数量×単価)。単価未登録なら1.0(重み付けなし)。"""
        price = self._unit_prices.get(variable)
        if price is None:
            return _DEFAULT_IMPACT
        return price * self._representative_quantity(variable, result)

    @property
    def has_any_price(self) -> bool:
        """1つでも単価が登録されている変数があるか。"""
        names = self._solver.variable_names()
        return any(name in names for name in self._unit_prices)

    def estimated_total_amount(self, result: SolveResult) -> float:
        """単価が登録されている要素だけを対象にした、見積もり金額合計の現時点の推定値。

        未確定の要素は、現在のレンジの中央値を暫定の数量として使う。したがって
        この値は、要素が確定していくにつれて動く「動く目標」であり、固定の
        分母ではない(``docs/killer_question_precision_report.md`` の設計上の
        注意点を参照)。
        """
        total = 0.0
        for name, price in self._unit_prices.items():
            solution = result.variables.get(name)
            if solution is None:
                continue
            total += price * self._representative_quantity(name, result)
        return total

    def confirmed_amount(self, result: SolveResult) -> float:
        """既に確定した要素だけの金額合計(単価が登録されているものに限る)。

        「確定した」の条件は **レンジ幅0かつ ``requires_confirmation`` が
        立っていないこと**。階層3(要確認)の要素は、値が1つに絞れていても
        人の確認を得ていないため計上しない(2026-09-21 修正。以前は
        人の確認が必要な金額が確定済みとして計上されていた)。
        """
        total = 0.0
        for name, price in self._unit_prices.items():
            solution = result.variables.get(name)
            if solution is None:
                continue
            if self._solver.requires_confirmation(name):
                continue
            lower, upper = solution.solved_range
            if lower == upper:
                total += price * lower
        return total

    def coverage(self, result: SolveResult) -> float:
        """累積確信度カバレッジ(確定済み金額 ÷ 見積もり全体の金額合計の推定値)。

        価格情報が一切無い、または合計が0の場合は、判定不能ではなく
        「これ以上カバレッジで語れることは無い」という扱いで1.0を返す
        (``has_any_price`` が False のときは、呼び出し側でこの値を
        使わない設計にしている。詳細は ``next_question`` を参照)。
        """
        total = self.estimated_total_amount(result)
        if total <= 0:
            return 1.0
        return self.confirmed_amount(result) / total

    # -- スコア計算 ------------------------------------------------------------

    def score_candidate(
        self, base_result: SolveResult, graph: DependencyGraph, variable: str
    ) -> CandidateScore:
        """1つの未確定要素について、v8 4-2節の手順でスコアを計算する。"""
        lower, upper = base_result.variables[variable].solved_range
        width = upper - lower + 1
        downstream = graph.connected_component(variable)
        if width > _MAX_CANDIDATE_ENUMERATION:
            # 列挙を諦める。スコア0・候補空で返すので、この要素は質問に
            # 選ばれず、未確定のまま ``remaining_unresolved`` に残る。
            return CandidateScore(
                variable, 0.0, 0.0, self._impact(variable, base_result),
                (), downstream, too_many_candidates=True,
            )
        candidates = tuple(range(lower, upper + 1))
        base_widths = {
            name: base_result.variables[name].solved_range[1]
            - base_result.variables[name].solved_range[0]
            for name in downstream
            if name in base_result.variables
        }
        impact = self._impact(variable, base_result)

        if not candidates or not base_widths:
            return CandidateScore(variable, 0.0, 0.0, impact, candidates, downstream)

        reductions: list[int] = []
        for value in candidates:
            hypothesis = self._solver.clone()
            hypothesis.add_relation(
                f"{_HYPOTHESIS_PREFIX}{variable}__{value}", variable, "==", value
            )
            hypothesis_result = hypothesis.solve()
            if not hypothesis_result.is_consistent:
                # この候補値は他の強い証拠と既に矛盾している。正解ではあり得ない
                # ため、削減量には寄与しない(0として扱う)。
                # 正直な制約: 候補の分母(len(candidates))からは除外していない。
                # 「まだ矛盾を知らない状態で、候補値それぞれが正解の可能性を
                # 均等に持つ」という v8 4-2 節の前提をそのまま踏襲したための
                # 選択であり、矛盾候補を除いた条件付き平均ではない
                # (詳細は docs/killer_question_report.md 5節)。
                reductions.append(0)
                continue
            total_reduction = 0
            for name, base_width in base_widths.items():
                solution = hypothesis_result.variables.get(name)
                if solution is None:
                    continue
                new_width = solution.solved_range[1] - solution.solved_range[0]
                total_reduction += max(0, base_width - new_width)
            reductions.append(total_reduction)

        reduction_score = sum(reductions) / len(reductions)
        return CandidateScore(
            variable, reduction_score * impact, reduction_score, impact, candidates, downstream
        )

    def _unresolved_names(self, result: SolveResult) -> list[str]:
        """まだ解決していない変数の名前。

        **レンジ幅が0でも、``requires_confirmation`` が立っていれば未解決と
        して扱う。** 幅0を無条件に「確定済み」と見なしていたため、階層3
        (要確認)の要素が質問対象から外れていた(2026-09-21 修正。
        `docs/top_priority_unit_safety_defect.md` 3-4節)。
        """
        return [
            name
            for name, solution in result.variables.items()
            if solution.solved_range[0] < solution.solved_range[1]
            or self._solver.requires_confirmation(name)
        ]

    def _tie_break(
        self, tied: list[CandidateScore]
    ) -> CandidateScore:
        """同点を、誤り率の低い軸→変数名の昇順で決定的に崩す(4-3節)。"""

        def error_rate(candidate: CandidateScore) -> float:
            # 確信度階層のラベル(firewall_*)ではなく、値の**出どころ**の軸で
            # 引く。``variable_axis()`` を使うと、firewall_bridge を通した
            # 変数では誤り率表に一致せず、このルールが発火しない。
            axis = self._solver.variable_error_rate_axis(candidate.variable)
            return self._axis_error_rates.get(axis, _UNKNOWN_AXIS_ERROR_RATE)

        return sorted(tied, key=lambda c: (error_rate(c), c.variable))[0]

    def _pick_best_by_score(self, scores: list[CandidateScore]) -> tuple[CandidateScore, bool]:
        """最終スコア(reduction_score × impact)が最も高い要素を選ぶ。"""
        max_score = max(s.score for s in scores)
        tied = [s for s in scores if s.score == max_score]
        if len(tied) == 1:
            return tied[0], False
        return self._tie_break(tied), True

    def _pick_best_by_impact(self, scores: list[CandidateScore]) -> tuple[CandidateScore, bool]:
        """インパクトスコア単体が最も高い要素を選ぶ(フォールバック専用)。

        文字通りの ``score = reduction_score * impact`` という式では、
        依存関係を一切持たない要素(``reduction_score == 0``)は、
        どれだけ金額インパクトが大きくても常に0点になってしまう。
        これでは概算モードが意図する「金額インパクトの大きい要素から
        優先的に質問する」という動作(依存関係の有無に関わらず)を
        実現できない。そのため、通常のスコアが全て0以下になった場合の
        フォールバックとして、インパクトスコア単体でのランキングを用意した。
        これは指示書の式をそのまま実装しただけでは目的の挙動にならなかった
        ための補足ロジックであり、``docs/killer_question_precision_report.md``
        2節で明示している。
        """
        max_impact = max(s.impact for s in scores)
        tied = [s for s in scores if s.impact == max_impact]
        if len(tied) == 1:
            return tied[0], False
        return self._tie_break(tied), True

    # -- 質問の選定・回答 --------------------------------------------------------

    def next_question(self) -> Question | None:
        """現在の状態から、次に聞くべき質問を1つだけ選ぶ(solverは変更しない)。

        モードごとの停止条件:

        - 標準: スコアが0以下になった時点で ``None``
        - 精密: 解決すべき要素が無くなるまで ``None`` を返さない
          (スコアが0以下でも、インパクト基準のフォールバックで質問を続ける)
        - 概算: 累積確信度カバレッジが目標値に達した時点で ``None``。
          価格情報が一切無ければ標準モードと同じ基準にフォールバックする
        """
        result = self._solver.solve()
        if not result.is_consistent:
            return None
        unresolved = self._unresolved_names(result)
        if not unresolved:
            return None

        if self._mode is PrecisionMode.ROUGH and self.has_any_price:
            if self.coverage(result) >= self._target_coverage:
                return None

        graph = build_dependency_graph(self._solver)
        all_scores = [self.score_candidate(result, graph, name) for name in unresolved]
        # 候補値を列挙できなかった要素は質問にできない(``is_askable`` を参照)。
        # 未確定のままにするのが正しい扱いなので、ここで選定対象から外すだけで、
        # ``_unresolved_names`` からは落とさない。
        scores = [s for s in all_scores if s.is_askable]
        if not scores:
            return None
        best, tie_broken = self._pick_best_by_score(scores)

        if best.score > 0:
            return Question(best.variable, best.score, best.candidate_values, tie_broken, self._mode, "reduction")

        # 標準モード、および価格情報が無い概算モード(フォールバック先が標準
        # モードと同じ)は、ここで打ち切る。精密モードと、価格情報のある
        # 概算モードだけが、依存関係による削減効果が無くても質問を続ける。
        continues_past_zero_score = self._mode is PrecisionMode.PRECISE or (
            self._mode is PrecisionMode.ROUGH and self.has_any_price
        )
        if not continues_past_zero_score:
            return None

        # インパクトスコア単体でランキングし直し、直接質問して解決を進める。
        fallback, tie_broken_fb = self._pick_best_by_impact(scores)
        return Question(
            fallback.variable, fallback.score, fallback.candidate_values,
            tie_broken_fb, self._mode, "impact_fallback",
        )

    def answer(self, variable: str, value: int) -> None:
        """質問への回答を、絶対的な前提として solver に反映する。

        あわせて ``requires_confirmation`` を降ろす。人が答えた時点でその要素は
        確認済みになるため。降ろさないと永久に未解決のままになり、質問の
        選定ループが終わらない。
        """
        self._solver.add_relation(
            f"{_ANSWER_PREFIX}{variable}__{len(self._answered)}", variable, "==", value
        )
        self._solver.mark_confirmed(variable)

    def run(
        self, answer_fn: Callable[[Question], int]
    ) -> SessionResult:
        """モードに応じた条件を満たすまで、質問の選定・回答を繰り返す。"""
        max_iterations = _MAX_ITERATIONS_PER_VARIABLE * max(
            1, len(self._solver.variable_names())
        )
        for _ in range(max_iterations):
            question = self.next_question()
            if question is None:
                return self._finish()
            answer = answer_fn(question)
            if answer not in question.candidate_values:
                raise ValueError(
                    f"回答 {answer} は '{question.variable}' の候補 "
                    f"{question.candidate_values} に含まれていません"
                )
            self.answer(question.variable, answer)
            self._answered.append(
                AnsweredQuestion(question.variable, answer, question.score)
            )
        raise RuntimeError(
            "反復選定ループが安全上限に達しました(想定外です。バグの可能性があります)"
        )

    def _has_only_unaskable_left(
        self, result: SolveResult, unresolved: list[str]
    ) -> bool:
        """未解決の要素が、すべて候補列挙不能かどうか。"""
        graph = build_dependency_graph(self._solver)
        scored = [self.score_candidate(result, graph, name) for name in unresolved]
        return bool(scored) and all(s.too_many_candidates for s in scored)

    def _finish(self) -> SessionResult:
        result = self._solver.solve()
        if not result.is_consistent:
            return SessionResult(
                answered=tuple(self._answered),
                remaining_unresolved=tuple(self._solver.variable_names()),
                final_solver=self._solver,
                final_result=result,
                stopped_reason="unsat",
                mode=self._mode,
                final_coverage=None,
            )

        unresolved = self._unresolved_names(result)
        coverage = self.coverage(result) if self.has_any_price else None
        if not unresolved:
            reason = "all_resolved"
        elif (
            self._mode is PrecisionMode.ROUGH
            and self.has_any_price
            and coverage is not None
            and coverage >= self._target_coverage
        ):
            reason = "coverage_reached"
        elif self._has_only_unaskable_left(result, unresolved):
            # 残っているのが「候補値が多すぎて列挙できない要素」だけの状態。
            # `no_further_reduction`(これ以上聞いても減らない)とは原因が
            # 違うので、別の理由として区別する。
            reason = "candidates_not_enumerable"
        else:
            reason = "no_further_reduction"
        return SessionResult(
            answered=tuple(self._answered),
            remaining_unresolved=tuple(unresolved),
            final_solver=self._solver,
            final_result=result,
            stopped_reason=reason,
            mode=self._mode,
            final_coverage=coverage,
        )
