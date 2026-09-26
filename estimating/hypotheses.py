"""仮説を通し、辻褄が合わなければ遡って捨てる(K-44、2026-09-26)。

**なぜこれが要るのか**

捏造を止めるために「ぴったり一致しなければ進めない」としていたら、捏造は 0 件になったが
読めなくなった。裏取りも完全一致で探すので、裏取りそのものが働いていなかった。
おーちゃんの K-44 は、**捏造と仮説を分ける**:

- **捏造** = 根拠なしに作ったもの(図面に無い名前、図面に無い数字)。**1 件も出さない。**
- **仮説** = 根拠はあるが確証が無いもの。**通す。ただし印を付ける。自動確定しない。**

ここが持つのは 3 つだけ:

1. 仮説の形(置いた答え・根拠・ほかの候補・選んだ理由・確からしさ)
2. **捏造の検査**: 引用した文字がそのページに本当にあるか、数量の式が引用した数字から
   計算できるか。1 つでも外れれば捏造として返す(呼ぶ側が止める)。
3. **辻褄の検算と、遡って捨てること**: 合わなければ、関わった仮説のうち確からしさが
   いちばん低いものを捨て、何をどの検算で捨てたかを記録する。

**何も確定させない。** 仮説から出た数量は、確からしさが「高」でも確定しない。
**記載が見当たらないことは根拠にならない**(K-42)。根拠が 0 の項目は仮説にしない。

確からしさの数え方(`docs/k44_hypothesis_story_criteria.md` の仮の判断 2):
互いに矛盾しない根拠 3 つ以上 → 高、2 つ → 中、1 つ → 低。同じページの同じ引用は 1 つと数える。
"""

from __future__ import annotations

import ast
import operator
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

LIKELIHOOD_HIGH = "高"
LIKELIHOOD_MID = "中"
LIKELIHOOD_LOW = "低"
LIKELIHOODS = (LIKELIHOOD_HIGH, LIKELIHOOD_MID, LIKELIHOOD_LOW)

EVIDENCE_TEXT = "文字"
EVIDENCE_FIGURE = "図"

#: 引用を部分一致で探すときの最短の長さ。これより短い引用は全文一致で探す。
MIN_PARTIAL_QUOTE = 4

#: 数量の式を計算し直したときに許すずれ(割合)。
FORMULA_TOLERANCE = 0.01


def _nfkc(text: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


@dataclass(frozen=True)
class Evidence:
    """根拠 1 つ。ページと、そのページの文字の引用(図なら見た目の説明)。"""

    page: int
    kind: str
    quote: str = ""
    note: str = ""

    def key(self) -> tuple[int, str]:
        """同じページの同じ引用を 1 つと数えるための鍵。"""
        return (self.page, _nfkc(self.quote) if self.kind == EVIDENCE_TEXT else _nfkc(self.note))


@dataclass
class Hypothesis:
    """仮に置いた答え 1 つ。**必ず「仮説」の印を持つ。確定しない。**"""

    item_id: str
    work: str
    place: str = ""
    quantity: float | None = None
    unit: str | None = None
    evidence: tuple[Evidence, ...] = ()
    formula: str | None = None
    alternatives: tuple[str, ...] = ()
    reason: str = ""
    order: int = 0
    discarded: bool = False

    label: str = field(default="仮説", init=False)
    is_confirmed: bool = field(default=False, init=False)

    def independent_evidence(self) -> int:
        return len({e.key() for e in self.evidence})

    @property
    def likelihood(self) -> str | None:
        """根拠の数から決める。根拠が 0 なら None(仮説にしてはいけない)。"""
        n = self.independent_evidence()
        if n >= 3:
            return LIKELIHOOD_HIGH
        if n == 2:
            return LIKELIHOOD_MID
        if n == 1:
            return LIKELIHOOD_LOW
        return None


@dataclass(frozen=True)
class Fabrication:
    """捏造と判定した理由。1 件でもあれば呼ぶ側は止める。"""

    item_id: str
    reason: str


def quote_found(quote: str, page_text: str) -> bool:
    """引用がページの文字の層にあるか。全角半角と空白をそろえて探す。"""
    q, t = _nfkc(quote), _nfkc(page_text)
    if not q:
        return False
    return q in t


_OPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _eval(node: ast.AST, numbers: list[float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, numbers)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        numbers.append(float(node.value))
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, numbers), _eval(node.right, numbers))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, numbers)
    raise ValueError("四則演算と数字以外は式に使えません")


