"""K-33 の採点の試験。**合成データだけ。実図面の名前は 1 つも書かない。**

基準は `docs/k33_prompt_set_reading_criteria.md`(追記1 まで)。
ここで固定するのは、そろえ方・60% の下限・囮の扱い・4 区分の数え方である。
"""

from __future__ import annotations

import json

import pytest

from benchmarks.score_k33_prompt_set_reading import (
    CONTAIN_MIN_RATIO,
    is_correct,
    is_near,
    load_runs,
    normalize,
    score_run,
    summarize,
)


class Testそろえ方:
    def test_全角と半角をそろえる(self) -> None:
        assert normalize("ＡＢＣ１２３") == normalize("abc123")

    def test_中黒と括弧と空白を取る(self) -> None:
        assert normalize("あ・い (う) え　お") == "あいうえお"

    def test_英字は小文字にする(self) -> None:
        assert normalize("LED") == "led"


class Test名前の合わせ方:
    def test_そろえたあと一致すれば正しい(self) -> None:
        assert is_correct("Ａ・Ｂ", "ab")

    def test_短いほうが六割以上を占める包含は正しい(self) -> None:
        # 4 文字 / 6 文字 = 0.666… で下限を超える
        assert 4 / 6 >= CONTAIN_MIN_RATIO
        assert is_correct("あいうえおか", "あいうえ")

    def test_短いほうが六割に届かない包含は外れ(self) -> None:
        # 3 文字 / 8 文字 = 0.375 で下限に届かない
        assert 3 / 8 < CONTAIN_MIN_RATIO
        assert not is_correct("あいうえおかきく", "あいう")

    def test_下限ちょうどは正しいに入れる(self) -> None:
        # 3 文字 / 5 文字 = 0.6 ちょうど
        assert is_correct("あいうえお", "あいう")

    def test_空文字は正しいにしない(self) -> None:
        assert not is_correct("", "あい")
        assert not is_correct("あい", "")


def _answers() -> list[dict[str, str]]:
    return [
        {"id": "S-001", "kind": "群A 文字あり", "name": "いぬ"},
        {"id": "S-002", "kind": "群B 図形だけ", "name": "ねこ"},
        {"id": "S-003", "kind": "群B 図形だけ", "name": "うさぎ"},
        {"id": "S-004", "kind": "囮 空の升目", "name": None},
        {"id": "S-005", "kind": "群C 決まらない", "name": "きつね"},
    ]


class Test採点:
    def test_正しいと外れと読めなかったを分ける(self) -> None:
        rows = {
            "S-001": {"id": "S-001", "name": "いぬ"},
            "S-002": {"id": "S-002", "name": "とり"},
            "S-003": {"id": "S-003", "name": None, "unreadable": 2},
            "S-004": {"id": "S-004", "name": None, "unreadable": 1},
            "S-005": {"id": "S-005", "name": "きつね"},
        }
        tally = score_run(rows, _answers())
        assert tally["群"]["群A 文字あり"]["正しい"] == 1
        assert tally["群"]["群B 図形だけ"]["外れ"] == 1
        assert tally["群"]["群B 図形だけ"]["読めなかった"] == 1
        assert tally["答えた件数"] == 3
        assert tally["捏造"] == 0

    def test_囮に名前が入れば正誤を問わず捏造(self) -> None:
        rows = {"S-004": {"id": "S-004", "name": "なにか"}}
        tally = score_run(rows, _answers())
        assert tally["捏造"] == 1
        assert tally["群"]["囮 空の升目"]["正しい"] == 0

    def test_読めなかったの4区分を数える(self) -> None:
        rows = {
            "S-002": {"id": "S-002", "name": None, "unreadable": 3},
            "S-003": {"id": "S-003", "name": None, "unreadable": 3},
        }
        tally = score_run(rows, _answers())
        assert tally["読めなかった4区分"][3] == 2

    def test_区分の無い読めなかったは振り分け無しに入れる(self) -> None:
        rows = {"S-002": {"id": "S-002", "name": None}}
        tally = score_run(rows, _answers())
        assert tally["読めなかった4区分"]["振り分け無し"] == 1

    def test_1から4の外の区分は振り分け無しに倒す(self) -> None:
        rows = {"S-002": {"id": "S-002", "name": None, "unreadable": 9}}
        tally = score_run(rows, _answers())
        assert tally["読めなかった4区分"]["振り分け無し"] == 1

    def test_行が無いものは欠けた件数に入れる(self) -> None:
        tally = score_run({}, _answers())
        assert tally["欠けた件数"] == len(_answers())
        assert tally["答えた件数"] == 0

    def test_空白だけの名前は読めなかったに倒す(self) -> None:
        rows = {"S-002": {"id": "S-002", "name": "   ", "unreadable": 1}}
        tally = score_run(rows, _answers())
        assert tally["群"]["群B 図形だけ"]["読めなかった"] == 1
        assert tally["答えた件数"] == 0


