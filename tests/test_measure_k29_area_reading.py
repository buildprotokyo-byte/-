"""K-29 の採点の試験。**合成データだけ**を使う。

実案件の室名・見積の数量はこのファイルにも、リポジトリのどこにも書かない。
"""

from __future__ import annotations

import json

import pytest

from benchmarks.measure_k29_area_reading import (
    BASIS_BAND,
    TOLERANCE,
    golden_area_rows,
    room_keys,
    score,
    truth_for,
)

ROOMS = [
    {"id": "R-01", "name": "合成室ア", "decoy": False},
    {"id": "R-02", "name": "合成室イ", "decoy": False},
    {"id": "R-03", "name": "合成の囮室", "decoy": True},
]


def _golden(tmp_path, items):
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"expected_items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _row(code, work_item, quantity, unit="㎡", terms=()):
    return {
        "code": code,
        "work_item": work_item,
        "quantity": quantity,
        "unit": unit,
        "trigger_terms": list(terms),
    }


def _answer(kind="面積を言えた", area=None, basis="不明", used=(("x",),), why="理由"):
    return {
        "kind": kind,
        "area_sqm": area,
        "used_numbers": [{"page": 1, "text": "1,000", "where": "外周"}] if used else [],
        "why": why,
        "formula": "1000 x 1000",
        "basis": basis,
        "reason": "",
    }


class Test正解の取り方:
    def test_単位が面積でない行は正解にしない(self, tmp_path):
        path = _golden(tmp_path, [_row("A", "合成室ア 床", 10.0, unit="m")])
        assert golden_area_rows(path) == []

    def test_品目名に床が無い行は正解にしない(self, tmp_path):
        path = _golden(tmp_path, [_row("A", "合成室ア 壁", 10.0)])
        assert golden_area_rows(path) == []

    def test_数量が無い行は正解にしない(self, tmp_path):
        path = _golden(tmp_path, [_row("A", "合成室ア 床", None)])
        assert golden_area_rows(path) == []

    def test_手がかり語からも室を当てる(self, tmp_path):
        path = _golden(tmp_path, [_row("A", "床の解体", 10.0, terms=("合成室ア",))])
        value, state, codes = truth_for(golden_area_rows(path), "合成室ア")
        assert (value, state, codes) == (10.0, "あり", ["A"])

    def test_合う行が無ければ正解なし(self, tmp_path):
        path = _golden(tmp_path, [_row("A", "合成室イ 床", 10.0)])
        value, state, _ = truth_for(golden_area_rows(path), "合成室ア")
        assert (value, state) == (None, "正解なし")

    def test_2行が近ければ中央値を正解にする(self, tmp_path):
        path = _golden(
            tmp_path, [_row("A", "合成室ア 床解体", 10.0), _row("B", "合成室ア 床新設", 10.2)]
        )
        value, state, _ = truth_for(golden_area_rows(path), "合成室ア")
        assert state == "あり"
        assert value == pytest.approx(10.1)

    def test_2行がばらけたら1つに決めない(self, tmp_path):
        path = _golden(
            tmp_path, [_row("A", "合成室ア 床解体", 10.0), _row("B", "合成室ア 床新設", 30.0)]
        )
        value, state, _ = truth_for(golden_area_rows(path), "合成室ア")
        assert (value, state) == (None, "正解が1つに決まらない")

    def test_改行で複数の室名が入った升目は行ごとに割る(self):
        assert room_keys("合成室ア\n合成室イ") == ("合成室ア", "合成室イ")


class Test採点:
    def _counts(self, tmp_path, answers, items=None):
        items = items if items is not None else [_row("A", "合成室ア 床", 10.0)]
        rows = golden_area_rows(_golden(tmp_path, items))
        counts, _ = score(ROOMS, answers, rows)
        return counts

    def test_許容差の中なら合った(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=10.0 * (1 + TOLERANCE))})
        assert counts["合った"] == 1
        assert counts["外れた"] == 0

    def test_許容差のすぐ外は外れた(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=10.0 * (1 + TOLERANCE) + 0.01)})
        assert counts["合った"] == 0
        assert counts["外れた"] == 1

    def test_芯々の申告で上振れは基準の違いに数える(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=12.0, basis="芯々")})
        assert counts["基準の違いで説明が付く外れ"] == 1
        assert counts["合った"] == 0
        assert counts["外れた"] == 0

    def test_芯々でも幅の外なら外れた(self, tmp_path):
        high = 10.0 * (1 + BASIS_BAND[1]) + 1.0
        counts = self._counts(tmp_path, {"R-01": _answer(area=high, basis="芯々")})
        assert counts["基準の違いで説明が付く外れ"] == 0
        assert counts["外れた"] == 1

    def test_内法の申告で下振れは基準の違いに数える(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=8.0, basis="内法")})
        assert counts["基準の違いで説明が付く外れ"] == 1

    def test_基準が不明なら幅の中でも外れた(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=12.0, basis="不明")})
        assert counts["基準の違いで説明が付く外れ"] == 0
        assert counts["外れた"] == 1

    def test_根拠が空の数字はでたらめに数える(self, tmp_path):
        counts = self._counts(
            tmp_path, {"R-01": _answer(area=10.0, used=(), why="")}
        )
        assert counts["でたらめ"] == 1

    def test_根拠があればでたらめに数えない(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(area=10.0)})
        assert counts["でたらめ"] == 0

    def test_囮に面積が付いたら数える(self, tmp_path):
        counts = self._counts(tmp_path, {"R-03": _answer(area=5.0)})
        assert counts["囮に面積"] == 1
        assert counts["合った"] == 0

    def test_囮に質疑は別に数える(self, tmp_path):
        counts = self._counts(tmp_path, {"R-03": _answer(kind="質疑にあたる", area=None)})
        assert counts["囮に質疑"] == 1
        assert counts["囮に面積"] == 0

    def test_見積に行が無い室は正解なしに数える(self, tmp_path):
        counts = self._counts(tmp_path, {"R-02": _answer(area=10.0)})
        assert counts["正解なし"] == 1
        assert counts["合った"] == 0
        assert counts["外れた"] == 0

    def test_答えが返ってこない室は答えなかったに数える(self, tmp_path):
        counts = self._counts(tmp_path, {})
        assert counts["答えなかった"] == 3

    def test_読めないは合ったにも外れたにも数えない(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(kind="読めない", area=None)})
        assert counts["読めない"] == 1
        assert counts["合った"] == 0
        assert counts["外れた"] == 0

    def test_質疑は読めないに混ぜない(self, tmp_path):
        counts = self._counts(tmp_path, {"R-01": _answer(kind="質疑にあたる", area=None)})
        assert counts["質疑にあたる"] == 1
        assert counts["読めない"] == 0


class Test室ごとの表の置き場所:
    def test_共有フォルダの外には書かない(self, tmp_path):
        from benchmarks.measure_k29_area_reading import _write_detail

        with pytest.raises(SystemExit):
            _write_detail(tmp_path / "detail.md", {"条件": []})
