"""K-69: パソコン側(Codex-A)で、**保存された出力を読むだけ**で採点する道具。AI は 1 回も呼ばない。

K-61〜K-67 の手順書の `...`(空欄)をここで埋める。正解ファイルはパソコンの中だけで開き、
**出すのは件数と割合と、部品が出した理由の文だけ**(行の名前・室名・金額そのものは出さない)。

使い方(どれも出力は JSON を標準出力へ)::

    python -m benchmarks.pc_kit 列 正解.json                 # 列の名前と型と件数だけ(値は出さない)
    python -m benchmarks.pc_kit k66 --run 回 --golden 正解.json
    python -m benchmarks.pc_kit k67 --run 回 --golden 正解.json
    python -m benchmarks.pc_kit k64 --run 回1 --run 回2 ... --golden 正解.json
    python -m benchmarks.pc_kit カード --run full_R1 --other full_R2 --other full_R3 --checklist 表.json --out cards.json
    python -m benchmarks.pc_kit k65 --run full_R1 --other full_R2 --other full_R3 \
        --checklist 工事チェック表_full_R1.json --ideal 理想の答え.json --golden 正解.json
    python -m benchmarks.pc_kit k71 --run 回 --golden 正解.json --errata 正誤表_区分.json   # K-71 性質ごとの線
    python -m benchmarks.pc_kit k73 --run 旗オンの回 --golden 正解.json --errata 正誤表_区分.json  # K-73 旗 × 足し上げ
    python -m benchmarks.pc_kit k73-出力側 --run 旗オンの回                                   # 正解を使わない数

「回」は `下書き.json` の入ったフォルダ(K-61 の `draft.run` の出力)。

**分母は 93 件**(G001・G002・G003・G004・G006・G099 を除く。K-36 追記)。除いた数が 6 でなければ止める。

正解の列の名前は既定で `code`・`work_item`・`unit`・`major_category`・`middle_category`・
`quantity`・`amount`。違っていたら `列` で名前を見てから `--col 数量=列名` のように渡す。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

EXCLUDED_CODES = ("G001", "G002", "G003", "G004", "G006", "G099")
DENOMINATOR = 93
DEFAULT_COLUMNS = {
    "符号": "code", "品名": "work_item", "単位": "unit", "科目": "major_category",
    "中科目": "middle_category", "数量": "quantity", "金額": "amount",
    "区分": "expected_source_type",
}
FLAG_STATES = ("仮説", "問い")
"""「印が出ていた」とみなす状態。確度「低」と、検算の食い違いも印に数える(K-67 4 節の「未確定・低・要確認」)。"""
MODES = ("概算", "通常", "精密")
COUNTS = (0, 3, 5, 10, 20, 40, 60)
SAME = "○"


def nfkc(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


# --------------------------------------------------------------------------- 列の名前


def columns(path: str | Path) -> dict[str, Any]:
    """正解ファイルの**列の名前と型と件数だけ**を返す。値は 1 つも返さない。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    top = {k: type(v).__name__ for k, v in payload.items()} if isinstance(payload, Mapping) else {}
    items = payload.get("expected_items") if isinstance(payload, Mapping) else None
    out: dict[str, Any] = {"一番上の欄": top}
    if not isinstance(items, list):
        out["expected_items"] = "無い(形が違う)"
        return out
    cols: dict[str, dict[str, int]] = {}
    for row in items:
        if not isinstance(row, Mapping):
            continue
        for k, v in row.items():
            kind = "空" if v in (None, "") else type(v).__name__
            cols.setdefault(k, {})[kind] = cols[k].get(kind, 0) + 1 if k in cols else 1
    code_col = DEFAULT_COLUMNS["符号"]
    codes = {nfkc(r.get(code_col)) for r in items if isinstance(r, Mapping)}
    out.update({
        "行の数": len(items),
        "列(名前: 型ごとの件数)": cols,
        "除く 6 件のうち符号が見つかった数": sum(1 for c in EXCLUDED_CODES if c in codes),
        "除いた後の行の数": sum(1 for r in items if isinstance(r, Mapping) and nfkc(r.get(code_col)) not in EXCLUDED_CODES),
        "既定の列の名前": DEFAULT_COLUMNS,
        "既定の列のうち見つからなかったもの": [k for k, v in DEFAULT_COLUMNS.items() if v not in cols],
    })
    return out


# --------------------------------------------------------------------------- 読み込み


def parse_cols(pairs: Sequence[str]) -> dict[str, str]:
    cols = dict(DEFAULT_COLUMNS)
    for pair in pairs or ():
        name, _, col = pair.partition("=")
        if name not in cols or not col:
            raise SystemExit(f"--col は 符号/品名/単位/科目/中科目/数量/金額/区分=列名 の形: {pair!r}")
        cols[name] = col
    return cols


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    text = nfkc(value).replace(",", "").replace("円", "")
    try:
        return float(text)
    except ValueError:
        return None


class Gold:
    """93 件の正解。`items[i]` と `raw[i]` が同じ行(採点の部品は `items` だけを見る)。"""

    def __init__(self, path: str | Path, cols: Mapping[str, str], *, allow_other_denominator: bool = False,
                 errata: Mapping[str, Any] | None = None):
        from estimating.scoring import GoldenItem

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = [r for r in payload.get("expected_items") or () if isinstance(r, Mapping)]
        kept = [r for r in rows if nfkc(r.get(cols["符号"])) not in EXCLUDED_CODES]
        removed = len(rows) - len(kept)
        if not allow_other_denominator and (removed != len(EXCLUDED_CODES) or len(kept) != DENOMINATOR):
            raise SystemExit(f"分母が 93 になりません(除いた {removed} 件、残り {len(kept)} 件)。"
                             "列の名前を `列` で確かめてください")
        self.cols = dict(cols)
        kept = [dict(r) for r in kept]  # 正誤表で区分を直すので写しにする(元のファイルは変えない)
        self.errata_result = None
        if errata is not None:
            from estimating.row_nature import apply_errata

            self.errata_result = apply_errata(kept, errata, code_col=cols["符号"], source_col=cols["区分"])
        self.raw = kept
        self.items = [GoldenItem(work_item=nfkc(r.get(cols["品名"])), unit=nfkc(r.get(cols["単位"])),
                                 code=None, major_category=nfkc(r.get(cols["科目"])) or None) for r in kept]
        # 符号は渡さない: 出力の行は符号を持たないので、符号で当てる道は使われない。
        self.index = {id(item): i for i, item in enumerate(self.items)}
        self.has = {name: any(col in r for r in kept) for name, col in cols.items()}

    def fields(self, i: int) -> dict[str, Any]:
        r = self.raw[i]
        return {"工事項目": nfkc(r.get(self.cols["品名"])), "単位": nfkc(r.get(self.cols["単位"])),
                "科目": nfkc(r.get(self.cols["科目"])), "中科目": nfkc(r.get(self.cols["中科目"]))}

    def quantity(self, i: int) -> float | None:
        return _number(self.raw[i].get(self.cols["数量"]))

    def amount(self, i: int) -> float:
        return _number(self.raw[i].get(self.cols["金額"])) or 0.0

    def unit(self, i: int) -> str:
        return nfkc(self.raw[i].get(self.cols["単位"]))

    def source(self, i: int) -> str:
        return nfkc(self.raw[i].get(self.cols["区分"]))

    def code(self, i: int) -> str:
        return nfkc(self.raw[i].get(self.cols["符号"]))