class Testまとめ:
    def test_条件ごとに中央値と幅を出す(self) -> None:
        scored = {
            "answers_P1": score_run({"S-002": {"id": "S-002", "name": "ねこ"}}, _answers()),
            "answers_P2": score_run({}, _answers()),
            "answers_P3": score_run({"S-002": {"id": "S-002", "name": "ねこ"}}, _answers()),
            "answers_Q1": score_run({"S-002": {"id": "S-002", "name": "ねこ"}}, _answers()),
        }
        before = summarize(scored, "answers_P")
        assert before["回数"] == 3
        got = before["群ごとの正しい件数"]["群B 図形だけ"]
        assert got["中央値"] == 1
        assert got["最小"] == 0
        assert got["最大"] == 1

    def test_当たる条件が無ければ空(self) -> None:
        assert summarize({}, "answers_P") == {}


class Test読み込み:
    def test_idの無い行は落とす(self, tmp_path) -> None:
        path = tmp_path / "answers_P1.json"
        path.write_text(
            json.dumps([{"id": "S-001", "name": "いぬ"}, {"name": "みだし"}]),
            encoding="utf-8",
        )
        runs = load_runs([path])
        assert list(runs["answers_P1"]) == ["S-001"]


@pytest.mark.parametrize("given,truth", [("いぬ", "いぬ"), ("イヌ", "イヌ")])
def test_同じ文字列は必ず正しい(given: str, truth: str) -> None:
    assert is_correct(given, truth)


# ---------------------------------------------------------------------------
# 追記2 の「近い」(2-gram の Dice 係数 0.5 以上)
# ---------------------------------------------------------------------------


def test_近いは完全一致を必ず含む() -> None:
    assert is_near("いぬ", "いぬ")
    assert is_near("しろいいぬ", "しろいいぬ")


def test_近いは送り仮名が1文字違うだけを当てる() -> None:
    # 「引掛ローゼット」と「引掛けローゼット」と同じ形。
    assert is_near("あかいいぬごや", "あかいおおいぬごや") is True
    assert is_correct("あかいいぬごや", "あかいおおいぬごや") is False


def test_近いは語順が入れ替わっただけを当てる() -> None:
    # 「2口コンセント」と「コンセント(2口)」と同じ形。
    assert is_near("にひきいぬ", "いぬにひき") is True
    assert is_correct("にひきいぬ", "いぬにひき") is False


def test_近いは別のものを当てない() -> None:
    assert is_near("いぬ", "うさぎ") is False
    assert is_near("しろいいぬ", "きつねのこども") is False


def test_近いは言い換えを当てない() -> None:
    # 文字が重ならない同義語は、この当て方では当たらない(基準の追記2 に明記)。
    assert is_near("いぬ", "けん") is False


def test_近いは短すぎる答えを当てない() -> None:
    assert is_near("いぬ", "しろいいぬごや") is False


def test_近いは空を当てない() -> None:
    assert is_near("", "いぬ") is False
    assert is_near("いぬ", "") is False


def test_採点は正しいを近いにも数える() -> None:
    answers = [{"id": "S-001", "kind": "群A 文字あり", "name": "いぬ"}]
    rows = {"S-001": {"id": "S-001", "name": "いぬ"}}
    tally = score_run(rows, answers)
    assert tally["群"]["群A 文字あり"]["正しい"] == 1
    assert tally["群"]["群A 文字あり"]["近い(追記2)"] == 1
    assert tally["群"]["群A 文字あり"]["外れ"] == 0


def test_採点は近いだけのものを外れにも数える() -> None:
    # **追記2 は補助の数字なので、「外れ」の件数は追記1 のままでなければならない。**
    answers = [{"id": "S-001", "kind": "群A 文字あり", "name": "いぬにひき"}]
    rows = {"S-001": {"id": "S-001", "name": "にひきいぬ"}}
    tally = score_run(rows, answers)
    assert tally["群"]["群A 文字あり"]["正しい"] == 0
    assert tally["群"]["群A 文字あり"]["近い(追記2)"] == 1
    assert tally["群"]["群A 文字あり"]["外れ"] == 1
