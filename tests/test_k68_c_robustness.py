"""K-68 C 周 1: 頑健性の穴(回転・拒否と落ち・資料が欠けた版)。**合成の図面と偽の AI で通す。実図面は使わない。**"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from draft import scorecard
from draft.ai import (MISSING_DROPPED, MISSING_REFUSED, MISSING_WAITING, AIRequest, ApiCaller, FolderCaller)
from draft.pages import WIDTH_PX, hide_pages, positioned_words, words_in_image
from draft.run import run, source_presence
from tests.test_draft_pipeline import FakeClient, FakeStream, _pdf, machine_output  # noqa: F401


# --- (a) 回転 --------------------------------------------------------------------------


def _rotated_pdf(path: Path, rotation: int) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((80, 120), "ROTATEDWORD", fontsize=20)
    page.insert_text((600, 500), "CORNER", fontsize=20)
    page.set_rotation(rotation)
    doc.save(path)
    return path


def _ink(page: pymupdf.Page) -> np.ndarray:
    zoom = WIDTH_PX / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).mean(axis=2) < 128


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_word_boxes_land_on_ink_in_rotated_pages(tmp_path, rotation):
    pdf = _rotated_pdf(tmp_path / f"r{rotation}.pdf", rotation)
    with pymupdf.open(pdf) as doc:
        page = doc.load_page(0)
        ink = _ink(page)
        words = words_in_image(page)
    assert {w for w, _ in words} == {"ROTATEDWORD", "CORNER"}
    for _, (x0, y0, x1, y1) in words:
        assert x1 > x0 and y1 > y0
        assert ink[int(y0):int(y1) + 1, int(x0):int(x1) + 1].any(), (rotation, x0, y0, x1, y1)
    # positioned_words も工事チェック表の語も同じ座標
    pw = positioned_words(pdf, 1)
    assert [p[0] for p in pw] == [w for w, _ in words]
    from draft.work_checklist import _page_words

    assert [b for _, b in _page_words(pdf, [1])[1]] == [[round(v, 1) for v in b] for _, b in words]


def test_unrotated_page_coordinates_did_not_change(tmp_path):
    pdf = _rotated_pdf(tmp_path / "r0.pdf", 0)
    with pymupdf.open(pdf) as doc:
        page = doc.load_page(0)
        zoom = WIDTH_PX / page.rect.width
        old = [[w[4], round(w[0] * zoom), round(w[1] * zoom), round(w[2] * zoom), round(w[3] * zoom)]
               for w in page.get_text("words")]
    assert positioned_words(pdf, 1) == old


# --- 未取得の読みを 0 にしない ---------------------------------------------------------------


def test_unobtained_reading_is_grey_not_zero(tmp_path, monkeypatch, machine_output):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)]) == 0
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["読む"]["読みが未取得のページ"] == [1, 2]
    for n in ("1", "2"):
        assert r["読む"]["ページ"][n]["落ちた率"] is None
        assert r["読む"]["ページ"][n]["読み落としの可能性が高い"] is True
    rt = r["読了率"]
    assert rt["読了率"] is None
    assert rt["読みが未取得のページ"] == [1, 2]
    assert all(p["読了率"] is None and p["信号"] == "灰" for p in rt["ページごと"])
    assert rt["案件全体の警告"]  # 灰にしても、読めていない案件の警告は消えない
    assert r["まとめ"]["読了率"] is None and r["まとめ"]["読みが取れたページ"] == "0 / 2"
    row = next(x for x in r["採点表"]["行"] if x["名前"] == "読みが取れたページ")
    assert row["いまの値"] == 0.0 and row["合否"] == "未達"
    assert r["AI を呼んだ記録"]["まとめ"]["未取得の内訳"][MISSING_WAITING] == \
        r["AI を呼んだ記録"]["まとめ"]["答えの出どころ"]["未取得"]


def test_obtained_reading_keeps_numbers(tmp_path, machine_output):  # noqa: F811
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["読む"]["読みが未取得のページ"] == []
    assert r["読了率"]["読了率"] is not None
    row = next(x for x in r["採点表"]["行"] if x["名前"] == "読みが取れたページ")
    assert row["いまの値"] == 1.0


# --- (b) 拒否と落ち ------------------------------------------------------------------------


class MixedClient:
    """鍵ごとに 拒否 / 例外 / 形の違う答え / 正しい答え を返す偽の AI。"""

    def __init__(self, plan: dict[str, str]) -> None:
        self.plan = plan
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, *, system: str, messages, **kwargs):
        what = self.plan[system]
        if what == "refusal":
            return _Final(SimpleNamespace(stop_reason="refusal", content=[]))
        if what == "error":
            raise ConnectionError("返ってこない")
        if what == "bad":
            return FakeStream("JSON ではない答え")
        return FakeStream('{"ok": 1}')


class _Final(FakeStream):
    def __init__(self, msg) -> None:
        self.msg = msg

    def get_final_message(self):
        return self.msg


def _req(name: str) -> AIRequest:
    return AIRequest(stage="通読", key=name, instructions=name)


def test_refusal_and_drop_are_counted_apart(tmp_path):
    plan = {"拒否": "refusal", "例外": "error", "形": "bad", "良い": "ok"}
    caller = ApiCaller(tmp_path, MixedClient(plan), "claude-opus-5-5")
    answers = [caller.call(_req(k)) for k in plan]
    assert [a.missing_kind for a in answers] == [MISSING_REFUSED, MISSING_DROPPED, MISSING_DROPPED, ""]
    summary = caller.summary("claude-opus-5-5")
    assert summary["未取得の内訳"] == {MISSING_REFUSED: 1, MISSING_DROPPED: 2, MISSING_WAITING: 0}
    assert summary["答えの出どころ"]["未取得"] == 3
    recs = {r.key: r.as_dict() for r in caller.records}
    assert recs["拒否"]["未取得の内訳"] == MISSING_REFUSED and "未取得の内訳" not in recs["良い"]


def test_no_key_counts_waiting_only(tmp_path):
    caller = FolderCaller(tmp_path)
    for k in ("a", "b"):
        caller.call(_req(k))
    assert caller.summary("claude-opus-5-5")["未取得の内訳"] == {MISSING_REFUSED: 0, MISSING_DROPPED: 0,
                                                              MISSING_WAITING: 2}


def test_placed_refusal_mark_counts_as_refusal(tmp_path):
    caller = FolderCaller(tmp_path)
    req = _req("断られた")
    fp = caller.fingerprint(req)
    (tmp_path / "答え").mkdir()
    (tmp_path / "答え" / f"{fp}.拒否.json").write_text(json.dumps({"理由": "分類器"}, ensure_ascii=False),
                                                     encoding="utf-8")
    ans = caller.call(req)
    assert ans.payload is None and ans.missing_kind == MISSING_REFUSED and "分類器" in ans.note
    # 断られた問いは「待っている問い」に書き出さない(答えを待っても来ない)
    assert not (tmp_path / "待っている問い").exists()


class BatchMixed:
    def __init__(self, kinds: dict[str, str]) -> None:
        self.kinds = kinds
        self.messages = SimpleNamespace(batches=SimpleNamespace(create=self.create, retrieve=None, results=self.results))

    def create(self, *, requests):
        self._out = []
        for r in requests:
            what = self.kinds[r["params"]["system"]]
            if what in ("errored", "expired"):
                res = SimpleNamespace(type=what)
            elif what == "refusal":
                res = SimpleNamespace(type="succeeded", message=SimpleNamespace(stop_reason="refusal", content=[]))
            else:
                res = SimpleNamespace(type="succeeded", message=SimpleNamespace(
                    stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok": 1}')]))
            self._out.append(SimpleNamespace(custom_id=r["custom_id"], result=res))
        return SimpleNamespace(id="b", processing_status="ended")

    def results(self, bid):
        return iter(self._out)


def test_batch_errored_is_drop_and_refusal_is_refusal(tmp_path):
    kinds = {"e": "errored", "x": "expired", "r": "refusal", "o": "ok"}
    caller = ApiCaller(tmp_path, BatchMixed(kinds), "claude-opus-5-5", batch=True, poll_seconds=0)
    caller.map([_req(k) for k in kinds], parallel=4)
    assert caller.summary("claude-opus-5-5")["未取得の内訳"] == {MISSING_REFUSED: 1, MISSING_DROPPED: 2,
                                                              MISSING_WAITING: 0}


def test_pipeline_with_refusals_and_drops_does_not_stop(tmp_path, machine_output):  # noqa: F811
    """整理を断られ、通読が返ってこなくても最後まで通り、内訳が数えられる。"""

    class Client(FakeClient):
        def stream(self, *, system: str, messages, **kwargs):
            if "読む前の整理" in system:
                self.calls.append("整理")
                return _Final(SimpleNamespace(stop_reason="refusal", content=[]))
            if "図面一式に、何が書いてあるか" in system:
                self.calls.append("通読")
                raise TimeoutError("返ってこない")
            return super().stream(system=system, messages=messages, **kwargs)

    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=Client()) == 0
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["段の中の例外"] == []
    kinds = r["AI を呼んだ記録"]["まとめ"]["未取得の内訳"]
    assert kinds[MISSING_REFUSED] == 1 and kinds[MISSING_DROPPED] >= 1 and kinds[MISSING_WAITING] == 0
    assert r["機械の検算"]["自動確定"] == 0


# --- (c) 資料の有無 ------------------------------------------------------------------------


def _org(kinds: dict[int, str], source: str = "AI") -> dict:
    return {"出どころ": source, "ページ": {n: {"種類": k} for n, k in kinds.items()}, "読む順": list(kinds)}


def test_source_presence_says_unknown_rather_than_absent():
    ok = {"原本": "原本あり", "原本のページ": [1]}
    none = {"原本": "原本なし", "原本のページ": []}
    s = source_presence(_org({1: "仕上表", 2: "仕様書"}), ok, {"行": [1], "形": "CSV"})
    assert s == {"仕上表": "あり", "仕様書": "あり", "原価表": "あり"}
    s = source_presence(_org({1: "平面図"}), none, None)
    assert s == {"仕上表": "なし", "仕様書": "なし", "原価表": "未取得"}
    s = source_presence(_org({1: "未取得"}, source="未取得"), none, None)
    assert s["仕上表"].startswith("未取得") and s["仕様書"].startswith("未取得")
    s = source_presence(_org({1: "仕上表"}), {"原本": "原本はあるが書き写しが未取得", "原本のページ": [1]}, None)
    assert s["仕上表"].startswith("未取得")
    assert source_presence(_org({1: "仕上表"}), ok, None, stage=1)["仕上表"].startswith("未取得")


def test_missing_sources_flow_to_checklist_and_summary(tmp_path, machine_output):  # noqa: F811
    """仕様書が無い・原価表だけがある・仕上表を隠した、を合成の図面で。0 にせず、なし/未取得と出る。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    csv_path = tmp_path / "原価表.csv"
    csv_path.write_text("工事項目,品番,単位,単価,数量\n床 フローリング張替,FL-1,m2,1000,\n", encoding="utf-8")
    out = tmp_path / "原価表あり"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--cost-table", str(csv_path)],
        client=FakeClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["まとめ"]["資料の有無"] == {"仕上表": "あり", "仕様書": "なし", "原価表": "あり"}
    assert "仕様書なし" in r["工事チェック表"]["足りない資料"]
    assert all(it["数量"] != 0 for it in r["理解"]["項目"])

    hidden = hide_pages(pdf, [1], tmp_path / "隠した.pdf")
    out2 = tmp_path / "仕上表なし"
    run([str(hidden), "--out", str(out2), "--machine-output", str(machine_output)], client=FakeClient())
    r2 = json.loads((out2 / "下書き.json").read_text(encoding="utf-8"))
    assert r2["まとめ"]["資料の有無"]["仕上表"] == "なし"
    assert r2["まとめ"]["資料の有無"]["原価表"] == "未取得"
    assert {"仕上表なし", "原価表未取得"} <= set(r2["工事チェック表"]["足りない資料"])
    assert r2["機械の検算"]["自動確定"] == 0


def test_scorecard_without_new_field_is_unchanged():
    card = scorecard.build(readthrough={"種類ごと(重なりなし)": {}})
    assert not any(row["名前"] == "読みが取れたページ" for row in card["行"])
