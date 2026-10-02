"""K-67 の 3 つの部品(読了率・工事チェック表・採点表)を固定する。

**この試験の値打ちは「読めていないのに高い数字が出ない」を固定することにある**:
大きい箱で囲んだだけの読みは読了率がほぼ 0 になり、文字の層が無いページは
「測れない」と出る。どちらも緩めると落ちる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from draft import readthrough as rt
from draft import scorecard, work_checklist


@pytest.fixture
def one_page(tmp_path: Path) -> Path:
    """文字と線と小さい四角を描いた 1 ページの PDF。"""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((50, 50), "洋室1 クロス張替", fontsize=11, fontname="china-s")
    page.insert_text((50, 80), "2400", fontsize=11, fontname="china-s")
    for i in range(6):
        page.draw_line((100, 150 + i * 20), (400, 150 + i * 20))
    page.draw_rect(pymupdf.Rect(450, 150, 470, 170))
    target = tmp_path / "1枚.pdf"
    doc.save(target)
    doc.close()
    return target


def _rate(pdf: Path, elements: list[dict]) -> dict:
    with pymupdf.open(pdf) as doc:
        return rt.page_readthrough(doc.load_page(0), 1, elements)


def test_読めた図形の割合が読了率になる(one_page: Path) -> None:
    nothing = _rate(one_page, [])
    assert nothing["読了率"] == 0.0
    assert nothing["信号"] == rt.RED
    assert nothing["未読"], "読めていない図形は 1 つずつ出る"


def test_未読は全部ページと位置で指せる(one_page: Path) -> None:
    result = _rate(one_page, [])
    assert result["未読の所在が指せた割合"] == 1.0
    for row in result["未読"]:
        assert row["ページ"] == 1
        assert len(row["位置"]) == 4


def test_大きい箱で囲んでも読了率は上がらない(one_page: Path) -> None:
    """**面積の上限 1% が効いていることの固定。**効かないと、何も読まずに 100% が出る。"""
    with pymupdf.open(one_page) as doc:
        page = doc.load_page(0)
        scale = rt.__dict__  # noqa: F841 - 読みやすさのため
        box = [0.0, 0.0, 2000.0, 2000.0 * page.rect.height / page.rect.width]
    result = _rate(one_page, [{"種類": "大きい箱", "位置": box}])
    assert result["読了率"] == 0.0


def test_面積の上限を変えると数字が動くことを画面に出す(one_page: Path) -> None:
    assert "位置の許容 1%" in rt.DEFINITION
    assert "理解した、ではない" in rt.DEFINITION


def test_文字の層が無いページは測れないと出る(tmp_path: Path) -> None:
    """**高い数字も低い数字も出さない。**(K-63 で落ちの物差しが壊れた所に名前を付ける)"""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 400))
    pix.clear_with(200)
    page.insert_image(page.rect, pixmap=pix)
    target = tmp_path / "スキャン.pdf"
    doc.save(target)
    doc.close()
    result = _rate(target, [])
    assert result["読了率"] is None
    assert result["信号"] == rt.GREY
    assert rt.CANNOT_MEASURE in result["測れない理由"]
    assert result["手段"] != "文字の層"


def test_信号の色() -> None:
    assert rt.signal(0.99) == rt.GREEN
    assert rt.signal(0.95) == rt.YELLOW
    assert rt.signal(0.80) == rt.RED
    assert rt.signal(None) == rt.GREY


# ---- 工事チェック表 ----

def test_枠は毎回16個出る() -> None:
    result = work_checklist.build([])
    assert result["枠の数"] == 16
    assert [f["枠"] for f in result["枠"]][0] == "仮設"
    assert [f["枠"] for f in result["枠"]][-1] == "その他"


def test_空の枠も出て記載が見当たらないと書く() -> None:
    result = work_checklist.build([])
    for row in result["枠"]:
        assert row["状態"] == work_checklist.NOT_FOUND
        assert row["分からないこと"]["理由"] == ["記載が見当たらない"]


def test_振り分けの根拠が残る() -> None:
    items = [{"工事": "壁クロス張替", "科目": "内装", "数量": 10.0, "単位": "m2", "ページ": 3, "位置": [1, 2, 3, 4]}]
    result = work_checklist.build(items)
    inside = [r for r in result["枠"] if r["件数"]]
    assert len(inside) == 1
    assert inside[0]["枠"] == "内装仕上"
    assert inside[0]["分かったこと"][0]["振り分けの根拠"]
    assert inside[0]["状態"] == work_checklist.CONFIRMED


def test_ないと言わない() -> None:
    """理由は決めた 5 つしか使わない。`記載あり:なし` 以外で「ない」と書かない。"""
    result = work_checklist.build([])
    for row in result["枠"]:
        assert set(row["分からないこと"]["理由"]) <= set(work_checklist.REASONS)
    assert result["語が見つかるのに記載が見当たらないとした枠"] == []


def test_数量が無い項目は一部確認に落ちる() -> None:
    items = [{"工事": "壁クロス張替", "数量": None, "単位": "m2", "ページ": 3, "位置": [1, 2, 3, 4]}]
    row = [r for r in work_checklist.build(items)["枠"] if r["件数"]][0]
    assert row["状態"] == work_checklist.PARTIAL
    assert "数量の根拠が足りない" in row["分からないこと"]["理由"]


# ---- 採点表 ----

def test_未取得は0にしない() -> None:
    card = scorecard.build()
    values = [r["いまの値"] for r in card["行"]]
    assert all(isinstance(v, str) and "未取得" in v for v in values)
    assert card["段階ごと"]["段階1 台帳"]["判定"] == "未測定"


def test_合格ラインはおーちゃんの数字() -> None:
    card = scorecard.build()
    lines = {r["名前"]: r["合格ライン"] for r in card["行"]}
    assert lines["読了率 文字"] == 0.98
    assert lines["読了率 線(長さ)"] == 0.90
    assert lines["「どこに書いてあるか」検索"] == 0.90
    assert lines["細目(同じ意味。K-66)"] == 0.70
    assert lines["確度「高」の的中率"] == 0.95


def test_到達度と合否() -> None:
    row = scorecard.Row(1, "例", "例", 0.98, 0.9484)
    assert row.合否 == "未達"
    assert row.到達度 == pytest.approx(0.9484 / 0.98)
    assert scorecard.Row(1, "例", "例", 0, 0, 向き="ちょうど").合否 == "合格"
    assert scorecard.Row(1, "例", "例", 600, 99.5, 向き="以下").合否 == "合格"


def test_測れない行があるとき合格と言わない() -> None:
    """**測定が消えることが点になってはいけない。**

    資料を隠した版で、測れなくなった行が落ちたせいで段階 2 が「合格」に見えた
    (2026-10-02 に実際に起きた)。測れた行が全部合格でも、未取得の行があれば合格と書かない。
    """
    card = scorecard.build(
        readthrough={"種類ごと(重なりなし)": {"文字": {"読了率": 1.0}, "記号": {"読了率": 1.0}},
                     "別の切り口(重なる)": {"表": {"読了率": 1.0},
                                     "数字だけの語(寸法の見込み)": {"読了率": 1.0}},
                     "墨の量で見た読了率": {"線(長さ)": {"読了率": 1.0},
                                     "点・小さい図形(面積)": {"読了率": 1.0}},
                     "未読の所在が指せた割合": 1.0},
        search={"正答率": 1.0},
    )
    stage1 = card["段階ごと"]["段階1 台帳"]
    assert stage1["未取得の行"] > 0
    assert stage1["判定"] != "合格"
    assert "未取得" in stage1["判定"]
