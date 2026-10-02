"""K-63 読みの設計 V2(目的から探す型)・V3(閉じた語彙型)と ``--design`` / ``--vocab`` の口の試験。

**合成の図面と偽の AI で通す。実図面は使わない。** 偽の AI は system の文(段の指示)で段を見分ける:

- 根拠探し: 「根拠が、このページのどこに」(根拠探し.txt だけにある。「工事概略」の語は根拠探しにもあるので先に見る)
- 工事概略: 「工事概略」(工事概略.txt と根拠探し.txt にある。根拠探しを外した後で見る)
- 語彙で読む: 「決まった語彙」(語彙で読む.txt だけにある)

それ以外の段(整理・通読・読み直し・理解・仕上表の原本)は ``tests/test_draft_pipeline.py`` の FakeClient に任せる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pytest

from benchmarks.erase_check import extract_primitives
from draft import designs
from draft.run import run
from tests.test_draft_pipeline import FakeClient, FakeStream, _pdf

V2_V3_STAGES = {"工事概略", "根拠探し", "語彙で読む"}
VOCAB = designs.load_vocab(designs.DEFAULT_VOCAB)
VOCAB_IDS = {s["id"] for s in VOCAB["細目"]}
VOCAB_NAMES = {s["id"]: s["名前"] for s in VOCAB["細目"]}


def _stage_of(system: str) -> str | None:
    if "根拠が、このページのどこに" in system:
        return "根拠探し"
    if "工事概略" in system:
        return "工事概略"
    if "決まった語彙" in system:
        return "語彙で読む"
    return None


def _pages_sent(messages) -> list[int]:
    out = []
    for b in messages[0]["content"]:
        if b.get("type") == "text":
            m = re.fullmatch(r"画像: p(\d+)\.png", b.get("text", ""))
            if m:
                out.append(int(m.group(1)))
    return out


def _answer(stage: str, calls: list[str], body: dict) -> FakeStream:
    calls.append(stage)
    return FakeStream(json.dumps(body, ensure_ascii=False))


@pytest.fixture()
def machine_output(tmp_path: Path) -> Path:
    p = tmp_path / "機械の出力.json"
    p.write_text(json.dumps({"工事項目": [], "自動確定": {"合計": 0}}), encoding="utf-8")
    return p


def _load(out: Path) -> dict:
    return json.loads((out / "下書き.json").read_text(encoding="utf-8"))


def test_stage_words_pick_exactly_one_prompt():
    """見分けの語が、その段の指示にだけある(ほかの段の指示に紛れない)。"""
    prompts = Path(designs.__file__).with_name("prompts")
    texts = {p.stem: p.read_text(encoding="utf-8") for p in prompts.glob("*.txt")}
    found = {name: _stage_of(t) for name, t in texts.items()}
    assert found["工事概略"] == "工事概略"
    assert found["根拠探し"] == "根拠探し"
    assert found["語彙で読む"] == "語彙で読む"
    for name in ("整理", "通読", "読み直し", "理解", "仕上表の原本"):
        assert found[name] is None, name


# ---------------------------------------------------------------------------
# V1(既定)は今までと同じ段だけ
# ---------------------------------------------------------------------------


class RecordingClient(FakeClient):
    """V1 の段は FakeClient のまま。V2・V3 の段が呼ばれたら記録する(答えは空)。"""

    def stream(self, *, system: str, messages, **kwargs):
        stage = _stage_of(system)
        if stage:
            return _answer(stage, self.calls, {})
        return super().stream(system=system, messages=messages, **kwargs)


def test_default_design_is_v1_and_calls_only_v1_stages(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    default = RecordingClient()
    assert run([str(pdf), "--out", str(tmp_path / "既定"), "--machine-output", str(machine_output)],
               client=default) == 0
    explicit = RecordingClient()
    assert run([str(pdf), "--out", str(tmp_path / "V1"), "--machine-output", str(machine_output), "--design", "V1"],
               client=explicit) == 0
    assert not (set(default.calls) & V2_V3_STAGES)
    assert sorted(default.calls) == sorted(explicit.calls)
    assert {"整理", "通読", "理解", "原本"} <= set(default.calls)
    r = _load(tmp_path / "既定")
    assert "読みの設計" not in r
    recorded = {x["段"] for x in r["AI を呼んだ記録"]["1回ずつ"]}
    assert recorded and not (recorded & V2_V3_STAGES)


def test_unknown_design_is_refused(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    with pytest.raises(SystemExit):
        run([str(pdf), "--out", str(tmp_path / "出力"), "--machine-output", str(machine_output), "--design", "V9"],
            client=FakeClient())


# ---------------------------------------------------------------------------
# V2 目的から探す型
# ---------------------------------------------------------------------------

OUTLINE = {"項目": [
    {"id": "G001", "室": "洋室1", "部位": "床", "工事": "床 フローリング張替", "区分": "張替", "科目": "内装",
     "品番": "FL-1", "数量": None, "単位": "m2", "出典": [1], "探す図": [2]},
    {"id": "G002", "室": "洋室1", "部位": "壁", "工事": "壁 クロス張替", "区分": "張替", "科目": "内装",
     "品番": "", "数量": None, "単位": "m2", "出典": [1], "探す図": [2]},
    {"id": "G003", "室": "洋室1", "部位": "電気", "工事": "コンセント 新設", "区分": "新設", "科目": "電気設備",
     "品番": "", "数量": None, "単位": "箇所", "出典": [1], "探す図": [2]},
]}

SEARCH = {"ページ": [
    {"ページ": 1, "見つけた": [], "概略に無い": []},
    {"ページ": 2, "見つけた": [
        {"id": "G001", "位置": [[150, 520, 400, 700]], "数": None, "確度": "中"},
        {"id": "G003", "位置": [[600, 600, 620, 620]], "数": 2, "確度": "高"},
        {"id": "G003", "位置": [[700, 600, 720, 620]], "数": None, "確度": "中"},
    ], "概略に無い": [
        {"工事": "照明器具 交換", "部位": "電気", "室": "洋室1", "区分": "交換",
         "位置": [[400, 400, 420, 420], [450, 400, 470, 420]], "数": 2},
    ]},
]}


class V2Client(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.search_pages: list[list[int]] = []

    def stream(self, *, system: str, messages, **kwargs):
        stage = _stage_of(system)
        if "読む前の整理" in system:
            return _answer("整理", self.calls, {"ページ": [
                {"ページ": 1, "種類": "仕様書", "描かれているもの": "特記仕様書"},
                {"ページ": 2, "種類": "平面図", "描かれているもの": "改修平面図"},
                {"ページ": 3, "種類": "白紙"}], "読む順": [1, 2, 3]})
        if stage == "工事概略":
            return _answer(stage, self.calls, OUTLINE)
        if stage == "根拠探し":
            self.search_pages.append(_pages_sent(messages))
            return _answer(stage, self.calls, SEARCH)
        if stage:
            raise AssertionError(f"V2 で呼ばれるはずの無い段: {stage}")
        return super().stream(system=system, messages=messages, **kwargs)


def test_v2_outline_then_search(tmp_path, machine_output):
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = V2Client()
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", "V2"],
               client=client)
    assert code == 0
    # V1 の読み(通読・読み直し・理解)は呼ばない
    assert {"工事概略", "根拠探し"} <= set(client.calls)
    assert not ({"通読", "読み直し", "理解", "語彙で読む"} & set(client.calls))
    # 白紙のページは根拠探しに渡さない
    assert all(3 not in pages for pages in client.search_pages)
    r = _load(out)
    assert r["読みの設計"]["案"] == "V2"
    plan = r["読みの設計"]["中身"]
    assert plan["渡したページ"] == [1]
    assert plan["見つからなかった項目"] == ["G002"]
    items = r["理解"]["項目"]

    # 概略に無い工事は「推論」・確度「低」
    extra = [it for it in items if it["工事"] == "照明器具 交換"]
    assert extra and all(it["状態"] == "推論" and it["確度"] == "低" for it in extra)
    assert all(it["状態"] != "観測" for it in extra)

    # 図面で見つからない概略の項目は「問い」、数量は null(0 にしない)、選択肢つき
    missing = [it for it in items if it["工事"] == "壁 クロス張替"]
    assert len(missing) == 1
    m = missing[0]
    assert m["状態"] == "問い" and m["数量"] is None and m["数量"] != 0
    assert m["選択肢"] and any("分からない" in o for o in m["選択肢"])
    assert "無いことの証拠にしない" in m["理由"]

    # 見つけたが数が書かれていない項目も 0 にしない
    floor = [it for it in items if it["工事"] == "床 フローリング張替" and it["位置"]]
    assert floor and all(it["数量"] is None for it in floor)
    # 数のある所だけ足し、数の無い所は足さない(0 として数えない)
    outlet = [it for it in items if it["工事"] == "コンセント 新設" and it["位置"]]
    assert len(outlet) == 1 and outlet[0]["数量"] == 2
    assert "入れていない" in outlet[0]["式"]

    # どこにも 0 の数量を作らない
    assert all(it["数量"] != 0 for it in items)
    # 自動確定は 0
    assert r["機械の検算"]["自動確定"] == 0
    assert r["まとめ"]["自動確定"] == 0


def test_v2_outline_missing_does_not_stop(tmp_path, machine_output):
    """工事概略の答えが無くても止まらず、根拠探しは「概略に無い」だけで進む。"""

    class NoOutline(V2Client):
        def stream(self, *, system: str, messages, **kwargs):
            if _stage_of(system) == "工事概略":
                return _answer("工事概略", self.calls, ["概略ではない"])
            return super().stream(system=system, messages=messages, **kwargs)

    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", "V2"],
               client=NoOutline()) == 0
    r = _load(out)
    assert any(s["段"] == "工事概略" for s in r["止まった所"])
    assert r["読みの設計"]["中身"]["出どころ"] == "未取得"
    items = r["理解"]["項目"]
    assert items and all(it["状態"] == "推論" for it in items)
    assert r["機械の検算"]["自動確定"] == 0


# ---------------------------------------------------------------------------
# V3 閉じた語彙型
# ---------------------------------------------------------------------------


def _pdf_v3(path: Path) -> Path:
    """V3 用: 2 ページ目は小さな図形だけ(要素ごとの四角で拾える大きさ)。"""
    doc = pymupdf.open()
    p1 = doc.new_page(width=842, height=595)
    p1.insert_text((50, 50), "Finish table 内部仕上表", fontname="japan")
    p2 = doc.new_page(width=842, height=595)
    p2.insert_text((100, 100), "洋室1", fontname="japan")
    for i in range(10):
        x = 80 + i * 50
        p2.draw_line((x, 250), (x + 20, 250))
        p2.draw_line((x, 300), (x, 320))
    doc.new_page(width=842, height=595)  # 白紙
    doc.save(path)
    return path


def _prim_boxes(pdf: Path, n: int) -> list[list[float]]:
    with pymupdf.open(pdf) as doc:
        prims = extract_primitives(doc.load_page(n - 1), n)
    return [[p.bbox[0] - 1, p.bbox[1] - 1, p.bbox[2] + 1, p.bbox[3] + 1] for p in prims]


def _v3_page(n: int, write_boxes: list[list[float]]) -> dict:
    items = [
        # 語彙にある id
        {"細目": "N04", "部位": "壁", "区分": "張替", "室": "R01", "単位": "m2", "数量": None, "式": "", "品番": "",
         "状態": "観測", "確度": "中", "位置": [[200, 590, 260, 600]], "選択肢": []},
        # 語彙に無い id
        {"細目": "Q42", "部位": "壁", "区分": "張替", "室": "R01", "単位": "m2", "数量": None, "式": "", "品番": "",
         "状態": "観測", "確度": "中", "位置": [[300, 590, 360, 600]], "選択肢": []},
        # id の代わりに自由記述の名前、さらに語彙に無い「工事」「品名」の欄
        {"細目": "天井ビニールクロス貼替(自由記述)", "工事": "天井 クロス張替(自由記述)", "品名": "自由な品名",
         "部位": "天井", "区分": "張替", "室": "R02", "単位": "m2", "数量": 12, "式": "", "品番": "",
         "状態": "観測", "確度": "中", "位置": [[400, 590, 460, 600]], "選択肢": []},
        # 語彙の id に自由記述の工事名を添えたもの(工事名は使わない)
        {"細目": "N05", "工事": "天井 クロス張替 特注品(自由記述)", "部位": "天井", "区分": "張替", "室": "未確定",
         "単位": "m2", "数量": None, "式": "", "品番": "", "状態": "問い", "確度": "低",
         "位置": [[500, 590, 560, 600]], "選択肢": ["洋室1", "洋室2"]},
    ]
    return {"ページ": n, "項目": items if n == 2 else [],
            "書き込み": [["N2", *b] for b in write_boxes], "分からない": []}


class V3Client(FakeClient):
    def __init__(self, pdf: Path) -> None:
        super().__init__()
        self.pdf = pdf
        self.vocab_calls: list[list[int]] = []
        self.sent_vocab: list[dict] = []

    def stream(self, *, system: str, messages, **kwargs):
        stage = _stage_of(system)
        if stage == "語彙で読む":
            pages = _pages_sent(messages)
            self.vocab_calls.append(pages)
            for b in messages[0]["content"]:
                if b.get("type") == "text" and b["text"].startswith("データ 語彙:"):
                    self.sent_vocab.append(json.loads(b["text"].split("\n", 1)[1]))
            full = {n: _prim_boxes(self.pdf, n) for n in (1, 2)}
            if pages == [2]:  # 読み直し: 2 ページ目の図形を全部挙げる
                return _answer("語彙で読む(読み直し)", self.calls, {"ページ": [_v3_page(2, full[2])]})
            # 1 回目: 1 ページ目は全部挙げ、2 ページ目は 1 つしか挙げない(落ちが多い)
            return _answer("語彙で読む", self.calls,
                           {"ページ": [_v3_page(1, full[1]), _v3_page(2, full[2][:1])]})
        if stage:
            raise AssertionError(f"V3 で呼ばれるはずの無い段: {stage}")
        return super().stream(system=system, messages=messages, **kwargs)


def test_v3_closed_vocabulary(tmp_path, machine_output):
    pdf = _pdf_v3(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = V3Client(pdf)
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", "V3"],
               client=client)
    assert code == 0
    assert not ({"通読", "読み直し", "理解", "工事概略", "根拠探し"} & set(client.calls))
    # 1 回目は 1・2 ページを 1 回で、読み直しは 2 ページ目だけ。白紙は渡さない
    assert client.vocab_calls[0] == [1, 2]
    assert [2] in client.vocab_calls[1:]
    assert all(3 not in c for c in client.vocab_calls)
    # 語彙と室の表(仕上表の原本から R01・R02)を渡している
    v = client.sent_vocab[0]
    assert ["R01", "洋室1"] in v["室の表"] and ["R02", "洋室2"] in v["室の表"]
    assert {row[0] for row in v["細目"]} == VOCAB_IDS

    r = _load(out)
    assert r["読みの設計"]["案"] == "V3"
    reading = r["読む"]
    # 落ちが多いページが読み直しに回り、1 ページ目(全部挙げた)は回らない
    assert reading["読み直したページ"] == [2]
    p2 = reading["ページ"]["2"]
    assert p2["通読の落ちた率"] > 0.33 and p2["読み直した"] is True
    assert p2["落ちた率"] < p2["通読の落ちた率"]
    assert reading["ページ"]["1"]["読み直した"] is False

    items = r["理解"]["項目"]
    assert len(items) == 4
    # 工事の名前は語彙の名前だけ(自由記述は出ない)
    names = set(VOCAB_NAMES.values())
    assert all(it["工事"] in names and it["何"] in names for it in items)
    rows = r["組み立て"]["内訳の行"]
    assert rows and all(row["工事項目"] in names for row in rows)
    assert "自由な品名" not in json.dumps(r["組み立て"], ensure_ascii=False)
    for it in items:
        assert "自由" not in it["工事"] and "自由" not in it["何"] and "自由" not in it["品番"]
    assert all("自由" not in row["工事項目"] for row in rows)

    # 語彙に無い id は使わない: X99 として扱う(または問い)
    by_box = {tuple(it["位置"][0]): it for it in items}
    q42 = by_box[(300.0, 590.0, 360.0, 600.0)]
    free = by_box[(400.0, 590.0, 460.0, 600.0)]
    for it in (q42, free):
        assert it["工事"] == VOCAB_NAMES["X99"]
        assert it["科目"] == next(s["科目"] for s in VOCAB["細目"] if s["id"] == "X99")
        assert any("語彙に無い id" in x for x in it["検算"])
    for it in items:
        assert it["細目"] in VOCAB_IDS or it["状態"] == "問い", it
    assert r["読みの設計"]["中身"]["語彙に無い細目"] == 2
    # 語彙の id は語彙の名前に、室の表の id は室名になる
    n04 = by_box[(200.0, 590.0, 260.0, 600.0)]
    assert n04["工事"] == VOCAB_NAMES["N04"] and n04["場所"] == "洋室1" and n04["細目"] == "N04"
    n05 = by_box[(500.0, 590.0, 560.0, 600.0)]
    assert n05["工事"] == VOCAB_NAMES["N05"] and n05["場所"] == "未確定" and n05["状態"] == "問い"
    # 自動確定は 0
    assert r["機械の検算"]["自動確定"] == 0
    assert r["まとめ"]["自動確定"] == 0


def test_v3_vocab_option_replaces_vocabulary(tmp_path, machine_output):
    """``--vocab`` で語彙を差し替えると、その語彙に無い id(既定の語彙にはある N04 など)は X99 になる。"""
    small = {k: VOCAB[k] for k in VOCAB if k != "細目"}
    small["細目"] = [s for s in VOCAB["細目"] if s["id"] in ("N05", "X99")]
    vocab_path = tmp_path / "語彙.json"
    vocab_path.write_text(json.dumps(small, ensure_ascii=False), encoding="utf-8")
    pdf = _pdf_v3(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    client = V3Client(pdf)
    assert run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", "V3",
                "--vocab", str(vocab_path)], client=client) == 0
    assert {row[0] for row in client.sent_vocab[0]["細目"]} == {"N05", "X99"}
    r = _load(out)
    assert r["読みの設計"]["中身"]["語彙の細目の数"] == 2
    small_names = {s["名前"] for s in small["細目"]}
    assert all(it["工事"] in small_names for it in r["理解"]["項目"])
    assert r["読みの設計"]["中身"]["語彙に無い細目"] == 3


def test_load_vocab_reads_env(tmp_path, monkeypatch):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"細目": [{"id": "A1"}]}), encoding="utf-8")
    monkeypatch.setenv(designs.VOCAB_ENV, str(p))
    assert designs.load_vocab()["細目"] == [{"id": "A1"}]
    monkeypatch.delenv(designs.VOCAB_ENV)
    assert designs.load_vocab()["細目"] == VOCAB["細目"]


def test_room_table_does_not_repeat_same_room():
    rooms = designs.room_table([{"室": "洋室1"}, {"室": "洋室(1)"}, {"室": "洋室2"}, {"室": "洋室1"}])
    assert rooms == [["R01", "洋室1"], ["R02", "洋室2"]]


# ---------------------------------------------------------------------------
# 鍵が無いとき
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("design, stage", [("V2", "工事概略"), ("V2", "根拠探し"), ("V3", "語彙で読む")])
def test_no_key_runs_to_the_end_and_writes_pending(tmp_path, monkeypatch, machine_output, design, stage):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--design", design])
    assert code == 0
    r = _load(out)
    assert (out / "確認画面.html").exists()
    assert r["読みの設計"]["案"] == design
    assert r["段の中の例外"] == []
    pending = list((out / "AIの答え" / "待っている問い").glob("*/指示.md"))
    names = {p.parent.name.split("_")[0] for p in pending}
    assert {"整理", stage} <= names
    assert not ({"通読", "理解"} & names)
    stops = {s["段"] for s in r["止まった所"]}
    assert "整理" in stops and stage in stops
    assert r["理解"]["項目"] == []
    assert r["まとめ"]["自動確定"] == 0
