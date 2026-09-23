"""本番の入口に抜き取り検査を差し込んだことの回帰テスト。

設計は `docs/audit_production_integration_design.md`。

**テスト用の PDF は合成のベクター PDF である。** 顧客の図面は匿名化済みでも
リポジトリに置かない決まりなので、テストが実図面に依存してはならない。

ここで固定したいこと
--------------------
1. **入口は正解データに触らない。** 呼ぶのは抽出(`plan_tiered_audit`)までで、
   照合(`score_audit_plan`)は呼ばない。
2. **母集団0件を「監査して全部当たった」と報告しない。** いまの経路は全対象が
   階層3になるので、母集団は0件が正しい出力である。
3. **抜き取った一覧に根拠が付く。** ページ番号と読んだ元の文字列。
4. **同じ図面・同じ案件で読み直しても、抜き取られる対象が変わらない。**
   人が確かめている最中に一覧が入れ替わると、確かめた分が捨てられる。
5. **記録はリポジトリの外にしか書かない。** パスが無ければ書かない。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from arbitration.provisional_audit import TierAuditPolicy, TieredAuditPlan
from intake.drawing_intake import (
    PRODUCTION_AUDIT_POLICIES,
    IntakeConfig,
    read_drawing,
)

PT_PER_MM_AT_50 = 1.0 / 50.0 * 72.0 / 25.4


def _draw_quarter_arc(page: pymupdf.Page, x: float, y: float, radius_pt: float) -> None:
    shape = page.new_shape()
    shape.draw_sector(
        pymupdf.Point(x, y), pymupdf.Point(x + radius_pt, y), 90, fullSector=False
    )
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


@pytest.fixture()
def drawing(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(
        pymupdf.Point(850, 780), "縮尺 1/50", fontname="japan", fontsize=11
    )
    page.insert_text(
        pymupdf.Point(850, 800), "専有延床面積 95.54 ㎡", fontname="japan", fontsize=11
    )
    x = 100.0
    for width_mm in (800.0, 750.0, 900.0):
        _draw_quarter_arc(page, x, 300.0, width_mm * PT_PER_MM_AT_50)
        x += 200.0
    # 2 ページ目。**開き戸はページごとに別の対象になる**ので、これで対象が
    # 3 件になり、階層3に落ちる対象を 1 件は必ず作れる。
    second = doc.new_page(width=1190, height=842)
    second.insert_text(
        pymupdf.Point(850, 780), "縮尺 1/50", fontname="japan", fontsize=11
    )
    _draw_quarter_arc(second, 100.0, 300.0, 820.0 * PT_PER_MM_AT_50)
    doc.save(path)
    doc.close()
    return path


def _config(drawing: Path, tmp_path: Path, **extra) -> IntakeConfig:
    return IntakeConfig(
        pdf_path=drawing,
        case_id="TEST-AUDIT-001",
        answers_path=tmp_path / "answers.json",
        **extra,
    )


class _StubOrchestrator:
    """階層を指定できる仲裁層の代わり。

    いまの本番経路は**全対象が階層3**になるので(図面 PDF は 1 データ源で、
    手法も未校正)、階層1・階層2が出たときの振る舞いは実物では試せない。
    **階層が上がった将来を、いま固定しておくための代役である。**
    """

    def __init__(self, tiers_by_order: dict[int, tuple[int, str]]) -> None:
        self._tiers = tiers_by_order
        self._seen: list[str] = []

    def process(self, request):  # noqa: ANN001 - 代役なので型は緩く
        target = request["element_id"]
        if target not in self._seen:
            self._seen.append(target)
        # **見た順に割り当てる。** 並べ替えてから位置を取ると、対象が増える
        # たびに同じ対象の階層が変わってしまう。
        index = self._seen.index(target)
        tier, action = self._tiers.get(index, (3, "requires_review"))
        decision = SimpleNamespace(
            tier=tier,
            action=action,
            confirmed_range=(4, 6) if action != "requires_review" else None,
            reasons=("代役",),
        )
        return SimpleNamespace(
            decision=decision, trace_id=f"stub::{target}", events=(), is_invalid=False
        )


# =====================================================================
# 1. 入口は正解データに触らない
# =====================================================================


def test_the_intake_never_takes_a_ground_truth_source() -> None:
    """`read_drawing()` が正解の引き口を引数に取らないこと。

    取れてしまうと、入口から正解を渡す経路がそこにできる。
    `read_drawing()` の docstring の約束
    (「正解データはここでは一切読まない」)を型で守る。
    """
    import inspect

    parameters = set(inspect.signature(read_drawing).parameters)

    assert not any(
        word in name
        for name in parameters
        for word in ("truth", "golden", "expected", "answer_key")
    )


def test_the_result_carries_a_plan_not_a_hit_rate(
    drawing: Path, tmp_path: Path
) -> None:
    """入口の結果に的中率が入っていないこと(照合していないので出せない)。"""
    result = read_drawing(_config(drawing, tmp_path))

    assert isinstance(result.audit_plan, TieredAuditPlan)
    assert not hasattr(result.audit_plan, "hit_rate")
    assert not hasattr(result, "hit_rate")


# =====================================================================
# 2. 母集団0件を「全部当たった」と報告しない
# =====================================================================


def test_the_population_is_empty_on_the_real_path_and_says_so(
    drawing: Path, tmp_path: Path
) -> None:
    """いまの経路では母集団が0件で、それが記録に残ること。

    **これは不具合ではない。** 図面 PDF は 1 つのデータ源なので独立した強い
    軸が 2 つ揃わず、全対象が階層3(人の確認)になる。抜き取り検査の母集団は
    階層1と階層2だけなので、0件が正しい出力である。
    """
    result = read_drawing(_config(drawing, tmp_path))

    assert result.decisions, "判定が1件も無ければこのテストは何も確かめていない"
    assert all(item.tier == 3 for item in result.decisions)

    for tier in (1, 2):
        assert result.audit_plan.plan_for(tier).status == "no_population"
        assert result.audit_plan.plan_for(tier).sample_size == 0
    assert result.audit_plan.has_population is False


def test_the_summary_says_no_target_not_all_correct(
    drawing: Path, tmp_path: Path
) -> None:
    """要約が「監査対象なし」と書き、「全件一致」と書かないこと。"""
    text = read_drawing(_config(drawing, tmp_path)).summary()

    assert "抜き取り検査" in text
    assert "監査対象なし" in text
    assert "全件一致" not in text
    assert "全部一致" not in text


# =====================================================================
# 3. 階層1・階層2だけが母集団に入る
# =====================================================================


def test_only_tier1_and_tier2_enter_the_population(
    drawing: Path, tmp_path: Path
) -> None:
    """階層1と階層2が母集団に入り、階層3は入らないこと。"""
    stub = _StubOrchestrator({0: (1, "auto_confirm"), 1: (2, "provisional_audit")})
    result = read_drawing(_config(drawing, tmp_path), orchestrator=stub)

    assert len(result.decisions) >= 3, "階層3の対象が1件も無いと確かめられない"
    assert result.audit_plan.plan_for(1).population_size == 1
    assert result.audit_plan.plan_for(2).population_size == 1
    # 階層3は母集団に入らない。
    total = sum(
        result.audit_plan.plan_for(tier).population_size for tier in (1, 2)
    )
    assert total == 2 < len(result.decisions)


def test_each_tier_uses_the_production_sampling_rate(
    drawing: Path, tmp_path: Path
) -> None:
    """本番の抽出率が階層ごとに効くこと。"""
    assert PRODUCTION_AUDIT_POLICIES[1] == TierAuditPolicy(0.10, 3)
    assert PRODUCTION_AUDIT_POLICIES[2] == TierAuditPolicy(0.30, 5)

    stub = _StubOrchestrator({0: (1, "auto_confirm"), 1: (2, "provisional_audit")})
    result = read_drawing(_config(drawing, tmp_path), orchestrator=stub)

    for tier in (1, 2):
        plan = result.audit_plan.plan_for(tier)
        assert plan.sampling_rate == PRODUCTION_AUDIT_POLICIES[tier].sampling_rate
        assert plan.minimum_sample == PRODUCTION_AUDIT_POLICIES[tier].minimum_sample
        # 母集団1件なら、抽出率を掛けて0件になる空回りを起こさず1件抜く。
        assert plan.sample_size == 1


def test_the_tier1_default_is_not_baked_into_the_library(
    drawing: Path, tmp_path: Path
) -> None:
    """階層1の既定値が部品の側ではなく本番の入口にあること。

    部品(`arbitration/provisional_audit.py`)の既定に階層1を入れると、
    値が決まっていない段階で他の呼び出しにも効いてしまう。
    """
    from arbitration.provisional_audit import DEFAULT_TIER_POLICIES

    assert 1 not in DEFAULT_TIER_POLICIES
    assert 1 in PRODUCTION_AUDIT_POLICIES


# =====================================================================
# 4. 抜き取った一覧に根拠が付く
# =====================================================================


def test_the_sampled_items_carry_the_grounds_a_person_needs(
    drawing: Path, tmp_path: Path
) -> None:
    """抜き取った1件に、人が確かめるための根拠が付いていること。

    対象名だけでは、図面のどこを見ればいいのか分からない。
    """
    stub = _StubOrchestrator({0: (1, "auto_confirm"), 1: (2, "provisional_audit")})
    result = read_drawing(_config(drawing, tmp_path), orchestrator=stub)

    sampled = [
        item
        for tier in (1, 2)
        for item in result.audit_plan.plan_for(tier).sampled
    ]
    assert sampled
    for item in sampled:
        assert item.method_id, "手法が無いと拡大監査の単位が決まらない"
        assert item.provenance, "根拠が落ちている"
        # **どのページを見ればいいのかが、必ず 1 つの欄で答えられること。**
        # 根拠の形は読みの種類ごとに違うので、そこを人に探させない。
        assert item.provenance["page_numbers"], "どのページを見ればいいか分からない"


# =====================================================================
# 5. 読み直しても一覧が入れ替わらない
# =====================================================================


def test_reading_the_same_drawing_twice_samples_the_same_items(
    drawing: Path, tmp_path: Path
) -> None:
    """同じ図面・同じ案件なら、抜き取られる対象が変わらないこと。

    人が確かめている最中に一覧が入れ替わると、確かめた分が捨てられる。
    シードに時刻や実行回数を混ぜていないことを、ここで固定する。
    """
    stub_a = _StubOrchestrator({0: (2, "provisional_audit"), 1: (2, "provisional_audit")})
    stub_b = _StubOrchestrator({0: (2, "provisional_audit"), 1: (2, "provisional_audit")})
    first = read_drawing(_config(drawing, tmp_path), orchestrator=stub_a)
    second = read_drawing(_config(drawing, tmp_path), orchestrator=stub_b)

    assert first.audit_plan.seed == second.audit_plan.seed
    assert [c.target for c in first.audit_plan.plan_for(2).sampled] == [
        c.target for c in second.audit_plan.plan_for(2).sampled
    ]


def test_a_different_drawing_gets_a_different_seed(
    drawing: Path, tmp_path: Path
) -> None:
    """図面の中身が変われば、抜き取りのシードも変わること。"""
    other = tmp_path / "other.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(850, 780), "縮尺 1/50", fontname="japan", fontsize=11)
    doc.save(other)
    doc.close()

    first = read_drawing(_config(drawing, tmp_path))
    second = read_drawing(
        IntakeConfig(
            pdf_path=other, case_id="TEST-AUDIT-001",
            answers_path=tmp_path / "answers.json",
        )
    )

    assert first.audit_plan.seed != second.audit_plan.seed


# =====================================================================
# 6. 記録はリポジトリの外にしか書かない
# =====================================================================


def test_nothing_is_written_without_a_path(drawing: Path, tmp_path: Path) -> None:
    """記録のパスを渡さなければ、何も書かないこと。"""
    before = sorted(p.name for p in tmp_path.iterdir())
    read_drawing(_config(drawing, tmp_path))
    after = sorted(p.name for p in tmp_path.iterdir())

    assert after == before or after == sorted(before + ["answers.json"])


def test_the_record_is_written_where_it_was_asked_for(
    drawing: Path, tmp_path: Path
) -> None:
    """渡したパスに JSON で追記されること。"""
    log_path = tmp_path / "outside" / "audit.json"
    stub = _StubOrchestrator({0: (1, "auto_confirm"), 1: (2, "provisional_audit")})
    result = read_drawing(
        _config(drawing, tmp_path, audit_log_path=log_path), orchestrator=stub
    )

    assert log_path.exists()
    records = json.loads(log_path.read_text(encoding="utf-8"))
    assert len(records) == 1
    record = records[0]
    assert record["case_id"] == "TEST-AUDIT-001"
    assert record["source_fingerprint"] == result.source_fingerprint
    assert record["seed"] == result.audit_plan.seed
    assert record["tiers"]["1"]["population"] == 1
    assert record["tiers"]["2"]["sampled"][0]["provenance"]["page_numbers"]


def test_the_record_is_appended_not_overwritten(
    drawing: Path, tmp_path: Path
) -> None:
    """2 回目の読み取りが 1 回目の記録を消さないこと。"""
    log_path = tmp_path / "audit.json"
    read_drawing(_config(drawing, tmp_path, audit_log_path=log_path))
    read_drawing(_config(drawing, tmp_path, audit_log_path=log_path))

    assert len(json.loads(log_path.read_text(encoding="utf-8"))) == 2


def test_the_record_keeps_the_empty_population_too(
    drawing: Path, tmp_path: Path
) -> None:
    """母集団0件の回も記録に残ること。

    **残らないと「検査していない」と区別がつかない。** いまの経路は常に
    0件なので、この記録だけが「検査は走っている」ことの証拠になる。
    """
    log_path = tmp_path / "audit.json"
    read_drawing(_config(drawing, tmp_path, audit_log_path=log_path))

    record = json.loads(log_path.read_text(encoding="utf-8"))[0]
    assert record["tiers"]["1"]["status"] == "no_population"
    assert record["tiers"]["2"]["status"] == "no_population"
    assert record["tiers"]["2"]["sampled"] == []
