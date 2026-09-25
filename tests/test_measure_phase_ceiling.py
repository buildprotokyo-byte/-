"""周37 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

import json
from pathlib import Path

import benchmarks.measure_phase_ceiling as m
from axes.reading.meaning import (
    PHASE_EXISTING,
    PHASE_PLANNED,
    PURPOSE_UNESTABLISHED,
    Meaning,
)
from estimating.quantities import QuantityItem
from estimating.rules import load_rules


def 数量(target="建具::WD-01", page=1, meaning=None, value=(2.0, 2.0)):
    return QuantityItem(
        target=target,
        value_range=value,
        unit="箇所",
        axis_id="drawing",
        method_id="test",
        derivation="read",
        meaning=meaning,
        provenance={"page_number": page},
    )


def test_器が無い数量にも位相が入る():
    out = m.with_phase([数量()], "全部現況")
    assert out[0].meaning is not None
    assert out[0].phase == PHASE_EXISTING


def test_元の数量は変わらない():
    もと = 数量()
    m.with_phase([もと], "全部現況")
    assert もと.meaning is None
    assert もと.phase is None


def test_器がある数量は位相だけ差し替える():
    もと = 数量(
        meaning=Meaning(
            what="建具", where="ページ3の建具表", phase="不明", purpose_link="目的未確立"
        )
    )
    out = m.with_phase([もと], "全部計画")[0]
    assert out.meaning.what == "建具"
    assert out.meaning.where == "ページ3の建具表"
    assert out.phase == PHASE_PLANNED


def test_作る器は読めていない欄を埋めない():
    out = m.with_phase([数量(target="建具::WD-01", page=7)], "全部現況")[0]
    assert out.meaning.what == "建具"
    assert out.meaning.where == "ページ7"
    assert out.meaning.purpose_link == PURPOSE_UNESTABLISHED


def test_ページが分からなければそう書く():
    item = QuantityItem(
        target="建具::WD-01",
        value_range=(1.0, 1.0),
        unit="箇所",
        axis_id="drawing",
        method_id="test",
        derivation="read",
    )
    assert m.with_phase([item], "全部現況")[0].meaning.where == "ページ不明"


def test_交互の振り方は奇数が現況で偶数が計画():
    奇 = m.with_phase([数量(page=3)], "ページ番号で交互")[0]
    偶 = m.with_phase([数量(page=4)], "ページ番号で交互")[0]
    assert 奇.phase == PHASE_EXISTING
    assert 偶.phase == PHASE_PLANNED


def test_3通りは全部ちがう振り方():
    assert len(set(m.ASSIGNMENTS)) == 3


def test_合成の規則は位相を条件に持つ(tmp_path: Path):
    path = m.phase_ruleset(["建具"], tmp_path / "r.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    位相 = [r["phase"] for r in payload["rules"]]
    assert [PHASE_EXISTING] in 位相
    assert [PHASE_PLANNED] in 位相
    assert load_rules(path).rules


def test_合成の規則は実案件に使わないと書いてある(tmp_path: Path):
    path = m.phase_ruleset(["建具"], tmp_path / "r.json")
    assert "実案件には使わない" in path.read_text(encoding="utf-8")


def test_いまある見本の規則は位相を条件に持たない():
    """**追記1 で読んだこと。**これが変われば周37 の読み方も変わる。"""
    for path in m.EXISTING_RULES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert all("phase" not in rule for rule in payload.get("rules", []))


def test_撤去の行を文言で見分ける():
    assert m.demolition_lines(("r|c|合成の撤去の行|箇所|1|1",)) == 1
    assert m.demolition_lines(("r|c|合成の新設の行|箇所|1|1",)) == 0


def test_指紋は値まで含む():
    """値が変われば囮の突き合わせで違いとして出る。"""

    class _行:
        rule_id, code, work_item, unit = "r", "c", "w", "箇所"
        value_range = (1.0, 2.0)

    class _対応:
        lines = (_行(),)

    class _下書き:
        mapping = type("M", (), {"mappings": (_対応(),)})()

    assert m.line_fingerprints(_下書き()) == ("r|c|w|箇所|1.0|2.0",)


def test_ページが取れなければ0():
    assert m.page_of(数量(page=1)) == 1

    class _無し:
        provenance: dict = {}

    assert m.page_of(_無し()) == 0


# --- 追記2: 器を新しく作らない(いまの上限) ---


def test_器を作らない指定では器の無い数量に位相は入らない():
    もと = 数量()
    out = m.with_phase([もと], "全部現況", create_containers=False)[0]
    assert out.meaning is None
    assert out.phase is None


def test_器を作らない指定でも器がある数量には入る():
    もと = 数量(
        meaning=Meaning(
            what="建具", where="ページ3", phase="不明", purpose_link="目的未確立"
        )
    )
    out = m.with_phase([もと], "全部現況", create_containers=False)[0]
    assert out.phase == PHASE_EXISTING


def test_器を作らない指定でも件数は減らない():
    items = [数量(), 数量(meaning=Meaning(what="建具", where="p", phase="不明", purpose_link="目的未確立"))]
    assert len(m.with_phase(items, "全部現況", create_containers=False)) == 2
