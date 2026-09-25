"""周36 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import benchmarks.measure_declared_phase as m
from axes.reading.meaning import PHASE_UNKNOWN
from benchmarks.measure_declared_phase import (
    DECLARED_KIND,
    EVEN_PAGE_PHASE,
    ODD_PAGE_PHASE,
    confirmed,
    decided,
    declarations_for,
    phase_counts,
    phase_ruleset,
)
from estimating.rules import load_rules
from intake.start_kit import PAGE_KINDS, PAGE_PHASES


def item(phase, is_confirmed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(phase=phase, is_confirmed=is_confirmed, kind="あ")


def test_渡す位相は許された値である() -> None:
    assert ODD_PAGE_PHASE in PAGE_PHASES
    assert EVEN_PAGE_PHASE in PAGE_PHASES
    assert DECLARED_KIND in PAGE_KINDS


def test_渡す位相は現況と計画の2種類だけ() -> None:
    # 値に意味は無いが、不明を混ぜると線1 が測れなくなる。
    assert PHASE_UNKNOWN not in (ODD_PAGE_PHASE, EVEN_PAGE_PHASE)
    assert ODD_PAGE_PHASE != EVEN_PAGE_PHASE


def test_全ページぶんの宣言ができる() -> None:
    got = declarations_for(5)
    assert [d.page_number for d in got] == [1, 2, 3, 4, 5]


def test_奇数と偶数で位相が分かれる() -> None:
    got = {d.page_number: d.phase for d in declarations_for(4)}
    assert got == {1: ODD_PAGE_PHASE, 2: EVEN_PAGE_PHASE, 3: ODD_PAGE_PHASE, 4: EVEN_PAGE_PHASE}


def test_ページが無ければ宣言も無い() -> None:
    assert declarations_for(0) == ()


def test_不明は決まったと数えない() -> None:
    assert decided([item(PHASE_UNKNOWN)]) == 0


def test_意味そのものが無いものも決まったと数えない() -> None:
    assert decided([item(None)]) == 0


def test_現況は決まったと数える() -> None:
    assert decided([item("現況"), item("計画"), item(None)]) == 2


def test_位相の内訳は意味なしを分けて数える() -> None:
    got = phase_counts([item(None), item(PHASE_UNKNOWN), item("現況")])
    assert got == {"意味なし": 1, PHASE_UNKNOWN: 1, "現況": 1}


def test_自動確定を数える() -> None:
    assert confirmed([item("現況", True), item("現況", False)]) == 1


def test_合成の規則は現況を条件にする(tmp_path: Path) -> None:
    path = phase_ruleset(["あ", "い"], tmp_path / "r.json")
    ruleset = load_rules(path)
    assert len(ruleset.rules) == 2
    assert all(rule.phase == ("現況",) for rule in ruleset.rules)


def test_同じ種類が並んでも規則は1本になる(tmp_path: Path) -> None:
    ruleset = load_rules(phase_ruleset(["あ", "あ"], tmp_path / "r.json"))
    assert len(ruleset.rules) == 1


def test_合成の規則には実案件に使わない断りが入っている(tmp_path: Path) -> None:
    payload = json.loads(
        phase_ruleset(["あ"], tmp_path / "r.json").read_text(encoding="utf-8")
    )
    assert "実案件には使わない" in payload["description"]


def test_合成の規則は版3である(tmp_path: Path) -> None:
    # phase を条件に書けるのは版 3 から。
    payload = json.loads(
        phase_ruleset(["あ"], tmp_path / "r.json").read_text(encoding="utf-8")
    )
    assert payload["format_version"] == 3


# --- 追記1: 関門を閉じない種類 ---


def test_追記1の種類は関門をどれも閉じない():
    """`平面図` は開き戸の円弧と繰り返し記号のどちらの関門も通す。"""
    from intake.start_kit import (
        DOOR_ARC_EXPECTED_PAGE_KINDS,
        REPEATED_SYMBOL_PAGE_KINDS,
    )

    assert m.NEUTRAL_KIND in DOOR_ARC_EXPECTED_PAGE_KINDS
    assert m.NEUTRAL_KIND in REPEATED_SYMBOL_PAGE_KINDS


def test_1回目の種類はどちらの関門も閉じる():
    """**`その他` は当たり障りのない値ではない。** これが線2 が落ちた理由。"""
    from intake.start_kit import (
        DOOR_ARC_EXPECTED_PAGE_KINDS,
        REPEATED_SYMBOL_PAGE_KINDS,
    )

    assert m.DECLARED_KIND not in DOOR_ARC_EXPECTED_PAGE_KINDS
    assert m.DECLARED_KIND not in REPEATED_SYMBOL_PAGE_KINDS


def test_宣言の種類は差し替えられ位相の振り方は変わらない():
    既定 = m.declarations_for(4)
    追記 = m.declarations_for(4, m.NEUTRAL_KIND)

    assert [d.kind for d in 既定] == [m.DECLARED_KIND] * 4
    assert [d.kind for d in 追記] == [m.NEUTRAL_KIND] * 4
    assert [d.phase for d in 既定] == [d.phase for d in 追記]
