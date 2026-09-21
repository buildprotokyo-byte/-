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
   候補値それぞれが正解である可能性を均等に見て平均を取る)
4. 平均削減数が最も高い要素を、次の質問として選ぶ

運用上のルール(4-3節)
------------------------
- スコアが同点の場合は、誤り率が低いデータ源(軸)から取得できる要素を優先する
  (トライアル9)。``axis_error_rates`` にデータ源ごとの誤り率を渡すことで有効になる
- スコアが0以下(これ以上質問しても候補が減らない)になった時点で打ち切る。
  これは他の要素と依存関係を持たない(または既に他の情報で決着している)要素は、
  killer_question の対象にならないことを意味する。そのような要素は
  v8 3-3節の「階層3: 要確認」へそのまま回す運用を想定する(本エンジンの範囲外)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from arbitration.consistency_solver import ConsistencySolver, SolveResult
from killer_question.dependency_graph import DependencyGraph, build_dependency_graph

#: 仮確定・回答の確定に使う制約名の接頭辞(名前の衝突を避けるための予約語)。
_HYPOTHESIS_PREFIX = "__killer_question_hypothesis__"
_ANSWER_PREFIX = "__killer_question_answer__"

#: 未知の軸(誤り率が登録されていない)に割り当てる、最も信頼しない扱いの誤り率。
_UNKNOWN_AXIS_ERROR_RATE = 1.0

#: 反復選定ループの安全上限。1回の反復で必ず1つの変数が解決するか、ループが
#: 終了するため理論上は不要だが、想定外のバグで無限ループになることを防ぐ
#: 最終防波堤として置いている。
_MAX_ITERATIONS_PER_VARIABLE = 4


@dataclass(frozen=True)
class CandidateScore:
    """1つの未確定要素について計算した、キラークエスチョンスコア。"""

    variable: str
    score: float
    candidate_values: tuple[int, ...]
    downstream: frozenset[str]


@dataclass(frozen=True)
class Question:
    """次に人へ確認すべき質問1件。"""

    variable: str
    score: float
    candidate_values: tuple[int, ...]
    tie_broken_by_axis: bool = False


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
    ) -> None:
        # 呼び出し側の solver は変更しない(内部で複製してから使う)。
        self._solver = solver.clone()
        self._axis_error_rates = dict(axis_error_rates or {})
        self._answered: list[AnsweredQuestion] = []

    @property
    def solver(self) -> ConsistencySolver:
        """現在の(質問への回答を反映した)solverの状態。"""
        return self._solver

    @property
    def answered(self) -> tuple[AnsweredQuestion, ...]:
        return tuple(self._answered)

    # -- スコア計算 ------------------------------------------------------------

    def score_candidate(
        self, base_result: SolveResult, graph: DependencyGraph, variable: str
    ) -> CandidateScore:
        """1つの未確定要素について、v8 4-2節の手順でスコアを計算する。"""
        lower, upper = base_result.variables[variable].solved_range
        candidates = tuple(range(lower, upper + 1))
        downstream = graph.connected_component(variable)
        base_widths = {
            name: base_result.variables[name].solved_range[1]
            - base_result.variables[name].solved_range[0]
            for name in downstream
            if name in base_result.variables
        }

        if not candidates or not base_widths:
            return CandidateScore(variable, 0.0, candidates, downstream)

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

        score = sum(reductions) / len(reductions)
        return CandidateScore(variable, score, candidates, downstream)

    def _unresolved_names(self, result: SolveResult) -> list[str]:
        return [
            name
            for name, solution in result.variables.items()
            if solution.solved_range[0] < solution.solved_range[1]
        ]

    def _pick_best(self, scores: list[CandidateScore]) -> tuple[CandidateScore, bool]:
        """最高スコアの要素を選ぶ。同点の場合は誤り率の低い軸を優先する(4-3節)。"""
        max_score = max(s.score for s in scores)
        tied = [s for s in scores if s.score == max_score]
        if len(tied) == 1:
            return tied[0], False

        def error_rate(candidate: CandidateScore) -> float:
            axis = self._solver.variable_axis(candidate.variable)
            return self._axis_error_rates.get(axis, _UNKNOWN_AXIS_ERROR_RATE)

        tied.sort(key=lambda c: (error_rate(c), c.variable))
        return tied[0], True

    # -- 質問の選定・回答 --------------------------------------------------------

    def next_question(self) -> Question | None:
        """現在の状態から、次に聞くべき質問を1つだけ選ぶ(solverは変更しない)。

        すべて解決済み、矛盾している、またはこれ以上質問しても候補が減らない
        場合は ``None`` を返す。
        """
        result = self._solver.solve()
        if not result.is_consistent:
            return None
        unresolved = self._unresolved_names(result)
        if not unresolved:
            return None
        graph = build_dependency_graph(self._solver)
        scores = [self.score_candidate(result, graph, name) for name in unresolved]
        best, tie_broken = self._pick_best(scores)
        if best.score <= 0:
            return None
        return Question(best.variable, best.score, best.candidate_values, tie_broken)

    def answer(self, variable: str, value: int) -> None:
        """質問への回答を、絶対的な前提として solver に反映する。"""
        self._solver.add_relation(
            f"{_ANSWER_PREFIX}{variable}__{len(self._answered)}", variable, "==", value
        )

    def run(
        self, answer_fn: Callable[[Question], int]
    ) -> SessionResult:
        """すべての要素が解決するか、これ以上質問しても候補が減らなくなるまで繰り返す。"""
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

    def _finish(self) -> SessionResult:
        result = self._solver.solve()
        if not result.is_consistent:
            reason = "unsat"
            remaining = tuple(self._solver.variable_names())
        else:
            unresolved = self._unresolved_names(result)
            reason = "all_resolved" if not unresolved else "no_further_reduction"
            remaining = tuple(unresolved)
        return SessionResult(
            answered=tuple(self._answered),
            remaining_unresolved=remaining,
            final_solver=self._solver,
            final_result=result,
            stopped_reason=reason,
        )
