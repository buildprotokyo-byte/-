"""K-46: 「工事の行か」の線引きを AI に寄せる。**合成のデータだけを使う。**

守りたいこと

1. 線引きの役は `estimating/line_judge.py` の 1 か所。AI の判定(ファイル・その場で呼ぶ)と
   語の一覧(案 B)を差し替えられる。
2. **本番の既定は AI の判定。**判定が渡されていない行・判定が無い行は落とさずに要確認。
3. 「工事の行ではない」とされた行は見積の行から外すが、理由つきで残す(消さない)。
4. どの役が判定したかが行の根拠に残る。線引きは確かさを上げない。**自動確定は 0 件のまま。**
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
from estimating import line_judge as lj
from tests.test_k42_notes_hatch import _full_pdf, _run

ROWS = [
    lj.LineText(1, "可動棚6枚", "洋室1"),
    lj.LineText(2, "床見切新設", "洋室1"),
    lj.LineText(3, "壁 クロス 改修", "書斎"),
    lj.LineText(4, "取付位置確認", "書斎"),
    lj.LineText(5, "洋室1", ""),
]


# ---------------------------------------------------------------------------
# 1. 役
# ---------------------------------------------------------------------------


def test_word_list_judge_is_plan_b_with_kaishu() -> None:
    verdicts = lj.WordListJudge().judge(ROWS)

    assert [v.verdict for v in verdicts] == [
        lj.VERDICT_NOT_WORK,  # 工事の語が無い(案 B はここを落としていた)
        lj.VERDICT_WORK,
        lj.VERDICT_WORK,  # 「改修」は K-42(a) で足した語
        lj.VERDICT_WORK,  # 「取付」を含む(案 B は指示も通す)
        lj.VERDICT_NOT_WORK,
    ]
    assert all(v.judge == lj.JUDGE_WORDS for v in verdicts)
    assert "改修" in lj.WORK_WORDS


def test_ai_file_judge_looks_up_by_text_then_work_then_number() -> None:
    judge = lj.AIJudgmentFileJudge(
        [
            {"工事": "可動棚６枚", "場所": "洋室 1", "判定": "工事の行", "理由": "棚を取り付ける"},
            {"工事": "取付位置確認", "判定": "工事の行ではない", "理由": "指示"},
            {"番号": 5, "判定": "工事の行ではない", "理由": "室名だけ"},
        ]
    )
    verdicts = judge.judge(ROWS)

    assert [(v.verdict, v.judge) for v in verdicts] == [
        (lj.VERDICT_WORK, lj.JUDGE_AI),  # NFKC・空白を除いて引く
        (lj.VERDICT_NEEDS_CHECK, lj.JUDGE_NONE),  # 判定が無い → 落とさずに要確認
        (lj.VERDICT_NEEDS_CHECK, lj.JUDGE_NONE),
        (lj.VERDICT_NOT_WORK, lj.JUDGE_AI),
        (lj.VERDICT_NOT_WORK, lj.JUDGE_AI),
    ]
    assert verdicts[0].reason == "棚を取り付ける"
    assert verdicts[1].reason == lj.REASON_AI_GAVE_NOTHING


def test_no_ai_judgment_means_every_row_needs_check() -> None:
    verdicts = lj.make_line_judge().judge(ROWS)

    assert {v.verdict for v in verdicts} == {lj.VERDICT_NEEDS_CHECK}
    assert {v.judge for v in verdicts} == {lj.JUDGE_NONE}


def test_ai_file_judge_refuses_bad_or_conflicting_entries(tmp_path: Path) -> None:
    with pytest.raises(lj.LineJudgeError):
        lj.AIJudgmentFileJudge([{"番号": 1, "判定": "たぶん"}])
    with pytest.raises(lj.LineJudgeError):
        lj.AIJudgmentFileJudge(
            [
                {"工事": "床見切新設", "場所": "洋室1", "判定": "工事の行"},
                {"工事": "床見切 新設", "場所": "洋室1", "判定": "工事の行ではない"},
            ]
        )
    with pytest.raises(lj.LineJudgeError):
        lj.AIJudgmentFileJudge([{"判定": "工事の行"}])
    path = tmp_path / "j.json"
    path.write_text(json.dumps({"判定": "工事の行"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(lj.LineJudgeError):
        lj.AIJudgmentFileJudge.from_path(path)
    with pytest.raises(lj.LineJudgeError):
        lj.make_line_judge("だれか")


class _FakeClient:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.sent: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.sent.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answer)])


def test_anthropic_judge_reads_the_answer_and_keeps_missing_rows() -> None:
    client = _FakeClient(
        '答え: [{"番号": 1, "判定": "工事の行", "理由": "棚"},'
        ' {"番号": 5, "判定": "工事の行ではない", "理由": "室名"},'
        ' {"番号": 4, "判定": "わからない"}]'
    )
    verdicts = lj.AnthropicLineJudge(model="m", client=client).judge(ROWS)

    assert [v.verdict for v in verdicts] == [
        lj.VERDICT_WORK,
        lj.VERDICT_NEEDS_CHECK,
        lj.VERDICT_NEEDS_CHECK,
        lj.VERDICT_NEEDS_CHECK,  # 形の違う答えは使わない
        lj.VERDICT_NOT_WORK,
    ]
    sent = json.loads(client.sent[0]["messages"][0]["content"])
    assert sent[0] == {"番号": 1, "工事": "可動棚6枚", "場所": "洋室1"}
    assert set(sent[0]) == {"番号", "工事", "場所"}  # 数量は渡さない


def test_ai_api_without_a_key_falls_back_to_needs_check(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(lj.LineJudgeUnavailable):
        lj.AnthropicLineJudge()
    verdicts = lj.make_line_judge("ai-api").judge(ROWS)

    assert {v.verdict for v in verdicts} == {lj.VERDICT_NEEDS_CHECK}
    assert all("AI を呼べなかった" in v.reason for v in verdicts)


# ---------------------------------------------------------------------------
# 2. 本番の経路(app.py)
# ---------------------------------------------------------------------------


def test_app_default_drops_nothing_and_marks_every_row_needs_check(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    words = _run(pdf, tmp_path, line_judge=lj.WordListJudge())
    result = _run(pdf, tmp_path)

    assert result.auto_confirmed_total == 0
    assert result.not_work_lines == []
    assert len(result.lines) == len(words.lines) + len(words.not_work_lines)
    for line in result.lines:
        assert line.certainty == app.CERTAINTY_NEEDS_CHECK
        assert line.evidence[-1] == {
            "線引き": lj.JUDGE_NONE,
            "判定": lj.VERDICT_NEEDS_CHECK,
            "理由": lj.REASON_NO_AI_JUDGMENT,
        }
    payload = result.as_dict()
    assert payload["線引き"]["判定ごと"] == {lj.VERDICT_NEEDS_CHECK: len(result.lines)}
    assert payload["自動確定"]["合計"] == 0


def test_app_uses_the_ai_judgment_and_keeps_rows_it_rejects(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    judgments = tmp_path / "ai.json"
    judgments.write_text(
        json.dumps(
            [
                {"工事": "可動棚6枚", "場所": "洋室1", "判定": "工事の行", "理由": "棚を取り付ける"},
                {"工事": "床見切新設", "判定": "工事の行", "理由": "新設"},
                {"工事": "照明移設", "判定": "工事の行ではない", "理由": "合成の囮"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(pdf, tmp_path, line_judgments=judgments)

    assert result.auto_confirmed_total == 0
    shelf = next(line for line in result.lines if line.work_item == "可動棚6枚")
    assert shelf.quantity == 6.0  # 案 B が落としていた、数の刷られた注記が通る
    assert shelf.certainty == app.CERTAINTY_CANDIDATE
    assert shelf.as_answer_row(1)["線引き"] == {
        "線引き": lj.JUDGE_AI, "判定": lj.VERDICT_WORK, "理由": "棚を取り付ける"
    }
    edges = [line for line in result.lines if line.work_item == "床見切新設"]
    assert len(edges) == 2 and all(e.certainty == app.CERTAINTY_CANDIDATE for e in edges)

    # 工事の行ではない → 見積の行から外れるが、理由つきで残る
    assert not any(line.work_item == "照明移設" for line in result.lines)
    rejected = result.as_dict()["工事の行ではないと判定した行"]
    assert [row["工事項目"] for row in rejected] == ["照明移設"]
    assert rejected[0]["線引き"]["理由"] == "合成の囮"

    # 判定の無い行は要確認のまま残る。推し量った行は AI が何と言っても要確認
    others = [line for line in result.lines if line.work_item not in ("可動棚6枚", "床見切新設")]
    assert others and all(line.certainty == app.CERTAINTY_NEEDS_CHECK for line in others)
    assert result.stage_totals()[app.STAGE_ASSEMBLE] == len(result.lines)
    assert result.stages[app.PATH_PLAN_NOTES][app.STAGE_ASSEMBLE] == 4


def test_app_word_list_moves_the_shelf_note_aside_not_away(tmp_path: Path) -> None:
    result = _run(_full_pdf(tmp_path / "full.pdf"), tmp_path, line_judge=lj.WordListJudge())

    assert not any(line.work_item == "可動棚6枚" for line in result.lines)
    rejected = {line.work_item: line for line in result.not_work_lines}
    assert rejected["可動棚6枚"].quantity == 6.0
    assert rejected["可動棚6枚"].evidence[-1]["線引き"] == lj.JUDGE_WORDS
    assert result.auto_confirmed_total == 0


def test_cli_takes_the_ai_judgment_file(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    judgments = tmp_path / "ai.json"
    judgments.write_text(
        json.dumps([{"工事": "照明移設", "判定": "工事の行ではない", "理由": "合成"}], ensure_ascii=False),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    code = app.main(
        [str(pdf), "--case-id", "K46", "--out", str(out), "--no-ledger", "--reader", "machine",
         "--line-judgments", str(judgments)]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["線引き"]["判定した役ごと"][lj.JUDGE_AI] == 1
    assert [row["工事項目"] for row in payload["工事の行ではないと判定した行"]] == ["照明移設"]

    code = app.main(
        [str(pdf), "--case-id", "K46", "--out", str(out), "--no-ledger", "--reader", "machine",
         "--line-judge", "words"]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["線引き"]["役"] == lj.JUDGE_WORDS
