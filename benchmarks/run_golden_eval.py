"""ゴールデンベンチマーク renovation-golden-001(99 項目)との突き合わせ。

**抽出と採点を分ける(ベンチマーク自身の運用規則)**

`usage_policy` に次のようにある。

    expected_items are used only after estimate generation for scoring.
    Source quantities and prices must not be exposed to generation agents.

読み取り側が正解を見た状態で測ると数字が無意味になるので、このファイルは
2 つの関数をはっきり分けてある。

- `extract_from_drawing(pdf_path)` … **引数は PDF だけ。** ゴールデンも
  見積明細書も読まない。図面から取れる証拠だけを返す。
- `score(evidence, golden_path)` … 上の戻り値とゴールデンを突き合わせる。
  ここで初めて正解に触れる。

この順序を守る限り、抽出側は正解を一度も見ない。

**何を測るのか**

数量そのものではなく、まず **証拠が図面から出てくるか**を測る。
ゴールデンの各項目は `trigger_terms`(その項目を起こすきっかけになる語)と
`recommended_pages`(それが書かれているはずのページ)を持っているので、
「そのページから抽出した文字に trigger_terms が現れるか」を見れば、
**図面がその項目を支持できるかどうか**が、正解の数量を見ずに測れる。
これは `acceptance_thresholds.evidence_coverage`(0.9)に対応する。

数量は、図面から実際に導けたものだけを出す。導けないものを埋めない。

実行: ``python -m benchmarks.run_golden_eval --pdf <図面.pdf> --golden <golden.json>``
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axes.image_axis.pdf_pages import rasterize
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale, find_door_arcs

#: 平面図のページ(0 始まり)。縮尺と建具記号はここから取る。
PLAN_PAGE_INDEX = 7

#: 面積の記載を拾う正規表現。`専有延床面積 95.54 ㎡` のような並びを想定。
_AREA_RE = re.compile(r"(専有延床面積|施工床面積)\s*\n?\s*([0-9]+(?:\.[0-9]+)?)")


@dataclass
class DrawingEvidence:
    """図面だけから取り出した証拠。**正解データは一切含まない。**"""

    page_texts: dict[int, str] = field(default_factory=dict)
    """1 始まりのページ番号 → そのページの文字列。"""

    scale: DrawingScale | None = None
    door_arc_count: int = 0
    door_arc_widths_mm: list[float] = field(default_factory=list)
    areas_sqm: dict[str, float] = field(default_factory=dict)
    total_text_spans: int = 0
    pages_with_text: int = 0
    pages_total: int = 0

    def text_of(self, pages: list[int]) -> str:
        """指定ページ(1 始まり)の文字列を連結する。"""
        return "\n".join(self.page_texts.get(p, "") for p in pages)

    @property
    def all_text(self) -> str:
        return "\n".join(self.page_texts.values())


# ---------------------------------------------------------------------------
# 第1段階: 図面だけを読む
# ---------------------------------------------------------------------------


def extract_from_drawing(pdf_path: str | Path, dpi: int = 72) -> DrawingEvidence:
    """**PDF だけ**を読んで、図面から取れる証拠を返す。

    ゴールデンも見積明細書も開かない。引数にも取らない。
    """
    pages = rasterize(pdf_path, dpi=dpi)
    evidence = DrawingEvidence(
        page_texts={p.page_index + 1: p.text for p in pages},
        total_text_spans=sum(p.text_span_count for p in pages),
        pages_with_text=sum(1 for p in pages if p.text_span_count),
        pages_total=len(pages),
    )

    evidence.scale = extract_scale(pdf_path, PLAN_PAGE_INDEX)
    if evidence.scale is not None:
        arcs = find_door_arcs(pdf_path, PLAN_PAGE_INDEX, evidence.scale)
        evidence.door_arc_count = len(arcs)
        evidence.door_arc_widths_mm = [round(a.width_mm, 1) for a in arcs]

    for label, value in _AREA_RE.findall(evidence.all_text):
        evidence.areas_sqm[label] = float(value)

    return evidence


# ---------------------------------------------------------------------------
# 第2段階: ここで初めて正解に触れる
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """全角・半角と記号のゆれを吸収して突き合わせやすくする。

    **半角カタカナの正規化が要る。** この図面は注記を半角カタカナで書いており
    (`ﾋﾟｸﾁｬｰﾚｰﾙ` `ｸﾛｽ見切`)、ゴールデン側の `trigger_terms` は全角
    (`ピクチャーレール`)。ここを揃えないと、図面に書いてある語を
    「書いていない」と数えてしまう(実際、最初の実装はこれで 4 項目を
    取りこぼした)。NFKC は半角カタカナと濁点の合成もまとめて片付ける。
    """
    return unicodedata.normalize("NFKC", text).replace(" ", "").replace("\n", "")


@dataclass
class ItemVerdict:
    code: str
    work_item: str
    major_category: str
    unit: str
    expected_source_type: str
    trigger_terms: list[str]
    recommended_pages: list[int]
    terms_found: list[str]
    terms_missing: list[str]
    specific_terms_found: list[str] = field(default_factory=list)

    @property
    def evidenced(self) -> bool:
        """trigger_terms が 1 つでも図面に現れたか。"""
        return bool(self.terms_found)

    @property
    def fully_evidenced(self) -> bool:
        return not self.terms_missing and bool(self.terms_found)

    @property
    def specifically_evidenced(self) -> bool:
        """文書のごく一部にしか出ない語で当たったか(厳しめの判定)。"""
        return bool(self.specific_terms_found)


#: 「特異な語」とみなす上限。これ以下のページ数にしか出てこない語だけを
#: 証拠として数える厳しめの指標に使う。`全面改装` のように文書中に
#: 何度も出る語で当ててしまうのを防ぐ。
SPECIFIC_TERM_MAX_PAGES = 3


def _pages_containing(evidence: DrawingEvidence, term: str) -> int:
    needle = _normalize(term)
    return sum(1 for text in evidence.page_texts.values() if needle in _normalize(text))


def shuffled_control(evidence: DrawingEvidence, golden_path: str | Path) -> dict[str, Any]:
    """**対照実験: 推奨ページを取り違えても当たってしまわないか。**

    各項目の `trigger_terms` を、**別の項目の**推奨ページで探す。
    ここでのカバー率が本番と変わらないなら、その指標は
    「文書のどこかにその語がある」以上のことを何も言っていない。
    段階Aで学んだ「検査が働いているように見えて何も見ていない」型の
    取り違えを防ぐために必ず併記する。
    """
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    items = golden["expected_items"]
    shifted = items[1:] + items[:1]
    hits = 0
    for item, other in zip(items, shifted):
        pages = other.get("recommended_pages") or []
        haystack = _normalize(evidence.text_of(pages))
        if any(_normalize(t) in haystack for t in (item.get("trigger_terms") or [])):
            hits += 1
    return {
        "note": "各項目のtrigger_termsを別項目の推奨ページで探した場合",
        "coverage": round(hits / len(items), 3) if items else 0.0,
        "hits": hits,
        "total": len(items),
    }


def score(evidence: DrawingEvidence, golden_path: str | Path) -> dict[str, Any]:
    """図面から取れた証拠と、ゴールデンの 99 項目を突き合わせる。

    **数量・単価には触れない。** 触れるのは項目名・単位・trigger_terms・
    recommended_pages・expected_source_type だけ。
    """
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    items = golden["expected_items"]

    verdicts: list[ItemVerdict] = []
    for item in items:
        terms = list(item.get("trigger_terms") or [])
        pages = list(item.get("recommended_pages") or [])
        haystack = _normalize(evidence.text_of(pages) if pages else evidence.all_text)
        found = [t for t in terms if _normalize(t) in haystack]
        missing = [t for t in terms if _normalize(t) not in haystack]
        specific = [t for t in found if _pages_containing(evidence, t) <= SPECIFIC_TERM_MAX_PAGES]
        verdicts.append(
            ItemVerdict(
                code=item.get("code", ""),
                work_item=item.get("work_item", ""),
                major_category=item.get("major_category", ""),
                unit=item.get("unit", ""),
                expected_source_type=item.get("expected_source_type", ""),
                trigger_terms=terms,
                recommended_pages=pages,
                terms_found=found,
                terms_missing=missing,
                specific_terms_found=specific,
            )
        )

    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "evidenced": 0})
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "evidenced": 0})
    for v in verdicts:
        by_source[v.expected_source_type]["total"] += 1
        by_category[v.major_category]["total"] += 1
        if v.evidenced:
            by_source[v.expected_source_type]["evidenced"] += 1
            by_category[v.major_category]["evidenced"] += 1

    evidenced = sum(1 for v in verdicts if v.evidenced)
    fully = sum(1 for v in verdicts if v.fully_evidenced)
    specific = sum(1 for v in verdicts if v.specifically_evidenced)

    return {
        "benchmark_id": golden.get("benchmark_id"),
        "item_count": len(items),
        "evidence_coverage": round(evidenced / len(items), 3) if items else 0.0,
        "evidence_coverage_strict": round(fully / len(items), 3) if items else 0.0,
        "evidence_coverage_specific": round(specific / len(items), 3) if items else 0.0,
        "evidenced": evidenced,
        "fully_evidenced": fully,
        "specifically_evidenced": specific,
        "threshold_evidence_coverage": golden["acceptance_thresholds"]["evidence_coverage"],
        "by_expected_source_type": {
            k: {**v, "rate": round(v["evidenced"] / v["total"], 3)} for k, v in by_source.items()
        },
        "by_major_category": {
            k: {**v, "rate": round(v["evidenced"] / v["total"], 3)} for k, v in by_category.items()
        },
        "unit_distribution": dict(Counter(v.unit for v in verdicts)),
        "unevidenced_items": [
            {
                "code": v.code,
                "work_item": v.work_item,
                "major_category": v.major_category,
                "expected_source_type": v.expected_source_type,
                "trigger_terms": v.trigger_terms,
                "recommended_pages": v.recommended_pages,
            }
            for v in verdicts
            if not v.evidenced
        ],
    }


def compare_areas(evidence: DrawingEvidence, golden_path: str | Path) -> dict[str, Any]:
    """図面から読んだ面積と、ベンチマークの `input_profile` を突き合わせる。

    `input_profile` は正解の数量(`expected_items`)ではなく入力側の諸元なので、
    ここで参照しても生成側への漏れにはならない。
    """
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    profile = golden.get("input_profile", {})
    pairs = {
        "専有延床面積": profile.get("floor_area_sqm"),
        "施工床面積": profile.get("construction_floor_area_sqm"),
    }
    out = {}
    for label, expected in pairs.items():
        read = evidence.areas_sqm.get(label)
        out[label] = {
            "図面から読めた値": read,
            "ベンチマークの値": expected,
            "一致": (read is not None and expected is not None and abs(read - expected) < 0.01),
        }
    return out


# ---------------------------------------------------------------------------
# 数量そのものを出せるか
# ---------------------------------------------------------------------------

#: 「住戸ぜんたいに 1 回かかる」ことが項目名から分かる語。
#: 室ごとの床・壁・天井はここに入らない(室の輪郭を取る実装が無いため)。
#: **正解側の情報は使っていない。** 使っているのは見積内訳の項目名だけで、
#: これは積算する側が最初から持っているものである。
_WHOLE_DWELLING_TERMS: tuple[str, ...] = ("養生", "墨出し", "清掃", "足場")

#: 予測が当たったとみなす相対誤差。
_QUANTITY_TOLERANCE = 0.05


def attempt_quantities(evidence: DrawingEvidence, golden_path: str | Path) -> dict[str, Any]:
    """**実装済みの部分だけで数量そのものを出せるか**を測る。

    ここが本題である。語彙が図面にあるかどうか(``score()``)は、
    数量が出せることを意味しない。

    出せるのは、図面に書いてある床面積 1 つで値が決まる項目だけ。
    **ただし、図面には床面積が 2 つ書いてある**(専有延床面積と施工床面積)。
    **どちらを使うかは図面のどこにも書かれていない。** 積算の慣習
    (過去実績軸)は未実装なので、ここは決められない。

    だから片方を選んで「当たった/外れた」と報告するのではなく、
    **2 つの候補それぞれで何件当たるかを両方出す。** 差がそのまま、
    未実装の軸が持っている情報量である。
    """
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))

    sqm_items = [i for i in golden.get("expected_items", []) if i.get("unit") == "㎡"]
    targets = [
        i
        for i in sqm_items
        if any(term in i.get("work_item", "") for term in _WHOLE_DWELLING_TERMS)
    ]

    candidates = {
        label: evidence.areas_sqm.get(label)
        for label in ("専有延床面積", "施工床面積")
    }

    by_candidate: dict[str, Any] = {}
    for label, base in candidates.items():
        rows = []
        for item in targets:
            truth = item.get("quantity")
            hit = (
                base is not None
                and truth is not None
                and truth > 0
                and abs(base - truth) / truth <= _QUANTITY_TOLERANCE
            )
            rows.append(
                {
                    "code": item.get("code"),
                    "work_item": item.get("work_item"),
                    "予測": base,
                    "正解": truth,
                    "一致": bool(hit),
                }
            )
        by_candidate[label] = {
            "予測に使った値": base,
            "一致": sum(1 for r in rows if r["一致"]),
            "項目": rows,
        }

    return {
        "㎡ の項目数": len(sqm_items),
        "試みた項目数": len(targets),
        "試みなかった項目数": len(sqm_items) - len(targets),
        "試みなかった理由": "室の輪郭を取る実装が無く、室ごとの床・壁・天井の面積が出せない",
        "候補ごとの結果": by_candidate,
        "図面はどちらを使うか書いているか": False,
        "許容誤差": _QUANTITY_TOLERANCE,
    }


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    # 第1段階: 図面だけ。ゴールデンのパスは渡さない。
    evidence = extract_from_drawing(args.pdf)

    report: dict[str, Any] = {
        "extraction": {
            "pages_total": evidence.pages_total,
            "pages_with_text": evidence.pages_with_text,
            "total_text_spans": evidence.total_text_spans,
            "scale": (
                {"denominator": evidence.scale.denominator, "source_text": evidence.scale.source_text}
                if evidence.scale
                else None
            ),
            "mm_per_pixel_at_200dpi": (
                round(evidence.scale.mm_per_pixel(200), 4) if evidence.scale else None
            ),
            "door_arc_count": evidence.door_arc_count,
            "door_arc_widths_mm": evidence.door_arc_widths_mm,
            "areas_sqm": evidence.areas_sqm,
        }
    }

    # 第2段階: ここで初めて正解に触れる。
    report["scoring"] = score(evidence, args.golden)
    report["shuffled_control"] = shuffled_control(evidence, args.golden)
    report["area_check"] = compare_areas(evidence, args.golden)
    report["quantity_attempt"] = attempt_quantities(evidence, args.golden)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