class Line(dict):
    """内訳の行(辞書)に、旧規則が読む属性を足したもの。**新規則は辞書として読む。**"""

    work_item = property(lambda self: self.get("工事項目") or "")
    unit = property(lambda self: self.get("単位") or "")
    major_category = property(lambda self: self.get("科目") or "")
    code = ""


def as_lines(rows: Sequence[Mapping[str, Any]]) -> list[Line]:
    return [Line(r) for r in rows]


def load_run(path: str | Path) -> dict[str, Any]:
    return json.loads((Path(path) / "下書き.json").read_text(encoding="utf-8"))


def rows_of(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    return as_lines(draft["組み立て"]["内訳の行"])


def row_confidence(row: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> str | None:
    """行の確度 = まとめた項目の確度の**いちばん低いもの**(高は全部が高のときだけ)。"""
    order = {"低": 0, "中": 1, "高": 2}
    values = [by_id[i].get("確度") for i in row.get("項目") or () if i in by_id]
    values = [v for v in values if v in order]
    return min(values, key=order.__getitem__) if values else None


def row_flagged(row: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """印 = 確度が低 / 状態が仮説・問い / 検算が食い違った / 数量が未取得 のどれか。"""
    if row.get("数量") is None:
        return True
    for i in row.get("項目") or ():
        it = by_id.get(i) or {}
        if it.get("確度") == "低" or it.get("状態") in FLAG_STATES or it.get("検算"):
            return True
    return False


# --------------------------------------------------------------------------- 突き合わせ


def _clean_reason(match: Mapping[str, Any]) -> str:
    """規則 8 の理由には品名が括弧で入るので、そこを伏せる。"""
    if match.get("規則") == "細目8":
        return "細目8: 構造は取れていないが、揃えた品名と単位が丸ごと同じ(品名は伏せた)"
    return f"{match.get('規則')}: {match.get('理由')}"


def pairs(result: Any, gold: Gold) -> list[tuple[Any, int, dict[str, Any] | None]]:
    """当たった (行, 正解の番号, 理由) の組。`hit_lines` と `matched_items` は同じ順に積まれている。"""
    sames = [m for m in result.matches if m.get("判定") == SAME] if result.rule == "新" else []
    out = []
    for n, (line, item) in enumerate(zip(result.hit_lines, result.matched_items)):
        out.append((line, gold.index[id(item)], sames[n] if n < len(sames) else None))
    return out


def qverdict(line: Mapping[str, Any], gold: Gold, i: int):
    from sameness import quantity_verdict

    return quantity_verdict(line.get("数量"), gold.quantity(i), line.get("単位"), unit_b=gold.unit(i))


def score(rows: Sequence[Mapping[str, Any]], gold: Gold, rule: str = "新"):
    from estimating.scoring import score_lines

    return score_lines(list(rows), gold.items, rule=rule)


def _as_dict(result: Any) -> dict[str, Any]:
    try:
        return result.as_dict()
    except Exception as exc:  # 理由別の集計が辞書の行を受け付けない版でも件数は出す
        return {"言い当てた項目数": len(result.matched_items), "正解の項目数": result.total_items,
                "言い当てた割合": result.coverage, "外した行": len(result.extra_lines),
                "突き合わせの規則": result.rule, "as_dict が失敗した理由": type(exc).__name__}


def detail_counts(result: Any, gold: Gold) -> dict[str, int]:
    ps = pairs(result, gold)
    return {"名前だけ": len(ps),
            "数量あり": sum(1 for line, _, _ in ps if line.get("数量") is not None),
            "数量が合った": sum(1 for line, i, _ in ps if qverdict(line, gold, i).hit)}


def category_agreement(rows: Sequence[Mapping[str, Any]], gold: Gold, rule: str) -> dict[str, Any]:
    """K-66 手順書 1 の 3 節: 正解の科目(93 件だと 8 つ)のうち、出した科目のどれかと同じだった数と金額の被覆。"""
    from sameness import compare

    gold_cats = sorted({gold.fields(i)["科目"] for i in range(len(gold.raw))} - {""})
    out_cats = sorted({nfkc(r.get("科目")) for r in rows} - {""})
    hit: set[str] = set()
    for g in gold_cats:
        for o in out_cats:
            same = (o == g) if rule == "旧" else compare({"科目": o}, {"科目": g}, level="科目").value == SAME
            if same:
                hit.add(g)
                break
    total = sum(gold.amount(i) for i in range(len(gold.raw)))
    covered = sum(gold.amount(i) for i in range(len(gold.raw)) if gold.fields(i)["科目"] in hit)
    return {"一致": len(hit), "正解の科目の数": len(gold_cats),
            "金額の被覆": round(covered / total, 4) if total else None}


def strict_category(result: Any, gold: Gold, level: str) -> dict[str, Any]:
    """当たった組のうち、行の科目(中科目)が正解と同じ(○)だった割合。分母は当たった組。"""
    from sameness import compare

    ps = pairs(result, gold)
    if level == "中科目" and not gold.has["中科目"]:
        return {"値": "未取得(正解に中科目の列が無い)", "分子": None, "分母": len(ps)}
    same = sum(1 for line, i, _ in ps
               if compare(dict(line), gold.fields(i), level=level).value == SAME)
    return {"値": round(same / len(ps), 4) if ps else None, "分子": same, "分母": len(ps),
            "参考: 93 件を分母にした値": round(same / DENOMINATOR, 4)}


def amount_share(rows: Sequence[Mapping[str, Any]], gold: Gold, picked: Sequence[int] | None) -> dict[str, Any]:
    """数量が許容差内の細目の金額 ÷ 総額。**`式` など判定しない単位は分子からも分母からも外す。**"""
    from sameness.quantity import NOT_JUDGED

    subset = [rows[n] for n in picked] if picked is not None else list(rows)
    result = score(subset, gold)
    judged = [i for i in range(len(gold.raw))
              if qverdict({"数量": gold.quantity(i), "単位": gold.unit(i)}, gold, i).value != NOT_JUDGED]
    total = sum(gold.amount(i) for i in judged)
    hit = sum(gold.amount(i) for line, i, _ in pairs(result, gold) if i in set(judged) and qverdict(line, gold, i).hit)
    return {"値": round(hit / total, 4) if total else None, "判定した細目": len(judged), "行": len(subset)}


def mode_rows(draft: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, list[int] | None]:
    """段階ごとに拾う行の番号。K-61 の出力には番号が無いので、無ければ `stages.mode_outputs` で組み直す。"""
    saved = draft["組み立て"].get("段階ごとの出力") or {}
    if all("行の番号" in (saved.get(m) or {}) for m in MODES):
        return {m: list(saved[m]["行の番号"]) for m in MODES}
    from draft import stages

    rebuilt = stages.mode_outputs(list(rows), draft["理解"]["項目"])
    return {m: list(rebuilt[m]["行の番号"]) for m in MODES}


# --------------------------------------------------------------------------- K-66 手順書 1


def k66(run: Path, gold: Gold) -> dict[str, Any]:
    draft = load_run(run)
    rows = rows_of(draft)
    new, old = score(rows, gold, "新"), score(rows, gold, "旧")
    new_hit = {i: (line, m) for line, i, m in pairs(new, gold)}
    old_hit = {i: line for line, i, _ in pairs(old, gold)}
    from sameness import compare

    gained = [_clean_reason(m) if m else "理由なし" for i, (_, m) in new_hit.items() if i not in old_hit]
    lost = []
    for i, line in old_hit.items():
        if i in new_hit:
            continue
        v = compare(dict(line), gold.fields(i))
        lost.append(_clean_reason({"規則": v.rule, "理由": v.reason}) + f"(判定 {v.value})")
    return {
        "対象": f"K-61 {run.name}",
        "規則": {"旧": _as_dict(old), "新": _as_dict(new)},
        "細目": {"旧": detail_counts(old, gold), "新": detail_counts(new, gold)},
        "科目": {"旧": category_agreement(rows, gold, "旧"), "新": category_agreement(rows, gold, "新")},
        "新で当たりになった組の理由": _tally(gained),
        "新で外れになった組の理由": _tally(lost),
        "新で外れになった組の数": len(lost),
        "自動確定": draft["まとめ"].get("自動確定"),
    }


def _tally(texts: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in texts:
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- K-67 手順書 1


def k67(run: Path, gold: Gold) -> dict[str, Any]:
    draft = load_run(run)
    rows = rows_of(draft)
    by_id = {it["id"]: it for it in draft["理解"]["項目"]}
    new, old = score(rows, gold, "新"), score(rows, gold, "旧")
    shares = {m: amount_share(rows, gold, picked) for m, picked in mode_rows(draft, rows).items()}
    extra = list(new.extra_lines)
    hit_ids = {id(line) for line in new.hit_lines}
    high = [r for r in rows if row_confidence(r, by_id) == "高"]
    qhit = {id(line) for line, i, _ in pairs(new, gold) if qverdict(line, gold, i).hit}
    from sameness.quantity import NOT_JUDGED

    shiki = [i for i in range(len(gold.raw))
             if qverdict({"数量": gold.quantity(i), "単位": gold.unit(i)}, gold, i).value == NOT_JUDGED]
    total = sum(gold.amount(i) for i in range(len(gold.raw)))
    new_set = {i for _, i, _ in pairs(new, gold)}
    old_set = {i for _, i, _ in pairs(old, gold)}
    table = [
        ("科目(厳密)", 0.95, strict_category(new, gold, "科目")["値"]),
        ("中科目(厳密)", 0.90, strict_category(new, gold, "中科目")["値"]),
        ("細目(同じ意味。K-66)", 0.70, round(new.coverage, 4) if new.coverage is not None else None),
        ("数量が合った細目の金額の割合(概算)", 0.50, shares["概算"]["値"]),
        ("数量が合った細目の金額の割合(通常)", 0.70, shares["通常"]["値"]),
        ("数量が合った細目の金額の割合(精密)", 0.90, shares["精密"]["値"]),
        ("外れた項目に印が出ていた割合", 0.80,
         round(sum(1 for r in extra if row_flagged(r, by_id)) / len(extra), 4) if extra else None),
        ("確度「高」の的中率", 0.95, round(sum(1 for r in high if id(r) in hit_ids) / len(high), 4) if high else None),
    ]
    return {
        "対象": f"K-61 {run.name}",
        "表": [{"行": n, "合格ライン": line, "出た値": v,
                "合否": "未取得" if not isinstance(v, (int, float)) else ("合格" if v >= line else "未達")}
               for n, line, v in table],
        "補い": {
            "科目(厳密)の分子と分母": strict_category(new, gold, "科目"),
            "中科目(厳密)の分子と分母": strict_category(new, gold, "中科目"),
            "金額の割合の内訳": shares,
            "精密と通常は同じ行で数えている(精密は検算の通過を別に見る)": True,
            "概算の行の出どころ": ("保存された行の番号" if all("行の番号" in ((draft["組み立て"].get("段階ごとの出力") or {}).get(m) or {})
                                         for m in MODES) else "保存に番号が無いので stages.mode_outputs で組み直した"),
            "外れた行の数": len(extra), "出せなかった正解の数": len(new.missed_items),
            "確度「高」の行の数": len(high),
            "確度「高」で数量まで合った割合": round(sum(1 for r in high if id(r) in qhit) / len(high), 4) if high else None,
        },
        "式で判定しなかった細目": {"件数": len(shiki),
                           "金額の割合": round(sum(gold.amount(i) for i in shiki) / total, 4) if total else None},
        "旧と新で細目の当たりが変わった数": {"増えた": len(new_set - old_set), "減った": len(old_set - new_set)},
        "減った組の理由": k66_lost_reasons(rows, gold, old, new) if old_set - new_set else {},
        "自動確定": draft["まとめ"].get("自動確定"),
    }


def k66_lost_reasons(rows: Sequence[Mapping[str, Any]], gold: Gold, old: Any, new: Any) -> dict[str, int]:
    from sameness import compare

    new_set = {i for _, i, _ in pairs(new, gold)}
    out = []
    for line, i, _ in pairs(old, gold):
        if i not in new_set:
            v = compare(dict(line), gold.fields(i))
            out.append(_clean_reason({"規則": v.rule, "理由": v.reason}) + f"(判定 {v.value})")
    return _tally(out)


# --------------------------------------------------------------------------- K-64 手順書 5 の 1


def high_rows(draft: Mapping[str, Any], gold: Gold, *, flag: bool) -> dict[str, Any]:
    from draft import stages

    items = deepcopy(draft["理解"]["項目"])
    capped = stages.cap_by_page_readability(items, draft["読む"]) if flag else None
    by_id = {it["id"]: it for it in items}
    rows = rows_of(draft)
    result = score(rows, gold)
    match = {id(line): i for line, i, _ in pairs(result, gold)}
    high = [r for r in rows if row_confidence(r, by_id) == "高"]
    right = [r for r in high if id(r) in match and qverdict(r, gold, match[id(r)]).hit]
    name_only = [r for r in high if id(r) in match and r not in right]
    return {"高の行": len(high), "正解の数量と合う": len(right),
            "合わない": len(high) - len(right),
            "合わないのうち名前は当たった": len(name_only),
            "下げた項目": (capped or {}).get("高から中に下げた項目")}


def k64(runs: Sequence[Path], gold: Gold) -> dict[str, Any]:
    per = {}
    for run in runs:
        draft = load_run(run)
        per[run.name] = {"旗オフ": high_rows(draft, gold, flag=False), "旗オン": high_rows(draft, gold, flag=True),
                         "自動確定": draft["まとめ"].get("自動確定")}
    s = lambda arm, key: sum(v[arm][key] for v in per.values())
    off_wrong, on_wrong = s("旗オフ", "合わない"), s("旗オン", "合わない")
    off_right, on_right = s("旗オフ", "正解の数量と合う"), s("旗オン", "正解の数量と合う")
    return {"回ごと": per,
            "合計": {"旗オフ": {"合う": off_right, "合わない": off_wrong},
                   "旗オン": {"合う": on_right, "合わない": on_wrong}},
            "線: 旗オンで高なのに違う行が減る": on_wrong < off_wrong,
            "線: 旗オンの高で合う行が旗オフの半分以上残る": on_right * 2 >= off_right,
            "注": "K-64 の線は 12 件の合計。ここは P011 v4 だけなので、案件 1 件ぶんの値"}


# --------------------------------------------------------------------------- K-65 手順書 1 の 3


def k65(run: Path, others: Sequence[Path], checklist: Path | None, ideal: Path, gold: Gold) -> dict[str, Any]:
    from draft import questioning, stages, uncertainty  # questioning は K-65 のブランチにある

    draft = load_run(run)
    other_items = [load_run(p)["理解"]["項目"] for p in others]
    check = json.loads(checklist.read_text(encoding="utf-8")) if checklist else None
    raw = stages.question_candidates(draft["理解"], draft["仕上表"], draft["読む"], None)
    answers = json.loads(ideal.read_text(encoding="utf-8")).get("回答") or []
    by_key = {nfkc(a["鍵"]): a for a in answers}
    out: dict[str, Any] = {"対象": f"K-61 {run.name}", "理想の答えの数": len(answers),
                           "正しい値が選択肢に無かった(型ごと)": {}}
    tables: dict[str, Any] = {}
    for how in ("連鎖の金額順", "ランダム"):
        built = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], other_runs=other_items,
                                  how=how, checklist=check)
        cards = built["カード"]
        if how == "連鎖の金額順":
            missing: dict[str, int] = {}
            for c in cards:
                a = by_key.get(nfkc(c["鍵"]))
                if a and a.get("正しい値が選択肢に無かった"):
                    missing[c["型"]] = missing.get(c["型"], 0) + 1
            out["正しい値が選択肢に無かった(型ごと)"] = missing
            out["カードの数"] = len(cards)
            out["答えの無いカード"] = sum(1 for c in cards if nfkc(c["鍵"]) not in by_key)
        table = []
        for k in COUNTS:
            chosen = [by_key[nfkc(c["鍵"])] for c in cards[:k] if nfkc(c["鍵"]) in by_key]
            understanding, finish = deepcopy(draft["理解"]), deepcopy(draft["仕上表"])
            stages.apply_answers(understanding, finish, {nfkc(a["鍵"]): a["選択肢"] for a in chosen})
            rows = as_lines(stages.assembly_rows(understanding["項目"])[0])
            new = score(rows, gold)
            states = uncertainty.classify(understanding["項目"], finish=finish, other_runs=other_items)
            table.append({
                "問数": k, "戻した答え": len(chosen),
                "科目(厳密)": strict_category(new, gold, "科目")["値"],
                "中科目(厳密)": strict_category(new, gold, "中科目")["値"],
                "細目(同じ意味)": round(new.coverage, 4) if new.coverage is not None else None,
                "数量一致": detail_counts(new, gold)["数量が合った"],
                "数量が合った金額の割合": amount_share(rows, gold, None)["値"],
                "未確定の残り": states["3状態の分布"][uncertainty.UNSETTLED],
                "回答時間(秒)": sum(a.get("秒") or 0 for a in chosen),
            })
        tables[how] = table
    tables["質問ゼロ"] = [tables["連鎖の金額順"][0]]
    out["表"] = tables
    out["自動確定"] = draft["まとめ"].get("自動確定")
    return out


def cards(run: Path, others: Sequence[Path], checklist: Path | None) -> list[dict[str, Any]]:
    """PC-4 の前段: 理想の答えを作るためのカードを書き出す(正解は読まない)。"""
    from draft import questioning, stages

    draft = load_run(run)
    other_items = [load_run(p)["理解"]["項目"] for p in others]
    check = json.loads(checklist.read_text(encoding="utf-8")) if checklist else None
    raw = stages.question_candidates(draft["理解"], draft["仕上表"], draft["読む"], None)
    return questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], other_runs=other_items,
                             checklist=check)["カード"]



