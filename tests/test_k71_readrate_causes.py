"""K-71 作業 3 周 0: 落ちの原因(位置の取り方 P1〜P4)の分け方を合成の図で固定する。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks import erase_check as ec
from benchmarks import k71_readrate_causes as kc


@pytest.fixture
def page_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    # 4 本の横線(それぞれ別の原因に落とす)
    for i in range(4):
        page.draw_line((100, 100 + i * 60), (300, 100 + i * 60))
    target = tmp_path / "線.pdf"
    doc.save(target)
    doc.close()
    return target


def _px(v: float) -> float:
    return v * ec.WIDTH_PX / 600


def test_位置の取り方を4つに分ける(page_pdf: Path) -> None:
    y = [_px(100 + i * 60) for i in range(4)]
    x0, x1 = _px(100), _px(300)
    elements = [
        # 線 0: 大きい箱(上限 1% 超え)にだけ入る
        {"種類": "図", "位置": [x0 - 50, y[0] - 50, x1 + 50, y[0] + 50]},
        # 線 1: 左端だけ小さい箱に入る(見本の点 5 つのうち 1 つ)
        {"種類": "線", "位置": [x0 - 5, y[1] - 5, x0 + 5, y[1] + 5]},
        # 線 2: 少し離れた小さい箱(15 画素下)
        {"種類": "文字", "位置": [x0, y[2] + 18, x0 + 40, y[2] + 30]},
        # 線 3: 何も無い
    ]
    with pymupdf.open(page_pdf) as doc:
        got = kc.page_causes(doc.load_page(0), 1, elements, "平面図", line_types=False)
    causes = sorted((round(r["長さ"]), r["原因"]) for r in got["落ち"])
    assert [c for _, c in causes].count(kc.P1) == 1
    assert [c for _, c in causes].count(kc.P2) == 1
    assert [c for _, c in causes].count(kc.P3) == 1
    assert [c for _, c in causes].count(kc.P4) == 1
    big = [r for r in got["落ち"] if r["原因"] == kc.P1][0]
    assert big["大きい箱の種類"] == "図"
    assert all(r["ページの種類"] == "平面図" for r in got["落ち"])
    tally = kc.tally(got["落ち"])
    assert tally["線"]["落ちた数"] == 4
    assert sum(v["数"] for v in tally["線"]["位置の取り方"].values()) == 4
    assert abs(sum(v["長さの割合"] for v in tally["線"]["位置の取り方"].values()) - 1.0) < 1e-3


def test_印の付いた線は落ちに数えない(page_pdf: Path) -> None:
    y = [_px(100 + i * 60) for i in range(4)]
    elements = [{"種類": "線", "位置": [_px(100), yy - 3, _px(300), yy + 3]} for yy in y]
    with pymupdf.open(page_pdf) as doc:
        got = kc.page_causes(doc.load_page(0), 1, elements, "平面図", line_types=False)
    assert got["落ち"] == []


def test_いちばん大きい原因の選び方() -> None:
    def run(table: dict, line: dict) -> dict:
        return {"表": {"位置の取り方": {k: {"割合": v} for k, v in table.items()}},
                "線": {"位置の取り方": {k: {"長さの割合": v} for k, v in line.items()}}}

    runs = [run({kc.P1: 0.8, kc.P4: 0.2}, {kc.P2: 0.3, kc.P4: 0.7})] * 3
    got = kc.pick_cause(runs)
    assert got["いちばん大きい原因"] == kc.P4  # (0.2+0.7)/2=0.45 > (0.8+0)/2=0.40
