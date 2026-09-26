"""K-49: AI が読んだ答案を本番の入力にし、機械は検算に回る。**合成のデータだけを使う。**

守りたいこと

1. 答案の形を確かめて読み込む。全体の形が違えば止め、行の崩れは理由つきで落として数える。
2. 答案の行が見積の行になる。**数量は答案のまま、null は null のまま(0 にしない)。**
3. 決め手は「AI の読み」(証拠=読み手が無ければ名乗れない)。**確かさは上げない。自動確定は 0 件。**
4. 機械の読みは行を作らず、同じ工事・場所の AI の行に「機械の検算」として並ぶ。
   食い違えば要確認の理由に書く。**機械は答案を直さない。**
5. 本番の入口の既定は AI の側。AI をその場で呼ぶ役は、鍵が無くても止まらずに「読んでいない」。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
from estimating import decisive
from intake import ai_reading as air
from tests.test_k42_notes_hatch import _full_pdf


def _row(work: str, place: str, quantity, unit: str = "枚", formula: str = "", basis: str = "合成"):
    return {"工事": work, "場所": place, "数量": quantity, "単位": unit, "式": formula, "根拠": basis}


def _answer(*rows) -> dict:
    return {"行": list(rows), "ページごとの所要": [{"ページ": 1, "見たか": True}]}


# ---------------------------------------------------------------------------
# 1. 答案の形
# ---------------------------------------------------------------------------


def test_null_quantity_stays_null_and_numbers_stay_as_given() -> None:
    reading = air.parse_ai_reading(
        _answer(_row("棚 新設", "室A", 3), _row("床 張替", "室A", None, "㎡"), _row("壁 塗装", "室B", 12.5, "㎡")),
        reader="合成",
    )

    assert [r.quantity for r in reading.rows] == [3, None, 12.5]
    assert reading.dropped == []
    assert reading.summary()["数量が空の行"] == 1


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"工事": "棚", "場所": "室A", "数量": 1, "単位": "枚", "式": ""}, "欄が欠けている"),
        (_row("棚", "室A", "6"), "数量が数でも null でもない"),
        (_row("棚", "室A", True), "数量が数でも null でもない"),
        (_row("棚", "室A", float("nan")), "有限の数ではない"),
        (_row("", "室A", 1), "工事が空"),
        (_row("棚", 3, 1), "場所が文字でも null でもない"),
        ("棚 6枚", "辞書ではない"),
    ],
)
def test_broken_rows_are_dropped_with_a_reason_and_counted(bad, why) -> None:
    reading = air.parse_ai_reading(_answer(_row("棚 新設", "室A", 1), bad))

    assert [r.work for r in reading.rows] == ["棚 新設"]
    assert len(reading.dropped) == 1
    assert reading.dropped[0]["番号"] == 2
    assert why in reading.dropped[0]["理由"]
    assert reading.summary()["形が崩れて落とした行"] == 1
    assert reading.summary()["答案の行"] == 2


@pytest.mark.parametrize(
    "payload",
    [[_row("棚", "室A", 1)], {"ページごとの所要": []}, {"行": {"工事": "棚"}}, {"行": [], "ページごとの所要": 3}],
)
def test_a_wrong_overall_shape_stops_the_load(payload) -> None:
    with pytest.raises(air.AIReadingError):
        air.parse_ai_reading(payload)


def test_a_broken_json_file_stops_the_load(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{行: ", encoding="utf-8")
    with pytest.raises(air.AIReadingError):
        air.load_ai_reading(path)


def test_no_answer_file_means_not_read_with_a_reason() -> None:
    reading = air.load_ai_reading(None)

    assert reading.status == air.STATUS_NOT_READ
    assert reading.rows == []
    assert "--ai-reading" in reading.reason


# ---------------------------------------------------------------------------
# 2・3. 見積の行と決め手
# ---------------------------------------------------------------------------


def test_answer_rows_become_lines_with_the_ai_reading_as_the_decisive_reason() -> None:
    reading = air.parse_ai_reading(
        _answer(_row("棚 新設", "室A", 3, formula="1+2"), _row("床 張替", "室A", None, "㎡")),
        reader="答案のファイル:合成.json",
    )
    lines = app.ai_reading_lines(reading)

    assert [line.quantity for line in lines] == [3, None]  # null は 0 にならない
    assert all(line.path == app.PATH_AI_READING for line in lines)
    assert all(line.certainty == app.CERTAINTY_CANDIDATE for line in lines)  # 確かさは上げない
    assert [[r.kind for r in line.decisive] for line in lines] == [[decisive.REASON_AI_READING]] * 2
    assert lines[0].decisive[0].ai_reader == "答案のファイル:合成.json"
    row = lines[1].as_answer_row(2)
    assert row["数量"] is None
    assert row["出どころ"] == "AI が読んだ"
    assert row["決め手"][0]["kind"] == decisive.REASON_AI_READING
    assert lines[0].as_answer_row(1)["式"] == "1+2"
    assert decisive.counts_by_reason(lines) == {decisive.REASON_AI_READING: 2}


def test_the_ai_reading_reason_needs_its_evidence() -> None:
    with pytest.raises(decisive.DecisiveError):
        decisive.DecisiveReason(kind=decisive.REASON_AI_READING)
    with pytest.raises(decisive.DecisiveError):
        decisive.DecisiveReason(kind=decisive.REASON_OBSERVED, ai_reader="anthropic:x")
    # 読み手が無ければ「AI の読み」は作らない(観測のまま)。
    kinds = [r.kind for r in decisive.decisive_reasons_for(effective_derivation="read")]
    assert kinds == [decisive.REASON_OBSERVED]


# ---------------------------------------------------------------------------
# 4. 機械の検算
# ---------------------------------------------------------------------------


def _machine(number: int, work: str, place: str, quantity, unit: str = "枚") -> dict:
    return {"番号": number, "工事項目": work, "場所": place, "数量": quantity, "単位": unit, "道": "合成"}


def test_machine_numbers_are_placed_beside_the_ai_line_and_a_disagreement_needs_check() -> None:
    reading = air.parse_ai_reading(
        _answer(
            _row("棚 新設", "室A", 6),  # 機械も 6 → 並べるだけ
            _row("棚　新設", "室Ｂ", 5),  # 機械は 4 → 食い違い(文字は NFKC・空白を除いて引く)
            _row("床 張替", "室A", None, "㎡"),  # AI は空、機械は 10 → 埋めずに要確認
            _row("壁 塗装", "室C", 8, "㎡"),  # 機械に無い → 何も並べない
        )
    )
    lines = app.ai_reading_lines(reading)
    summary = app.check_with_machine(
        lines,
        [
            _machine(1, "棚 新設", "室A", 6.0),
            _machine(2, "棚新設", "室B", 4),
            _machine(3, "床 張替", "室A", 10, "㎡"),
            _machine(4, "照明 移設", "室D", 2, "台"),  # AI の行に引けない
            _machine(5, "壁 塗装", "室C", None, "㎡"),  # 数が無い → 並べない
        ],
        "合成",
    )

    same, differs, empty, alone = lines
    assert same.certainty == app.CERTAINTY_CANDIDATE
    assert same.extra["機械の検算"][0]["数量"] == 6.0
    assert differs.certainty == app.CERTAINTY_NEEDS_CHECK
    assert differs.quantity == 5  # 機械は答案を直さない
    assert "食い違う" in differs.extra["要確認の理由"][0]
    assert empty.certainty == app.CERTAINTY_NEEDS_CHECK
    assert empty.quantity is None  # 機械の数で埋めない
    assert "埋めていない" in empty.extra["要確認の理由"][0]
    assert alone.certainty == app.CERTAINTY_CANDIDATE
    assert "機械の検算" not in alone.extra
    assert summary["機械の数を並べた AI の行"] == 3
    assert summary["食い違って要確認にした AI の行"] == 2
    assert summary["AI の行に引けなかった機械の行"] == 1
    assert summary["引けなかった機械の行"][0]["工事項目"] == "照明 移設"


def test_a_unit_disagreement_needs_check() -> None:
    lines = app.ai_reading_lines(air.parse_ai_reading(_answer(_row("棚 新設", "室A", 6, "枚"))))
    app.check_with_machine(lines, [_machine(1, "棚 新設", "室A", 6, "箇所")])

    assert lines[0].certainty == app.CERTAINTY_NEEDS_CHECK
    assert "単位" in lines[0].extra["要確認の理由"][0]


def test_without_machine_rows_the_check_says_it_did_not_run() -> None:
    lines = app.ai_reading_lines(air.parse_ai_reading(_answer(_row("棚 新設", "室A", 6))))
    summary = app.check_with_machine(lines, None, "止めた")

    assert summary == {"動かした": False, "理由": "止めた"}
    assert lines[0].certainty == app.CERTAINTY_CANDIDATE


def test_the_ai_result_confirms_nothing_even_with_many_rows() -> None:
    rows = [_row(f"工事{i}", "室A", i) for i in range(1, 30)]
    result = app.run_ai_reading(air.parse_ai_reading(_answer(*rows)), case_id="K49")

    assert result.auto_confirmed_total == 0
    assert len(result.lines) == 29
    payload = result.as_dict()
    assert payload["自動確定"]["合計"] == 0
    assert payload["読み手"] == app.READER_AI_FILE
    assert payload["段ごとの件数"]["合計"][app.STAGE_ASSEMBLE] == 29


# ---------------------------------------------------------------------------
# 5. 本番の入口
# ---------------------------------------------------------------------------


def test_the_default_reader_is_the_ai_side(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(_answer(_row("棚 新設", "洋室1", None)), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.json"

    code = app.main([str(pdf), "--case-id", "K49", "--out", str(out), "--no-ledger",
                     "--ai-reading", str(answer), "--no-machine-check"])

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["読み手"] == app.READER_AI_FILE
    assert [(r["工事項目"], r["数量"], r["道"]) for r in payload["工事項目"]] == [("棚 新設", None, app.PATH_AI_READING)]
    assert payload["自動確定"]["合計"] == 0
    assert payload["機械の検算"]["動かした"] is False


def test_the_default_without_an_answer_does_not_stop_and_says_why(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    out = tmp_path / "out.json"

    code = app.main([str(pdf), "--case-id", "K49", "--out", str(out), "--no-ledger", "--no-machine-check"])

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["工事項目"] == []
    assert payload["AI の答案"]["状態"] == air.STATUS_NOT_READ
    assert any("読んでいない" in gap for gap in payload["足りないもの"])


def test_the_machine_reads_only_as_a_check_on_the_ai_side(tmp_path: Path) -> None:
    """合成の図面で機械が「可動棚6枚/洋室1 = 6」を読む。AI が 5 と書けば要確認になる。"""
    pdf = _full_pdf(tmp_path / "full.pdf")
    reading = air.parse_ai_reading(
        _answer(_row("可動棚6枚", "洋室1", 5), _row("床見切新設", "洋室1", None, "m")), reader="合成"
    )
    machine = app.run(pdf, case_id="K49", answers_path=tmp_path / "a.json", build_ledger_stage=False)
    machine_rows = [line.as_answer_row(i) for i, line in enumerate(machine.lines, 1)]
    result = app.run_ai_reading(
        reading, case_id="K49", machine_rows=machine_rows, machine_auto_confirmed=machine.auto_confirmed
    )

    assert {line.path for line in result.lines} == {app.PATH_AI_READING}  # 機械は行を作らない
    shelf, edge = result.lines
    assert shelf.quantity == 5
    assert shelf.certainty == app.CERTAINTY_NEEDS_CHECK
    assert edge.quantity is None
    assert result.auto_confirmed_total == 0
    assert result.machine_check["機械の行"] == len(machine.lines)
    assert result.checks["機械の検算と食い違う行"]


def test_machine_output_file_can_be_reused_for_the_check(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    machine_out = tmp_path / "machine.json"
    machine_out.write_text(
        json.dumps({"工事項目": [_machine(1, "棚 新設", "洋室1", 2)], "自動確定": {"入口の判定で確定": 0, "合計": 0}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(_answer(_row("棚 新設", "洋室1", 3)), ensure_ascii=False), encoding="utf-8")
    result = app.run_production(
        pdf, case_id="K49", answers_path=tmp_path / "a.json", ai_reading=answer, machine_output=machine_out
    )

    assert result.lines[0].certainty == app.CERTAINTY_NEEDS_CHECK
    assert result.auto_confirmed == {"AI の読みで確定": 0, "機械の検算の側: 入口の判定で確定": 0}


def test_the_machine_reader_keeps_the_old_shape(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    result = app.run_production(
        pdf, case_id="K49", answers_path=tmp_path / "a.json", reader=app.READER_MACHINE,
        build_ledger_stage=False,
    )

    assert result.reader == app.READER_MACHINE
    assert app.PATH_AI_READING not in {line.path for line in result.lines}
    assert result.lines


# ---------------------------------------------------------------------------
# AI をその場で呼ぶ(偽の相手だけ)
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, message) -> None:
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def get_final_message(self):
        return self._message


class _FakeClient:
    """合成の答えを返す偽の相手。**鍵もネットワークも使わない。**"""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict] = []
        message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason
        )
        self.messages = SimpleNamespace(stream=self._stream)
        self._message = message

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._message)


def test_without_a_key_the_api_reader_does_not_stop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pdf = _full_pdf(tmp_path / "full.pdf")

    result = app.run_production(
        pdf, case_id="K49", answers_path=tmp_path / "a.json", reader=app.READER_AI_API, machine_check=False
    )

    assert result.lines == []
    assert result.ai_reading["状態"] == air.STATUS_NOT_READ
    assert "ANTHROPIC_API_KEY" in result.ai_reading["理由"]
    assert result.auto_confirmed_total == 0


def test_the_api_reader_sends_every_page_and_reads_the_answer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_READING_MODEL", "合成のモデル")
    pdf = _full_pdf(tmp_path / "full.pdf")
    client = _FakeClient(
        "前置き " + json.dumps(_answer(_row("棚 新設", "洋室1", 6), _row("床 張替", "洋室1", None, "㎡")), ensure_ascii=False)
    )

    reading = air.AnthropicDrawingReader(client=client).read(pdf)

    assert reading.status == air.STATUS_READ
    assert [r.quantity for r in reading.rows] == [6, None]
    assert reading.reader == "anthropic:合成のモデル"
    call = client.calls[0]
    assert call["model"] == "合成のモデル"
    assert call["system"] == air.load_instructions()
    images = [b for b in call["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 3  # 合成の図面は 3 ページ
    assert images[0]["source"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize(
    "text, stop, why",
    [("読めませんでした", "end_turn", "形が違う"), ("{}", "refusal", "断った"), ('{"行": 3}', "end_turn", "形が違う")],
)
def test_a_bad_api_answer_is_not_read_with_a_reason(tmp_path: Path, text, stop, why) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    reading = air.read_with_api(pdf, client=_FakeClient(text, stop))

    assert reading.status == air.STATUS_NOT_READ
    assert reading.rows == []
    assert why in reading.reason


def test_a_failing_call_is_not_read_with_a_reason(tmp_path: Path) -> None:
    class Broken:
        messages = SimpleNamespace(stream=lambda **kw: (_ for _ in ()).throw(ConnectionError("合成の失敗")))

    reading = air.read_with_api(_full_pdf(tmp_path / "full.pdf"), client=Broken())

    assert reading.status == air.STATUS_NOT_READ
    assert "合成の失敗" in reading.reason


def test_the_default_model_follows_the_expert_reader() -> None:
    from knowledge.expertise.reader import DEFAULT_MODEL

    assert air.DEFAULT_MODEL == DEFAULT_MODEL


def test_the_instructions_live_in_a_file_and_say_what_not_to_do() -> None:
    text = air.load_instructions()

    assert air.INSTRUCTIONS_PATH.exists()
    for must in ("数を作らない", "null", "記載が無い", "捨てず", "二重に数えない"):
        assert must in text
