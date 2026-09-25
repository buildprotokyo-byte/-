"""周30: **定数を import せず文字列で書いた引き当て**を数える。

**この道具はコードを 1 行も直さない。数えるだけである。**

周28・周29 で、**`app.py` が巾木の数量を `周長` という文字列で探し、
作られているのは `室の周長`(`KIND_PERIMETER`)**という食い違いを見つけた。

**この欠陥の正体は「定数を import せずに、文字列で書き写したこと」である。**
**書き写しは、書き写した先が変わっても気づかない。**
**そして「作れなかった理由を残す」仕掛けでは原理的に拾えない。**

**正規表現ではなく構文木で読む。**説明文(docstring)の中の文字列を
拾わないためである。

基準は `docs/loop_round30_literal_vs_constant_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable, NamedTuple

#: 数える場所。
ROOTS = ("app.py", "axes", "estimating", "intake", "arbitration")

#: 定数の値として数える最小の長さ。
MIN_LENGTH = 2


class Constant(NamedTuple):
    name: str
    value: str
    file: str


class Literal(NamedTuple):
    value: str
    file: str
    line: int


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """説明文(docstring)として使われている文字列の id。**数えない。**"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", ())
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def constants_of(path: Path, tree: ast.AST) -> list[Constant]:
    """**モジュールの直下で、大文字の名前に文字列を入れている代入。**"""
    out: list[Constant] = []
    for node in getattr(tree, "body", ()):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if not target.id.isupper() or len(value.value) < MIN_LENGTH:
                continue
            out.append(Constant(target.id, value.value, str(path)))
    return out


def literals_of(path: Path, tree: ast.AST) -> list[Literal]:
    """**そのファイルの文字列リテラル。説明文は除く。**"""
    skip = _docstring_nodes(tree)
    out: list[Literal] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip or len(node.value) < MIN_LENGTH:
            continue
        out.append(Literal(node.value, str(path), node.lineno))
    return out


def table_literals_of(path: Path, tree: ast.AST) -> set[tuple[str, int]]:
    """**あとから足した診断の欄。**線1〜線3 の判定には使っていない。

    足した理由: 線1 の「惜しい組」が 490 件出て、**そのほとんどが
    `床` と `床面積` のような、別のものを指す名前の偶然の重なり**だったため。
    **数えた件数は動かさず、そこから見る場所を狭める欄をあとに足した。**

    ここで拾うのは、**モジュールの直下に置かれた入れ物(辞書・組・一覧)の中の
    文字列**である。**引き当ての表はそこに書かれる**
    (`app.py` の `FINISH_PART_QUANTITY` がまさにこの形)。
    """
    out: set[tuple[str, int]] = set()
    for node in getattr(tree, "body", ()):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, (ast.Dict, ast.Tuple, ast.List, ast.Set)):
            continue
        for inner in ast.walk(value):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                out.add((inner.value, inner.lineno))
    return out


def python_files(root: Path, roots: Iterable[str] = ROOTS) -> list[Path]:
    out: list[Path] = []
    for name in roots:
        here = root / name
        if here.is_file() and here.suffix == ".py":
            out.append(here)
        elif here.is_dir():
            out.extend(sorted(here.rglob("*.py")))
    return out


def collect(root: Path) -> tuple[list[Constant], list[Literal], set[tuple[str, str, int]]]:
    constants: list[Constant] = []
    literals: list[Literal] = []
    tables: set[tuple[str, str, int]] = set()
    for path in python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants.extend(constants_of(path, tree))
        literals.extend(literals_of(path, tree))
        tables.update((str(path), value, line) for value, line in table_literals_of(path, tree))
    return constants, literals, tables


def near_misses(constants: list[Constant], literals: list[Literal]) -> list[dict]:
    """**「惜しい」組**: 片方がもう片方の一部だが、一致しない。"""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for constant in constants:
        for literal in literals:
            if literal.file == constant.file or literal.value == constant.value:
                continue
            if constant.value in literal.value or literal.value in constant.value:
                key = (constant.name, constant.value, literal.value)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "定数": constant.name,
                        "定数の値": constant.value,
                        "定数のファイル": constant.file,
                        "書かれた文字列": literal.value,
                        "書いたファイル": literal.file,
                        "行": literal.line,
                    }
                )
    return out


def copies(constants: list[Constant], literals: list[Literal]) -> list[dict]:
    """**「書き写し」**: 定数があるのに、別のファイルで同じ文字列を直接書いている。"""
    by_value: dict[str, Constant] = {}
    for constant in constants:
        by_value.setdefault(constant.value, constant)
    out: list[dict] = []
    for literal in literals:
        constant = by_value.get(literal.value)
        if constant is None or constant.file == literal.file:
            continue
        out.append(
            {
                "定数": constant.name,
                "値": constant.value,
                "定数のファイル": constant.file,
                "書いたファイル": literal.file,
                "行": literal.line,
            }
        )
    return out


def measure(root: Path) -> dict:
    constants, literals, tables = collect(root)
    near = near_misses(constants, literals)
    in_tables = [
        row
        for row in near
        if (row["書いたファイル"], row["書かれた文字列"], row["行"]) in tables
    ]
    copied = copies(constants, literals)
    files = {}
    for row in copied:
        files[row["書いたファイル"]] = files.get(row["書いたファイル"], 0) + 1
    control = [
        row
        for row in near
        if row["定数の値"] == "室の周長" and row["書かれた文字列"] == "周長"
    ]
    return {
        "数えた定数": len(constants),
        "数えた文字列": len(literals),
        "線1_惜しい組": {"件数": len(near), "中身": near},
        "参考_惜しい組のうち、引き当ての表の中にあるもの": {
            "件数": len(in_tables),
            "中身": in_tables,
            "断り": "結果を見てから足した診断の欄。線1 の件数は動かしていない",
        },
        "線2_書き写し": {
            "件数": len(copied),
            "ファイルごと": dict(sorted(files.items(), key=lambda item: -item[1])),
            "判定": "置いていない(記述であって通過・不通過の線ではない)",
        },
        "線3_対照_既に分かっている1件を拾えるか": {
            "拾えた": bool(control),
            "中身": control,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    args = parser.parse_args()
    print(json.dumps(measure(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