# --------------------------------------------------------------------------- K-71 作業1 性質ごとの線


def natures_of(gold: Gold) -> list[Any]:
    """正解の 93 行の性質(訂正後の区分で)。`estimating.row_nature.classify` だけを使う。"""
    from estimating.row_nature import classify

    return [classify(gold.fields(i), gold.source(i)) for i in range(len(gold.raw))]


def nature_table(gold: Gold, natures: Sequence[Any]) -> dict[str, Any]:
    from estimating.row_nature import UNCLASSIFIED

    counts = {str(k): 0 for k in (1, 2, 3, 4, 5, 6)}
    counts[UNCLASSIFIED] = 0
    reasons: dict[str, int] = {}
    unclassified = []
    for i, nat in enumerate(natures):
        counts[str(nat.value)] += 1
        reasons[f"{nat.value}: {nat.reason}"] = reasons.get(f"{nat.value}: {nat.reason}", 0) + 1
        if nat.value == UNCLASSIFIED:
            unclassified.append(f"{gold.code(i)}: {nat.reason}")  # G 番号と理由だけ(名前は出さない)
    return {"件数": counts, "合計": sum(counts.values()), "未分類の数": counts[UNCLASSIFIED],
            "未分類の理由(1 行ずつ)": unclassified, "振り分けの根拠ごとの件数": dict(sorted(reasons.items()))}


