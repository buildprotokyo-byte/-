"""凡例の表を**罫線の升目のまま**写す道(`symbol_rules_from_tables`)の回帰テスト。

なぜ要るのか
------------
最初の写し方は、文字の x と y の並びから行と列を組み直していた。
**凡例のページを罫線の表として数え直したところ、記号の升目に 2 つ以上の
かたまりが入っている行(`N (S)` のように間が空いている記号)が丸ごと
落ちていた。**升目の中の空白で切ってしまうためである。

`axes/image_axis/pdf_tables.find_tables()` は罫線から升目を組むので、
升目の中に空白があっても 1 つの升目として返る。この道に替える。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**
記号も名前も、この試験のためだけに作った語を使う(実図面の凡例の語は
リポジトリに置かない決まり)。

守りたいこと
------------
1. **記号の升目に空白があっても 1 つの記号として写すこと。**(落ちていた行)
2. **記号の升目が空の行(図形だけで描かれた記号)は写さないこと。**
   文字が無いので引き当てられない。**それらしい名前を当てない。**
3. **名前の升目の「※」から後ろは注記なので、名前に含めないこと。**
4. **群の見出し(〈…〉)を、その下の行に付けること。**
5. **注記の行(記号の升目が説明文になっている行)は写さないこと。**
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks.build_legend_lookup import symbol_rules_from_tables
from tests.test_pdf_tables import draw_table


@pytest.fixture(scope="module")
def legend_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """合成の「名称 / 記号」凡例。語はこの試験のためだけのもの。"""
    path = tmp_path_factory.mktemp("legend") / "synthetic_legend.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    draw_table(
        page,
        origin=(60.0, 80.0),
        col_widths=(180.0, 90.0, 90.0),
        row_height=22.0,
        rows=(
            ("名称", "記号", ""),
            ("〈合成の群(甲)〉", "", ""),
            ("合成の器具(一)", "アイ", ""),
            ("合成の器具(二)", "ウ (エ)", ""),
            ("合成の器具(三)", "", ""),
            ("合成の器具(四) ※ここから後ろは注記", "オカ", ""),
            ("合成の器具(五)", "", "キク"),
            ("〈合成の群(乙)〉", "", ""),
            ("合成の器具(六)", "ケコ", ""),
            ("これは注記の行である", "※この升目は説明文なので記号ではない", ""),
        ),
    )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="module")
def rules(legend_pdf: Path) -> list[dict]:
    return symbol_rules_from_tables(legend_pdf, 1)


def code_of(rules: list[dict], name: str) -> str | None:
    for rule in rules:
        if rule["name"] == name:
            return rule["code"]
    return None


def test_a_code_with_a_space_inside_the_cell_is_copied_whole(rules) -> None:
    """**落ちていた行。**升目の中の空白で記号を切らない。"""
    assert code_of(rules, "合成の器具(二)") == "ウ (エ)"


def test_a_row_whose_symbol_cell_is_empty_is_not_copied(rules) -> None:
    """図形だけで描かれた記号は文字が無い。**名前を当てずに落とす。**"""
    assert code_of(rules, "合成の器具(三)") is None


def test_the_note_after_a_star_is_not_part_of_the_name(rules) -> None:
    assert code_of(rules, "合成の器具(四)") == "オカ"


def test_a_symbol_in_a_further_column_is_still_found(rules) -> None:
    """記号の列が 2 つに分かれている凡例がある。**右の升目も見る。**"""
    assert code_of(rules, "合成の器具(五)") == "キク"


def test_the_group_heading_is_carried_down(rules) -> None:
    assert [r["group"] for r in rules if r["name"] == "合成の器具(一)"] == ["合成の群(甲)"]
    assert [r["group"] for r in rules if r["name"] == "合成の器具(六)"] == ["合成の群(乙)"]


def test_a_note_row_is_not_copied(rules) -> None:
    assert code_of(rules, "これは注記の行である") is None


def test_the_group_heading_itself_is_not_a_rule(rules) -> None:
    assert [r for r in rules if r["name"].startswith("〈")] == []


def test_every_rule_says_which_page_it_came_from(rules) -> None:
    assert {r["source_page"] for r in rules} == {1}


def test_a_page_without_a_name_and_symbol_header_gives_nothing(
    tmp_path: Path,
) -> None:
    """**0 件は「凡例が無い」であって「読めなかった」ではない。**"""
    path = tmp_path / "not_a_legend.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    draw_table(
        page,
        origin=(60.0, 80.0),
        col_widths=(120.0, 120.0),
        row_height=22.0,
        rows=(("室名", "面積"), ("合成の室(甲)", "10.0")),
    )
    doc.save(path)
    doc.close()
    assert symbol_rules_from_tables(path, 1) == []
