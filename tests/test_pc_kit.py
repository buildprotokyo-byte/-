"""K-69 の `benchmarks/pc_kit.py`。合成の正解と合成の下書きだけで確かめる。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import pc_kit

SECRET = "ひみつの品名"


def _gold(tmp_path: Path, *, drop: int = 0) -> Path:
    items = [{"code": c, "work_item": "仮設の式", "unit": "式", "major_category": "仮設工事",
              "quantity": 1, "amount": 100} for c in pc_kit.EXCLUDED_CODES]
    items.append({"code": "G100", "work_item": "壁クロス張替", "unit": "㎡", "major_category": "内装仕上工事",
                  "quantity": 20.0, "amount": 1000})
    items.append({"code": "G101", "work_item": "コンセント新設", "unit": "ヶ所", "major_category": "電気設備工事",
                  "quantity": 3, "amount": 500})
    for n in range(91 - drop):
        items.append({"code": f"G{200 + n}", "work_item": f"{SECRET}{n}", "unit": "式",
                      "major_category": "仮設工事", "quantity": 1, "amount": 10})
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"expected_items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path: Path) -> Path:
    items = [
        {"id": "a", "確度": "高", "状態": "観測", "検算": [], "ページ": 1, "根拠の種類": "図面から読んだ"},
        {"id": "b", "確度": "高", "状態": "観測", "検算": [], "ページ": 2, "根拠の種類": "図面から読んだ"},
        {"id": "c", "確度": "低", "状態": "推論", "検算": [], "ページ": 1, "根拠の種類": "図面から読んだ"},
    ]
    rows = [
        {"科目": "内装", "区分": "張替", "工事項目": "壁クロス張替", "摘要": "", "場所": "洋室",
         "数量": 20.5, "単位": "㎡", "項目": ["a"], "メモ": ""},
        {"科目": "電気設備", "区分": "新設", "工事項目": "コンセント新設", "摘要": "", "場所": "洋室",
         "数量": 9, "単位": "ヶ所", "項目": ["b"], "メモ": ""},
        {"科目": "内装", "区分": "", "工事項目": "床の何か", "摘要": "", "場所": "",
         "数量": None, "単位": "㎡", "項目": ["c"], "メモ": ""},
    ]
    draft = {
        "まとめ": {"自動確定": 0},
        "理解": {"項目": items},
        "読む": {"ページ": {"1": {"落ちた率": 0.05}, "2": {"落ちた率": 0.5}}},
        "組み立て": {"内訳の行": rows, "段階ごとの出力": {
            "概算": {"行の番号": [1]}, "通常": {"行の番号": [0, 1, 2]}, "精密": {"行の番号": [0, 1, 2]}}},
    }
    run = tmp_path / "run"
    run.mkdir()
    (run / "下書き.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return run


def test_columns_shows_names_but_no_values(tmp_path):
    out = json.dumps(pc_kit.columns(_gold(tmp_path)), ensure_ascii=False)
    assert "work_item" in out and "quantity" in out
    assert SECRET not in out and "壁クロス張替" not in out and "G100" not in out
    assert '"除いた後の行の数": 93' in out


def test_denominator_must_be_93(tmp_path):
    with pytest.raises(SystemExit):
        pc_kit.Gold(_gold(tmp_path, drop=1), pc_kit.DEFAULT_COLUMNS)
    assert len(pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS).items) == 93


def test_k66_counts_three_stages_and_old_rule_reads_dict_rows(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    out = pc_kit.k66(_run(tmp_path), gold)
    assert out["細目"]["旧"]["名前だけ"] == 2  # 旧規則でも辞書の行を読めている
    assert out["細目"]["新"]["数量あり"] == out["細目"]["新"]["名前だけ"] == 2
    assert out["細目"]["新"]["数量が合った"] == 1  # 20.5 は 20 の ±5% の中、9 は 3 の ±1 の外
    assert SECRET not in json.dumps(out, ensure_ascii=False)


def test_k67_table_has_eight_rows_and_excludes_shiki_from_amount(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    out = pc_kit.k67(_run(tmp_path), gold)
    assert len(out["表"]) == 8
    values = {r["行"]: r["出た値"] for r in out["表"]}
    # 判定する細目は 2 件(1000 円と 500 円)。合ったのはクロスだけ。式の 91 件は分子にも分母にも入らない。
    assert values["数量が合った細目の金額の割合(通常)"] == round(1000 / 1500, 4)
    assert values["数量が合った細目の金額の割合(概算)"] == 0.0  # 概算はコンセントの行だけ
    assert values["中科目(厳密)"] == "未取得(正解に中科目の列が無い)"
    assert values["確度「高」の的中率"] == 1.0
    assert out["式で判定しなかった細目"]["件数"] == 91


def test_k64_flag_lowers_high_rows_on_unreadable_pages(tmp_path):
    gold = pc_kit.Gold(_gold(tmp_path), pc_kit.DEFAULT_COLUMNS)
    out = pc_kit.k64([_run(tmp_path)], gold)
    run = out["回ごと"]["run"]
    assert run["旗オフ"]["高の行"] == 2
    assert run["旗オン"]["高の行"] == 1  # 2 ページは読めた割合 0.5 なので高から中へ
    assert run["旗オン"]["正解の数量と合う"] == 1
