"""周30 の道具(`benchmarks/measure_literal_vs_constant.py`)の試験。

**合成のコードで、数え方だけを固定する。**
"""

from __future__ import annotations

import ast
from pathlib import Path

from benchmarks.measure_literal_vs_constant import (
    Constant,
    Literal,
    constants_of,
    copies,
    literals_of,
    near_misses,
    table_literals_of,
)

SOURCE = '''
"""この説明文の中の "床面積" は数えない。"""

KIND = "室の周長"
_SMALL = "a"
TABLE = {"巾木": ("周長", "m")}
lower = "数えない"


def f():
    """説明文の中の "周長" も数えない。"""
    return "外の文字列"
'''


def _parse(text: str) -> ast.AST:
    return ast.parse(text)


def test_大文字の代入だけを定数として数える() -> None:
    tree = _parse(SOURCE)
    names = {c.name for c in constants_of(Path("a.py"), tree)}
    assert names == {"KIND"}  # _SMALL は 1 文字、lower は小文字、TABLE は文字列でない


def test_説明文の中の文字列は数えない() -> None:
    values = {lit.value for lit in literals_of(Path("a.py"), _parse(SOURCE))}
    assert "床面積" not in values
    assert "周長" in values
    assert "外の文字列" in values


def test_表の中の文字列だけを拾う() -> None:
    got = {value for value, _ in table_literals_of(Path("a.py"), _parse(SOURCE))}
    assert got == {"巾木", "周長", "m"}
    assert "外の文字列" not in got


def test_惜しい組を拾う() -> None:
    constants = [Constant("KIND_PERIMETER", "室の周長", "b.py")]
    literals = [Literal("周長", "a.py", 5)]
    (row,) = near_misses(constants, literals)
    assert row["定数の値"] == "室の周長"
    assert row["書かれた文字列"] == "周長"


def test_同じファイルの中は数えない() -> None:
    constants = [Constant("KIND_PERIMETER", "室の周長", "a.py")]
    literals = [Literal("周長", "a.py", 5)]
    assert near_misses(constants, literals) == []


def test_完全に一致するものは惜しい組にしない() -> None:
    constants = [Constant("KIND", "室の周長", "b.py")]
    literals = [Literal("室の周長", "a.py", 5)]
    assert near_misses(constants, literals) == []


def test_完全に一致するものは書き写しに数える() -> None:
    constants = [Constant("KIND", "室の周長", "b.py")]
    literals = [Literal("室の周長", "a.py", 5)]
    (row,) = copies(constants, literals)
    assert row["定数"] == "KIND"
    assert row["書いたファイル"] == "a.py"


def test_同じファイルの中は書き写しに数えない() -> None:
    constants = [Constant("KIND", "室の周長", "a.py")]
    literals = [Literal("室の周長", "a.py", 5)]
    assert copies(constants, literals) == []