def evaluate_formula(formula: str) -> tuple[float, list[float]]:
    """式の先頭の四則演算の部分を計算し、値と使った数字を返す。

    式は ``"3.64*2.73 (p8 の寸法 3,640 と 2,730)"`` のように、計算の後ろに説明が付いてよい。
    """
    expr = unicodedata.normalize("NFKC", formula).split("(")[0]
    expr = expr.replace("×", "*").replace("÷", "/").replace(",", "").strip()
    numbers: list[float] = []
    value = _eval(ast.parse(expr, mode="eval"), numbers)
    return value, numbers


def _number_printed(number: float, texts: Iterable[str]) -> bool:
    """数字が引用のどれかに印字されているか。mm と m の書き換え(×1000)も同じ数字と見る。"""
    printed: set[float] = set()
    for text in texts:
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?", unicodedata.normalize("NFKC", text)):
            try:
                printed.add(float(token.replace(",", "")))
            except ValueError:
                continue
    for candidate in (number, number * 1000, number / 1000):
        if any(abs(candidate - p) <= 1e-6 * max(1.0, abs(p)) for p in printed):
            return True
    return False


def check_fabrication(
    hypothesis: Hypothesis,
    page_texts: Mapping[int, str],
    *,
    figure_confirmed: Callable[[Evidence], bool] | None = None,
    allowed_constants: Sequence[float] = (1.0, 2.0),
) -> list[Fabrication]:
    """捏造の検査。**空の並びが返れば捏造ではない。**

    - 根拠が 0 → 捏造(根拠なしに作った)。
    - 文字の根拠の引用が、そのページの文字の層に無い → 捏造。
    - 図の根拠は `figure_confirmed` が確かめる。渡されなければ図の根拠は確かめられないので
      捏造として返す(確かめていないものを通さない)。
    - 数量があるのに式が無い(個数を図で数えた場合を除く)、式の値が合わない、
      式の数字が引用に無い → 捏造。`allowed_constants` は「2 面」「片面」などの係数。
    """
    found: list[Fabrication] = []
    hid = hypothesis.item_id
    if not hypothesis.evidence:
        return [Fabrication(hid, "根拠が 0 のまま仮説にした")]
    for ev in hypothesis.evidence:
        if ev.kind == EVIDENCE_TEXT:
            page_text = page_texts.get(ev.page)
            if page_text is None:
                found.append(Fabrication(hid, f"{ev.page} ページが無い"))
            elif not quote_found(ev.quote, page_text):
                found.append(Fabrication(hid, f"{ev.page} ページに引用 {ev.quote!r} が無い"))
        elif ev.kind == EVIDENCE_FIGURE:
            if figure_confirmed is None or not figure_confirmed(ev):
                found.append(Fabrication(hid, f"{ev.page} ページの図の根拠が確かめられない: {ev.note!r}"))
        else:
            found.append(Fabrication(hid, f"根拠の種類が分からない: {ev.kind!r}"))
    if hypothesis.quantity is not None and hypothesis.formula:
        try:
            value, numbers = evaluate_formula(hypothesis.formula)
        except (SyntaxError, ValueError, ZeroDivisionError):
            found.append(Fabrication(hid, f"数量の式が計算できない: {hypothesis.formula!r}"))
        else:
            if abs(value - hypothesis.quantity) > FORMULA_TOLERANCE * max(1.0, abs(hypothesis.quantity)):
                found.append(Fabrication(hid, f"式の値 {value:g} と数量 {hypothesis.quantity:g} が合わない"))
            quotes = [e.quote for e in hypothesis.evidence]
            for n in numbers:
                if n in allowed_constants:
                    continue
                if not _number_printed(n, quotes):
                    found.append(Fabrication(hid, f"式の数字 {n:g} が引用のどこにも無い"))
    elif hypothesis.quantity is not None:
        counted_on_figure = any(e.kind == EVIDENCE_FIGURE for e in hypothesis.evidence)
        printed = _number_printed(hypothesis.quantity, [e.quote for e in hypothesis.evidence])
        if not (counted_on_figure or printed):
            found.append(Fabrication(hid, "数量に式も印字も図で数えた根拠も無い"))
    return found