def _ratio(num: int, den: int) -> dict[str, Any]:
    return {"値": round(num / den, 4) if den else None, "分子": num, "分母": den}


def summed_hits(rows: Sequence[Mapping[str, Any]], gold: Gold, nat: Sequence[Any]) -> dict[str, Any]:
    """K-73 作業2: 行を同じ鍵で足し上げた組で突き合わせる(`docs/k73_flags_sum_criteria.md` 3.2)。

    ① 組 ↔ 正解の 1 行を今の規則(符号 → 構造のキー `○` → 規則 8)で当てる(1 組は 1 行まで)。
    ② 当たらなかった正解の行に、まだ使っていない `△粒度` の組を集めて合計し、合計と位置が合えば格上げする。
    返す: 組の行(`lines`)・当たり(`hit`: 正解の番号 → 組の行)・内訳(`members`: id(組の行) → まとめる前の行)と数。
    """
    from estimating.summed_scoring import HAS_MISSING, grain_upgrade, group_line, group_rows, merge

    groups = group_rows(list(rows))
    lines = as_lines([group_line(g) for g in groups])
    members = {id(line): g.rows for line, g in zip(lines, groups)}
    index = {id(line): n for n, line in enumerate(lines)}
    result = score(lines, gold, "新")
    hit = {i: line for line, i, _ in pairs(result, gold)}
    taken = {index[id(line)] for line in hit.values()}
    promoted = stopped = 0
    for i in range(len(gold.raw)):
        if i in hit:
            continue
        free = [n for n in range(len(groups)) if n not in taken]
        res = grain_upgrade(gold.fields(i), [groups[n] for n in free], gold_quantity=gold.quantity(i),
                            gold_unit=gold.unit(i), nature=nat[i])
        if res["判定"] is not None:
            picked = [free[k] for k in res["組"]]
            merged = merge([groups[n] for n in picked])
            line = Line(group_line(merged))
            line["格上げ"] = res["理由"]
            members[id(line)] = merged.rows
            hit[i] = line
            taken.update(picked)
            promoted += 1
        elif res["組"] and str(res["理由"]).startswith(HAS_MISSING):
            stopped += 1
    return {"lines": lines, "hit": hit, "members": members, "result": result,
            "数": {"組の数": len(groups), "格上げで当たった": promoted, "格上げの候補が未取得ありで止まった": stopped,
                  "未取得ありで当たった組": sum(1 for line in hit.values() if line.get("合計の状態") == HAS_MISSING),
                  "当たった組に入った行の最大": max((len(members[id(line)]) for line in hit.values()), default=0)}}


