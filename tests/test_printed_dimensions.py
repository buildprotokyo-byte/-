"""22周目: 印字された寸法を拾う層と、突き合わせる層のテスト。

**合成データだけを使う。** 実案件の図面の値は出てこない。
"""

from __future__ import annotations

import pymupdf
import pytest

from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from axes.image_axis.printed_dimensions import (
    MAX_DIMENSION_MM,
    METHOD_PRINTED_DIMENSION,
    MIN_DIMENSION_MM,
    PrintedDimension,
    distinct_values,
    read_printed_dimensions,
)
from estimating.dimension_check import (
    DIGIT_SHIFT,
    FOUND,
    NOT_FOUND,
    check_values,
)


def _pdf(tmp_path, text: str, name: str = "plan.pdf"):
    document = pymupdf.open()
    page = document.new_page(width=842, height=595)
    page.insert_text((50, 100), text, fontsize=9)
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


# ---------------------------------------------------------------- 拾う層


def test_裸の整数を寸法の候補として拾う(tmp_path):
    path = _pdf(tmp_path, "3640 910 1820")
    values = distinct_values(read_printed_dimensions(path, 1))
    assert values == (910, 1820, 3640)


def test_窓の値が変わっていない():
    # **定数そのものを書く。** `MIN_DIMENSION_MM - 1` のように定数から作った値で
    # 試すと、定数を書き換えたときに試験も一緒に動いてしまって検出できない
    # (22周目の壊し試験で実際に見逃した)。
    assert MIN_DIMENSION_MM == 300
    assert MAX_DIMENSION_MM == 50_000


def test_先頭が0の数は拾わない(tmp_path):
    path = _pdf(tmp_path, "0910 3640")
    assert distinct_values(read_printed_dimensions(path, 1)) == (3640,)


def test_小数を含む数は拾わない(tmp_path):
    path = _pdf(tmp_path, "3.640 36.40 3640")
    assert distinct_values(read_printed_dimensions(path, 1)) == (3640,)


def test_窓の外は拾わない(tmp_path):
    # **定数から作らず、literal で書く。**
    path = _pdf(tmp_path, "299 50001 3640")
    assert distinct_values(read_printed_dimensions(path, 1)) == (3640,)


def test_二桁の数は窓が弾く(tmp_path):
    # 桁数の検査は置いていない。**窓の下限が同じことをしている。**
    path = _pdf(tmp_path, "12 7 99 3640")
    assert distinct_values(read_printed_dimensions(path, 1)) == (3640,)


def test_窓のふちは拾う(tmp_path):
    path = _pdf(tmp_path, f"{MIN_DIMENSION_MM} {MAX_DIMENSION_MM}")
    assert distinct_values(read_printed_dimensions(path, 1)) == (
        MIN_DIMENSION_MM,
        MAX_DIMENSION_MM,
    )


def test_同じ値が何度出てもそのぶん並ぶ(tmp_path):
    # **数を減らさない。** 何回出てきたかが後で効く。
    path = _pdf(tmp_path, "910 910 910")
    dimensions = read_printed_dimensions(path, 1)
    assert len(dimensions) == 3
    assert distinct_values(dimensions) == (910,)


def test_ページは一始まりである(tmp_path):
    path = _pdf(tmp_path, "3640")
    read_printed_dimensions(path, 1)
    with pytest.raises(ValueError, match="ありません"):
        read_printed_dimensions(path, 0)
    with pytest.raises(ValueError, match="ありません"):
        read_printed_dimensions(path, 2)


def test_候補はページ番号を持つ(tmp_path):
    path = _pdf(tmp_path, "3640")
    (dimension,) = read_printed_dimensions(path, 1)
    assert dimension.page_number == 1
    assert dimension.value_mm == 3640


def test_ゼロ以下のページ番号は作れない():
    with pytest.raises(ValueError, match="1 始まり"):
        PrintedDimension(3640, 0)


def test_手法は未校正で上限はweak():
    # **ここを校正済みにすると、人が1回入れた値と印字の一致だけで階層1に届く。**
    policy = DEFAULT_METHOD_POLICIES[METHOD_PRINTED_DIMENSION]
    assert policy.calibrated is False
    assert policy.max_strength == "weak"


def test_手法IDの文字列が変わっていない():
    assert METHOD_PRINTED_DIMENSION == "pdf_printed_dimension"


# ---------------------------------------------------------------- 突き合わせ


def test_印字にあれば見つかる():
    result = check_values("室A", [("縦", 3640.0)], [910, 3640])
    (finding,) = result.findings
    assert finding.status == FOUND
    assert finding.is_mismatch is False


def test_印字に無ければ食い違いになる():
    result = check_values("室A", [("縦", 3641.0)], [910, 3640])
    (finding,) = result.findings
    assert finding.status == NOT_FOUND
    assert finding.is_mismatch is True


def test_足し合わせでは一致させない():
    # **910 + 1820 = 2730 は「印字にある」にしない。**
    # 和を許すとどんな数でも一致してしまう(22周目に測って決めた)。
    result = check_values("室A", [("縦", 2730.0)], [910, 1820])
    assert result.findings[0].status == NOT_FOUND


def test_十倍の桁違いを名指しする():
    result = check_values("室A", [("縦", 36400.0)], [3640])
    (finding,) = result.findings
    assert finding.status == DIGIT_SHIFT
    assert "桁の取り違え" in finding.note


def test_十分の一の桁違いを名指しする():
    result = check_values("室A", [("縦", 364.0)], [3640])
    assert result.findings[0].status == DIGIT_SHIFT


def test_百倍の桁違いも名指しする():
    result = check_values("室A", [("縦", 364.0)], [36400])
    assert result.findings[0].status == DIGIT_SHIFT


def test_桁違いは印字にあるより優先されない():
    # そのまま印字にあるなら、桁違いとは言わない。
    result = check_values("室A", [("縦", 3640.0)], [364, 3640])
    assert result.findings[0].status == FOUND


def test_入れていない値は突き合わせない():
    # **入れていないものを誤りにしない。**
    result = check_values("室A", [("縦", None), ("横", 3640.0)], [3640])
    assert len(result.findings) == 1
    assert result.findings[0].label == "横"


def test_印字が空なら全部食い違いになる():
    result = check_values("室A", [("縦", 3640.0)], [])
    assert result.findings[0].status == NOT_FOUND
    assert result.printed_count == 0


def test_食い違いだけを取り出せる():
    result = check_values("室A", [("縦", 3640.0), ("横", 9999.0)], [3640])
    assert len(result.findings) == 2
    assert len(result.mismatches) == 1
    assert result.mismatches[0].label == "横"


def test_突き合わせた印字の数を持つ():
    result = check_values("室A", [("縦", 3640.0)], [910, 910, 3640])
    assert result.printed_count == 2


def test_この層は数量も確定も作らない():
    result = check_values("室A", [("縦", 3640.0)], [3640])
    assert not hasattr(result, "quantities")
    assert not hasattr(result.findings[0], "tier")
    assert not hasattr(result.findings[0], "confirmed_range")


def test_出どころの手法を持つ():
    result = check_values("室A", [("縦", 3640.0)], [3640])
    assert result.method_id == "pdf_printed_dimension"