@dataclass(frozen=True)
class Discard:
    """遡って捨てた記録。何を仮に置き、どの検算で合わなかったか。"""

    item_id: str
    work: str
    check: str
    detail: str


@dataclass(frozen=True)
class Inconsistency:
    """検算 1 つが見つけた食い違い。関わった仮説の item_id を持つ。"""

    check: str
    detail: str
    item_ids: tuple[str, ...]


# ---- 辻褄の検算 --------------------------------------------------------------

AREA_PARTS = ("床", "壁", "天井")
AREA_UNITS = {"㎡", "m2", "m²"}
COUNT_UNITS = {"箇所", "個", "台", "枚", "組", "本"}
FIXTURE_WORDS = ("コンセント", "スイッチ", "照明", "ダウンライト", "LAN", "TV端子", "シーリング")


def check_units(hs: Sequence[Hypothesis]) -> list[Inconsistency]:
    """単位が合わない: 床・壁・天井の仕上と下地は ㎡、器具は個数。"""
    out = []
    for h in hs:
        if h.quantity is None or not h.unit:
            continue
        work = _nfkc(h.work)
        unit = _nfkc(h.unit)
        is_fixture = any(_nfkc(w) in work for w in FIXTURE_WORDS)
        is_surface = (not is_fixture) and any(
            k in work for k in ("クロス", "ボード", "下地", "塩ビタイル", "フロアシート", "野縁")
        )
        if is_surface and unit not in {_nfkc(u) for u in AREA_UNITS}:
            out.append(Inconsistency("単位が合わない", f"{h.work} が {h.unit}", (h.item_id,)))
        if is_fixture and unit not in {_nfkc(u) for u in COUNT_UNITS}:
            out.append(Inconsistency("単位が合わない", f"{h.work} が {h.unit}", (h.item_id,)))
    return out


def check_physical(hs: Sequence[Hypothesis]) -> list[Inconsistency]:
    """物理的にありえない: 数量が 0 以下。"""
    return [
        Inconsistency("物理的にありえない", f"{h.work} の数量が {h.quantity:g}", (h.item_id,))
        for h in hs
        if h.quantity is not None and h.quantity <= 0
    ]


def check_floor_total(hs: Sequence[Hypothesis], printed_floor_area: float | None) -> list[Inconsistency]:
    """数が合わない: 室の床面積の仮説の合計が、印字された施工床面積を超える。

    印字された施工床面積が無ければ、この検算はしない(**0 や既定値で代えない**)。
    """
    if printed_floor_area is None:
        return []
    floor = [
        h for h in hs
        if h.quantity is not None and _nfkc(h.unit) in {_nfkc(u) for u in AREA_UNITS}
        and _nfkc(h.work).startswith("床") and "撤去" not in h.work and "解体" not in h.work
    ]
    by_place: dict[str, Hypothesis] = {}
    for h in floor:
        by_place.setdefault(_nfkc(h.place), h)
    total = sum(h.quantity or 0.0 for h in by_place.values())
    if total > printed_floor_area * 1.05:
        return [Inconsistency(
            "数が合わない",
            f"室の床面積の合計 {total:.2f}㎡ が施工床面積 {printed_floor_area:g}㎡ を超える",
            tuple(h.item_id for h in by_place.values()),
        )]
    return []


def check_double_count(hs: Sequence[Hypothesis]) -> list[Inconsistency]:
    """同じものを 2 回数えている: 同じ場所・同じ工事・同じ数量の仮説が 2 つ以上。"""
    groups: dict[tuple[str, str, float, str], list[Hypothesis]] = {}
    for h in hs:
        if h.quantity is None:
            continue
        key = (_nfkc(h.place), _nfkc(h.work), round(h.quantity, 2), _nfkc(h.unit))
        groups.setdefault(key, []).append(h)
    return [
        Inconsistency("同じものを 2 回数えている", f"{g[0].work}({g[0].place})が {len(g)} 回", tuple(h.item_id for h in g))
        for g in groups.values()
        if len(g) >= 2
    ]


