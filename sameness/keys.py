"""K-66 2 節(c): 構造のキー。品名から**工事の種類・部位・状態・材料**の 4 つを取り出す。

**品名の文字はここから先に出ない。**判定(`sameness.judge`)が見るのはこの 4 つだけである。

取り方の順:

1. 行が欄(`科目`・`区分`・`部位`・`細目` など)を持っていればそれを使う(読み取りが既に決めたもの)
2. 無ければ、辞書(`sameness.terms`)の語を品名+摘要から探す
3. 工事の種類は `draft/normalize.py` の手がかりを土台にする(K-63 で入った層をそのまま使う)。
   ただし**手がかりの最後の「どれにも当たらなければ T02」は使わない**——
   `笠木撤去` と `とい撤去` がどちらも `壁仕上の撤去` になり、別の工事が同じキーになってしまう。
   当たらなければ `None`(= 判定は `比較不能`)にする。**`○` を増やす方向に倒れない。**
4. それでも工事の種類が取れない品名だけ、AI に 1 回だけ聞いてキャッシュする
   (`sameness.cache`。同じ品名には同じキー。AI が無ければ `None` のまま)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from draft.normalize import load_clues
from sameness.normalize import flatten
from sameness.terms import Terms, default_terms

#: 品名が入っている欄。読み取りの出力と見積の行で名前が違うので両方見る。
NAME_FIELDS = ("工事項目", "工事", "品名", "名前", "摘要", "何", "仕様")

#: 細目 id → 既定の部位。`draft/vocab/default.json` の名前から機械で引く表の代わりに、
#: id の頭文字と名前で決まるものだけを書く(**推測で埋めない**)。
_CODE_PART: dict[str, str] = {
    "T01": "BU_床", "T02": "BU_壁", "T03": "BU_天井", "T04": "BU_巾木", "T05": "BU_建具",
    "T06": "BU_造作", "T07": "BU_設備", "T08": "BU_電気", "T09": "BU_内壁", "T10": "BU_天井",
    "M01": "BU_内壁", "M02": "BU_壁", "M03": "BU_天井", "M04": "BU_天井", "M05": "BU_床",
    "M07": "BU_巾木", "M08": "BU_廻縁", "M09": "BU_建具", "M10": "BU_造作", "M11": "BU_造作",
    "M12": "BU_天井", "M13": "BU_壁",
    "N01": "BU_床", "N02": "BU_床", "N03": "BU_床", "N04": "BU_壁", "N05": "BU_天井",
    "N06": "BU_巾木", "N07": "BU_床",
    "P01": "BU_壁", "P02": "BU_天井",
    "Y01": "BU_壁", "Y02": "BU_床",
    "D01": "BU_建具", "D02": "BU_建具", "D03": "BU_建具", "D04": "BU_建具", "D05": "BU_建具",
    "F01": "BU_造作", "F02": "BU_設備", "F03": "BU_設備",
    "E01": "BU_設備", "E02": "BU_設備", "E03": "BU_設備", "E04": "BU_設備", "E05": "BU_設備",
    "E06": "BU_設備", "E07": "BU_設備", "E08": "BU_設備",
    "L01": "BU_電気", "L02": "BU_電気", "L03": "BU_電気", "L04": "BU_電気", "L05": "BU_電気",
    "L06": "BU_電気", "L07": "BU_電気", "L08": "BU_電気", "L09": "BU_電気", "L10": "BU_電気",
    "L11": "BU_電気",
    "K01": "BU_仮設", "K02": "BU_仮設", "K03": "BU_仮設",
}

#: 細目 id → 既定の科目(`draft/vocab/default.json` の `科目` を辞書の組に寄せたもの)。
_CODE_KAMOKU: dict[str, str] = {
    "K": "KA_仮設", "T": "KA_撤去", "M": "KA_木工事", "N": "KA_内装", "P": "KA_塗装",
    "Y": "KA_タイル", "S": "KA_左官", "D": "KA_建具", "Z": "KA_金属", "F": "KA_家具",
    "E": "KA_機械設備", "L": "KA_電気設備", "X": "KA_雑",
}

#: 撤去の細目 id。品名に撤去の語があるときは、状態を `撤去` と読む。
_REMOVAL_CODES = tuple(f"T{n:02d}" for n in range(1, 11))


@dataclass(frozen=True)
class StructureKey:
    """構造のキー。**判定が見るのはこの 4 つ。**

    `科目` は 4 つには入らない(K-66 1 節は 4 つと決めている)。科目の段の判定と、
    「片方が細目・片方がその科目だけ」の上位・下位の判定にだけ使う。
    """

    工事の種類: str | None = None
    部位: str | None = None
    状態: str | None = None
    材料: str | None = None
    科目: str | None = None
    中科目: str | None = None
    由来: tuple[str, ...] = ()
    """どの層が埋めたか(`欄`・`辞書`・`手がかり`・`AI`)。理由の 1 行に出す。"""

    @property
    def four(self) -> tuple[str | None, str | None, str | None, str | None]:
        return (self.工事の種類, self.部位, self.状態, self.材料)

    @property
    def 取れた数(self) -> int:
        return sum(1 for v in self.four if v)

    def as_dict(self) -> dict[str, Any]:
        return {
            "工事の種類": self.工事の種類, "部位": self.部位, "状態": self.状態,
            "材料": self.材料, "科目": self.科目, "中科目": self.中科目,
            "由来": list(self.由来),
        }


def _name_text(row: Mapping[str, Any] | str) -> str:
    if isinstance(row, str):
        return row
    return " ".join(str(row.get(f) or "") for f in NAME_FIELDS)


def _code_from_clues(text: str, removal: bool, clues: Mapping[str, Any]) -> str | None:
    """`draft/normalize.py` の手がかりで細目 id を当てる。**空の手がかり(受け皿)は使わない。**"""
    for words, code in clues["撤去" if removal else "そのほか"]:
        if not words:
            continue
        if all(w in text for w in words):
            return code
    return None


def structure_key(
    row: Mapping[str, Any] | str,
    *,
    terms: Terms | None = None,
    clues: Mapping[str, Any] | None = None,
    ask_ai: Callable[[str], Mapping[str, Any] | None] | None = None,
    cache: Any | None = None,
) -> StructureKey:
    """1 行(または品名 1 つ)から構造のキーを作る。"""
    terms = terms or default_terms()
    clues = clues or load_clues()
    fields: Mapping[str, Any] = {} if isinstance(row, str) else row
    raw_name = _name_text(row)
    text = flatten(raw_name)
    origins: list[str] = []

    # 1. 欄(読み取りが既に決めたもの)
    code = None
    if fields.get("細目"):
        candidate = str(fields["細目"]).strip()
        if candidate and candidate != "X99":
            code = candidate
            origins.append("欄:細目")
    部位 = terms.exact("部位", fields.get("部位")) or terms.find("部位", fields.get("部位"))
    if 部位:
        origins.append("欄:部位")
    状態 = terms.exact("状態", fields.get("区分")) or terms.find("状態", fields.get("区分"))
    if 状態:
        origins.append("欄:区分")
    科目 = terms.find("科目", fields.get("科目"))
    if 科目:
        origins.append("欄:科目")
    中科目 = terms.find("中科目", fields.get("中科目"))

    # 2. 辞書(品名+摘要から)
    if not 状態:
        状態 = terms.find("状態", text)
        if 状態:
            origins.append("辞書:状態")
    if not 部位:
        部位 = terms.find("部位", text)
        if 部位:
            origins.append("辞書:部位")
    材料 = terms.find("材料", text)
    if 材料:
        origins.append("辞書:材料")

    # 3. 工事の種類(手がかり。受け皿は使わない)
    if not code:
        code = terms.find("工事の種類", text)
        if code:
            origins.append("辞書:工事の種類")
    if not code:
        removal = 状態 == "JO_撤去" or fields.get("区分") == "撤去" or fields.get("科目") == "撤去"
        code = _code_from_clues(text, removal, clues)
        if code:
            origins.append("手がかり")

    # 4. それでも取れなければ AI に 1 回だけ聞く(キャッシュ)
    if not code and (ask_ai or cache is not None):
        from sameness.cache import ask_once

        answer = ask_once(raw_name, ask_ai=ask_ai, cache=cache)
        if answer:
            code = answer.get("工事の種類") or code
            部位 = 部位 or answer.get("部位")
            状態 = 状態 or answer.get("状態")
            材料 = 材料 or answer.get("材料")
            origins.append("AI")

    # 細目 id から埋められるもの(欄や辞書で取れていないときだけ)
    if code:
        部位 = 部位 or _CODE_PART.get(code)
        科目 = 科目 or _CODE_KAMOKU.get(code[:1])
        if code in _REMOVAL_CODES:
            状態 = 状態 or "JO_撤去"

    # 科目・中科目の段の判定は、品名しか無いときもある(`大工工事` という 1 語など)。
    # **細目 id から引けたものを上書きしない**(細目のほうが確かなので、空のときだけ埋める)。
    if not 科目:
        科目 = terms.find("科目", text)
        if 科目:
            origins.append("辞書:科目")
    if not 中科目:
        中科目 = terms.find("中科目", text)

    return StructureKey(
        工事の種類=code, 部位=部位, 状態=状態, 材料=材料,
        科目=科目, 中科目=中科目, 由来=tuple(dict.fromkeys(origins)),
    )
