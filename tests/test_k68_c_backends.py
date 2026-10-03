"""K-68 C 周 2: クラウドだけで通す・呼び出し口を段ごとに切り替える。**合成の図面と偽の AI だけ。**

ローカルの AI は使えない前提。既定は全段 cloud。local にできるのは合格の記録に載った段だけ。
"""

from __future__ import annotations

import json

import pytest

from draft import ai
from draft.ai import AI_STAGES, LocalNotPassed, load_local_passed, parse_backend_plan, resolve_backend_plan
from draft.run import run
from tests.test_draft_designs import V2Client, V3Client, _pdf_v3
from tests.test_draft_flags import FlagClient, legend  # noqa: F401  (fixture)
from tests.test_draft_pipeline import FakeClient, _pdf, machine_output  # noqa: F401  (fixture)


def _records(out) -> list[dict]:
    return json.loads((out / "下書き.json").read_text(encoding="utf-8"))["AI を呼んだ記録"]["1回ずつ"]


@pytest.fixture(autouse=True)
def _no_backend_env(monkeypatch):
    monkeypatch.delenv(ai.BACKEND_ENV, raising=False)


def test_repository_record_has_no_passed_stage():
    """線 4: いまの合格の記録は 0 段(ローカルの AI は使えない前提)。"""
    assert load_local_passed() == {}
    assert set(resolve_backend_plan().values()) == {"cloud"}


def test_every_v1_and_flag_stage_runs_on_the_cloud(tmp_path, machine_output):  # noqa: F811
    """線 1: 設定なしで、V1 の全段と旗の AI の段が偽のクラウドの口で通る。記録の口はすべて cloud。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = FlagClient()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--mode", "精密",
                "--with-branch-questions", "--with-line-judge"], client=client)
    assert code == 0
    recs = _records(out)
    stages = {r["段"] for r in recs}
    assert {"整理", "通読", "読み直し", "理解", "仕上表の原本", "分かれ道", "線引き"} <= stages
    assert {r["呼び出し口"] for r in recs} == {"cloud"}
    assert all(r["答えの出どころ"] == "その場で呼んだ" for r in recs)
    result = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert result["機械の検算"]["自動確定"] == 0
    assert result["呼び出し口"]["local の段"] == [] and result["呼び出し口"]["合格の記録にある段"] == []
    assert result["AI を呼んだ記録"]["まとめ"]["呼び出し口ごと"] == {"cloud": len(recs), "local": 0}


@pytest.mark.parametrize("design", ["V2", "V3"])
def test_v2_v3_stages_run_on_the_cloud(tmp_path, machine_output, design):  # noqa: F811
    pdf = _pdf_v3(tmp_path / "図面.pdf") if design == "V3" else _pdf(tmp_path / "図面.pdf")
    client = V3Client(pdf) if design == "V3" else V2Client()
    out = tmp_path / "出力"
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", design],
               client=client) == 0
    recs = _records(out)
    assert recs and {r["呼び出し口"] for r in recs} == {"cloud"}


def test_local_for_a_stage_without_a_pass_is_refused_before_any_call(tmp_path, machine_output, capsys):  # noqa: F811
    """線 2: 合格の記録が無い段を local にしようとしたら、AI を 1 回も呼ばず何も書かずに拒む。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = FakeClient()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output),
                "--backend-per-stage", "通読=local"], client=client)
    assert code == 3
    assert client.calls == []
    assert not (out / "下書き.json").exists()
    assert "通読" in capsys.readouterr().err


def test_old_env_local_is_refused_too(tmp_path, machine_output, monkeypatch):  # noqa: F811
    monkeypatch.setenv(ai.BACKEND_ENV, "local")
    pdf = _pdf(tmp_path / "図面.pdf")
    client = FakeClient()
    assert run([str(pdf), "--out", str(tmp_path / "出力"), "--machine-output", str(machine_output)],
               client=client) == 3
    assert client.calls == []
    with pytest.raises(LocalNotPassed):
        resolve_backend_plan()


@pytest.mark.parametrize("text", ["読む=cloud", "通読=gpu", "通読", "通読=cloud,理解=どこか"])
def test_unknown_stage_or_backend_is_refused(tmp_path, machine_output, text):  # noqa: F811
    with pytest.raises(ValueError):
        parse_backend_plan(text)
    pdf = _pdf(tmp_path / "図面.pdf")
    client = FakeClient()
    assert run([str(pdf), "--out", str(tmp_path / "出力"), "--machine-output", str(machine_output),
                "--backend-per-stage", text], client=client) == 3
    assert client.calls == []


def test_explicit_cloud_is_the_same_as_default():
    assert resolve_backend_plan("通読=cloud,理解=cloud") == {stage: "cloud" for stage in AI_STAGES}


def test_passed_stage_can_go_local_and_never_reaches_the_cloud(tmp_path, machine_output, monkeypatch):  # noqa: F811
    """線 3: 試験の中だけで「通読は合格」の記録を渡すと、通読は local(クラウドに行かず待っている問い)、ほかは cloud。"""
    record = tmp_path / "local_passed.json"
    record.write_text(json.dumps({"合格した段": {"通読": {"日付": "試験", "線": "試験", "結果": "試験"}}},
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ai, "LOCAL_PASSED_PATH", record)
    assert resolve_backend_plan("通読=local")["通読"] == "local"
    with pytest.raises(LocalNotPassed):
        resolve_backend_plan("通読=local,理解=local")
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = FakeClient()
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output),
                "--backend-per-stage", "通読=local"], client=client) == 0
    assert "通読" not in client.calls and "整理" in client.calls
    recs = _records(out)
    for r in recs:
        if r["段"] == "通読":
            assert r["呼び出し口"] == "local" and r["答えの出どころ"] == "未取得"
            assert r["未取得の内訳"] == "待っている問い"
        else:
            assert r["呼び出し口"] == "cloud"
    pending = list((out / "AIの答え" / "待っている問い").glob("通読_*/指示.md"))
    assert pending
    result = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert result["呼び出し口"]["local の段"] == ["通読"]


def test_batch_mode_keeps_local_stages_out_of_the_batch(tmp_path, monkeypatch):
    record = tmp_path / "local_passed.json"
    record.write_text(json.dumps({"合格した段": {"通読": {"結果": "試験"}}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ai, "LOCAL_PASSED_PATH", record)

    class Batches:
        def __init__(self):
            self.sent = []

        def create(self, *, requests):
            self.sent.append(len(requests))
            raise AssertionError("local の段をまとめて送った")

    from types import SimpleNamespace

    b = Batches()
    caller = ai.make_caller(tmp_path / "答え", client=SimpleNamespace(messages=SimpleNamespace(batches=b)),
                            batch=True, stage_backends=resolve_backend_plan("通読=local"))
    answers = caller.map([ai.AIRequest(stage="通読", key=f"k{i}", instructions=f"i{i}") for i in range(3)], 3)
    assert b.sent == [] and all(a.missing_kind == "待っている問い" for a in answers)