def shares_from_hit(hit: Mapping[int, Mapping[str, Any]], gold: Gold, nat: Sequence[Any]) -> dict[str, Any]:
    """金額の割合(旧の線・新の線)を、足し上げの当たりから数える(`amount_share`・`amount_share_new` と同じ分子・分母)。"""
    from sameness.quantity import MATCH, NOT_JUDGED, quantity_verdict

    judged = [i for i in range(len(gold.raw))
              if qverdict({"数量": gold.quantity(i), "単位": gold.unit(i)}, gold, i).value != NOT_JUDGED]
    total = sum(gold.amount(i) for i in judged)
    got = sum(gold.amount(i) for i in judged if i in hit and qverdict(hit[i], gold, i).hit)
    judged_new = [i for i in range(len(gold.raw)) if nat[i] in (1, 2, 3)]
    total_new = sum(gold.amount(i) for i in judged_new)
    got_new = sum(gold.amount(i) for i in judged_new if i in hit and quantity_verdict(
        hit[i].get("数量"), gold.quantity(i), hit[i].get("単位"), unit_b=gold.unit(i), side="新", nature=nat[i]).value == MATCH)
    return {"旧": round(got / total, 4) if total else None,
            "新": {"値": round(got_new / total_new, 4) if total_new else None, "判定した細目": len(judged_new)}}