def check_conflicting_evidence(hs: Sequence[Hypothesis]) -> list[Inconsistency]:
    """別の資料と食い違う: 同じ場所・同じ工事に、違う数量の仮説がある(許容差 ±5% を超える)。"""
    groups: dict[tuple[str, str, str], list[Hypothesis]] = {}
    for h in hs:
        if h.quantity is None:
            continue
        groups.setdefault((_nfkc(h.place), _nfkc(h.work), _nfkc(h.unit)), []).append(h)
    out = []
    for g in groups.values():
        values = [h.quantity for h in g if h.quantity is not None]
        if len(values) >= 2 and max(values) > min(values) * 1.05:
            out.append(Inconsistency("別の資料と食い違う", f"{g[0].work}({g[0].place})に {sorted(values)}", tuple(h.item_id for h in g)))
    return out


REMOVAL_WORDS = ("撤去", "解体", "剥がし")
FOLLOW_WORDS = ("新設", "張", "貼", "組", "既存", "重張", "交換", "復旧", "戻す")


def check_chain(hs: Sequence[Hypothesis]) -> list[Inconsistency]:
    """工事の連鎖が切れている: 床・壁・天井の撤去があるのに、同じ部位の新設・張替・既存利用の仮説がどこにも無い。

    撤去の仮説そのものの文に後の工事(「→貼替」など)が書かれていれば、連鎖は切れていないと見る。
    場所は比べない(室名の書き方が仮説ごとにそろわないため。見落とす向きに倒している)。
    """
    out = []
    for h in hs:
        work = _nfkc(h.work)
        if not any(w in work for w in REMOVAL_WORDS):
            continue
        parts = [p for p in AREA_PARTS if p in work]
        if not parts:
            continue
        if any(w in work for w in FOLLOW_WORDS):
            continue
        followed = any(
            other is not h
            and any(p in _nfkc(other.work) for p in parts)
            and any(w in _nfkc(other.work) for w in FOLLOW_WORDS)
            and not any(w in _nfkc(other.work) for w in REMOVAL_WORDS)
            for other in hs
        )
        if not followed:
            out.append(Inconsistency("工事の連鎖が切れている", f"{h.work} の後の工事が無い", (h.item_id,)))
    return out


DEFAULT_CHECKS: tuple[Callable[[Sequence[Hypothesis]], list[Inconsistency]], ...] = (
    check_units,
    check_physical,
    check_double_count,
    check_conflicting_evidence,
    check_chain,
)

#: この層では見られない検算。見られないことを黙らせないために名前を出す。
CHECKS_NOT_AVAILABLE = ("金額の桁が違う", "範囲が重なる")


_RANK = {LIKELIHOOD_HIGH: 0, LIKELIHOOD_MID: 1, LIKELIHOOD_LOW: 2, None: 3}


def _weakest(hs: Sequence[Hypothesis]) -> Hypothesis:
    """確からしさがいちばん低いもの。同じなら後から置いたもの。"""
    return max(hs, key=lambda h: (_RANK[h.likelihood], h.order))


def backtrack(
    hypotheses: Sequence[Hypothesis],
    *,
    printed_floor_area: float | None = None,
    extra_checks: Sequence[Callable[[Sequence[Hypothesis]], list[Inconsistency]]] = (),
    max_rounds: int = 10,
) -> tuple[list[Hypothesis], list[Discard]]:
    """検算を回し、合わなければ関わった仮説のうち最も弱いものを捨てる。矛盾が無くなるまで。

    戻り値は (残った仮説, 捨てた記録)。**捨てた仮説も消さずに `discarded=True` で残す。**
    """
    alive = [h for h in hypotheses if not h.discarded]
    discards: list[Discard] = []
    for _ in range(max_rounds):
        found: list[Inconsistency] = []
        for check in (*DEFAULT_CHECKS, *extra_checks):
            found.extend(check(alive))
        found.extend(check_floor_total(alive, printed_floor_area))
        if not found:
            break
        by_id = {h.item_id: h for h in alive}
        dropped = False
        for inc in found:
            involved = [by_id[i] for i in inc.item_ids if i in by_id and not by_id[i].discarded]
            if not involved:
                continue
            victim = _weakest(involved)
            victim.discarded = True
            discards.append(Discard(victim.item_id, victim.work, inc.check, inc.detail))
            dropped = True
        alive = [h for h in alive if not h.discarded]
        if not dropped:
            break
    return alive, discards
