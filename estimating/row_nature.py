"""K-71 作業1: 行の**性質**(1〜6)と、出力の行の**理由**。AI は呼ばない。

基準は `docs/k71_scoring_lines_criteria.md`(測る前にコミット)。

- `classify(row, source_type=None)` … 行を性質 1〜6 か「未分類」に振り分ける。根拠を 1 行で返す。
  正解の行(`source_type` に導き方の区分)にも、出力の行(区分なし)にも使う。
- `row_reasons(row, by_id, check=None)` … 出力の行に付いた理由(未確定・要確認・根拠が図面に無い・会社ルールが要る)。
- `apply_errata(rows, errata, column)` … 正誤表の機械形(G 番号と区分だけ)で区分を直す。
- `output_counts(draft)` … 正解を使わずに数えられる数(クラウドで出す)。

**数量の許容と単位の同値はここに置かない**(`sameness.quantity` の 1 か所だけ)。
ここで単位を見るのは「どの性質か」を決めるためだけ。語の一覧はすべて「候補」(差し替えられる)。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from sameness.normalize import canonical_unit, flatten
from sameness.quantity import NEW_CONTINUOUS, NEW_COUNTS

UNCLASSIFIED = "未分類"
NATURE_NAMES = {
    1: "図面から数える個数", 2: "図面から測る面積・長さ", 3: "仕上面積から出る派生",
    4: "会社ルールの行", 5: "職人の見積が根拠の行", 6: "波及",
}

# --- 手がかり(すべて候補。建築の知識で作った最初の案)---
PROPAGATION = "propagation"
STANDARD_RULE = "standard_rule"
SOURCE_FALLBACK = {"symbol_count": 1, "geometry_derived": 2}

COMPANY_KINDS = ("K02", "K03", "K04", "X01")
"""辞書の細目 id: 墨出し・清掃・小運搬・X01(清掃・美装)。"""
COMPANY_CATEGORIES = ("KA_清掃", "KA_美装", "KA_クリーニング", "KA_諸経費", "KA_共通費",
                      "KA_現場管理費", "KA_一般管理費", "KA_共通仮設")
COMPANY_WORDS = ("墨出", "清掃", "クリーニング", "美装", "小運搬", "荷上", "荷揚", "駐車",
                 "諸経費", "現場管理費", "一般管理費", "共通費", "共通仮設")

CRAFT_UNITS = ("式", "一式", "人工", "人", "日", "回")
CRAFT_WORDS = ("手間", "工数", "人工")
"""**揃える前の字(NFKC だけ)で探す。**`flatten` は価格の接尾として「手間」を落とすため。"""

DERIVED_WORDS = ("下地", "ボード", "PB", "石膏", "クロス", "見切", "巾木", "幅木", "廻縁", "廻り縁", "回り縁",
                 "胴縁", "野縁", "パテ", "捨張")
DERIVED_UNITS = ("m", "m2")

REASON_KINDS = ("未確定", "要確認", "根拠が図面に無い", "会社ルールが要る")
UNSETTLED_STATES = ("仮説", "問い")
NO_DRAWING_BASIS = ("仮に置いた", "公開基準から推論")
COMPANY_REASON_WORDS = ("会社", "ルール", "原価表", "職人", "諸経費")


def _nfkc(value: Any) -> str:
    return unicodedata.normalize("NFKC", "" if value is None else str(value)).strip()


def _get(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, Mapping) and row.get(name) not in (None, ""):
            return row.get(name)
        value = getattr(row, name, None) if not isinstance(row, Mapping) else None
        if value not in (None, ""):
            return value
    return None


@dataclass(frozen=True)
class Nature:
    value: int | str
    """1〜6 か `未分類`。"""
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"性質": self.value, "根拠": self.reason}


def _terms():
    from sameness.terms import default_terms

    return default_terms()


def classify(row: Any, source_type: Any = None, *, terms=None) -> Nature:
    """1 行の性質。**上から順に見て最初に当たったもの**(基準 2 節)。"""
    from sameness.keys import structure_key

    terms = terms or _terms()
    name = _nfkc(_get(row, "工事項目", "work_item", "品名"))
    category = _nfkc(_get(row, "科目", "major_category"))
    raw_unit = _nfkc(_get(row, "単位", "unit"))
    unit = canonical_unit(raw_unit)
    source = _nfkc(source_type)
    flat = flatten(name)

    if source == PROPAGATION:
        return Nature(6, "区分が波及(propagation)")

    if source == STANDARD_RULE:
        return Nature(4, "区分が図面に現れない行(standard_rule)")
    key = structure_key({"工事項目": name}, terms=terms)
    if key.工事の種類 in COMPANY_KINDS:
        return Nature(4, f"辞書の細目 {key.工事の種類}(会社ルール)")
    cats = {key.科目, terms.find("科目", category) if category else None}
    hit = next((c for c in COMPANY_CATEGORIES if c in cats), None)
    if hit:
        return Nature(4, f"辞書の科目 {hit}(会社ルール)")
    word = next((w for w in COMPANY_WORDS if w in name or flatten(w) in flat), None)
    if word:
        return Nature(4, f"品名に会社ルールの語「{word}」")

    if unit in CRAFT_UNITS or raw_unit in CRAFT_UNITS:
        return Nature(5, f"単位 {unit or raw_unit}(職人の見積)")
    word = next((w for w in CRAFT_WORDS if w in name), None)
    if word:
        return Nature(5, f"品名に職人の見積の語「{word}」")

    if unit in DERIVED_UNITS:
        word = next((w for w in DERIVED_WORDS if w in name or flatten(w) in flat), None)
        if word:
            return Nature(3, f"単位 {unit} で品名に派生の語「{word}」")
    if unit in NEW_COUNTS:
        return Nature(1, f"単位 {unit}(個数)")
    if unit in NEW_CONTINUOUS:
        return Nature(2, f"単位 {unit}(面積・長さ)")
    if source in SOURCE_FALLBACK:
        return Nature(SOURCE_FALLBACK[source], f"単位 {unit or '(空)'} で決まらず、区分 {source}")
    return Nature(UNCLASSIFIED, f"単位 {unit or '(空)'} は数える・測るのどちらでもなく、"
                                f"区分 {source or '(無し)'} でも決まらない")


# --------------------------------------------------------------------------- 正誤表


def errata_map(errata: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """正誤表の機械形 → {G 番号: (訂正前, 訂正後)}。**名前の欄があれば止める。**"""
    rows = errata.get("行") if isinstance(errata, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("正誤表の形が違う(`行` の一覧が無い)")
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        extra = set(r) - {"G", "訂正前", "訂正後", "注記"}
        if extra:
            raise ValueError(f"正誤表に G 番号と区分以外の欄がある: {sorted(extra)}")
        out[_nfkc(r["G"])] = (_nfkc(r["訂正前"]), _nfkc(r["訂正後"]))
    return out


def errata_counts(errata: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """正誤表の機械形だけから数える(区分ごとの件数、訂正前と訂正後)。"""
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    for old, new in errata_map(errata).values():
        before[old] = before.get(old, 0) + 1
        after[new] = after.get(new, 0) + 1
    return {"訂正前": before, "訂正後": after}


def apply_errata(rows: Sequence[dict[str, Any]], errata: Mapping[str, Any], *, code_col: str,
                 source_col: str) -> dict[str, Any]:
    """区分を直す(**区分が訂正前と同じ行だけ**)。行を書き換え、前後の件数を返す。"""
    fix = errata_map(errata)

    def tally() -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            k = _nfkc(r.get(source_col)) or "(空)"
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))

    before = tally()
    changed, already, other = 0, 0, 0
    found = set()
    for r in rows:
        code = _nfkc(r.get(code_col))
        if code not in fix:
            continue
        found.add(code)
        old, new = fix[code]
        current = _nfkc(r.get(source_col))
        if current == old and old != new:
            r[source_col] = new
            changed += 1
        elif current == new:
            already += 1
        else:
            other += 1
    return {"直す前の区分ごとの件数": before, "直した後の区分ごとの件数": tally(),
            "置き換えた行": changed, "すでに訂正後だった行": already,
            "区分が訂正前とも訂正後とも違った行": other,
            "正誤表の G 番号のうち正解に無かった数": len(set(fix) - found)}


# --------------------------------------------------------------------------- 理由


def row_reasons(row: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]],
                check: Sequence[Any] | None = None) -> list[str]:
    """出力の行に付いた理由(基準 3 節の 4 種類)。無ければ空の一覧。"""
    items = [by_id[i] for i in row.get("項目") or () if i in by_id]
    memo = _nfkc(row.get("メモ"))
    texts = " ".join([memo] + [_nfkc(it.get("理由")) for it in items])
    found: list[str] = []
    if any(it.get("状態") in UNSETTLED_STATES or it.get("確度") == "低" for it in items):
        found.append("未確定")
    if any(it.get("検算") for it in items) or check or memo.startswith("ページで数量が違う"):
        found.append("要確認")
    if any(it.get("根拠の種類") in NO_DRAWING_BASIS for it in items) or (
            row.get("数量") is None and any(_nfkc(it.get("理由")) for it in items)):
        found.append("根拠が図面に無い")
    if any(w in texts for w in COMPANY_REASON_WORDS):
        found.append("会社ルールが要る")
    return found


def _confidence(row: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> str | None:
    order = {"低": 0, "中": 1, "高": 2}
    values = [by_id[i].get("確度") for i in row.get("項目") or () if i in by_id]
    values = [v for v in values if v in order]
    return min(values, key=order.__getitem__) if values else None


def _old_flag(row: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """K-69 の印(`pc_kit.row_flagged` と同じ決め方)。前の数として並べるため。"""
    if row.get("数量") is None:
        return True
    return any((by_id.get(i) or {}).get("確度") == "低" or (by_id.get(i) or {}).get("状態") in UNSETTLED_STATES
               or (by_id.get(i) or {}).get("検算") for i in row.get("項目") or ())


def output_counts(draft: Mapping[str, Any]) -> dict[str, Any]:
    """**正解を使わずに**数えられる数(基準 8 節)。前(K-69 の数え方)と後(この基準)。"""
    rows = list(draft["組み立て"]["内訳の行"])
    by_id = {it["id"]: it for it in draft["理解"]["項目"]}
    checks = list(((draft.get("機械の検算") or {}).get("行ごとの要確認")) or [])
    terms = _terms()
    natures: dict[str, int] = {}
    reasons: dict[str, int] = {k: 0 for k in REASON_KINDS}
    with_reason = unknown_with_reason = unknown_no_reason = 0
    no_basis: dict[str, int] = {}
    unclassified: dict[str, int] = {}
    high = high_with_qty = 0
    for n, row in enumerate(rows):
        nat = classify(row, terms=terms)
        key = str(nat.value)
        natures[key] = natures.get(key, 0) + 1
        if nat.value == UNCLASSIFIED:
            unclassified[nat.reason] = unclassified.get(nat.reason, 0) + 1
        found = row_reasons(row, by_id, checks[n] if n < len(checks) else None)
        for k in found:
            reasons[k] += 1
        if found:
            with_reason += 1
        if row.get("数量") is None:
            if found:
                unknown_with_reason += 1
            else:
                unknown_no_reason += 1
        elif not found:
            no_basis[key] = no_basis.get(key, 0) + 1
        if _confidence(row, by_id) == "高":
            high += 1
            high_with_qty += row.get("数量") is not None
    order = lambda d: dict(sorted(d.items(), key=lambda kv: (len(kv[0]), kv[0])))
    return {
        "前(K-69 の数え方)": {
            "行の数": len(rows),
            "数量のある行": sum(1 for r in rows if r.get("数量") is not None),
            "印のある行": sum(1 for r in rows if _old_flag(r, by_id)),
            "確度「高」の行": high,
        },
        "後(K-71 の基準)": {
            "性質ごとの行の数(出力の行。区分なしで単位と語だけ)": order(natures),
            "未分類の理由": unclassified,
            "理由つきの行": with_reason,
            "理由の種類ごと(重なる)": reasons,
            "理由つきで「分からない」(数量が未取得で理由つき)": unknown_with_reason,
            "数量が未取得で理由も無い行": unknown_no_reason,
            "根拠なしの数量(数量があって理由が無い行、性質ごと)": order(no_basis),
            "根拠なしの数量の合計": sum(no_basis.values()),
            "確度「高」の行": high,
            "確度「高」で数量のある行": high_with_qty,
        },
        "自動確定": (draft.get("まとめ") or {}).get("自動確定"),
    }


def tally(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out
