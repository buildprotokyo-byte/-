"""K-55 位置つきの読みから記号を室ごとに数える(intake/positioned_symbol_count.py)。合成データだけ。"""
import json

import app
from intake.positioned_symbol_count import NO_ROOM, count_symbols, place_in_room


def _reading():
    return {
        "ページ": [
            {
                "ページ": 1,
                "要素": [
                    {"id": "p1-001", "種類": "文字", "内容": "室A", "位置": [100, 100, 140, 120]},
                    {"id": "p1-002", "種類": "文字", "内容": "室B", "位置": [900, 100, 940, 120]},
                    {"id": "p1-003", "種類": "記号", "内容": "丸", "位置": [150, 150, 160, 160]},
                    {"id": "p1-004", "種類": "記号", "内容": "丸", "位置": [110, 200, 120, 210]},
                    {"id": "p1-005", "種類": "記号", "内容": "丸", "位置": [900, 150, 910, 160]},
                    {"id": "p1-006", "種類": "記号", "内容": "丸", "位置": [500, 800, 510, 810]},
                    {"id": "p1-007", "種類": "記号", "内容": "?", "位置": [120, 130, 130, 140]},
                    {"id": "p1-008", "種類": "記号", "内容": "丸", "位置": [130, 130, 135, 135]},
                ],
            },
            {
                "ページ": 2,
                "要素": [
                    {"id": "p2-001", "種類": "文字", "内容": "室A", "位置": [100, 100, 140, 120]},
                    {"id": "p2-002", "種類": "記号", "内容": "丸", "位置": [150, 150, 160, 160]},
                ],
            },
        ]
    }


def _naming():
    return {
        "記号": [
            {"id": "p1-003", "凡例の名前": "コンセント", "区分": "新設"},
            {"id": "p1-004", "凡例の名前": "コンセント", "区分": "新設"},
            {"id": "p1-005", "凡例の名前": "コンセント", "区分": "新設"},
            {"id": "p1-006", "凡例の名前": "コンセント", "区分": "新設"},
            {"id": "p1-007", "凡例の名前": "凡例に無い", "区分": "不明"},
            {"id": "p2-002", "凡例の名前": "コンセント", "区分": "新設"},
        ],
        "室名": [
            {"id": "p1-001", "室名": "室A"},
            {"id": "p1-002", "室名": "室B"},
            {"id": "p2-001", "室名": "室A"},
        ],
    }


def test_place_in_room_takes_nearest_within_distance():
    rooms = [("室A", (100, 100, 140, 120)), ("室B", (900, 100, 940, 120))]
    assert place_in_room([150, 150, 160, 160], rooms) == "室A"
    assert place_in_room([500, 800, 510, 810], rooms) == NO_ROOM


def test_counts_per_room_and_keeps_unplaced_and_unnamed_apart():
    res = count_symbols(_reading(), _naming())
    by = {(r.name, r.kind, r.room): r for r in res.rows}
    assert by[("コンセント", "新設", "室A")].quantity == 2
    assert by[("コンセント", "新設", "室B")].quantity == 1
    assert by[("コンセント", "新設", NO_ROOM)].quantity == 1
    assert res.without_room == 1
    assert res.without_name == 1  # 凡例に無いは行にしない
    assert res.unlabeled == 1  # 名前づけの無い記号も行にしない(数えるだけ)


def test_same_thing_on_two_pages_takes_larger_page_and_keeps_other():
    res = count_symbols(_reading(), _naming())
    row = next(r for r in res.rows if r.room == "室A")
    assert row.page == 1 and row.quantity == 2
    assert row.other_pages == ((2, 1),)
    answer = row.as_answer_row()
    assert answer["数量"] == 2 and "別の図にもある" in answer["式"]


def test_counted_rows_ride_behind_ai_rows_without_confirming(tmp_path):
    from intake.ai_reading import parse_ai_reading

    counts = tmp_path / "counts.json"
    counts.write_text(json.dumps(count_symbols(_reading(), _naming()).as_reading(), ensure_ascii=False))
    reading = parse_ai_reading({"行": [{"工事": "壁紙張替", "場所": "室A", "数量": None, "単位": "㎡",
                                       "式": "", "根拠": ""}]}, reader="テスト")
    result = app.run_ai_reading(reading, case_id="t", symbol_counts=app._load_symbol_counts(counts))
    assert len(result.lines) == 1 + 3
    counted = result.lines[1:]
    assert all(line.quantity is not None for line in counted)
    assert all(any("機械が室ごとに数えた" in n for n in line.notes) for line in counted)
    assert result.lines[0].quantity is None  # AI の空の数量は空のまま
    assert result.auto_confirmed_total == 0
    assert result.extras["記号の数え上げ"]["足した行"] == 3


def test_decoy_keeps_names_and_places_and_the_multiset_of_counts():
    from benchmarks.k55_symbol_count import decoy

    reading = count_symbols(_reading(), _naming()).as_reading()
    fake = decoy(reading, 1)
    assert [(r["工事"], r["場所"]) for r in fake["行"]] == [(r["工事"], r["場所"]) for r in reading["行"]]
    assert sorted(r["数量"] for r in fake["行"]) == sorted(r["数量"] for r in reading["行"])
