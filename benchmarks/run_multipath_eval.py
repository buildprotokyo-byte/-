"""トライアルB: 多方向からのアプローチの測定。**合成の案件の上でのみ測る。**

採否の基準は測る前に `docs/b_multipath_criteria.md` に置いてある。
結果を見てから基準を変えない。

**経路どうしは互いの出力を見ない。** ここでは、経路が別々の関数で、
互いの出力を引数に取らないことでそれを満たしている。

**負の対照を必ず一緒に回す。** 同じ PDF から来た一致が独立として数えられないこと、
食い違いでどちらも選ばないこと、1 経路だけの件が残ること、経路が 1 つだと
一致が 0 件になること。これらを確かめてから本測定の数字を読む。

実案件の PDF・見積・スタートキットはこの作業環境に無い。
だから**実案件での成績はここでは測れない。**
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbitration.multi_path_reconciler import (  # noqa: E402
    PathItem,
    coverage_by_path,
    path_specialties,
    reconcile,
)
from axes.image_axis.pdf_repeated_symbols import (  # noqa: E402
    find_repeated_symbols,
    name_clusters,
    read_legend_symbols,
)
from axes.image_axis.pdf_room_outlines import find_room_outlines  # noqa: E402
from axes.image_axis.pdf_tables import find_tables  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale  # noqa: E402
from benchmarks.run_repeated_symbol_eval import SYMBOLS, draw_clutter  # noqa: E402
from benchmarks.run_room_outline_eval import ALL_ROOMS, WALLS, _draw_wall  # noqa: E402
from estimating.rules import load_rules  # noqa: E402
from estimating.standing_lines import apply_standing_lines  # noqa: E402

SCALE = DrawingScale(denominator=50.0, source_text="1/50")
PT_PER_MM = (1 / 50) / 25.4 * 72

#: 合成した案件の記号の正解。**この数字を経路には渡さない。**
SYMBOL_TRUTH = {"埋込コンセント": 12, "片切スイッチ": 6, "引掛シーリング": 8, "TEL引出口": 3}

#: 図面に現れない行の正解(合成。実在の会社のものではない)。
#: **`estimating/examples/synthetic_standing_rules.json` の行の名前と同じにする。**
#: 1 回目はここを手で書いて 5 件の名前が合っておらず、分母が 16 になっていた。
#: 行の名前は合成した書式の側が正であって、正解の側で言い換えてよいものではない。
STANDING_TRUTH = (
    "仮設水道料", "仮設電気料", "小運搬費", "荷上費", "墨出し", "竣工時清掃", "駐車場代",
)

#: この案件のデータ源の指紋。**同じ PDF は同じ指紋。**
PDF_SOURCE = "合成案件001.pdf"
KIT_SOURCE = "合成スタートキット001"
FORM_SOURCE = "合成見積書式001"


# ---- 合成の案件を作る ----------------------------------------------------


def _draw_table(page, rows: list[list[str]], x: float, y: float, col_w: float, row_h: float) -> None:
    """罫線の表を描く。**升目は実際に埋める**(空の表は表として読まれない)。"""
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            rect = pymupdf.Rect(x + c * col_w, y + r * row_h, x + (c + 1) * col_w, y + (r + 1) * row_h)
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(color=(0, 0, 0), width=0.6)
            shape.commit()
            page.insert_text(pymupdf.Point(rect.x0 + 6, rect.y1 - 8), text, fontname="japan", fontsize=11)


def build_case(path: Path, symbol_counts: dict[str, int], table_counts: dict[str, int]) -> Path:
    """1 つの案件 = 1 つの PDF。平面図・電気設備図・凡例・器具表の 4 ページ。"""
    doc = pymupdf.open()

    # ページ0: 平面図(室の輪郭)
    plan = doc.new_page(width=1190, height=842)
    plan.insert_text(pymupdf.Point(950, 810), "縮尺 1/50")
    for wall in WALLS:
        _draw_wall(plan, wall)
    for room in ALL_ROOMS:
        plan.insert_text(
            pymupdf.Point(120 + room.x * PT_PER_MM + 8, 120 + room.y * PT_PER_MM + 16),
            room.name,
            fontname="japan",
        )

    # ページ1: 電気設備図(記号)
    elec = doc.new_page(width=1190, height=842)
    elec.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    draw_clutter(elec, seed=7)
    rng = random.Random(11)
    size = 200.0 * PT_PER_MM
    for name, count in symbol_counts.items():
        for _ in range(count):
            SYMBOLS[name](elec, rng.uniform(100, 1080), rng.uniform(100, 760), rng.choice([0, 90, 180, 270, 45]), size)

    # ページ2: 凡例
    legend = doc.new_page(width=1190, height=842)
    y = 150.0
    for name in SYMBOLS:
        legend.insert_text(pymupdf.Point(80, y + 5), name, fontname="japan")
        SYMBOLS[name](legend, 400.0, y, 0.0, size)
        y += 120.0

    # ページ3: 器具表(印字された個数)
    sched = doc.new_page(width=1190, height=842)
    sched.insert_text(pymupdf.Point(80, 100), "器具表", fontname="japan", fontsize=14)
    rows = [["名称", "数量", "単位"]] + [[n, str(c), "個"] for n, c in table_counts.items()]
    _draw_table(sched, rows, x=80, y=120, col_w=200, row_h=34)

    doc.save(path)
    doc.close()
    return path


# ---- 経路(互いの出力を見ない) ------------------------------------------


def path_from_below(pdf: Path) -> list[PathItem]:
    """下から: 電気設備図の線から、繰り返す図形を群にして数える。"""
    clusters = find_repeated_symbols(pdf, 1, SCALE)
    named = name_clusters(clusters, read_legend_symbols(pdf, 2, SCALE))
    out = []
    for item in named:
        if item.name is None:
            continue
        out.append(
            PathItem(
                path_id="下から(線を数える)",
                path_kind="下から",
                source_fingerprint=PDF_SOURCE,
                item_key=item.name,
                item_category="記号を数える行",
                value_range=(float(item.count), float(item.count)),
                unit="個",
                evidence="ページ2の繰り返す図形",
            )
        )
    return out


def path_from_side(pdf: Path) -> list[PathItem]:
    """横から: 器具表の印字された個数を読む。**線は見ない。**"""
    out = []
    for table in find_tables(pdf, 3):
        texts = table.texts()
        if not texts or "数量" not in texts[0]:
            continue
        qty_col = texts[0].index("数量")
        name_col = texts[0].index("名称")
        for row in texts[1:]:
            if len(row) <= max(qty_col, name_col):
                continue
            name, qty = row[name_col].strip(), row[qty_col].strip()
            if not name or not qty.isdigit():
                continue
            out.append(
                PathItem(
                    path_id="横から(表を読む)",
                    path_kind="横から",
                    source_fingerprint=PDF_SOURCE,
                    item_key=name,
                    item_category="記号を数える行",
                    value_range=(float(qty), float(qty)),
                    unit="個",
                    evidence="ページ4の器具表",
                )
            )
    return out


def path_from_geometry(pdf: Path) -> list[PathItem]:
    """別の角度: 平面図の壁から室の輪郭を閉じて面積を出す。"""
    out = []
    for room in find_room_outlines(pdf, 0, SCALE):
        if room.name is None:
            continue
        out.append(
            PathItem(
                path_id="別の角度(形から出す)",
                path_kind="別の角度",
                source_fingerprint=PDF_SOURCE,
                item_key=f"床面積::{room.name}",
                item_category="形から出す行",
                value_range=room.area_range_sqm,
                unit="m2",
                evidence="ページ1の壁の輪郭",
            )
        )
    return out


def path_from_above(kit: dict[str, tuple[float, float, str]]) -> list[PathItem]:
    """上から: 人の入力(スタートキット)。**図面とは別のデータ源。**"""
    return [
        PathItem(
            path_id="上から(人の入力)",
            path_kind="上から",
            source_fingerprint=KIT_SOURCE,
            item_key=key,
            item_category="人が入れた行",
            value_range=(low, high),
            unit=unit,
            evidence="スタートキットの記入欄",
        )
        for key, (low, high, unit) in kit.items()
    ]


def path_from_behind(rules_path: Path) -> list[PathItem]:
    """後ろから: 見積の書式にある行から逆算する。**正解は見ない。値も出さない。**"""
    ruleset = load_rules(rules_path)
    result = apply_standing_lines(ruleset, quantities=())
    return [
        PathItem(
            path_id="後ろから(見積の型)",
            path_kind="後ろから",
            source_fingerprint=FORM_SOURCE,
            item_key=line.work_item,
            item_category="図面に現れない行",
            value_range=None,
            evidence="見積の書式にこの行がある",
        )
        for line in result.lines
    ]


# ---- 測る ---------------------------------------------------------------


@dataclass
class Measurement:
    label: str
    truth: tuple[str, ...]
    per_path_recall: dict[str, str]
    union_recall: str
    best_single_recall: str
    agreed: int
    strengthened: int
    conflicting: int
    single_path: int
    wrong_agreements: list[str]
    missed_conflicts: list[str]


def _recall(found: set[str], truth: tuple[str, ...]) -> tuple[int, int]:
    return (len({k for k in truth if k in found}), len(truth))


def measure(label: str, items: list[PathItem], truth: tuple[str, ...],
            truth_values: dict[str, float]) -> Measurement:
    result = reconcile(items)
    coverage = coverage_by_path(items)
    per_path = {}
    best = 0
    for path_id, keys in sorted(coverage.items()):
        hit, total = _recall(keys, truth)
        per_path[path_id] = f"{hit}/{total}"
        best = max(best, hit)
    union_hit, total = _recall(set().union(*coverage.values()) if coverage else set(), truth)

    wrong_agreements = []
    for item in result.agreed():
        expected = truth_values.get(item.item_key)
        rng = item.agreed_range
        if expected is None or rng is None:
            continue
        if not (rng[0] - 1e-6 <= expected <= rng[1] + 1e-6):
            wrong_agreements.append(f"{item.item_key}: 一致した幅 {rng} に正解 {expected} が入っていない")

    missed_conflicts = []
    for item in result.agreed():
        values = {i.value_range for i in item.items if i.value_range is not None}
        if len(values) > 1:
            lows = [v[0] for v in values]
            highs = [v[1] for v in values]
            if max(lows) > min(highs):
                missed_conflicts.append(f"{item.item_key}: {sorted(values)} が一致になった")

    return Measurement(
        label=label,
        truth=truth,
        per_path_recall=per_path,
        union_recall=f"{union_hit}/{total}",
        best_single_recall=f"{best}/{total}",
        agreed=len(result.agreed()),
        strengthened=len(result.strengthened()),
        conflicting=len(result.conflicting()),
        single_path=len(result.single_path()),
        wrong_agreements=wrong_agreements,
        missed_conflicts=missed_conflicts,
    )


def _case_items(tmp: Path, label: str, symbol_counts, table_counts, rules_path) -> list[PathItem]:
    pdf = build_case(tmp / f"{label}.pdf", symbol_counts, table_counts)
    kit = {"埋込コンセント": (12.0, 12.0, "個"), "床面積::洋室1": (15.1, 15.2, "m2")}
    return (
        path_from_below(pdf)
        + path_from_side(pdf)
        + path_from_geometry(pdf)
        + path_from_above(kit)
        + path_from_behind(rules_path)
    )


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    tmp = root / ".bench_tmp"
    tmp.mkdir(exist_ok=True)
    rules_path = root / "estimating/examples/synthetic_standing_rules.json"

    truth_rows: tuple[str, ...] = tuple(SYMBOL_TRUTH) + tuple(
        f"床面積::{r.name}" for r in ALL_ROOMS
    ) + STANDING_TRUTH
    truth_values = {name: float(count) for name, count in SYMBOL_TRUTH.items()}
    truth_values.update({f"床面積::{r.name}": r.area_sqm for r in ALL_ROOMS})

    report: dict = {"負の対照": {}, "本測定": {}, "3回の繰り返し": {}}

    # --- 負の対照 ---
    base = _case_items(tmp, "neg_base", SYMBOL_TRUTH, SYMBOL_TRUTH, rules_path)

    same_source = [i for i in base if i.source_fingerprint == PDF_SOURCE
                   and i.item_category == "記号を数える行"]
    ss = reconcile(same_source)
    report["負の対照"]["1_同じPDFの2手法が一致"] = {
        "一致": len(ss.agreed()),
        "独立が2つ以上の一致": len(ss.strengthened()),
        "独立したデータ源の数": sorted({i.independent_source_count for i in ss.items}),
        "期待": "一致は出るが、独立は1、強める件は0",
        "合格": len(ss.strengthened()) == 0 and len(ss.agreed()) > 0,
    }

    disagreeing = _case_items(tmp, "neg_disagree", {**SYMBOL_TRUTH, "埋込コンセント": 20},
                              SYMBOL_TRUTH, rules_path)
    dd = reconcile([i for i in disagreeing if i.item_key == "埋込コンセント"])
    report["負の対照"]["2_わざと違う値"] = {
        "食い違い": len(dd.conflicting()),
        "重なりを返したか": [i.agreed_range for i in dd.items],
        "期待": "食い違いになり、どちらも選ばない(None)",
        "合格": len(dd.conflicting()) == 1 and all(i.agreed_range is None for i in dd.conflicting()),
    }

    only_one = reconcile(base)
    behind_only = {i.item_key for i in only_one.single_path()
                   if any(x.path_id == "後ろから(見積の型)" for x in i.items)}
    report["負の対照"]["3_1経路だけの行"] = {
        "片方にしか無いもの": len(only_one.single_path()),
        "図面に現れない行が残ったか": sorted(behind_only),
        "期待": "捨てられずに残る",
        "合格": set(STANDING_TRUTH) <= behind_only,
    }

    single = reconcile([i for i in base if i.path_id == "下から(線を数える)"])
    report["負の対照"]["4_経路を1つだけにする"] = {
        "一致": len(single.agreed()),
        "片方にしか無いもの": len(single.single_path()),
        "期待": "一致は0件。全件が片方にしか無いもの",
        "合格": len(single.agreed()) == 0 and len(single.single_path()) == len(single.items),
    }

    # --- 本測定 ---
    main_m = measure("図面と表が合っている案件", base, truth_rows, truth_values)
    report["本測定"]["合っている案件"] = main_m.__dict__ | {"truth": list(main_m.truth)}

    mismatch = _case_items(tmp, "mismatch", {**SYMBOL_TRUTH, "埋込コンセント": 13},
                           SYMBOL_TRUTH, rules_path)
    mm = measure("図面が13個・表が12個の案件", mismatch, truth_rows, truth_values)
    report["本測定"]["図面と表が食い違う案件"] = mm.__dict__ | {"truth": list(mm.truth)}

    report["経路ごとの得意分野"] = {
        p: dict(c) for p, c in sorted(path_specialties(base).items())
    }

    # --- 3 回の繰り返し ---
    repeats = []
    for run in range(3):
        items = _case_items(tmp, f"repeat{run}", SYMBOL_TRUTH, SYMBOL_TRUTH, rules_path)
        m = measure(f"{run+1}回目", items, truth_rows, truth_values)
        repeats.append({
            "合わせた再現": m.union_recall,
            "いちばん良い単独": m.best_single_recall,
            "一致": m.agreed,
            "強める一致": m.strengthened,
            "食い違い": m.conflicting,
            "片方にしか無い": m.single_path,
        })
    report["3回の繰り返し"]["各回"] = repeats
    report["3回の繰り返し"]["すべて同じか"] = all(r == repeats[0] for r in repeats)

    report["判定"] = {
        "合わせた再現がいちばん良い単独より大きい":
            main_m.union_recall != main_m.best_single_recall,
        "誤った一致": main_m.wrong_agreements,
        "取りこぼした食い違い": main_m.missed_conflicts,
        "負の対照はすべて合格": all(v["合格"] for v in report["負の対照"].values()),
    }

    out = root / "docs/b_multipath_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
