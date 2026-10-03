"""K-70 作業 3: 割れた値を人に並べて見せるカード(合成のデータと合成の小さな PDF だけ。正解は使わない)。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from draft import answers_io, split_cards
from draft import uncertainty as unc


def _it(rid: str, name: str, qty, unit: str = "m2", room: str = "室A", page: int = 1,
        box=(100.0, 100.0, 300.0, 160.0)) -> dict:
    return {"id": rid, "工事": name, "何": name, "場所": room, "部位": "", "品番": "", "数量": qty, "単位": unit,
            "ページ": page, "囲み": list(box) if box else None, "要素": ["e1", "e2"], "状態": "観測", "確度": "中",
            "区分": "新設", "科目": "内装", "式": "", "読み取った値": "", "検算": [], "根拠の種類": "図面から読んだ"}


def _runs() -> dict[str, list[dict]]:
    return {
        "R1": [_it("a1", "壁のクロス張", 10.0), _it("a2", "床のフロアタイル張", 20.0, room="室A"),
               _it("a3", "天井のボード張", 5.0), _it("a4", "コンセントの新設", 3, "箇所"),
               _it("a5", "床の下地", None)],
        "R2": [_it("b1", "壁のクロス張り", 12.0), _it("b2", "床のフロアタイル張", 20.0, room="室B"),
               _it("b3", "天井のボード張", 5.0), _it("b4", "コンセントの新設", 3, "箇所"),
               _it("b5", "床の下地", 8.0), _it("b6", "幅木の撤去", 4.0, "m")],
        "R3": [_it("c1", "壁のクロス張", 10.0), _it("c2", "床のフロアタイル張", 20.0, room="室A"),
               _it("c3", "天井のボード張", 5.0), _it("c4", "コンセントの新設", 3, "箇所"),
               _it("c4b", "コンセントの新設", 2, "箇所"), _it("c5", "床の下地", 8.0)],
    }


def test_splits_are_all_accounted_and_cards_have_closed_options():
    runs = _runs()
    f = split_cards.find_splits(runs)
    by = f["入れ先ごとの鍵"]
    carded = sum(c["割れた鍵の数"] for c in f["カード"])
    reasons = sum(x["割れた鍵の数"] for x in f["カードにできなかった組"])
    # 線 1: 割れた鍵は全部、カード・できなかった理由・有無だけのどれか 1 つに入る。
    assert carded + reasons + by[split_cards.PRESENCE_ONLY] == f["割れた鍵"]
    assert sum(by.values()) == f["割れた鍵"]
    dims = {c["次元"] for c in f["カード"]}
    assert "数量" in dims and "室" in dims
    qty = next(c for c in f["カード"] if c["次元"] == "数量")
    # 3 回の値(重複は 1 つ)+ どれでもない + 分からない。小さい順。
    assert qty["選択肢"] == ["10m2", "12m2", split_cards.NONE_OF_THESE, split_cards.DONT_KNOW]
    assert qty["推奨"] is None and not qty["数字の入力"] and not qty["自由記述"] and qty["見込み"] is None
    assert {s["辿る"]["回"] for s in qty["値の出どころ"]["10m2"]} == {"R1", "R3"}
    room = next(c for c in f["カード"] if c["次元"] == "室")
    assert split_cards.real_options(room) == ["室A", "室B"]
    reasons_seen = {x["理由"] for x in f["カードにできなかった組"]}
    # コンセントは R3 に 2 行 → 対応が決まらない。床の下地は R1 が未取得 → 値が 1 つしか無い(0 を作らない)。
    assert "回の中で対応が 1 つに決まらない" in reasons_seen
    assert "値が 1 つしか無い" in reasons_seen
    assert by[split_cards.PRESENCE_ONLY] >= 1  # 幅木の撤去は R2 にしか無い
    for c in f["カード"]:
        assert all(o not in ("0m2", "0") for o in c["選択肢"])
    # 名前だけの違い(「張」と「張り」)は鍵が同じなのでカードにしない。
    assert all("張り" not in c["問い"] or c["次元"] != "名前" for c in f["カード"])


def test_name_only_difference_is_not_a_card():
    runs = {"R1": [_it("a", "壁のクロス張", 10.0)], "R2": [_it("b", "壁クロス張り", 10.0)],
            "R3": [_it("c", "壁のクロス張", 10.0)]}
    f = split_cards.find_splits(runs)
    assert f["カード"] == [] and f["割れた鍵"] == 0
    assert f["名前だけの違い(辞書で解いた)"] == 1


def test_no_crop_is_a_reason_not_a_silent_drop():
    runs = {n: [_it(n + "x", "壁のクロス張", q, box=None)] for n, q in (("R1", 10.0), ("R2", 12.0), ("R3", 10.0))}
    f = split_cards.find_splits(runs)
    assert f["カード"] == []
    assert [x["理由"] for x in f["カードにできなかった組"]] == ["切り抜きが無い"]


def test_machine_and_scale_values_join_without_showing_which():
    runs = {n: [_it(n + "x", "壁のクロス張", q)] for n, q in (("R1", 10.0), ("R2", 12.0), ("R3", 10.0))}
    machine = {"R1": {"R1x": [{"値": 11.0, "単位": "m2", "辿る": {"機械の行": 3}},
                              {"値": 0, "単位": "m2", "辿る": {}}, {"値": 9.0, "単位": "m", "辿る": {}}]}}
    scale = {"R2": {"R2x": [{"値": 12.0, "単位": "m2", "辿る": {"ページ": [1]}}]}}
    f = split_cards.find_splits(runs, machine=machine, scale=scale)
    card = f["カード"][0]
    # 0 と単位の違う値は使わない。12m2 は 3 回の値と縮尺の値が同じ文字なので 1 つ。
    assert split_cards.real_options(card) == ["10m2", "11m2", "12m2"]
    assert {s["出どころ"] for s in card["値の出どころ"]["12m2"]} == {"3回の値", "縮尺で換算した値"}
    assert all("機械" not in o and "回" not in o for o in card["選択肢"])


def test_apply_resolves_split_and_dont_know_changes_nothing():
    runs = _runs()
    f = split_cards.find_splits(runs)
    qty = next(c for c in f["カード"] if c["次元"] == "数量")
    room = next(c for c in f["カード"] if c["次元"] == "室")
    before = deepcopy(runs)
    for none in split_cards.TAIL:
        res = split_cards.apply(runs, [qty, room], {qty["鍵"]: none, room["鍵"]: none})
        assert res["決めた行"] == [] and len(res["書くだけの答え"]) == 2
    assert runs == before
    res = split_cards.apply(runs, [qty], {qty["鍵"]: "999m2"})
    assert res["戻せなかった答え"][0]["理由"] == "カードの選択肢に無い答え"
    assert runs == before
    split_before = unc.readings_split(list(runs.values()))["割れた鍵"]
    res = split_cards.apply(runs, [qty, room], {qty["鍵"]: "12m2", room["鍵"]: "室A"})
    assert not res["戻せなかった答え"]
    assert {it["数量"] for r in runs.values() for it in r if it["id"] in qty["行"].values()} == {12.0}
    assert {it["場所"] for r in runs.values() for it in r if it["id"] in room["行"].values()} == {"室A"}
    for r in runs.values():
        for it in r:
            if it["id"] in set(qty["行"].values()) | set(room["行"].values()):
                assert it["根拠の種類"] == "人の回答" and it["確度"] == "高"
    split_after = unc.readings_split(list(runs.values()))["割れた鍵"]
    assert len(split_after) < len(split_before)


def test_order_puts_settling_cards_first_and_groups_by_frame():
    runs = _runs()
    f = split_cards.find_splits(runs)
    ordered = split_cards.order(f["カード"], runs, "R1")
    flags = [c["1つの答えで確定する行"] for c in ordered]
    assert flags == sorted(flags, reverse=True)
    assert all(c["金額"].startswith("未取得") for c in ordered)
    assert all(c.get("枠") for c in ordered)
    shown = split_cards.numbered(ordered, 10, group_by_frame=True)
    assert [c["番号"] for c in shown] == list(range(1, len(shown) + 1))
    assert all([o["番号"] for o in c["番号つきの選択肢"]] == list(range(1, len(c["選択肢"]) + 1)) for c in shown)


def test_parse_numbered_reads_variants_and_counts_unreadable():
    cards = [{"鍵": f"k{i}", "選択肢": ["1m2", "2m2", "どれでもない", "分からない"]} for i in range(1, 5)]
    got = answers_io.parse_numbered("1-3、２－１ 3ー2,4−4\n5-1 x-1 3-1 2-1 1 4 - 9 ３－", cards)
    assert got["答え"] == {"k1": "どれでもない", "k2": "1m2", "k4": "分からない"}
    reasons = sorted(r["理由"] for r in got["戻せなかった答え"])
    assert reasons.count("同じカードに違う答え") == 2  # 3-2 と 3-1 は両方戻さない
    assert "カードの番号が無い" in reasons and "選択肢の番号が無い" in reasons and "形が読めない" in reasons
    # 4-4 と 4-9: 4-9 は選択肢の番号が無いので、4 は 4-4 で戻る。
    # 黙って捨てた文字は 0。
    assert got["読んだ文字の数"] == len(got["戻した文字"]) + len(got["戻せなかった答え"])
    assert set(r for r in answers_io.NUMBERED_REASONS) >= set(reasons)


def test_parse_numbered_keeps_k4_and_roundtrip():
    cards = [{"鍵": f"k{i}", "選択肢": ["1m2", "2m2", "どれでもない", "分からない"]} for i in range(1, 4)]
    picks = {"k1": "2m2", "k3": "分からない"}
    text = answers_io.numbered_text(cards, picks)
    assert text == "1-2、3-4"
    got = answers_io.parse_numbered(text, cards)
    assert got["答え"] == picks and got["戻せなかった答え"] == [] and got["答えの無いカード"] == [2]


def test_decoys_and_paraphrases_lines():
    from benchmarks.measure_k70_split_cards import decoy_check, parse_check

    d = decoy_check()
    assert d["線5(i): 名前だけの違いに入った囮"] == 0
    assert d["線5(ii): 数量のカードにならなかった数量違い"] == 0
    assert parse_check()["黙って捨てた"] == 0


def _pdf(tmp_path: Path) -> Path:
    import pymupdf

    path = tmp_path / "合成.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.draw_rect(pymupdf.Rect(500, 300, 600, 350), color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((100, 100), "TEST", fontsize=20)
    doc.save(path)
    doc.close()
    return path


def test_crop_uses_image_pixel_coordinates(tmp_path):
    """読みの位置は幅 2000 画素の画像の座標。PDF の点に戻してから K-65 の切り抜きに渡すので、四角が黒の上に来る。"""
    import io

    from PIL import Image

    from benchmarks.make_k70_split_cards import crop_image

    pdf = _pdf(tmp_path)
    zoom = 2000 / 1190
    box_px = [500 * zoom, 300 * zoom, 600 * zoom, 350 * zoom]
    jpeg, marker = crop_image(pdf, 1, box_px)
    img = Image.open(io.BytesIO(jpeg)).convert("L")
    x = int((marker[0] + marker[2] / 2) / 100 * img.width)
    y = int((marker[1] + marker[3] / 2) / 100 * img.height)
    assert img.getpixel((x, y)) < 60  # 四角の真ん中は黒い所
    assert img.getpixel((2, 2)) > 200  # 角は白い所


def _shown(tmp_path: Path, count: int = 3):
    from benchmarks.make_k70_split_cards import with_images

    runs = _runs()
    f = split_cards.find_splits(runs)
    extra = [dict(f["カード"][0], 鍵=f"写し{i}") for i in range(count)]
    ordered = split_cards.order(f["カード"], runs, "R1") + [dict(c, 枠="その他") for c in extra]
    return with_images(split_cards.numbered(ordered, count + 2), _pdf(tmp_path))


def test_pdf_opens_a4_large_text_and_matches_html(tmp_path):
    from benchmarks.make_k70_split_cards import cards_in_html, check_pdf, render_html, render_pdf

    shown = _shown(tmp_path)
    out = tmp_path / "カード.pdf"
    info = render_pdf(shown, out)
    check = check_pdf(out, shown)
    assert check["開けた"] and check["A4縦"]
    assert check["いちばん小さい字(pt)"] >= 12
    assert check["画像の数"] >= len(shown)
    assert all(1 <= n <= 2 for n in check["1ページのカードの数"])
    assert sum(check["1ページのカードの数"]) == len(shown)
    assert check["見つからなかった問い"] == 0 and check["見つからなかった選択肢"] == 0
    assert check["答え方が書いてある"]
    assert info["カード"] == len(shown)
    html = tmp_path / "カード.html"
    render_html(shown, html)
    in_html = cards_in_html(html)
    assert [(c["番号"], c["問い"], [(o["番号"], o["文字"]) for o in c["選択肢"]]) for c in in_html] == [
        (c["番号"], c["問い"], [(o["番号"], o["文字"]) for o in c["番号つきの選択肢"]]) for c in shown]
    text = html.read_text(encoding="utf-8")
    # 機械の推す答え・出どころ・見込みは出さない。
    for word in ("値の出どころ", "機械の値", "3回の値", "縮尺で換算", "見込み", "推奨"):
        assert word not in text
    assert "1-3、2-1" in text


def test_unit_spelling_only_is_name_only_not_a_card():
    """「1か所」と「1箇所」は K-66 の辞書で揃えると同じ値。カードにしない(1 回目の測りでカードになっていた)。"""
    runs = {"R1": [_it("a", "扉の取手の移設", 1, "か所")], "R2": [_it("b", "扉の取手の移設", 1, "箇所")],
            "R3": [_it("c", "扉の取手の移設", 1, "ヶ所")]}
    f = split_cards.find_splits(runs)
    assert f["割れた鍵"] == 1  # 割れの信号(K-68 B のまま)は単位の文字の違いを割れと数える
    assert f["カード"] == []
    assert f["入れ先ごとの鍵"][split_cards.UNIT_NAME_ONLY] == 1
    runs["R2"][0]["数量"] = 5  # 個数の許容差(±1)の外
    f = split_cards.find_splits(runs)
    assert split_cards.real_options(f["カード"][0]) == ["1箇所", "5箇所"]


def test_room_options_merge_by_k66_room_key():
    """室の選択肢は K-66 の室の鍵でまとめる(2 回目の測りで、横棒の字だけ違う室が別の選択肢になっていた)。"""
    runs = {"R1": [_it("a", "壁のクロス張", 10.0, room="室A-室B(1)")],
            "R2": [_it("b", "壁のクロス張", 10.0, room="室A−室B(1)")],
            "R3": [_it("c", "壁のクロス張", 10.0, room="室B(1)ー室C")]}
    f = split_cards.find_splits(runs)
    card = next(c for c in f["カード"] if c["次元"] == "室")
    real = split_cards.real_options(card)
    assert real == ["室A-室B(1)", "室B(1)ー室C"]
    assert {s["辿る"]["回"] for s in card["値の出どころ"]["室A-室B(1)"]} == {"R1", "R2"}
    # 室の横棒の字だけの違いは、K-66 の鍵で同じ室なので、そもそも割れない。
    same = {n: [_it(n, "壁のクロス張", 10.0, room=r)] for n, r in (("R1", "室A-室B(1)"), ("R2", "室A−室B(1)"),
                                                                  ("R3", "室Aー室B(1)"))}
    assert split_cards.find_splits(same)["割れた鍵"] == 0


def test_pc_side_truth_measure_with_synthetic_golden(tmp_path):
    """PC 側の測り(正解を使う)を、合成の正解ファイルで動かす。実の正解は使わない。"""
    import json

    from benchmarks.measure_k70_split_cards_truth import measure
    from benchmarks.pc_kit import DEFAULT_COLUMNS, Gold

    runs = {n: [_it(n + "q", "壁のクロス張", q), _it(n + "r", "床のフロアタイル張", 20.0, room=r)]
            for n, q, r in (("R1", 10.0, "室A"), ("R2", 12.0, "室B"), ("R3", 10.0, "室A"))}
    gold_path = tmp_path / "合成の正解.json"
    gold_path.write_text(json.dumps({"expected_items": [
        {"code": "X1", "work_item": "壁のクロス張", "unit": "m2", "quantity": 12.0, "major_category": "内装"},
        {"code": "X2", "work_item": "床のフロアタイル張", "unit": "m2", "quantity": 20.0, "major_category": "内装"},
    ]}, ensure_ascii=False), encoding="utf-8")
    gold = Gold(gold_path, DEFAULT_COLUMNS, allow_other_denominator=True)
    f = split_cards.find_splits(runs)
    real = {"base": "R1", "runs": runs, "ordered": split_cards.order(f["カード"], runs, "R1")}
    out = measure(real, gold, answer_text="1-2、2-1、9-9")
    assert out["照合できたカード"] == 1  # 室のカードは正解に室が無いので照合しない
    assert out["照合できなかった理由"] == {"正解に室が無い": 1}
    assert out["選択肢に正しい値が無いカード"] == 0
    ideal = out["理想の人(全部のカード)"]
    assert ideal["正解と合う行 前→後"] == [0, 1] and ideal["合っていたのに合わなくなった行"] == 0
    assert ideal["自動確定"] == 0
    assert out["方針 A(1 つ目)が正しかったカード"] == 0  # 1 つ目は 10m2、正解は 12m2
    human = out["人の答え(PDF の 10 枚)"]
    assert human["戻せなかった答え"] == 1 and human["戻せなかった理由"] == {"カードの番号が無い": 1}
    assert runs["R1"][0]["数量"] == 10.0  # 元の読みは書き換えない
