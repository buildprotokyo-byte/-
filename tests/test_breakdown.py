"""見積の行を、公共建築工事内訳書標準書式の段に組む(K-36 改訂版 3 節)。

守りたいこと

1. **科目の一覧を先に固定しない。** 読み取った行にある科目だけが出る。空の科目は出ない。
2. **中科目は、分ける必要があるときだけ立てる。** 行が中科目を持たなければ段ごと省く。
3. 細目は数量・単位・単価・金額・摘要を持つ。**単価が無ければ金額は空のまま。作らない。**
4. 科目の金額は、細目の金額が全部そろったときだけ足す。そろわなければ空で、足りない件数を出す。
5. 共通費に入る科目は名前で分ける(仮の判断)。科目の無い行は「科目未定」に集め、黙って捨てない。
"""

from __future__ import annotations

import pytest

from estimating.breakdown import (
    DIRECT_COST,
    COMMON_COST,
    UNDECIDED_KAMOKU,
    build_breakdown,
)


def _row(kamoku, name, *, qty=1.0, unit="箇所", price=None, spec="", middle=""):
    return {
        "科目": kamoku,
        "中科目": middle,
        "工事項目": name,
        "摘要": spec,
        "数量": qty,
        "単位": unit,
        "単価": price,
    }


def test_only_the_kamoku_that_appear_in_the_rows_are_raised() -> None:
    book = build_breakdown([_row("内装仕上工事", "床 フローリング"), _row("建具工事", "扉")])

    names = [k.name for s in book.shumoku for k in s.kamoku]
    assert names == ["内装仕上工事", "建具工事"]
    assert "電気設備工事" not in names


def test_the_middle_level_is_omitted_when_nothing_needs_it() -> None:
    book = build_breakdown([_row("内装仕上工事", "床"), _row("内装仕上工事", "壁")])

    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.middles == []
    assert [d.name for d in kamoku.details] == ["床", "壁"]


def test_the_middle_level_appears_when_the_rows_split_the_kamoku() -> None:
    book = build_breakdown(
        [
            _row("電気設備工事", "コンセント", middle="電灯コンセント設備"),
            _row("電気設備工事", "LAN", middle="情報通信設備"),
        ]
    )

    kamoku = book.shumoku[0].kamoku[0]
    assert [m.name for m in kamoku.middles] == ["電灯コンセント設備", "情報通信設備"]
    assert kamoku.details == []


def test_a_detail_without_a_unit_price_has_no_amount() -> None:
    book = build_breakdown([_row("内装仕上工事", "床", qty=10.0, unit="㎡", price=None)])

    detail = book.shumoku[0].kamoku[0].details[0]
    assert detail.amount is None
    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.amount is None
    assert kamoku.details_without_amount == 1


def test_the_kamoku_amount_is_summed_only_when_every_detail_has_one() -> None:
    book = build_breakdown(
        [
            _row("内装仕上工事", "床", qty=10.0, unit="㎡", price=3000),
            _row("内装仕上工事", "壁", qty=2.0, unit="㎡", price=1000),
        ]
    )

    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.amount == 32000
    assert kamoku.details_without_amount == 0


def test_the_spec_column_is_kept_on_the_detail() -> None:
    book = build_breakdown([_row("内装仕上工事", "床", spec="仕上: フローリング / 下地: 既存")])

    assert book.shumoku[0].kamoku[0].details[0].spec == "仕上: フローリング / 下地: 既存"


def test_site_management_goes_to_the_common_cost() -> None:
    book = build_breakdown([_row("内装仕上工事", "床"), _row("現場管理・諸経費", "諸経費")])

    by_name = {s.name: [k.name for k in s.kamoku] for s in book.shumoku}
    assert by_name == {DIRECT_COST: ["内装仕上工事"], COMMON_COST: ["現場管理・諸経費"]}


def test_rows_without_a_kamoku_are_gathered_not_dropped() -> None:
    book = build_breakdown([_row("", "何かの記号"), _row(None, "別の記号")])

    kamoku = book.shumoku[0].kamoku
    assert [k.name for k in kamoku] == [UNDECIDED_KAMOKU]
    assert len(kamoku[0].details) == 2


def test_no_shumoku_is_raised_for_an_empty_input() -> None:
    assert build_breakdown([]).shumoku == []


def test_the_dict_form_keeps_the_four_levels() -> None:
    book = build_breakdown([_row("内装仕上工事", "床", spec="仕上: A")])

    out = book.as_dict()
    detail = out["種目"][0]["科目"][0]["細目"][0]
    assert set(detail) >= {"名称", "摘要", "数量", "単位", "単価", "金額"}
    assert "中科目" not in out["種目"][0]["科目"][0]


def test_the_one_pass_output_carries_the_breakdown(tmp_path) -> None:
    """一本通した出力に内訳書が付き、科目は行にあるものだけ。"""
    import app
    from tests.test_app_one_pass import _build_pdf, _legend_table

    result = app.run(
        _build_pdf(tmp_path / "plan.pdf"),
        case_id="K36-TEST",
        answers_path=tmp_path / "a.json",
        legend_table=_legend_table(tmp_path / "legend.json"),
        build_ledger_stage=False,
    )
    book = result.as_dict()["内訳書"]
    kamoku = [k["名称"] for s in book["種目"] for k in s["科目"]]
    assert set(kamoku) == {"内装仕上工事", "解体・撤去工事", UNDECIDED_KAMOKU}
    assert "電気設備工事" not in kamoku
    details = [d for s in book["種目"] for k in s["科目"] for d in k.get("細目", [])]
    assert len(details) == len(result.lines)
    floor = next(d for d in details if d["名称"] == "床 フローリング 改修")
    assert floor["摘要"] == "仕上: フローリング / 下地: 既存"
    assert floor["金額"] is None  # 単価が無いので作らない
    assert result.auto_confirmed_total == 0


