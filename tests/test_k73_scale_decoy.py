"""K-73 作業 5 の確かめ(`benchmarks/k73_scale_decoy.py`)。**合成のデータだけ**(室は作った名前)。"""

from __future__ import annotations

from benchmarks import k73_scale_decoy as k

MPP = 50.0 * 25.4 / 72.0  # 1/50 の紙の 1pt が何 mm か


def rd(i, value, orient, s, e, ruler=False):
    paper = abs(e[0] - s[0]) + abs(e[1] - s[1])
    return {"id": i, "値": value, "向き": orient, "s": s, "e": e, "紙": paper, "目盛りで拾った": ruler}


def pt(mm):
    return mm / MPP


def page():
    """横 3000(上の線)= 1000 + 2000(内の線)、縦 2000(右の線、目盛りで拾った)。"""
    return [
        rd("R", 3000, "横", (0, 0), (pt(3000), 0)),
        rd("a", 1000, "横", (0, 10), (pt(1000), 10)),
        rd("b", 2000, "横", (pt(1000), 10), (pt(3000), 10)),
        rd("L", 2500, "縦", (pt(3000) + 10, 0), (pt(3000) + 10, pt(2500)), ruler=True),
    ]


SIDES = {"横": [3000], "縦": [2500]}
LABELS = {"室A": [(pt(1500), pt(1200))], "室B": [(pt(5000), pt(1200))], "室C": []}


def test_k1_unique_side():
    rs = page()
    assert k.side_readings(rs, [3000], "横")[0]["id"] == "R"
    assert k.side_readings(rs, [3000], "縦") is None  # 向きが違う
    assert k.side_readings(rs + [rd("R2", 3000.3, "横", (0, 50), (pt(3000), 50))], [3000], "横") is None  # 2 つ
    assert k.side_readings(rs, [4000], "横") is None  # 0 件


def test_k2_scale_line():
    r = page()[0]
    assert k.scale_ok(r, MPP)
    assert k.scale_ok(r, MPP * 1.009)
    assert not k.scale_ok(r, MPP * 1.02)
    assert not k.scale_ok(r, MPP * 0.95)


def test_k3_page_half_line():
    rs = page()
    res = k.page_check(rs, MPP, "R")
    assert res["見た読み"] == 2 and res["合う読み"] == 2 and res["成り立つ"]  # 目盛りで拾った L と基準 R は見ない
    rs.append(rd("x", 999, "横", (0, 90), (pt(1500), 90)))
    rs.append(rd("y", 999, "横", (0, 95), (pt(1600), 95)))
    res = k.page_check(rs, MPP, "R")
    assert res["合う読み"] == 2 and res["見た読み"] == 4 and res["成り立つ"]  # ちょうど半分は成り立つ
    rs.append(rd("z", 999, "横", (0, 99), (pt(1700), 99)))
    assert not k.page_check(rs, MPP, "R")["成り立つ"]
    assert not k.page_check([], MPP, None)["成り立つ"]


def test_k4_chain_sum():
    rs = page()
    whole = rs[0]
    assert k.chain_check(rs, [whole]) == {"食い違い": 0, "鎖で合う": 1, "確かめられない": 0}
    # 部分の側から見ても合う
    assert k.chain_check(rs, [rs[1]])["鎖で合う"] == 1
    # 和が合わなければ食い違い
    bad = [dict(rs[0], 値=3200)] + rs[1:]
    assert k.chain_check(bad, [bad[0]])["食い違い"] == 1
    # 隙間があれば確かめられない
    gap = [rs[0], rs[1], rd("b", 1800, "横", (pt(1200), 10), (pt(3000), 10)), rs[3]]
    assert k.chain_check(gap, [gap[0]]) == {"食い違い": 0, "鎖で合う": 0, "確かめられない": 1}
    # 鎖の無い縦は確かめられない
    assert k.chain_check(rs, [rs[3]])["確かめられない"] == 1


def test_evaluate_real_and_scale_decoy():
    rs = page()
    real = k.evaluate(rs, MPP, "R", SIDES, LABELS, "室A")
    assert real["合う"] and all(real[c] for c in k.CHECKS)
    off = k.evaluate(rs, MPP * 1.05, "R", SIDES, LABELS, "室A")
    assert not off["合う"] and not off["K2 辺の目盛り"] and not off["K3 ページの検算"]
    # 辺が決まらなければ全部落ちる
    gone = k.evaluate(rs[:3], MPP, "R", SIDES, LABELS, "室A")
    assert not gone["合う"] and not any(gone[c] for c in k.CHECKS)


def test_k5_k6_labels():
    rs = page()
    other_inside = dict(LABELS, 室B=[(pt(100), pt(100))])
    ev = k.evaluate(rs, MPP, "R", SIDES, other_inside, "室A")
    assert ev["K5 室名が長方形の中"] and not ev["K6 ほかの室名が中に無い"] and not ev["合う"]
    ev = k.evaluate(rs, MPP, "R", SIDES, LABELS, "室B")
    assert not ev["K5 室名が長方形の中"] and not ev["合う"]


def test_move_decoys():
    rs = page()
    real = k.evaluate(rs, MPP, "R", SIDES, LABELS, "室A")
    real["_box"] = k.box([rs[0]], [rs[3]])
    moved = k.move_decoys(real, LABELS, "室A")
    assert moved["件数"] == 2 and moved["合う"] == 0
    assert moved["弱い囮(移した先の室名が見つからない)"] == 1  # 室C は室名が無い
    assert moved["K6 で落ちた"] == 2  # 本物の室名が中にある
    # 移した先の室名も長方形の中にあり、本物の室名が無ければ、囮が合ってしまう(それを数えられること)
    tricky = {"室A": [], "室B": [(pt(100), pt(100))]}
    m2 = k.move_decoys(real, tricky, "室A")
    assert m2["合う"] == 1 and m2["合う(弱い囮を除く)"] == 1


def test_fixed_sides_reference_only():
    rs = page()
    dup = rs + [rd("R2", 3000.3, "横", (0, 50), (pt(3000), 50), ruler=True)]
    assert not k.evaluate(dup, MPP, "R", SIDES, LABELS, "室A")["合う"]  # 目盛りの後で横が 2 つ → K1 で落ちる
    ev = k.evaluate(dup, MPP, "R", SIDES, LABELS, "室A", fixed={"横": [rs[0]], "縦": [rs[3]]})
    assert ev["K1 辺が1つに決まる"] and ev["K5 室名が長方形の中"]
