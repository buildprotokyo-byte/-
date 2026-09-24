"""凡例の対照表で**照合する**部品のテスト(K-20)。

ここで使う対照表は**全部その場で作った合成データ**である。記号も名前も意味も、
**実図面の凡例には無い、この試験のためだけの語**にしてある。実図面から写した表は
共有フォルダにあり、リポジトリには入れない。

このテストが守らせたいことは 1 つだけ。**名前を作らないこと。**11 周目は 1 つの
名前に 474 通りの形を付けた。あれは「近いものを探して当てにいった」結果なので、
ここでは**完全一致だけを一致とし、決まらないものは「不明」**にする。
"""

from __future__ import annotations

import json

import pytest

from axes.image_axis.legend_lookup import (
    KIND_EQUIPMENT,
    KIND_LINE_COLOR,
    KIND_LINE_STYLE,
    KIND_WORK,
    REASON_AMBIGUOUS,
    REASON_NOT_IN_TABLE,
    REASON_NO_SAMPLE,
    REASON_UNDISTINGUISHABLE,
    UNKNOWN,
    LegendTable,
    match_line_colors,
    match_line_styles,
    match_marks,
    summarize,
)


def _table(**overrides) -> LegendTable:
    """**合成の対照表。**語はすべてこの試験のための作り物である。"""
    payload = {
        "binding": "案件の凡例",
        "work_marks": [
            {"code": "ア", "meaning": "合成の意味・その一", "source_page": 6},
            {"code": "イ", "meaning": "合成の意味・その二", "source_page": 6},
        ],
        "symbols": [
            {
                "code": "XQ7",
                "name": "合成の器具(甲)",
                "group": "合成の群",
                "source_page": 22,
            }
        ],
        "line_colors": [
            {
                "color": [0.0, 1.0, 0.0],
                "label": "緑色",
                "meaning": "合成の線の意味",
                "source_page": 6,
            }
        ],
        "line_styles": [],
    }
    payload.update(overrides)
    return LegendTable.from_payload(payload)


def test_exact_text_gets_the_legend_meaning() -> None:
    """凡例が自分で書いている対だけを、そのまま返す。"""
    (match,) = match_marks(["ア"], _table())
    assert match.name == "ア"
    assert match.meaning == "合成の意味・その一"
    assert match.kind == KIND_WORK
    assert match.source_page == 6


def test_equipment_symbol_keeps_its_group() -> None:
    """凡例の小見出し(どの設備か)も落とさない。"""
    (match,) = match_marks(["XQ7"], _table())
    assert (match.name, match.group, match.kind) == (
        "合成の器具(甲)",
        "合成の群",
        KIND_EQUIPMENT,
    )


def test_partial_text_is_not_a_match() -> None:
    """**壊し試験の的。**「アイウ」は「ア」に当たらない。

    引き当てを部分一致に緩めると、ここが当たってしまう。11 周目の壊れ方そのもの
    なので、緩めたら落ちる形で固定しておく。
    """
    (match,) = match_marks(["アイウ"], _table())
    assert match.name is None
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_NOT_IN_TABLE


def test_text_missing_from_the_table_is_unknown() -> None:
    (match,) = match_marks(["ヲヲヲ"], _table())
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_NOT_IN_TABLE


def test_one_code_with_two_names_is_unknown() -> None:
    """同じ記号が 2 つの名前を指すなら、どちらを選んでも捏造になる。"""
    table = _table(
        symbols=[
            {"code": "ZZ", "name": "合成の器具(乙)", "group": "", "source_page": 22},
            {"code": "ZZ", "name": "合成の器具(丙)", "group": "", "source_page": 22},
        ]
    )
    (match,) = match_marks(["ZZ"], table)
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_AMBIGUOUS


def test_the_same_pair_on_two_pages_is_one_entry() -> None:
    """同じ表の同じ対が 2 ページに出ても、**別々の証言として数えない。**"""
    table = _table(
        line_colors=[],
        work_marks=[
            {"code": "ア", "meaning": "合成の意味・その一", "source_page": 6},
            {"code": "ア", "meaning": "合成の意味・その一", "source_page": 22},
        ],
    )
    (match,) = match_marks(["ア"], table)
    assert match.name == "ア"
    assert match.source_pages == (6, 22)


def test_width_and_spacing_do_not_change_the_answer() -> None:
    """全角・半角と空白のゆれだけは同じとみなす(NFKC)。**語の中身は変えない。**"""
    (match,) = match_marks(["ＸＱ７"], _table())
    assert match.name == "合成の器具(甲)"


def test_an_empty_table_names_nothing() -> None:
    """対照表を空にしたら 1 件も名前が付かない(C3 の対照)。"""
    table = _table(work_marks=[], symbols=[], line_colors=[], line_styles=[])
    matches = match_marks(["ア", "XQ7"], table)
    assert [m.name for m in matches] == [None, None]


def test_summarize_counts_what_the_report_needs() -> None:
    matches = match_marks(["ア", "XQ7", "ヲヲヲ", "アイウ"], _table())
    counts = summarize(matches)
    assert counts.total == 4
    assert counts.named == 2
    assert counts.unknown == 2
    assert counts.by_reason[REASON_NOT_IN_TABLE] == 2


