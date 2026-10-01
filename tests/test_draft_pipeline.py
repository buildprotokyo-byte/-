"""K-61 一本道(draft)の試験。**合成の図面と偽の AI で通す。実図面は使わない。**"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from draft import stages
from draft.ai import FolderCaller, AIRequest, make_caller
from draft.pages import hide_pages
from draft.run import run


def _pdf(path: Path) -> Path:
    doc = pymupdf.open()
    p1 = doc.new_page(width=842, height=595)
    p1.insert_text((50, 50), "Finish table 内部仕上表", fontname="japan")
    p1.draw_rect(pymupdf.Rect(40, 80, 400, 300))
    p1.draw_line((40, 150), (400, 150))
    p2 = doc.new_page(width=842, height=595)
    p2.insert_text((100, 100), "洋室1", fontname="japan")
    for x in range(60, 700, 40):
        p2.draw_line((x, 200), (x + 30, 200))
    p2.draw_rect(pymupdf.Rect(60, 220, 500, 500))
    doc.new_page(width=842, height=595)  # 白紙
    doc.save(path)
    return path


def _page_answer(n: int, box: list[float]) -> dict:
    return {"ページ": n, "描かれているもの": "試験", "要素": [
        {"id": f"p{n}-001", "種類": "文字", "内容": "洋室1", "位置": box, "確かさ": "読めた"},
        {"id": f"p{n}-002", "種類": "記号", "内容": "円の中に×", "位置": [400, 400, 420, 420], "確かさ": "読めた"},
        {"id": f"p{n}-003", "種類": "記号", "内容": "円の中に×", "位置": [450, 400, 470, 420], "確かさ": "読めた"},
    ], "分からなかったもの": [{"位置": [10, 10, 30, 30], "理由": "かすれ"}]}


def _understood(n: int) -> dict:
    return {"ページ": n, "項目": [
        {"要素": [f"p{n}-001"], "読み取った値": "洋室1 フローリング", "何": "床の張替え", "部位": "床", "場所": "洋室1",
         "区分": "張替", "工事": "床 フローリング張替", "科目": "内装", "品番": "FL-1", "数量": None, "単位": "m2",
         "式": "", "状態": "観測", "確度": "中", "根拠の種類": "図面から読んだ", "理由": "", "選択肢": []},
        {"要素": [f"p{n}-002", f"p{n}-003"], "読み取った値": "円の中に×", "何": "照明器具", "部位": "電気",
         "場所": "洋室1", "区分": "交換", "工事": "照明器具 交換", "科目": "電気設備", "品番": "", "数量": 3,
         "単位": "台", "式": "記号 3 個を数えた", "状態": "推論", "確度": "低", "根拠の種類": "凡例から",
         "理由": "6ページの凡例", "選択肢": []},
        {"要素": [f"p{n}-001"], "読み取った値": "6", "何": "棚板", "部位": "造作", "場所": "未確定", "区分": "新設",
         "工事": "可動棚", "科目": "木工事", "品番": "", "数量": "6", "単位": "枚", "式": "", "状態": "問い",
         "確度": "低", "根拠の種類": "仮に置いた", "理由": "", "選択肢": ["洋室1", "廊下"]},
    ], "決められなかった要素": []}


class FakeStream:
    def __init__(self, text: str) -> None:
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.text)])


class FakeClient:
    """段ごとに決まった答えを返す偽の AI。何回呼ばれたかを数える。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, *, system: str, messages, **kwargs):
        if "読む前の整理" in system:
            stage, body = "整理", {"ページ": [{"ページ": 1, "種類": "仕上表"}, {"ページ": 2, "種類": "平面図"},
                                             {"ページ": 3, "種類": "白紙"}], "読む順": [2, 1, 3]}
        elif "1 ページだけ" in system:
            n = 2 if "2 ページ" in system else 1
            stage, body = "読み直し", {"ページ": [_page_answer(n, [90, 90, 300, 520])]}
        elif "図面一式に、何が書いてあるか" in system:
            stage, body = "通読", {"ページ": [_page_answer(1, [5, 5, 10, 10]), _page_answer(2, [5, 5, 10, 10])]}
        elif "仕上表です" in system:
            stage, body = "原本", {"行": [{"室": "洋室1", "部位": "床", "仕上": "タイルカーペット", "位置": [50, 50, 90, 70]},
                                         {"室": "洋室2", "部位": "壁", "仕上": "クロス"}], "読めなかった所": []}
        else:
            n = 2 if "2 ページ" in system else 1
            stage, body = "理解", _understood(n)
        self.calls.append(stage)
        return FakeStream(json.dumps(body, ensure_ascii=False))