# ---------------------------------------------------------------------------
# K-38 原則11: 数量は最小の単位で出し、内訳を持ったまま足し上げる
# ---------------------------------------------------------------------------


def _room_row(room, qty, *, name="壁 ビニルクロス 新設", unit="㎡", kamoku="内装仕上工事"):
    row = _row(kamoku, name, qty=qty, unit=unit)
    row["場所"] = room
    return row


def test_the_same_work_in_several_rooms_is_one_detail_with_a_breakdown() -> None:
    book = build_breakdown([_room_row("玄関", 10.0), _room_row("書斎", 2.86)])

    details = book.shumoku[0].kamoku[0].details
    assert len(details) == 1
    detail = details[0]
    assert [(p.place, p.quantity) for p in detail.parts] == [("玄関", 10.0), ("書斎", 2.86)]
    assert detail.quantity == pytest.approx(12.86)
    assert detail.missing == ()


def test_a_missing_room_is_kept_as_missing_and_the_total_is_not_given() -> None:
    book = build_breakdown(
        [_room_row("玄関", 10.0), _room_row("ホール", None), _room_row("書斎", 2.86)]
    )

    detail = book.shumoku[0].kamoku[0].details[0]
    assert detail.quantity is None  # 未取得があるうちは合計を出さない
    assert detail.missing == ("ホール",)
    assert [p.quantity for p in detail.parts] == [10.0, None, 2.86]  # 0 にしない
    out = detail.as_dict()
    assert out["数量"] is None
    assert out["未取得"] == ["ホール"]
    assert {"場所": "ホール", "数量": "未取得"} in out["内訳"]


def test_different_units_or_names_stay_separate_details() -> None:
    book = build_breakdown(
        [
            _room_row("玄関", 10.0),
            _room_row("玄関", 4.0, name="巾木 新設", unit="m"),
            _room_row("書斎", 2.0, unit="m"),
        ]
    )
    names = [(d.name, d.unit) for d in book.shumoku[0].kamoku[0].details]
    assert names == [("壁 ビニルクロス 新設", "㎡"), ("巾木 新設", "m"), ("壁 ビニルクロス 新設", "m")]


def test_no_amount_is_made_while_a_room_is_missing() -> None:
    rows = [_room_row("玄関", 10.0), _room_row("ホール", None)]
    for row in rows:
        row["単価"] = 1000
    detail = build_breakdown(rows).shumoku[0].kamoku[0].details[0]
    assert detail.amount is None


# --- K-54: 公開書式(B-08)の出力の決まり ---------------------------------------------


def _krow(kamoku, name, *, kind="", middle="", qty=1.0, unit="箇所"):
    row = _row(kamoku, name, qty=qty, unit=unit, middle=middle)
    row["区分"] = kind
    return row


def test_k54_renovation_removal_and_renewal_are_middles_of_a_building_kamoku() -> None:
    book = build_breakdown(
        [
            _krow("内装改修", "床 フローリング撤去", kind="撤去", unit="㎡"),
            _krow("内装改修", "床 フローリング", kind="新設", unit="㎡"),
        ]
    )

    kamoku = book.shumoku[0].kamoku[0]
    assert [m.name for m in kamoku.middles] == ["撤去", "改修"]


def test_k54_equipment_removal_is_a_detail_of_the_equipment_kamoku_not_a_middle() -> None:
    book = build_breakdown(
        [
            _krow("電気設備", "ダウンライト", kind="撤去"),
            _krow("電気設備", "ダウンライト", kind="新設"),
        ]
    )

    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.middles == []
    assert [d.name for d in kamoku.details] == ["ダウンライト撤去", "ダウンライト"]


def test_k54_a_removal_name_that_already_says_removal_is_not_doubled() -> None:
    book = build_breakdown([_krow("電気設備", "照明器具撤去", kind="撤去")])

    assert [d.name for d in book.shumoku[0].kamoku[0].details] == ["照明器具撤去"]


def test_k54_a_middle_that_is_not_in_the_official_table_is_not_added() -> None:
    """正式な中科目が無い科目に、仕様書の節名を中科目として足さない。"""
    book = build_breakdown(
        [_krow("木工事", "巾木", middle="造作工事(仕様書 3 節)")],
        official_middles={"電気設備": ("電灯設備",)},
    )

    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.middles == []
    detail = kamoku.details[0]
    assert detail.as_dict()["書式に無い中科目"] == "造作工事(仕様書 3 節)"


def test_k54_an_official_middle_is_kept() -> None:
    book = build_breakdown(
        [_krow("電気設備", "コンセント", middle="電灯設備")],
        official_middles={"電気設備": ("電灯設備",)},
    )

    assert [m.name for m in book.shumoku[0].kamoku[0].middles] == ["電灯設備"]


def test_k54_no_empty_kamoku_or_middle_is_output() -> None:
    book = build_breakdown(
        [
            _krow("内装改修", "床", kind="撤去"),
            _krow("木工事", "巾木", middle="造作工事"),
        ],
        official_middles={},
    )

    out = book.as_dict()
    for shumoku in out["種目"]:
        for kamoku in shumoku["科目"]:
            if "中科目" in kamoku:
                assert kamoku["中科目"] and all(m["細目"] for m in kamoku["中科目"])
            else:
                assert kamoku["細目"]


def test_k54_without_a_kind_or_middle_nothing_changes() -> None:
    book = build_breakdown([_krow("内装改修", "床"), _krow("内装改修", "壁")])

    kamoku = book.shumoku[0].kamoku[0]
    assert kamoku.middles == []
    assert [d.name for d in kamoku.details] == ["床", "壁"]
