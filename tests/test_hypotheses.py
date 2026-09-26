"""K-44: 仮説を通し、辻褄が合わなければ遡って捨てる。合成のデータだけで確かめる。"""

from estimating.hypotheses import (
    EVIDENCE_FIGURE,
    EVIDENCE_TEXT,
    Evidence,
    Hypothesis,
    backtrack,
    check_fabrication,
    evaluate_formula,
)

PAGES = {
    1: "計画概要 施工床面積 80.00 ㎡ 天井組 有",
    2: "平面図 洋室A 3,640 2,730 床見切新設",
    3: "仕上表 洋室A 床 塩ビタイル 下地 合板12㎜",
}


def _h(item, work, *, q=None, unit=None, ev=(), formula=None, place="洋室A", order=0):
    return Hypothesis(item, work, place=place, quantity=q, unit=unit, evidence=tuple(ev), formula=formula, order=order)


def test_likelihood_counts_independent_evidence():
    ev = [Evidence(2, EVIDENCE_TEXT, "床見切新設"), Evidence(2, EVIDENCE_TEXT, "床見切新設"), Evidence(3, EVIDENCE_TEXT, "塩ビタイル")]
    h = _h("a", "床見切", ev=ev)
    assert h.independent_evidence() == 2
    assert h.likelihood == "中"
    h3 = _h("b", "床", ev=ev + [Evidence(1, EVIDENCE_TEXT, "天井組 有")])
    assert h3.likelihood == "高"


def test_hypothesis_is_labelled_and_never_confirmed():
    h = _h("a", "床", ev=[Evidence(1, EVIDENCE_TEXT, "天井組")])
    assert h.label == "仮説"
    assert h.is_confirmed is False


def test_no_evidence_is_fabrication():
    assert check_fabrication(_h("a", "床暖房"), PAGES)


def test_quote_not_on_page_is_fabrication():
    h = _h("a", "床暖房", ev=[Evidence(2, EVIDENCE_TEXT, "床暖房パネル")])
    reasons = check_fabrication(h, PAGES)
    assert reasons and "引用" in reasons[0].reason


def test_quote_matches_after_width_normalisation():
    h = _h("a", "床", ev=[Evidence(3, EVIDENCE_TEXT, "合板12mm")])
    # 全角「㎜」と半角「mm」は NFKC でそろう
    assert check_fabrication(h, PAGES) == []


def test_formula_from_printed_numbers_passes():
    h = _h("a", "床 塩ビタイル", q=9.9372, unit="㎡", formula="3.64*2.73 (2 ページの寸法)",
           ev=[Evidence(2, EVIDENCE_TEXT, "3,640 2,730")])
    assert check_fabrication(h, PAGES) == []


def test_formula_number_not_printed_is_fabrication():
    h = _h("a", "床 塩ビタイル", q=10.92, unit="㎡", formula="4.0*2.73",
           ev=[Evidence(2, EVIDENCE_TEXT, "3,640 2,730")])
    assert any("4" in r.reason for r in check_fabrication(h, PAGES))


def test_formula_value_mismatch_is_fabrication():
    h = _h("a", "床", q=12.0, unit="㎡", formula="3.64*2.73", ev=[Evidence(2, EVIDENCE_TEXT, "3,640 2,730")])
    assert any("合わない" in r.reason for r in check_fabrication(h, PAGES))


def test_quantity_without_any_basis_is_fabrication():
    h = _h("a", "床", q=12.0, unit="㎡", ev=[Evidence(3, EVIDENCE_TEXT, "塩ビタイル")])
    assert check_fabrication(h, PAGES)


def test_figure_evidence_needs_confirmation():
    ev = Evidence(2, EVIDENCE_FIGURE, "", "赤いコンセントの記号 2 つ")
    h = _h("a", "コンセント", q=2, unit="箇所", ev=[ev])
    assert check_fabrication(h, PAGES)
    assert check_fabrication(h, PAGES, figure_confirmed=lambda e: True) == []


def test_evaluate_formula_ignores_trailing_note():
    value, numbers = evaluate_formula("1.2×3 (説明)")
    assert abs(value - 3.6) < 1e-9 and numbers == [1.2, 3.0]


def test_backtrack_discards_weakest_on_unit_mismatch_and_keeps_record():
    strong = _h("s", "壁 クロス", q=20.0, unit="㎡", ev=[Evidence(i, EVIDENCE_TEXT, "x") for i in (1, 2, 3)])
    bad = _h("b", "壁 クロス", q=20.0, unit="m", place="洋室B", ev=[Evidence(2, EVIDENCE_TEXT, "x")], order=1)
    alive, discards = backtrack([strong, bad])
    assert [h.item_id for h in alive] == ["s"]
    assert discards[0].item_id == "b" and discards[0].check == "単位が合わない"
    assert bad.discarded is True


def test_backtrack_double_count_drops_later_of_equals():
    a = _h("a", "床 塩ビタイル", q=9.94, unit="㎡", ev=[Evidence(2, EVIDENCE_TEXT, "x")], order=0)
    b = _h("b", "床 塩ビタイル", q=9.94, unit="㎡", ev=[Evidence(3, EVIDENCE_TEXT, "y")], order=1)
    alive, discards = backtrack([a, b])
    assert [h.item_id for h in alive] == ["a"]
    assert discards[0].check == "同じものを 2 回数えている"


def test_floor_total_check_only_when_printed():
    rooms = [_h(f"r{i}", "床 塩ビタイル", q=30.0, unit="㎡", place=f"室{i}", ev=[Evidence(2, EVIDENCE_TEXT, "x")], order=i) for i in range(3)]
    alive, discards = backtrack(rooms, printed_floor_area=None)
    assert len(alive) == 3 and discards == []
    rooms = [_h(f"r{i}", "床 塩ビタイル", q=30.0, unit="㎡", place=f"室{i}", ev=[Evidence(2, EVIDENCE_TEXT, "x")], order=i) for i in range(3)]
    alive, discards = backtrack(rooms, printed_floor_area=80.0)
    assert len(alive) == 2 and discards[0].check == "数が合わない"


def test_consistent_hypotheses_all_survive():
    hs = [_h("a", "床 塩ビタイル", q=9.94, unit="㎡", ev=[Evidence(2, EVIDENCE_TEXT, "x")]),
          _h("b", "コンセント 新設", q=2, unit="箇所", ev=[Evidence(2, EVIDENCE_TEXT, "y")])]
    alive, discards = backtrack(hs)
    assert len(alive) == 2 and discards == []


def test_chain_break_when_removal_has_no_follow_up():
    rm = _h("r", "天井 撤去", ev=[Evidence(2, EVIDENCE_TEXT, "x")])
    alive, discards = backtrack([rm])
    assert alive == [] and discards[0].check == "工事の連鎖が切れている"


def test_chain_ok_when_follow_up_exists():
    rm = _h("r", "天井 撤去", ev=[Evidence(2, EVIDENCE_TEXT, "x")])
    new = _h("n", "天井組 新設", ev=[Evidence(3, EVIDENCE_TEXT, "y")])
    alive, discards = backtrack([rm, new])
    assert len(alive) == 2 and discards == []