@pytest.fixture()
def machine_output(tmp_path: Path) -> Path:
    p = tmp_path / "機械の出力.json"
    p.write_text(json.dumps({"工事項目": [], "自動確定": {"合計": 0}}), encoding="utf-8")
    return p


def test_no_key_runs_to_the_end_and_writes_pending(tmp_path, monkeypatch, machine_output):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)])
    assert code == 0
    result = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert (out / "確認画面.html").exists()
    stops = {s["段"] for s in result["止まった所"]}
    assert {"整理", "読む"} <= stops
    pending = list((out / "AIの答え" / "待っている問い").glob("*/指示.md"))
    assert {p.parent.name.split("_")[0] for p in pending} >= {"整理", "通読"}
    # 白紙のページは AI に渡さない
    text = next(p for p in pending if p.parent.name.startswith("通読")).read_text(encoding="utf-8")
    assert "p3.png" not in text and "p2.png" in text
    assert result["まとめ"]["原価表"] == "未取得"
    assert result["理解"]["項目"] == []


def test_full_pass_with_fake_ai(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = FakeClient()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--mode", "精密"], client=client)
    assert code == 0
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["機械の検算"]["自動確定"] == 0
    items = r["理解"]["項目"]
    # 数量: null は null のまま、文字の "6" は数に直さず未取得にして理由を書く
    shelf = [it for it in items if it["工事"] == "可動棚"]
    assert shelf and all(it["数量"] is None for it in shelf)
    assert any("数ではなかった" in n for n in shelf[0]["検算"])
    # 記号を 2 個しか挙げていないのに数量 3 → 機械の検算が食い違いを書く
    lamp = next(it for it in items if it["工事"] == "照明器具 交換")
    assert any("食い違う" in n for n in lamp["検算"])
    # 仕上表: 原本あり、ひな型は仕上表以外のページだけから作る
    f = r["仕上表"]
    assert f["原本"] == "原本あり" and f["原本のページ"] == [1]
    statuses = {(c["室"], c["部位"]): c["照らし合わせ"] for c in f["照らし合わせ"]}
    assert statuses[("洋室1", "床")] == "違う"
    assert statuses[("洋室2", "壁")] == "原本のみ"
    assert all(rr["資料の種類"] != "仕上表" for t in f["ひな型"] for rr in t["根拠"])
    # 質問: 推奨なし・上限どおり・金額の順ではないと書く
    q = r["質問"]
    assert "原価表 未取得" in q["並べ方"]
    assert len(q["段階ごと"]["概算"]) <= 3 and len(q["段階ごと"]["精密"]) <= 10
    assert all("推奨" not in o for qq in q["段階ごと"]["精密"] for o in qq["選択肢"])
    # 組み立て: 2 ページの同じもの(同じ工事・場所・品番・区分)は 1 行、数量の違いは選ばず未取得
    rows = r["組み立て"]["内訳の行"]
    lamp_rows = [x for x in rows if x["工事項目"] == "照明器具 交換"]
    assert len(lamp_rows) == 1
    # 材料表: 数量が未取得のものは合計に入れない
    mat = next(m for m in r["組み立て"]["材料表"] if m["品番"] == "FL-1")
    assert mat["数量の合計(分かった分)"] == "未取得" and mat["未取得の件数"] == 1
    # 時間: 歩掛が無ければ未入力(0 にしない)
    assert all(t["人日"] == "未入力" for t in r["組み立て"]["時間"])
    modes = r["組み立て"]["段階ごとの出力"]
    assert modes["精密"]["検算を全部通ったか"] is False
    # 読み直し: 通読で落ちが多かったページを読み直した
    assert r["読む"]["読み直したページ"]
    assert "読み直し" in client.calls
    # 白紙のページは読まない
    assert "3" not in {str(k) for k in r["読む"]["ページ"]}
    html = (out / "確認画面.html").read_text(encoding="utf-8")
    assert "積算の下書き" in html and "data:image/jpeg;base64" in html


def test_second_pass_reuses_stored_answers(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    client = FakeClient()
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=client)
    assert client.calls == []
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["AI を呼んだ記録"]["まとめ"]["答えの出どころ"]["置かれた答え"] == r["AI を呼んだ記録"]["まとめ"]["呼んだ回数"]


def test_hidden_finish_schedule_gives_template_only(tmp_path, machine_output):
    pdf = hide_pages(_pdf(tmp_path / "図面.pdf"), [1], tmp_path / "隠した.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["仕上表"]["原本"] == "原本なし"
    assert all(c["照らし合わせ"] == "原本なし" for c in r["仕上表"]["照らし合わせ"])
    assert r["仕上表"]["ひな型"]


def test_answers_round_trip(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    q = next(x for x in r["質問"]["段階ごと"]["精密"] if x["種類"] == "原本との違い")
    answers = tmp_path / "答え.json"
    answers.write_text(json.dumps({q["鍵"]: q["選択肢"][0], "無い鍵": "x"}, ensure_ascii=False), encoding="utf-8")
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--answers", str(answers)],
        client=FakeClient())
    r2 = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r2["答えの往復"]["戻せた答え"] == 1
    assert r2["答えの往復"]["原本との違い(後)"] == r2["答えの往復"]["原本との違い(前)"] - 1
    assert q["鍵"] not in {x["鍵"] for x in r2["質問"]["段階ごと"]["精密"]}
    assert r2["質問"]["受け取れなかった答え"] == [{"鍵": "無い鍵", "答え": "x"}]


def test_fingerprint_changes_with_image(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    from PIL import Image

    Image.new("RGB", (10, 10), "white").save(a)
    Image.new("RGB", (10, 10), "black").save(b)
    r1 = AIRequest("読み直し", "p1", "指示", [a])
    r2 = AIRequest("読み直し", "p1", "指示", [b])
    assert r1.fingerprint() != r2.fingerprint()


def test_no_key_gives_folder_caller(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert isinstance(make_caller(tmp_path), FolderCaller)


def test_quantity_never_becomes_zero():
    by_id = {"p1-001": {"id": "p1-001", "種類": "文字", "位置": [0, 0, 1, 1]}}
    for raw in (None, "", "6", "不明", True):
        item = stages.check_item({"要素": ["p1-001"], "数量": raw, "状態": "観測", "確度": "高",
                                  "根拠の種類": "図面から読んだ"}, 1, by_id, 1)
        assert item["数量"] is None


def test_batched_full_read_keeps_only_its_own_pages(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = FakeClient()
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--pass1-batch", "1"], client=client)
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert client.calls.count("通読") == 2
    assert sorted(int(k) for k in r["読む"]["読み"]) == [1, 2]


# ---------------------------------------------------------------------------
# K-62
# ---------------------------------------------------------------------------


def _item(i: int, page: int, room: str, part: str, work: str) -> dict:
    return {"id": f"u{i}", "ページ": page, "場所": room, "部位": part, "工事": work, "品番": "", "確度": "中",
            "区分": "張替", "数量": None}


def test_room_name_variants_make_one_question():
    """「洋室1」と「洋室(1)」は同じ室。別々に問わない(K-61 の 3 回で 2 回ずつ問うていた)。"""
    items = [_item(1, 7, "洋室1", "壁", "クロス貼"), _item(2, 8, "洋室(1)", "壁", "クロス張替"),
             _item(3, 8, "ウォークインクローゼット", "床", "フローリング"), _item(4, 7, "WIC", "床", "CF")]
    original = [{"ページ": 3, "室": "洋室1", "部位": "壁", "仕上": "ビニルクロス", "位置": None},
                {"ページ": 3, "室": "WIC", "部位": "床", "仕上": "塩ビタイル", "位置": None}]
    template = stages.template_rows(items, {3}, {}, [r["室"] for r in original])
    keys = [(stages.room_key(t["室"]), t["部位"]) for t in template]
    assert len(keys) == len(set(keys))
    finish = {"照らし合わせ": stages.compare(template, original)}
    qs = stages.question_candidates({"項目": []}, finish, {"読み": {}}, None)
    assert len([q for q in qs if q["鍵"] == "仕上:洋室1:壁"]) == 1
    assert len([q for q in qs if q["鍵"] == "仕上:WIC:床"]) == 1
    merged = stages.room_variants([it["場所"] for it in items] + [r["室"] for r in original])
    assert {"揃えた名前": "洋室1", "元の書き方": ["洋室(1)", "洋室1"]} in merged


def test_room_key_keeps_different_rooms_apart():
    assert stages.room_key("洋室1") != stages.room_key("洋室2")
    assert stages.room_key("リビングダイニング") != stages.room_key("LDK")
    assert stages.room_key("トイレ・洗面室") not in (stages.room_key("トイレ"), stages.room_key("洗面室"))
    assert stages.room_key("キッチン ダイニング リビング") == stages.room_key("LDK")
    assert stages.room_key("洋室(1)") == stages.room_key("洋室１") == stages.room_key("洋室 1")


def test_original_page_that_is_not_a_finish_schedule_is_marked(tmp_path, machine_output):
    """K-61 の判断 2: 仕上の表を持つ別の図面(カラースキームなど)も原本と数え、「本来の仕上表ではない」と出す。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"

    class Client(FakeClient):
        def stream(self, *, system: str, messages, **kwargs):
            if "読む前の整理" in system:
                body = {"ページ": [{"ページ": 1, "種類": "仕上表", "描かれているもの": "カラースキームボード"},
                                   {"ページ": 2, "種類": "平面図", "描かれているもの": "改修平面図"},
                                   {"ページ": 3, "種類": "白紙"}], "読む順": [2, 1, 3]}
                self.calls.append("整理")
                return FakeStream(json.dumps(body, ensure_ascii=False))
            return super().stream(system=system, messages=messages, **kwargs)

    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=Client())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["仕上表"]["原本"] == "原本あり"
    assert r["仕上表"]["本来の仕上表ではないページ"] == [{"ページ": 1, "図面": "カラースキームボード"}]
    assert any("本来の仕上表ではない" in w for w in r["まとめ"]["精度の注意"])
    assert "本来の仕上表ではない" in (out / "確認画面.html").read_text(encoding="utf-8")


class UsageStream(FakeStream):
    def get_final_message(self):
        msg = super().get_final_message()
        msg.usage = SimpleNamespace(input_tokens=1000, output_tokens=2000, cache_creation_input_tokens=100,
                                    cache_read_input_tokens=400)
        msg.model = "claude-opus-5-5"
        return msg


class UsageClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.models: list[str] = []

    def stream(self, *, system: str, messages, **kwargs):
        self.models.append(kwargs.get("model"))
        s = super().stream(system=system, messages=messages, **kwargs)
        return UsageStream(s.text)


def test_api_usage_is_recorded_and_priced(tmp_path, machine_output):
    """K-62 の 1: 費用は字数の目安ではなく、API が返した使用量で数える(呼び出しごと・段ごと)。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=UsageClient())
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    rec = r["AI を呼んだ記録"]
    one = rec["1回ずつ"][0]
    assert one["使用量"] == {"入力": 1000, "出力": 2000, "キャッシュに書いた": 100, "キャッシュから読んだ": 400}
    # Opus 5.5: 入力 $4・出力 $20・キャッシュ書き込み 1.25 倍・読み出し $0.20(100 万トークンあたり)
    assert one["費用(ドル)"] == pytest.approx((1000 * 4 + 2000 * 20 + 100 * 5 + 400 * 0.2) / 1e6)
    real = rec["まとめ"]["使用量で数えた費用(ドル)"]
    assert real["呼び出しの数"] == len(rec["1回ずつ"])
    assert real["合計"] == pytest.approx(one["費用(ドル)"] * len(rec["1回ずつ"]), rel=1e-6)
    assert set(real["段ごと"]) >= {"整理", "通読", "理解"}
    # 2 回目は置かれた答えを使う。使用量は前の回のものを書いた記録から読み、2 度数えない
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=UsageClient())
    r2 = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r2["AI を呼んだ記録"]["まとめ"]["使用量で数えた費用(ドル)"]["この通しで払った分"] == 0


def test_stage_model_can_be_set_per_stage(tmp_path, machine_output, monkeypatch):
    """手段 c の口: DRAFT_AI_MODEL_<段> で段ごとにモデルを変えられる(既定は変えない)。"""
    monkeypatch.setenv("DRAFT_AI_MODEL_整理", "claude-sonnet-5-5")
    pdf = _pdf(tmp_path / "図面.pdf")
    client = UsageClient()
    run([str(pdf), "--out", str(tmp_path / "出力"), "--machine-output", str(machine_output)], client=client)
    stage_models = dict(zip(client.calls, client.models))
    assert stage_models["整理"] == "claude-sonnet-5-5"
    assert stage_models["通読"] == "claude-opus-5-5"


def test_full_read_is_split_by_default(tmp_path, machine_output):
    """K-61 の判断 1: 通読は既定で分けて読ませる。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    client = FakeClient()
    run([str(pdf), "--out", str(tmp_path / "出力"), "--machine-output", str(machine_output)], client=client)
    from draft.run import DEFAULT_PASS1_BATCH

    assert DEFAULT_PASS1_BATCH > 0
    assert client.calls.count("通読") == 1  # 2 ページなので 1 回に収まる


class BatchClient:
    """まとめて処理(Message Batches)の偽物。中身の答えは FakeClient と同じにする。"""

    def __init__(self) -> None:
        self.inner = UsageClient()
        self.created: list[int] = []
        self._results: dict[str, list] = {}
        self.messages = SimpleNamespace(stream=self._no_stream, batches=SimpleNamespace(
            create=self.create, retrieve=self.retrieve, results=self.results))

    def _no_stream(self, **kwargs):
        raise AssertionError("まとめて処理のときは 1 件ずつ呼ばない")

    def create(self, *, requests):
        bid = f"b{len(self.created)}"
        self.created.append(len(requests))
        out = []
        for r in requests:
            p = r["params"]
            s = self.inner.stream(system=p["system"], messages=p["messages"], model=p["model"])
            out.append(SimpleNamespace(custom_id=r["custom_id"],
                                       result=SimpleNamespace(type="succeeded", message=s.get_final_message())))
        self._results[bid] = out
        return SimpleNamespace(id=bid, processing_status="in_progress")

    def retrieve(self, bid):
        return SimpleNamespace(id=bid, processing_status="ended")

    def results(self, bid):
        return iter(self._results[bid])


def test_batch_mode_sends_each_wave_as_one_batch_at_half_price(tmp_path, machine_output):
    """手段 f: 評価用の回は、段ごとにまとめて送る(即時でない処理方式、50% 引き)。"""
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = BatchClient()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--batch",
                "--batch-poll-seconds", "0"], client=client)
    assert code == 0
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    assert r["理解"]["項目"]
    assert sum(client.created) == len(r["AI を呼んだ記録"]["1回ずつ"])
    one = r["AI を呼んだ記録"]["1回ずつ"][0]
    assert one["使用量"]["まとめて処理(半額)"] == 1
    assert one["費用(ドル)"] == pytest.approx((1000 * 4 + 2000 * 20 + 100 * 5 + 400 * 0.2) / 1e6 / 2)
