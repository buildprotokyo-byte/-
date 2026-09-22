"""標準ライブラリだけで .xlsx を読むための最小限のリーダー。

openpyxl を使わない理由: この実行環境からは PyPI(pypi.org)に到達できず
(2026-09-21 実測、403)、openpyxl をインストールできない。e-Stat が配布する
統計表は .xlsx 形式しか無いため、zipfile + xml.etree だけで読む。

必要なのは「セルの値を行列として取り出す」ことだけなので、数式・書式・
結合セルの解決などは一切扱わない(結合セルは左上のセルにだけ値が入り、
残りは None になる。``fetch.py`` 側はこれを前提に前方補完している)。
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

Row = list[str | None]


def _cell_position(ref: str) -> tuple[int, int]:
    """"C12" のようなセル参照を (列index, 行index) の0始まりに直す。"""
    match = re.match(r"([A-Z]+)(\d+)", ref)
    if match is None:
        raise ValueError(f"想定外のセル参照: {ref!r}")
    column = 0
    for char in match.group(1):
        column = column * 26 + (ord(char) - 64)
    return column - 1, int(match.group(2)) - 1


class Workbook:
    """``.xlsx`` を開いて、シート名から行列を取り出せるようにする。"""

    def __init__(self, path) -> None:
        self._zip = zipfile.ZipFile(path)
        self._shared = self._read_shared_strings()
        self._sheets = self._read_sheet_index()

    # -- 内部 ---------------------------------------------------------------

    def _read_shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self._zip.namelist():
            return []
        root = ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
        strings: list[str] = []
        for item in root:
            # <rPh> はふりがな(ルビ)。本文と一緒に拾うと "住宅ジュウタク" の
            # ように連結してしまうので必ず取り除く。
            for ruby in item.findall(_NS + "rPh"):
                item.remove(ruby)
            strings.append("".join(t.text or "" for t in item.iter(_NS + "t")))
        return strings

    def _read_sheet_index(self) -> dict[str, str]:
        relations = ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in relations}
        workbook = ET.fromstring(self._zip.read("xl/workbook.xml"))
        sheets: dict[str, str] = {}
        for sheet in workbook.iter(_NS + "sheet"):
            target = targets[sheet.get(_RNS + "id")] or ""
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            sheets[sheet.get("name") or ""] = target
        return sheets

    # -- 公開API ------------------------------------------------------------

    @property
    def sheet_names(self) -> list[str]:
        return list(self._sheets)

    def rows(self, sheet_name: str) -> list[Row]:
        """シート1枚分を、行ごとのリストとして返す(値はすべて文字列 or None)。"""
        if sheet_name not in self._sheets:
            raise KeyError(f"シートが見つからない: {sheet_name!r}(候補: {self.sheet_names})")
        root = ET.fromstring(self._zip.read(self._sheets[sheet_name]))
        result: list[Row] = []
        for row in root.iter(_NS + "row"):
            cells: dict[int, str | None] = {}
            for cell in row.iter(_NS + "c"):
                ref = cell.get("r")
                if ref is None:
                    continue
                column, _ = _cell_position(ref)
                cells[column] = self._cell_value(cell)
            if cells:
                width = max(cells) + 1
                result.append([cells.get(i) for i in range(width)])
        return result

    def _cell_value(self, cell: ET.Element) -> str | None:
        kind = cell.get("t")
        value = cell.find(_NS + "v")
        if kind == "s" and value is not None and value.text is not None:
            return self._shared[int(value.text)]
        if kind == "inlineStr":
            inline = cell.find(_NS + "is")
            if inline is None:
                return None
            return "".join(t.text or "" for t in inline.iter(_NS + "t"))
        if value is not None:
            return value.text
        return None
