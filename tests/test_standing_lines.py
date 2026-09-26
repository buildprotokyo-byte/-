"""`estimating/standing_lines.py` の回帰テスト。

**図面からは決まらない見積の行**(仮設水道料・仮設電気料・小運搬費・荷上費・
墨出し・竣工時清掃・駐車場代)は、6 本の答案すべてで 0 件だった。
読み取りをどれだけ良くしても 1 行も埋まらない。図面の外の決まりが要る。

守りたいのは 5 つ。

1. **ルールを与えれば、図面に何も書かれていなくても行が出ること**
2. **ルールが無いときに、黙って 0 行にせず「ルールが欠けている」と示すこと。**
   これがいちばん大事である。いまは「その行が無い見積」と
   「その行のルールを持っていない見積」が見分けられない
3. **前提が足りないときも、行を捨てずに「前提が足りない」として残すこと**
4. **基準の中身が空の規則は、行を作るが数量を作らないこと**
   (おーちゃんの確認待ちのまま進めるため)
5. **図面からは決まらない行を自動で確定させないこと**

**規則の中身は架空のものだけを使う。** 本物らしい率や式を書くと、
測っているのが仕組みなのかルールの中身なのか分からなくなる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estimating.quantities import QuantityItem
from estimating.rules import RuleError, parse_rules
from estimating.standing_lines import (
    GAP_AMBIGUOUS_QUANTITY,
    GAP_EMPTY_BASIS,
    GAP_MISSING_QUANTITY,
    GAP_NO_STANDING_RULES,
    apply_standing_lines,
)

#: おーちゃんが挙げた、図面からは決まらない行。**名前だけで、率も式も入れない。**
FALLING_LINES = (
    "仮設水道料",
    "仮設電気料",
    "小運搬費",
    "荷上費",
    "墨出し",
    "竣工時清掃",
    "駐車場代",
)


def _ruleset(standing: list | None, version: int = 2):
    payload = {
        "format_version": version,
        "ruleset_id": "架空のルール",
        "description": "テスト用。**中身は架空で、実際の会社のルールではない。**",
        # 版 1 の見本は「立て行の規則を持っていない古いファイル」を表す。
        # 古いファイルにも当てはめの規則は入っているので 1 つ置く。
        "rules": [
            {
                "rule_id": "架空の規則",
                "kind": "建具数量",
                "unit_dimension": "count",
                "attributes": {},
                "line_items": [
                    {"code": "X-1", "work_item": "架空の行", "major_category": "架空", "unit": "箇所"}
                ],
            }
        ],
    }
    if standing is not None:
        payload["standing_lines"] = standing
    return parse_rules(payload)


def _all_lump_sum() -> list:
    return [
        {
            "standing_id": f"s{index}",
            "work_item": name,
            "major_category": "仮設工事" if "仮設" in name else "現場管理・諸経費",
            "unit": "式",
            "basis": {"kind": "一式", "quantity": 1},
        }
        for index, name in enumerate(FALLING_LINES)
    ]


def _area_quantity(value: float = 90.0) -> QuantityItem:
    """施工床面積の数量。**架空の値。**"""
    return QuantityItem(
        target="施工床面積::ページ1",
        value_range=(value, value),
        unit="㎡",
        method_id="pdf_text_area",
    )


def test_ルールを与えれば図面に無くても行が出る() -> None:
    result = apply_standing_lines(_ruleset(_all_lump_sum()))
    assert [line.work_item for line in result.lines] == list(FALLING_LINES)
    assert result.gaps == ()


def test_ルールが無いときは黙って0行にせずルールが欠けていると示す() -> None:
    """**これがいちばん大事。**

    「その行が無い見積」と「その行のルールを持っていない見積」は別のことである。
    黙って 0 行にすると、見積を読む人には見分けがつかない。
    """
    result = apply_standing_lines(_ruleset([]))
    assert result.lines == ()
    assert len(result.gaps) == 1
    assert result.gaps[0].kind == GAP_NO_STANDING_RULES
    assert "ルール" in result.gaps[0].message
    assert result.has_standing_rules is False
    assert "欠けて" in result.summary() or "与えられ" in result.summary()


def test_古い版のルールファイルでもルールが欠けていると示す() -> None:
    """版が古いだけで「行が無い」と読まれてはいけない。"""
    result = apply_standing_lines(_ruleset(None, version=1))
    assert result.has_standing_rules is False
    assert result.gaps[0].kind == GAP_NO_STANDING_RULES


def _area_rule(per_unit=1.0) -> list:
    return [
        {
            "standing_id": "s-area",
            "work_item": "竣工時清掃",
            "major_category": "現場管理・諸経費",
            "unit": "㎡",
            "basis": {"kind": "数量参照", "target_kind": "施工床面積", "per_unit": per_unit},
        }
    ]


def test_もとになる数量が無い行は捨てずに残す() -> None:
    result = apply_standing_lines(_ruleset(_area_rule()))
    # 行は出るが、数量は入らない。
    assert len(result.lines) == 1
    assert result.lines[0].work_item == "竣工時清掃"
    assert result.lines[0].quantity_range is None
    assert any(gap.kind == GAP_MISSING_QUANTITY for gap in result.gaps)
    assert "施工床面積" in result.gaps[0].message


def test_もとになる数量が複数あるときは選ばない() -> None:
    result = apply_standing_lines(
        _ruleset(_area_rule()),
        quantities=(_area_quantity(90.0), _area_quantity(95.0)),
    )
    assert result.lines[0].quantity_range is None
    assert any(gap.kind == GAP_AMBIGUOUS_QUANTITY for gap in result.gaps)


def test_もとになる数量が揃えば数量が入る() -> None:
    result = apply_standing_lines(_ruleset(_area_rule()), quantities=(_area_quantity(90.0),))
    assert result.lines[0].quantity_range == pytest.approx((90.0, 90.0))
    assert result.lines[0].source_targets == ("施工床面積::ページ1",)
    assert result.gaps == ()


def test_基準の中身が空の規則は行だけ作って数量を作らない() -> None:
    """**おーちゃんの確認待ちのまま進めるための形。**

    率や式が決まっていなくても、行の存在だけは見積に載せられる。
    数量を勝手に 1 で埋めない。
    """
    result = apply_standing_lines(
        _ruleset(_area_rule(per_unit=None)), quantities=(_area_quantity(90.0),)
    )
    assert len(result.lines) == 1
    assert result.lines[0].quantity_range is None
    assert any(gap.kind == GAP_EMPTY_BASIS for gap in result.gaps)


def test_図面からは決まらない行を自動で確定させない() -> None:
    result = apply_standing_lines(_ruleset(_all_lump_sum()))
    assert all(not line.settled for line in result.lines)
    assert all(line.basis == "図面の外の決まりに基づく" for line in result.lines)
    assert all(line.requires_human_confirmation for line in result.lines)


def test_知らない項目の規則は断る() -> None:
    with pytest.raises(RuleError):
        _ruleset([{"standing_id": "x", "work_item": "a", "major_category": "b",
                   "unit": "式", "basis": {"kind": "一式", "quantity": 1},
                   "未知の項目": 1}])


def test_知らない基準の種類は断る() -> None:
    with pytest.raises(RuleError):
        _ruleset([{"standing_id": "x", "work_item": "a", "major_category": "b",
                   "unit": "式", "basis": {"kind": "知らない種類"}}])


def test_知らない単位は断る() -> None:
    with pytest.raises(RuleError):
        _ruleset([{"standing_id": "x", "work_item": "a", "major_category": "b",
                   "unit": "人工", "basis": {"kind": "一式", "quantity": 1}}])


def test_版1のファイルに立て行を書いたら断る() -> None:
    """版を上げずに新しい項目を書いたファイルを、半分だけ読まない。"""
    with pytest.raises(RuleError):
        _ruleset(_all_lump_sum(), version=1)


def test_見本の規則ファイルが読める(tmp_path: Path) -> None:
    """リポジトリに置く見本は**架空**であること。読めることだけを確かめる。"""
    from estimating.rules import load_rules

    path = Path(__file__).resolve().parent.parent / "estimating" / "examples" / "synthetic_standing_rules.json"
    ruleset = load_rules(path)
    assert ruleset.standing_lines
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "架空" in payload["description"]
