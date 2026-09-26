"""知識を判定に繋ぐ(1本目): **前と後を同じ入力で測る。**

基準は `docs/d_knowledge_rule_linkage_criteria.md`(測る前にコミット済み)。
おーちゃんの K-11 の 1 番。

**前**: `estimating/examples/synthetic_rules.json`(引用なし)
**後**: `estimating/examples/synthetic_knowledge_linked_rules.json`
(**同じ規則に knowledge_rule_ids を書き足しただけ**。率も係数も足していない)

対照は 6 つ。**どれかが落ちたら数字を出さない。**

- **C0**: それぞれの入力で行が 1 行以上出ること
- **C1**: 知識の表に無い ID を引いた規則が**断られる**こと
- **C2**: 引用を外すと `知識のルール` が **0 件に戻る**こと(壊し試験)
- **C3**: 既存の指標(行数・確定件数・基づきの内訳)が前後で**1 件も動かない**こと
- **C4**: `不採用` の知識は引用できないこと
- **C5**: 版 2 の規則ファイルに新しい欄を書いたら**断られる**こと

使い方::

    .venv/bin/python benchmarks/measure_knowledge_rule_linkage.py
    .venv/bin/python benchmarks/measure_knowledge_rule_linkage.py <実図面PDFのパス>

**出すのは件数と割合だけ。図面の中身は 1 文字も出さない。**
実図面を渡したときも、書き出すのは件数だけである(取り決め④: 実図面から作った
データはリポジトリに残さない)。**結果のファイルは既定では書かない**
(書き出したいときだけ `--out <パス>` を付ける)。
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from estimating.decisive import REASON_KNOWLEDGE_RULE, counts_by_reason  # noqa: E402
from estimating.from_intake import quantities_from_intake  # noqa: E402
from estimating.mapping import map_quantities  # noqa: E402
from estimating.quantities import QuantityItem  # noqa: E402
from estimating.rules import RuleError, load_rules, parse_rules  # noqa: E402
from estimating.standing_lines import apply_standing_lines  # noqa: E402
from knowledge.linkage import LinkageError, check_knowledge_links  # noqa: E402
from knowledge.table import (  # noqa: E402
    ADOPTION_STATUSES,
    load_knowledge,
    parse_knowledge,
)

BEFORE_RULES = ROOT / "estimating" / "examples" / "synthetic_rules.json"
BEFORE_STANDING = ROOT / "estimating" / "examples" / "synthetic_standing_rules.json"
AFTER_RULES = ROOT / "estimating" / "examples" / "synthetic_knowledge_linked_rules.json"

#: 引用先の知識の表。**公共建築数量積算基準からの候補**(全件 `候補`)。
KNOWLEDGE = ROOT / "benchmarks" / "fixtures" / "knowledge_candidates_encoded.json"


# ---------------------------------------------------------------------------
# 入力(**合成のみ**。実図面はパスを渡したときだけ)
# ---------------------------------------------------------------------------


def _write_synthetic_pdf(path: Path) -> None:
    """`tests/test_drawing_intake.py` と同じ合成平面図(+ スキャンのページ)。"""
    import pymupdf

    from tests.test_drawing_intake import _scanned_page, _vector_plan_page

    doc = pymupdf.open()
    _vector_plan_page(doc)
    _scanned_page(doc)
    doc.save(path)
    doc.close()


def _fixed_quantities() -> list[QuantityItem]:
    """64周目・66周目と同じ固定の合成数量。**実案件の数量ではない。**"""
    return [
        QuantityItem(
            target="建具数量::AW-1",
            value_range=(2.0, 2.0),
            unit="箇所",
            method_id="door_schedule_text",
            axis_id="text",
            attributes={"種別": "引戸"},
        ),
        QuantityItem(
            target="施工対象床面積::1階",
            value_range=(32.6, 32.6),
            unit="㎡",
            method_id="room_outline",
            axis_id="image",
        ),
    ]


def _measure(quantities, rules_path: Path) -> dict:
    """1 つの規則ファイルで測る。**判定はやり直さない。**"""
    ruleset = load_rules(rules_path)
    mapping = map_quantities(quantities, ruleset)
    standing = apply_standing_lines(ruleset, quantities)
    lines = [line for m in mapping.mappings for line in m.lines]
    reasons = counts_by_reason(lines)
    standing_reasons = counts_by_reason(standing.lines)
    return {
        "N1_見積の行に届いた数": len(lines),
        "N1_図面からは決まらない行": len(standing.lines),
        "N2_自動確定の件数": len(mapping.settled_lines()),
        "N3_決め手の種類別_当てはめた行": reasons,
        "N3_決め手の種類別_ルールだけの行": standing_reasons,
        "N3_知識のルールの行数": (
            reasons.get(REASON_KNOWLEDGE_RULE, 0)
            + standing_reasons.get(REASON_KNOWLEDGE_RULE, 0)
        ),
        "既存の指標_基づきの内訳": mapping.basis_counts_text(),
        "決め手が無い行": sum(1 for line in lines if not line.decisive),
        "決め手が無い_ルールだけの行": sum(
            1 for line in standing.lines if not line.decisive
        ),
    }


def _stripped_rules(tmp: Path) -> Path:
    """**同じ規則ファイルから引用だけを外したもの。**比べる相手をこれにする。

    同梱の見本(`synthetic_rules.json` + `synthetic_standing_rules.json`)と
    比べると、行の数そのものが違うので「引用を足した効き」と「ファイルの違い」が
    混ざる。**前と後で変わるのは引用の有無だけ**にしておく。
    """
    payload = json.loads(AFTER_RULES.read_text(encoding="utf-8"))
    for group in ("rules", "standing_lines"):
        for item in payload.get(group, []):
            item.pop("knowledge_rule_ids", None)
    payload["ruleset_id"] = payload["ruleset_id"] + "-引用なし"
    path = tmp / "stripped_rules.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _before_after(quantities, tmp: Path) -> dict:
    """同じ数量・同じ規則を、引用なし・引用ありの 2 つで通す。"""
    return {
        "前_引用なし": _measure(quantities, _stripped_rules(tmp)),
        "後_引用あり": _measure(quantities, AFTER_RULES),
        "参考_同梱の見本_引用の欄が無い版": _measure(quantities, BEFORE_RULES),
    }


def run_production_path(tmp: Path) -> dict:
    """**入力あ**: 入口 → 数量 → 見積の行(合成の図面 1 冊)。"""
    from intake.drawing_intake import IntakeConfig, read_drawing

    pdf = tmp / "synthetic_plan.pdf"
    _write_synthetic_pdf(pdf)
    result = read_drawing(
        IntakeConfig(
            case_id="KRL-BENCH", pdf_path=pdf, answers_path=tmp / "answers.json"
        )
    )
    return _before_after(quantities_from_intake(result), tmp)


def run_fixed_quantities(tmp: Path) -> dict:
    """**入力い**: 固定の合成数量。"""
    return _before_after(_fixed_quantities(), tmp)


def run_on_pdf(pdf_path: Path, tmp: Path) -> dict:
    """**入力う**: 渡された図面 1 冊を、本番経路と同じ順で通す。件数だけ返す。"""
    from intake.drawing_intake import IntakeConfig, read_drawing

    result = read_drawing(
        IntakeConfig(
            case_id="REAL", pdf_path=pdf_path, answers_path=tmp / "answers.json"
        )
    )
    return _before_after(quantities_from_intake(result), tmp)


# ---------------------------------------------------------------------------
# 対照
# ---------------------------------------------------------------------------


def controls(measurements: dict) -> dict:
    """C0〜C5。**1 つでも落ちたら結論を出さない。**"""
    table = load_knowledge(KNOWLEDGE)
    payload = json.loads(AFTER_RULES.read_text(encoding="utf-8"))

    # C0: 行が出たか
    c0 = {
        name: bool(m["後_引用あり"]["N1_見積の行に届いた数"])
        for name, m in measurements.items()
    }

    # C1: 表に無い ID を引いたら断るか
    broken = copy.deepcopy(payload)
    broken["rules"][0]["knowledge_rule_ids"] = ["そんな知識は無い"]
    try:
        check_knowledge_links(parse_rules(broken), table)
        c1 = False
    except LinkageError:
        c1 = True

    # C2: 引用を外すと 0 に戻るか(**壊し試験**)
    stripped = copy.deepcopy(payload)
    for group in ("rules", "standing_lines"):
        for item in stripped.get(group, []):
            item.pop("knowledge_rule_ids", None)
    stripped_set = parse_rules(stripped)
    quantities = _fixed_quantities()
    mapping = map_quantities(quantities, stripped_set)
    standing = apply_standing_lines(stripped_set, quantities)
    lines = [line for m in mapping.mappings for line in m.lines]
    c2_count = counts_by_reason(lines).get(REASON_KNOWLEDGE_RULE, 0) + counts_by_reason(
        standing.lines
    ).get(REASON_KNOWLEDGE_RULE, 0)

    # C3: 既存の指標が動かないか
    c3 = {}
    for name, m in measurements.items():
        before, after = m["前_引用なし"], m["後_引用あり"]
        c3[name] = (
            before["N1_見積の行に届いた数"] == after["N1_見積の行に届いた数"]
            and before["N2_自動確定の件数"] == after["N2_自動確定の件数"]
            and before["既存の指標_基づきの内訳"] == after["既存の指標_基づきの内訳"]
        )

    # C4: 不採用 の知識は引けないか
    rejected = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    for entry in rejected["entries"]:
        if entry["entry_id"] == "A-5":
            # **対照のためだけの書き換え。**リポジトリのファイルは触らない
            # (読み込んだ辞書の上で変える)。確認日も一緒に入れているのは、
            # 確認日が空欄の知識は採否を決められない(#110)ため。
            #
            # **採否の列を、名前ではなく値で見つけている。**列の名前を口にする
            # コードは読み込みだけ、という約束(K-04 6 番)をここでも崩さない。
            for column, value in list(entry.items()):
                if value in ADOPTION_STATUSES:
                    entry[column] = ADOPTION_STATUSES[2]
            entry["source"] = dict(entry["source"], checked_on="2026-09-23")
    try:
        check_knowledge_links(load_rules(AFTER_RULES), parse_knowledge(rejected))
        c4 = False
    except LinkageError:
        c4 = True

    # C5: 版 2 のファイルに新しい欄を書いたら断るか
    old_version = copy.deepcopy(payload)
    old_version["format_version"] = 2
    try:
        parse_rules(old_version)
        c5 = False
    except RuleError:
        c5 = True

    return {
        "C0_行が出たか": c0,
        "C1_表に無いIDを断るか": c1,
        "C2_引用を外すと0に戻るか": {"知識のルールの件数": c2_count, "通過": c2_count == 0},
        "C3_既存の指標が動かないか": c3,
        "C4_不採用は引けないか": c4,
        "C5_版2で新しい欄を断るか": c5,
    }


def main(argv: list[str]) -> int:
    out_path: Path | None = None
    args = list(argv)
    if "--out" in args:
        index = args.index("--out")
        out_path = Path(args[index + 1])
        del args[index : index + 2]

    table = load_knowledge(KNOWLEDGE)
    linkage = check_knowledge_links(load_rules(AFTER_RULES), table)

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        measurements = {
            "入力あ_本番経路": run_production_path(tmp),
            "入力い_固定の合成数量": run_fixed_quantities(tmp),
        }
        if args:
            measurements["入力う_実図面"] = run_on_pdf(Path(args[0]), tmp)

        results = controls(measurements)

    passed = (
        all(results["C0_行が出たか"].values())
        and results["C1_表に無いIDを断るか"]
        and results["C2_引用を外すと0に戻るか"]["通過"]
        and all(results["C3_既存の指標が動かないか"].values())
        and results["C4_不採用は引けないか"]
        and results["C5_版2で新しい欄を断るか"]
    )
    settled = {
        name: m["後_引用あり"]["N2_自動確定の件数"] for name, m in measurements.items()
    }
    payload = {
        "round": "K-11-1",
        "criteria_file": "docs/d_knowledge_rule_linkage_criteria.md",
        "benchmark": "benchmarks/measure_knowledge_rule_linkage.py",
        "引用の突き合わせ": linkage.as_dict(),
        "引用の突き合わせ_要約": linkage.summary(),
        "測定": measurements,
        "対照": results,
        "自動確定の件数": settled,
        "判定": (
            "採用(全件テストと CI が通ればマージ)"
            if passed and not any(settled.values())
            else "保留(対照が落ちた、または自動確定が増えた → 止めて報告)"
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if out_path is not None:
        out_path.write_text(text + "\n", encoding="utf-8")
        print(f"\n書き出し: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
