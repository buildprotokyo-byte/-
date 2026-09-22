"""答案の検査の回帰テスト。

**「欠陥を見つけた」テストだけでは足りない。** 1 回目の手書きの検査は、
仕様で `null` を許している欄を欠けと数えて 337 件の偽の欠けを出した。
だから**正しい答案で 0 件になること**を、同じ数だけ置いてある。
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from benchmarks.ab_reading_run_validator import (
    check_no_money,
    check_no_outside,
    check_record,
    summarize,
    validate,
)


def _item(number: int, **overrides: object) -> dict:
    item = {
        "番号": number,
        "工事項目": "天井 石膏ボード 撤去",
        "区分": "解体・撤去工事",
        "場所": "ＬＤＫ",
        "数量": None,
        "単位": None,
        "根拠": [{"ページ": 33, "位置": [110, 443], "読んだ文字": "共通撤去範囲"}],
        "確かさ": "確か",
        "出どころ": "直接読んだ",
        "問いの出どころ": "図面の観測から",
        "区分の根拠": "図面にそう書いてある",
        "段の数": 1,
        "途中で変えた": None,
        "波及の道筋": None,
        "備考": None,
    }
    item.update(overrides)
    return item


def _answer(**overrides: object) -> dict:
    answer = {
        "実行ID": "P-R1",
        "使ったモデル": "claude-opus-5[1m]",
        "入力パッケージ": "ab_reading_input_P011",
        "開始時刻": "2026-09-22T16:39:50Z",
        "終了時刻": "2026-09-22T16:56:30Z",
        "作業記録": {
            "所要時間_秒": 1000,
            "読んだページ": list(range(1, 35)),
            "開かなかったページ": [],
            "ページを開いた順": list(range(1, 35)),
            "読んだ文字量": 182161,
            "推論の数": 213,
            "計算の数": 12,
            "往復の回数": 2,
            "最初の工事項目が出た時刻": "2026-09-22T16:41:30Z",
            "全部出そろった時刻": "2026-09-22T16:54:00Z",
        },
        "工事項目": [_item(1), _item(2, 数量=7, 単位=None)],
        "要確認": [
            {"番号": 1, "内容": "床面積が2つある", "なぜ": "どちらか決められない",
             "種類": "選べない", "関係する根拠": []},
        ],
    }
    answer.update(overrides)
    return answer


# ---------------------------------------------------------------------------
# 否定対照: 正しい答案は 0 件
# ---------------------------------------------------------------------------
def test_valid_answer_has_no_finding() -> None:
    assert validate(_answer(), total_pages=34) == []


@pytest.mark.parametrize("field", ["数量", "単位", "途中で変えた"])
def test_nullable_field_may_be_null(field: str) -> None:
    """**仕様で null を許している欄を、欠けと数えてはいけない。**

    1 回目の手書きの検査はここで 337 件の偽の欠けを出した。
    """
    answer = _answer()
    answer["工事項目"] = [_item(1, **{field: None})]
    assert validate(answer, total_pages=34) == []


def test_quantity_without_unit_is_allowed() -> None:
    """「単位が書かれていないときは null」が仕様なので、数量だけでも欠けではない。"""
    answer = _answer()
    answer["工事項目"] = [_item(1, 数量=7, 単位=None)]
    assert validate(answer, total_pages=34) == []


def test_arc_and_circle_do_not_count_as_money() -> None:
    """「円弧」「円形」は金額ではない。欄の名前のときだけ拾う。"""
    assert check_no_money(json.dumps({"備考": "円弧で開き戸を拾う"}, ensure_ascii=False)) == []


# ---------------------------------------------------------------------------
# 欠陥はちゃんと見つかる
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["区分", "場所", "確かさ", "問いの出どころ", "区分の根拠"])
def test_empty_required_field_is_found(field: str) -> None:
    answer = _answer()
    answer["工事項目"] = [_item(1, **{field: ""})]
    findings = validate(answer, total_pages=34)
    assert findings and any(field in f.detail for f in findings)


def test_missing_nullable_field_is_found() -> None:
    """`null` は許すが、**欄そのものが無いのは欠け**である。"""
    item = _item(1)
    del item["途中で変えた"]
    answer = _answer()
    answer["工事項目"] = [item]
    findings = validate(answer, total_pages=34)
    assert any("途中で変えた の欄が無い" in f.detail for f in findings)


def test_quantity_without_evidence_is_found() -> None:
    answer = _answer()
    answer["工事項目"] = [_item(1, 数量=7, 根拠=[])]
    findings = validate(answer, total_pages=34)
    assert any("根拠が無い" in f.detail for f in findings)


def test_missing_page_is_found() -> None:
    record = copy.deepcopy(_answer()["作業記録"])
    record["読んだページ"] = list(range(1, 30))
    findings = check_record(record, total_pages=34)
    assert any("どちらにも出てこないページ" in f.detail for f in findings)


def test_page_in_both_lists_is_found() -> None:
    record = copy.deepcopy(_answer()["作業記録"])
    record["開かなかったページ"] = [3]
    findings = check_record(record, total_pages=34)
    assert any("両方に出るページ" in f.detail for f in findings)


def test_nonexistent_page_is_found() -> None:
    record = copy.deepcopy(_answer()["作業記録"])
    record["読んだページ"] = list(range(1, 36))
    findings = check_record(record, total_pages=34)
    assert any("存在しないページ番号" in f.detail for f in findings)


def test_missing_record_field_is_found() -> None:
    record = copy.deepcopy(_answer()["作業記録"])
    del record["往復の回数"]
    findings = check_record(record, total_pages=34)
    assert any("往復の回数 が無い" in f.detail for f in findings)


@pytest.mark.parametrize("term", ["金額", "単価", "価格"])
def test_money_is_found(term: str) -> None:
    blob = json.dumps({"工事項目": [{term: 1000}]}, ensure_ascii=False)
    assert check_no_money(blob)


def test_money_field_name_with_yen_is_found() -> None:
    blob = json.dumps({"工事項目": [{"単位あたり円": 1000}]}, ensure_ascii=False)
    assert check_no_money(blob)


@pytest.mark.parametrize("term", ["uploads/hearth", "/home/user/", "ab_sealed"])
def test_outside_location_is_found(term: str) -> None:
    blob = json.dumps({"備考": f"{term} を見た"}, ensure_ascii=False)
    assert check_no_outside(blob)


def test_inside_package_path_is_not_flagged() -> None:
    """否定対照: 渡したフォルダを指すのは当たり前なので、拾ってはいけない。"""
    blob = json.dumps(
        {"備考": "/mnt/project-files/ab_reading_input_P011/page_33.md"}, ensure_ascii=False
    )
    assert check_no_outside(blob) == []


def test_summarize_counts_rows() -> None:
    """**出した行数そのものを数える**（採点の決まり 7-2-2）。"""
    answer = _answer()
    answer["工事項目"] = [_item(1), _item(2, 数量=7), _item(3, 数量=1.5)]
    assert summarize(answer) == {
        "工事項目": 3,
        "数量あり": 2,
        "数量が空": 1,
        "要確認": 1,
    }


def test_区分が空でも根拠が分からないなら欠陥ではない() -> None:
    """`出力の形.md` は「分からなければ空にする」と決めている。

    ここを欠けと数えたのが、1 回目の手書きの検査が 337 件の偽の欠けを出した理由と
    同じ間違いである。**空であること自体は仕様どおり。**
    """
    for empty in (None, ""):
        answer = _answer(**{"工事項目": [_item(1, 区分=empty, 区分の根拠="分からない")]})
        assert validate(answer, total_pages=34) == []


def test_区分が空なのに根拠が分からないでなければ欠陥() -> None:
    """否定対照。仕様は「`区分` が空なら `区分の根拠` は `分からない`」と対で決めている。"""
    answer = _answer(**{"工事項目": [_item(1, 区分=None, 区分の根拠="要約資料から")]})
    findings = validate(answer, total_pages=34)
    assert [f.kind for f in findings] == ["欄の食い違い"]


def test_区分が入っていれば根拠は分からないでなくてよい() -> None:
    """否定対照。埋まっている行に上の検査が誤爆しないこと。"""
    answer = _answer(**{"工事項目": [_item(1, 区分="解体・撤去工事", 区分の根拠="図面にそう書いてある")]})
    assert validate(answer, total_pages=34) == []