def k71(run: Path, gold: Gold, natures: Sequence[Any] | None = None, *, rows: Sequence[Mapping[str, Any]] | None = None,
        extra_items: Mapping[str, Mapping[str, Any]] | None = None, summed: bool = False,
        draft: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """K-71 作業1: 性質ごとの線で採点し、旧い線と並べる(`docs/k71_scoring_lines_criteria.md`)。

    K-73: `rows`(採点用の行。旗の見方)と `extra_items`(旗が足したものの id → 状態・確度)を渡せる。
    `summed=True` で内訳の足し上げを入れる(`docs/k73_flags_sum_criteria.md` 3 節)。既定は今までと同じ。
    """
    from draft import scorecard
    from estimating.row_nature import UNCLASSIFIED, row_reasons
    from estimating.summed_scoring import HAS_MISSING, all_reasons
    from sameness.quantity import (BY_REASON, MATCH, MISSING, NEAR, NOT_JUDGED, new_unit,
                                   quantity_verdicts)

    natures = list(natures if natures is not None else natures_of(gold))
    nat = [n.value for n in natures]
    draft = draft if draft is not None else load_run(run)
    base = as_lines(rows) if rows is not None else rows_of(draft)
    by_id = {it["id"]: it for it in draft["理解"]["項目"]}
    by_id.update(extra_items or {})
    checks = list(((draft.get("機械の検算") or {}).get("行ごとの要確認")) or [])
    at = lambda line, n: row_reasons(line, by_id, checks[n] if n < len(checks) else None)
    position = {id(r): n for n, r in enumerate(base)}
    summed_info = None
    if summed:
        summed_info = summed_hits(base, gold, nat)
        rows = summed_info["lines"]
        members = summed_info["members"]
        reasons_of = lambda line: all_reasons(line, members[id(line)], at) if id(line) in members else []
        new, old = summed_info["result"], score(rows, gold, "旧")
        hit = dict(summed_info["hit"])
    else:
        rows = base
        reasons_of = lambda line: at(line, position[id(line)]) if id(line) in position else row_reasons(line, by_id, None)
        new, old = score(rows, gold, "新"), score(rows, gold, "旧")
        hit = {i: line for line, i, _ in pairs(new, gold)}
    verdict = {i: quantity_verdicts(line.get("数量"), gold.quantity(i), line.get("単位"), unit_b=gold.unit(i),
                                    nature=nat[i] if isinstance(nat[i], int) else None)
               for i, line in hit.items()}

    # --- 性質ごと ---
    per: dict[str, Any] = {}
    for k in (1, 2, 3):
        idx = [i for i in range(len(gold.raw)) if nat[i] == k]
        got = [i for i in idx if i in hit]
        row = {"正解の行": len(idx), "当たった": len(got),
               "数量あり": sum(1 for i in got if hit[i].get("数量") is not None),
               "新: 合う": sum(1 for i in got if verdict[i]["新"].value == MATCH),
               "新: 近い(±30%)": sum(1 for i in got if verdict[i]["新"].value == NEAR),
               "新: 違う": sum(1 for i in got if verdict[i]["新"].value == "違う"),
               "新: 比較不能(未取得)": sum(1 for i in got if verdict[i]["新"].value == MISSING),
               "旧: 合う(±1 / ±5%、単位の同値なし)": sum(1 for i in got if verdict[i]["旧"].hit),
               "理由つきの分からない(数量が未取得で理由つき。合否の分子に入れない)":
                   sum(1 for i in got if hit[i].get("数量") is None and reasons_of(hit[i])),
               "数量が未取得で理由も無い": sum(1 for i in got if hit[i].get("数量") is None and not reasons_of(hit[i])),
               "出していない": len(idx) - len(got)}
        if summed:
            row["うち未取得あり(足し上げで合計を出さなかった)"] = sum(
                1 for i in got if hit[i].get("合計の状態") == HAS_MISSING)
        row["合否の割合(新: 合う ÷ 正解の行)"] = _ratio(row["新: 合う"], len(idx))
        row["参考: 旧の線で同じ割合"] = _ratio(row["旧: 合う(±1 / ±5%、単位の同値なし)"], len(idx))
        per[f"性質{k}"] = row
    idx45 = [i for i in range(len(gold.raw)) if nat[i] in (4, 5)]
    passed = [i for i in idx45 if i in hit and reasons_of(hit[i])]
    no_reason = [i for i in idx45 if i in hit and not reasons_of(hit[i])]
    kinds: dict[str, int] = {}
    for i in passed:
        for r in reasons_of(hit[i]):
            kinds[r] = kinds.get(r, 0) + 1
    per["性質4・5"] = {"正解の行": len(idx45), "性質4": sum(1 for i in idx45 if nat[i] == 4),
                     "性質5": sum(1 for i in idx45 if nat[i] == 5),
                     "理由で合格": len(passed), "理由なし": len(no_reason),
                     "出していない": len(idx45) - len(passed) - len(no_reason),
                     "理由なしのうち数量を出していた(根拠なしの数量)": sum(1 for i in no_reason if hit[i].get("数量") is not None),
                     "理由の種類(重なる)": kinds, "合否の割合": _ratio(len(passed), len(idx45))}
    idx6 = [i for i in range(len(gold.raw)) if nat[i] == 6]
    per["性質6"] = {"正解の行": len(idx6), "名前で当たった": sum(1 for i in idx6 if i in hit),
                   "合否の割合": _ratio(sum(1 for i in idx6 if i in hit), len(idx6))}
    unclassified = sum(1 for v in nat if v == UNCLASSIFIED)

    # --- 根拠なしの数量(当たった行。合否に使わない) ---
    from estimating.row_nature import output_counts

    no_basis = {}
    for i, line in hit.items():
        if line.get("数量") is not None and not reasons_of(line):
            no_basis[str(nat[i])] = no_basis.get(str(nat[i]), 0) + 1

    # --- 確度(4 節) ---
    high = [r for r in rows if row_confidence(r, by_id) == "高"]
    hit_ids = {id(line): i for i, line in hit.items()}
    high_hit = [r for r in high if id(r) in hit_ids]
    high_q = [r for r in high_hit if r.get("数量") is not None and nat[hit_ids[id(r)]] in (1, 2, 3)]
    high_q_new = sum(1 for r in high_q if verdict[hit_ids[id(r)]]["新"].value == MATCH)
    high_q_old = sum(1 for r in high_q if verdict[hit_ids[id(r)]]["旧"].hit)
    confidence = {
        "「高」の行": len(high), "正解の行に当たった": len(high_hit),
        "当たらなかった(正解に無い行。誤りに数えない)": len(high) - len(high_hit),
        "名前・有無・状態の的中": _ratio(len(high_hit), len(high_hit)),
        "数量の的中(性質1〜3、新の線)": _ratio(high_q_new, len(high_q)),
        "数量の的中(性質1〜3、旧の線)": _ratio(high_q_old, len(high_q)),
        "旧い測り方(高の行のうち当たった割合)": _ratio(len(high_hit), len(high)),
    }

    # --- 外れた項目に印(新: 数量が違う行に理由) ---
    wrong = [i for i in hit if nat[i] in (1, 2, 3) and verdict[i]["新"].value == "違う"]
    flagged_new = sum(1 for i in wrong if reasons_of(hit[i]))
    extra = list(new.extra_lines)
    flagged_old = sum(1 for r in extra if row_flagged(r, by_id))

    # --- 金額(仮)(5 節) ---
    money = amount_by_category(gold, nat, hit)

    # --- 金額の割合 旧・新 ---
    shares = {}
    for m, picked in mode_rows(draft, base).items():
        if summed:
            shares[m] = shares_from_hit(summed_hits([base[n] for n in picked], gold, nat)["hit"], gold, nat)
        else:
            shares[m] = {"旧": amount_share(rows, gold, picked)["値"], "新": amount_share_new(rows, gold, picked, nat)}

    # --- 診断: 単位が違う ---
    pairs_old: dict[str, int] = {}
    pairs_new: dict[str, int] = {}
    for i, line in hit.items():
        v = verdict[i]
        if v["新"].value in (MATCH, BY_REASON, "有無だけ"):
            continue
        a, b = canonical(line.get("単位")), canonical(gold.unit(i))
        if a != b:
            pairs_old[f"{a or '(空)'} → {b or '(空)'}"] = pairs_old.get(f"{a or '(空)'} → {b or '(空)'}", 0) + 1
        na, nb = new_unit(line.get("単位")), new_unit(gold.unit(i))
        if na and nb and na != nb:
            pairs_new[f"{na} → {nb}"] = pairs_new.get(f"{na} → {nb}", 0) + 1

    values = {
        "科目(厳密)": strict_category(new, gold, "科目")["値"],
        "中科目(厳密)": strict_category(new, gold, "中科目")["値"],
        "細目(同じ意味。K-66)": (round(len(hit) / len(gold.raw), 4) if summed
                             else (round(new.coverage, 4) if new.coverage is not None else None)),
        "性質1 個数(±10%、少ないとき±1)": per["性質1"]["合否の割合(新: 合う ÷ 正解の行)"]["値"],
        "性質2 面積・長さ(±10%)": per["性質2"]["合否の割合(新: 合う ÷ 正解の行)"]["値"],
        "性質3 派生(±15%)": per["性質3"]["合否の割合(新: 合う ÷ 正解の行)"]["値"],
        "性質4・5 理由で合格": per["性質4・5"]["合否の割合"]["値"],
        "性質6 波及の有無": per["性質6"]["合否の割合"]["値"],
        "未分類の行": unclassified,
        "外れた項目に印が出ていた割合": round(flagged_new / len(wrong), 4) if wrong else None,
        "確度「高」の的中率": confidence["名前・有無・状態の的中"]["値"],
        "確度「高」の的中率(数量、性質1〜3)": confidence["数量の的中(性質1〜3、新の線)"]["値"],
    }
    values = {k: ("未取得(分母が 0)" if v is None else v) for k, v in values.items()}
    card = scorecard.build(golden=values)
    stage3 = [r for r in card["行"] if r["段階"] == 3 or r["名前"] in values]
    return {
        "対象": f"K-61 {run.name}",
        "性質ごと": per,
        "未分類の行": unclassified,
        "段階3の表": stage3,
        "段階3の判定": card["段階ごと"].get("段階3 下書き"),
        "旧と新": {
            "細目の数量が合った件数": {"旧": (sum(1 for i in hit if qverdict(hit[i], gold, i).hit) if summed
                                     else detail_counts(new, gold)["数量が合った"]),
                              "新": sum(1 for i in hit if verdict[i]["新"].value == MATCH),
                              "分母(当たった組)": len(hit)},
            "数量が合った細目の金額の割合": shares,
            "確度「高」の的中率": {"旧": confidence["旧い測り方(高の行のうち当たった割合)"],
                            "新(名前・有無・状態)": confidence["名前・有無・状態の的中"],
                            "新(数量、性質1〜3)": confidence["数量の的中(性質1〜3、新の線)"]},
            "外れた項目に印": {"旧(正解に無い行のうち印)": _ratio(flagged_old, len(extra)),
                         "新(数量が違う行のうち理由つき)": _ratio(flagged_new, len(wrong))},
        },
        "確度": confidence,
        "根拠なしの数量(当たった行、性質ごと。合否に使わない)": no_basis,
        "出力側の数(正解を使わない)": output_counts(draft),
        "金額(仮)": money,
        "診断: 数量が合わなかった細目の単位の組(出力 → 正解)": {"旧の正規形": pairs_old, "新の同値を通した後": pairs_new},
        "旧規則(文字)の参考": {"名前だけ": detail_counts(old, gold)["名前だけ"]},
        **({"足し上げ": {**summed_info["数"],
                         "注": "組 = 同じ鍵(構造のキー+科目+新の単位。室は入れない)の行。未取得が 1 つでもある組は合計を出さない。"
                               "科目・中科目(厳密)は格上げ前の当たりで数える"}} if summed else {}),
        "自動確定": draft["まとめ"].get("自動確定"),
    }


def canonical(unit: Any) -> str:
    from sameness.normalize import canonical_unit

    return canonical_unit(unit)


def amount_share_new(rows: Sequence[Mapping[str, Any]], gold: Gold, picked: Sequence[int] | None,
                     nat: Sequence[Any]) -> dict[str, Any]:
    """新: 性質 1〜3 の細目の金額のうち、新の線で数量が合った細目の金額の割合。"""
    from sameness.quantity import MATCH, quantity_verdict

    subset = [rows[n] for n in picked] if picked is not None else list(rows)
    result = score(subset, gold)
    judged = {i for i in range(len(gold.raw)) if nat[i] in (1, 2, 3)}
    total = sum(gold.amount(i) for i in judged)
    got = sum(gold.amount(i) for line, i, _ in pairs(result, gold) if i in judged and quantity_verdict(
        line.get("数量"), gold.quantity(i), line.get("単位"), unit_b=gold.unit(i), side="新", nature=nat[i]).value == MATCH)
    return {"値": round(got / total, 4) if total else None, "判定した細目": len(judged), "行": len(subset)}


LOW_COVERAGE = 0.50
"""被覆がこれ未満の科目は近さを出さない(基準の仮の判断 3)。"""


def amount_by_category(gold: Gold, nat: Sequence[Any], hit: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """金額(仮)。数量に正解の参考単価を借りる。**単価の当たり外れは測らない。**科目の名前は番号に置き換える。"""
    cats: dict[str, list[int]] = {}
    for i in range(len(gold.raw)):
        cats.setdefault(gold.fields(i)["科目"] or "(空)", []).append(i)

    def near(est: float, ref: float) -> dict[str, Any]:
        if not ref:
            return {"±15%": None, "±30%": None, "比": None}
        r = est / ref
        return {"±15%": abs(r - 1) <= 0.15 + 1e-9, "±30%": abs(r - 1) <= 0.30 + 1e-9, "比": round(r, 4)}

    out = []
    all_est = all_ref = 0.0
    skipped = 0
    for n, (_, idx) in enumerate(sorted(cats.items(), key=lambda kv: -sum(gold.amount(i) for i in kv[1]))):
        total = sum(gold.amount(i) for i in idx)
        picked = []
        for i in idx:
            line = hit.get(i)
            q = gold.quantity(i)
            if nat[i] in (1, 2, 3) and line is not None and line.get("数量") is not None and q:
                try:
                    picked.append((i, float(line["数量"]) * gold.amount(i) / q))
                except (TypeError, ValueError):
                    skipped += 1
        ref = sum(gold.amount(i) for i, _ in picked)
        est = sum(e for _, e in picked)
        all_est += est
        all_ref += ref
        coverage = ref / total if total else None
        row = {"科目": f"科目{n + 1}(金額の大きい順)", "正解の行": len(idx), "拾えた行": len(picked),
               "被覆": round(coverage, 4) if coverage is not None else None}
        if coverage is None or coverage < LOW_COVERAGE:
            row["近さ"] = "被覆が低いので出さない"
        else:
            row["近さ"] = near(est, ref)
        out.append(row)
    return {"科目ごと": out, "全体の合計の近さ(参考。拾えた行の合計で)": near(all_est, all_ref),
            "全体の被覆(参考)": round(all_ref / sum(gold.amount(i) for i in range(len(gold.raw))), 4)
            if sum(gold.amount(i) for i in range(len(gold.raw))) else None,
            "数に読めず外した行": skipped,
            "注": "単価は正解から借りる(利益の幅はこの測定に入らない)。被覆が 0.50 未満の科目は近さを出さない"}


def load_errata(path: Path | None) -> dict[str, Any]:
    if path is None:
        default = Path("正誤表_区分.json")
        if not default.exists():
            raise SystemExit("正誤表_区分.json が見つかりません(荷物A の一番上にあります)。--errata で場所を渡してください")
        path = default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def k71_all(runs: Sequence[Path], gold: Gold, errata: Mapping[str, Any]) -> dict[str, Any]:
    from estimating.row_nature import errata_counts

    natures = natures_of(gold)
    return {"正誤表(機械形だけから数えた件数)": errata_counts(errata),
            "正誤表を当てた結果": gold.errata_result,
            "性質の振り分け": nature_table(gold, natures),
            "回ごと": [k71(r, gold, natures) for r in runs]}

# --------------------------------------------------------------------------- K-73 旗オン/オフ × 足し上げ 旧/新


SCORINGS = (("旧(足し上げなし)", False), ("新(足し上げ)", True))


def _summary_row(name: str, how: str, label: str, r: Mapping[str, Any]) -> dict[str, Any]:
    """PC が返す 1 行(回 × 見方 × 採点)。割合は分子/分母で読めるように数で並べる。"""
    per = r["性質ごと"]
    diag = r["診断: 数量が合わなかった細目の単位の組(出力 → 正解)"]["新の同値を通した後"]
    money = r["金額(仮)"]
    summed = r.get("足し上げ") or {}
    p = lambda k, f: per[k][f]
    return {
        "回": name, "見方": how, "採点": label,
        "個数: 合う": p("性質1", "新: 合う"), "個数: 正解の行": p("性質1", "正解の行"), "個数: 出していない": p("性質1", "出していない"),
        "面積・長さ: 合う": p("性質2", "新: 合う"), "面積・長さ: 近い": p("性質2", "新: 近い(±30%)"),
        "面積・長さ: 正解の行": p("性質2", "正解の行"), "面積・長さ: 出していない": p("性質2", "出していない"),
        "派生: 合う": p("性質3", "新: 合う"), "派生: 正解の行": p("性質3", "正解の行"), "派生: 出していない": p("性質3", "出していない"),
        "性質1〜3: 未取得で比べられない": sum(p(f"性質{k}", "新: 比較不能(未取得)") for k in (1, 2, 3)),
        "性質1〜3: うち未取得あり(足し上げ)": sum(per[f"性質{k}"].get("うち未取得あり(足し上げで合計を出さなかった)", 0)
                                     for k in (1, 2, 3)),
        "会社ルール・職人見積: 理由で合格": per["性質4・5"]["理由で合格"], "会社ルール・職人見積: 正解の行": per["性質4・5"]["正解の行"],
        "波及: 当たった": per["性質6"]["名前で当たった"], "波及: 正解の行": per["性質6"]["正解の行"],
        "確度「高」の行": r["確度"]["「高」の行"], "確度「高」で正解の行に当たった": r["確度"]["正解の行に当たった"],
        "金額: 全体の被覆(参考)": money.get("全体の被覆(参考)"),
        "金額: 被覆が 0.50 以上の科目": sum(1 for c in money["科目ごと"] if (c.get("被覆") or 0) >= LOW_COVERAGE),
        "単位が違う組(新の同値の後)": sum(diag.values()),
        "格上げで当たった": summed.get("格上げで当たった"),
        "格上げの候補が未取得ありで止まった": summed.get("格上げの候補が未取得ありで止まった"),
        "組の数": summed.get("組の数"),
        "自動確定": r.get("自動確定"),
    }


def _natures_hit(r: Mapping[str, Any]) -> int:
    return sum(r["性質ごと"][f"性質{k}"]["新: 合う"] for k in (1, 2, 3))


def k73(run: Path, gold: Gold, natures: Sequence[Any] | None = None) -> dict[str, Any]:
    """K-73 作業1・2: 旗オフ / 旗オン(埋める)/ 旗オン(足す)× 足し上げ 旧 / 新 を並べる(`docs/k73_flags_sum_criteria.md`)。

    回は旗オンの `下書き.json`(「旗の部品」の欄がある)。旗オフの見方は同じ下書きの組み立ての行そのもの
    (旗は組み立てを書き換えないので、K-69 の荷物B の同じ回と同じ行)。「旗の部品」が無い回は 3 つの見方が同じになる。
    """
    from estimating.summed_scoring import VIEWS, decoy_rows, flag_items, flag_view

    natures = list(natures if natures is not None else natures_of(gold))
    draft = load_run(run)
    extra = flag_items(draft)
    out: dict[str, Any] = {"対象": f"K-61 {run.name}", "旗の部品": "あり" if "旗の部品" in draft else "なし(3 つの見方は同じ)",
                           "見方": {}, "要約": []}
    for how in VIEWS:
        view = flag_view(draft, how)
        entry: dict[str, Any] = {"行の作り方": view["数"]}
        for label, summed in SCORINGS:
            r = k71(run, gold, natures, rows=view["行"], extra_items=extra, summed=summed, draft=draft)
            entry[label] = r
            out["要約"].append(_summary_row(run.name, how, label, r))
        decoy = k71(run, gold, natures, rows=decoy_rows(view["行"]), extra_items=extra, summed=True, draft=draft)
        real = _natures_hit(entry["新(足し上げ)"])
        entry["囮(新、数量を入れ替え)"] = {"性質1〜3 新: 合う(本物)": real, "性質1〜3 新: 合う(囮)": _natures_hit(decoy),
                                       "本物が囮より多い": real > _natures_hit(decoy), "注": "参考。合否に入れない(基準 3.4)"}
        out["見方"][how] = entry
    flag_auto = ((draft.get("旗の部品") or {}).get("検算(旗の行を足した)") or {}).get("自動確定")
    autos = [draft["まとめ"].get("自動確定"), flag_auto]
    out["自動確定"] = sum(a for a in autos if isinstance(a, int)) if any(isinstance(a, int) for a in autos) else None
    out["自動確定の内訳"] = {"組み立て": autos[0], "旗の行を足した": flag_auto}
    return out


def k73_all(runs: Sequence[Path], gold: Gold, errata: Mapping[str, Any] | None) -> dict[str, Any]:
    natures = natures_of(gold)
    per = [k73(r, gold, natures) for r in runs]
    return {"正誤表を当てた結果": gold.errata_result, "性質の振り分け": nature_table(gold, natures),
            "要約(回 × 見方 × 採点)": [row for r in per for row in r["要約"]], "回ごと": per}


def k73_output_side(run: Path) -> dict[str, Any]:
    """K-73: **正解を使わずに**数えられる数(クラウドで出す)。見方ごとに、行・理由・確度・足し上げの組。"""
    from estimating.row_nature import _confidence, row_reasons
    from estimating.summed_scoring import VIEWS, flag_items, flag_view, output_counts

    draft = load_run(run)
    by_id = {it["id"]: it for it in draft["理解"]["項目"]}
    by_id.update(flag_items(draft))
    checks = list(((draft.get("機械の検算") or {}).get("行ごとの要確認")) or [])
    flags = draft.get("旗の部品") or {}
    out: dict[str, Any] = {"対象": run.name, "見方": {}}
    if flags:
        out["旗の部品"] = {
            "まとめ": flags.get("まとめ"),
            "部品ごとの動いたか": {k: v.get("動いたか") for k, v in (flags.get("部品") or {}).items()},
            "検算(旗の行を足した)": flags.get("検算(旗の行を足した)"),
            "作り方": flags.get("作り方(K-73)"),
        }
    for how in VIEWS:
        view = flag_view(draft, how)
        rows = view["行"]
        reasons = [row_reasons(r, by_id, checks[n] if n < len(checks) else None) for n, r in enumerate(rows)]
        out["見方"][how] = {
            "行の作り方": view["数"],
            "行": {"行の数": len(rows), "数量のある行": sum(1 for r in rows if r.get("数量") is not None),
                  "数量が未取得の行": sum(1 for r in rows if r.get("数量") is None),
                  "理由つきの行": sum(1 for x in reasons if x),
                  "数量が未取得で理由つき": sum(1 for r, x in zip(rows, reasons) if r.get("数量") is None and x),
                  "確度「高」の行": sum(1 for r in rows if _confidence(r, by_id) == "高")},
            "足し上げ": output_counts(rows),
        }
    out["自動確定"] = draft["まとめ"].get("自動確定")
    out["旗の行を足した自動確定"] = (flags.get("検算(旗の行を足した)") or {}).get("自動確定") if flags else None
    return out


# --------------------------------------------------------------------------- 入口


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="what", required=True)
    c = sub.add_parser("列")
    c.add_argument("golden", type=Path)
    k = sub.add_parser("カード")
    k.add_argument("--run", type=Path, required=True)
    k.add_argument("--other", type=Path, action="append", default=[])
    k.add_argument("--checklist", type=Path, default=None)
    k.add_argument("--out", type=Path, default=None)
    o = sub.add_parser("k73-出力側")
    o.add_argument("--run", type=Path, action="append", required=True)
    o.add_argument("--out", type=Path, default=None)
    for name in ("k66", "k67", "k64", "k65", "k71", "k73"):
        s = sub.add_parser(name)
        s.add_argument("--run", type=Path, action="append", required=True)
        s.add_argument("--golden", type=Path, required=True)
        s.add_argument("--col", action="append", default=[], help="符号/品名/単位/科目/中科目/数量/金額/区分=列名")
        s.add_argument("--out", type=Path, default=None)
        if name == "k73":
            s.add_argument("--errata", type=Path, default=None, help="正誤表_区分.json(既定は今のフォルダ。K-71 と同じ)")
        if name == "k71":
            s.add_argument("--errata", type=Path, default=None, help="正誤表_区分.json(既定は今のフォルダ)")
        if name == "k65":
            s.add_argument("--other", type=Path, action="append", default=[])
            s.add_argument("--checklist", type=Path, default=None)
            s.add_argument("--ideal", type=Path, required=True)
    a = p.parse_args(argv)
    if a.what == "列":
        result: Any = columns(a.golden)
    elif a.what == "カード":
        result = cards(a.run, a.other, a.checklist)
    elif a.what == "k73-出力側":
        result = [k73_output_side(r) for r in a.run]
    elif a.what == "k73":
        errata = load_errata(a.errata)
        result = k73_all(a.run, Gold(a.golden, parse_cols(a.col), errata=errata), errata)
    elif a.what == "k71":
        errata = load_errata(a.errata)
        result = k71_all(a.run, Gold(a.golden, parse_cols(a.col), errata=errata), errata)
    else:
        gold = Gold(a.golden, parse_cols(a.col))
        if a.what == "k66":
            result = [k66(r, gold) for r in a.run]
        elif a.what == "k67":
            result = [k67(r, gold) for r in a.run]
        elif a.what == "k64":
            result = k64(a.run, gold)
        else:
            result = k65(a.run[0], a.other, a.checklist, a.ideal, gold)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if getattr(a, "out", None):
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    found = result if isinstance(result, list) else (result.get("回ごと") if isinstance(result, Mapping) and "回ごと" in result and isinstance(result["回ごと"], list) else [result])
    autos = [r.get("自動確定") for r in found if isinstance(r, Mapping)]
    if any(isinstance(x, int) and x > 0 for x in autos):
        print("自動確定が 1 件以上あります。ここで止めて報告してください", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
