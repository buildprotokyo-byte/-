"""K-64 の試験(確度に読めた割合など)。**合成のデータだけ。実図面は使わない。**"""

from __future__ import annotations

import json

from draft import stages
from draft.run import run
from tests.test_draft_pipeline import FakeClient, _pdf, machine_output  # noqa: F401  (fixture)


def _item(i: str, page: int, conf: str, basis: str = "図面から読んだ") -> dict:
    return {"id": i, "ページ": page, "確度": conf, "根拠の種類": basis}


def test_high_is_lowered_only_on_poorly_read_pages():
    items = [_item("a", 1, "高"), _item("b", 2, "高"), _item("c", 3, "高"), _item("d", 2, "中"),
             _item("e", 2, "高", "人の回答"), _item("f", 4, "高")]
    reading = {"ページ": {1: {"落ちた率": 0.05}, 2: {"落ちた率": 0.4}, 3: {"落ちた率": None},
                          4: {"落ちた率": 0.15}}}
    out = stages.cap_by_page_readability(items, reading)
    conf = {it["id"]: it["確度"] for it in items}
    # 1 ページ(読めた 95%)と 4 ページ(ちょうど 85%)は高のまま、2 ページ(60%)と測れない 3 ページは中へ
    assert conf == {"a": "高", "b": "中", "c": "中", "d": "中", "e": "高", "f": "高"}
    assert out["高から中に下げた項目"] == 2 and out["下限を下回ったページ"] == [2, 3]
    assert "60%" in next(it for it in items if it["id"] == "b")["確度の上限"]
    assert "測れなかった" in next(it for it in items if it["id"] == "c")["確度の上限"]


def test_floor_is_the_fixed_high_miss_line():
    assert stages.PAGE_READ_FLOOR == 0.85


def test_flag_off_keeps_output_and_flag_on_never_raises(tmp_path, machine_output):  # noqa: F811
    pdf = _pdf(tmp_path / "図面.pdf")
    off, on = tmp_path / "off", tmp_path / "on"
    run([str(pdf), "--out", str(off), "--machine-output", str(machine_output)], client=FakeClient())
    code = run([str(pdf), "--out", str(on), "--machine-output", str(machine_output), "--with-page-confidence"],
               client=FakeClient())
    a = json.loads((off / "下書き.json").read_text(encoding="utf-8"))
    b = json.loads((on / "下書き.json").read_text(encoding="utf-8"))
    assert "確度に読めた割合" not in a and "確度に読めた割合" in b
    assert code == 0 and b["まとめ"]["自動確定"] == 0
    assert b["まとめ"]["確度ごと"]["高"] <= a["まとめ"]["確度ごと"]["高"]
    assert b["まとめ"]["項目の数"] == a["まとめ"]["項目の数"]
