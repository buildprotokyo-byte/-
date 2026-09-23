"""意味の 4 欄(`axes/reading/meaning.py`)の試験。

原則 2 は「読み取った値には、必ず意味を付ける」で、おーちゃんの回答9
(2026-09-22)により**新しく書くコードでは必須**になった。

ここで固定するのは 3 つ。

1. **4 欄のどれも空では作れない。** 空文字で通ると「意味を付けた」ことに
   なってしまう。
2. **まだ決まっていない欄は、決まっていないと書く。** `不明` `目的未確立`。
   これは埋め忘れではなく記録である。
3. **`is_complete` は、決まっていない欄があれば False。**
"""

from __future__ import annotations

import pytest

from axes.reading.meaning import (
    PHASE_UNKNOWN,
    PURPOSE_UNESTABLISHED,
    Meaning,
)


def test_all_four_columns_are_required() -> None:
    with pytest.raises(TypeError):
        Meaning(what="縮尺", where="ページ1", phase="現況")  # type: ignore[call-arg]


@pytest.mark.parametrize("field", ["what", "where", "phase", "purpose_link"])
def test_an_empty_column_is_rejected(field: str) -> None:
    values = {
        "what": "専有延床面積",
        "where": "ページ1の表題欄",
        "phase": "現況",
        "purpose_link": "水回りの改修範囲",
    }
    values[field] = "   "
    with pytest.raises(ValueError):
        Meaning(**values)


def test_an_unknown_phase_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        Meaning(
            what="専有延床面積",
            where="ページ1",
            phase="たぶん計画",
            purpose_link=PURPOSE_UNESTABLISHED,
        )


def test_undecided_columns_are_recorded_not_left_blank() -> None:
    meaning = Meaning(
        what="専有延床面積",
        where="ページ1の表題欄",
        phase=PHASE_UNKNOWN,
        purpose_link=PURPOSE_UNESTABLISHED,
    )
    assert meaning.is_complete is False
    assert meaning.unresolved == ("phase", "purpose_link")
    assert meaning.as_dict()["unresolved"] == ["phase", "purpose_link"]


def test_a_fully_decided_meaning_is_complete() -> None:
    meaning = Meaning(
        what="専有延床面積",
        where="ページ1の表題欄",
        phase="計画",
        purpose_link="水回りの改修範囲を決めるための面積",
    )
    assert meaning.is_complete is True
    assert meaning.unresolved == ()