def test_line_styles_without_a_sample_are_all_unknown() -> None:
    """凡例に線種の見本が無ければ、線がいくつ来ても 1 件も名前が付かない。"""
    matches = match_line_styles([(3.0, 1.5), (1.0, 1.0)], _table())
    assert [m.display_name for m in matches] == [UNKNOWN, UNKNOWN]
    assert {m.reason for m in matches} == {REASON_NO_SAMPLE}
    assert {m.kind for m in matches} == {KIND_LINE_STYLE}


def test_line_style_matches_only_when_the_ratio_agrees() -> None:
    """おーちゃんの決め(K-20 4 番): **刻みの比率が合うものだけ**を一致とする。"""
    table = _table(
        line_styles=[
            {"label": "合成の線種", "dashes": [4.0, 2.0], "source_page": 6},
        ]
    )
    same_ratio, other_ratio = match_line_styles([(8.0, 4.0), (4.0, 4.0)], table)
    assert same_ratio.name == "合成の線種"
    assert other_ratio.display_name == UNKNOWN


def test_line_colour_gets_the_meaning_the_legend_declares() -> None:
    """おーちゃんの決め(2026-09-24 01:12): **この図面は色で描き分けているので、
    線の一致は色で見てよい。**意味は凡例が書いているものをそのまま返す。
    """
    (match,) = match_line_colors([(0.0, 1.0, 0.0)], _table())
    assert match.name == "緑色"
    assert match.meaning == "合成の線の意味"
    assert match.kind == KIND_LINE_COLOR


def test_a_colour_outside_the_table_is_unknown() -> None:
    """**合わない色は近い色に寄せない。**"""
    (match,) = match_line_colors([(0.5, 0.5, 0.5)], _table())
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_NOT_IN_TABLE


def test_a_colour_is_not_rounded_to_the_nearest_legend_colour() -> None:
    """緑に近い暗い緑は「緑色」にしない。"""
    (match,) = match_line_colors([(0.0, 0.8, 0.0)], _table())
    assert match.name is None


def test_table_is_loaded_from_a_path(tmp_path) -> None:
    """**表の中身はコードに書かない。**読み込む先は引数で渡す。"""
    path = tmp_path / "lookup.json"
    path.write_text(
        json.dumps(
            {
                "binding": "案件の凡例",
                "work_marks": [
                    {"code": "ウ", "meaning": "合成の意味・その三", "source_page": 6}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    table = LegendTable.load(path)
    assert table.binding == "案件の凡例"
    (match,) = match_marks(["ウ"], table)
    assert match.meaning == "合成の意味・その三"


def test_binding_must_say_the_table_is_this_case_only() -> None:
    """この対照表は**この案件限り**で、ほかの案件には使えない。"""
    with pytest.raises(ValueError, match="案件の凡例"):
        LegendTable.from_payload({"binding": "業界指針", "work_marks": []})


def _weak_table() -> LegendTable:
    """見分けの付かない形の記号だけを入れた合成の対照表。"""
    return LegendTable.from_payload(
        {
            "binding": "案件の凡例",
            "work_marks": [
                {"code": "ア", "meaning": "合成の意味・その一", "source_page": 6}
            ],
            "symbols": [
                {"code": "Q", "name": "合成の器具(丁)", "group": "", "source_page": 22},
                {"code": "907", "name": "合成の器具(戊)", "group": "", "source_page": 22},
                {"code": "Q7", "name": "合成の器具(己)", "group": "", "source_page": 22},
            ],
        }
    )


def test_a_one_letter_equipment_code_is_not_a_symbol() -> None:
    """**仮の判断。**設備の記号は図面では「描かれた形」で、文字はその付け札にすぎない。
    1 文字の語は室番号や符号としても出るので、文字だけでは見分けが付かない。
    """
    (match,) = match_marks(["Q"], _weak_table())
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_UNDISTINGUISHABLE


def test_a_digits_only_equipment_code_is_not_a_symbol() -> None:
    """**仮の判断。**数字だけの語は寸法としても出るので、文字だけでは見分けが付かない。"""
    (match,) = match_marks(["907"], _weak_table())
    assert match.display_name == UNKNOWN
    assert match.reason == REASON_UNDISTINGUISHABLE


def test_a_two_character_code_with_a_letter_still_matches() -> None:
    (match,) = match_marks(["Q7"], _weak_table())
    assert match.name == "合成の器具(己)"


def test_a_short_work_mark_still_matches() -> None:
    """**工事の区分は事情が違う。**凡例が「語をそのまま書く」と決めている印なので、
    1 文字でも落とさない。
    """
    (match,) = match_marks(["ア"], _weak_table())
    assert match.meaning == "合成の意味・その一"


def test_the_provisional_filter_can_be_turned_off() -> None:
    """**仮の判断なので、外して測り直せる形にしておく。**"""
    (match,) = match_marks(["Q"], _weak_table(), strict_equipment_codes=False)
    assert match.name == "合成の器具(丁)"
