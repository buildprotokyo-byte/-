"""K-63 4 節: つなげていない 7 部品を旗の裏でつなぐ。**合成の図面と偽の AI だけで試す。実図面は使わない。**

確かめること:
- 旗がオフなら、出力に欄を足さない(今と同じ)。
- 旗をオンにしても、「理解」「組み立て」「質問」「機械の検算」は変わらない(部品は「旗の部品」の欄にだけ書く)。
- 旗オンで自動確定 0。部品が足したものは状態「推論」「仮説」「問い」・確度「中」「低」だけ。
- 数量が増える所には、部品・ページ・要素の根拠がある。
- AI を呼ぶ部品(分かれ道)は、機械の答え(機械が数えた数)を指示に入れない。鍵が無ければ未取得のまま進む。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from draft import flags
from draft.run import run
from tests.test_draft_pipeline import FakeClient, FakeStream, _pdf, _understood

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge" / "examples" / "synthetic_knowledge.json"
#: 時間・AI の呼び出しの記録は通しごとに変わる(旗の部品は AI を余分に呼ぶ)ので比べない。
VOLATILE = ("時間(秒)", "AI を呼んだ記録", "旗の部品")


def _understood_with_socket(n: int) -> dict:
    body = _understood(n)
    body["項目"].append(
        {"要素": [f"p{n}-002", f"p{n}-003"], "読み取った値": "円の中に×", "何": "コンセント", "部位": "電気",
         "場所": "洋室1", "区分": "新設", "工事": "コンセント 新設", "科目": "電気設備", "品番": "", "数量": None,
         "単位": "個", "式": "", "状態": "推論", "確度": "低", "根拠の種類": "凡例から", "理由": "", "選択肢": []})
    return body


class FlagClient(FakeClient):
    """旗の部品の段(分かれ道・線引き)にも答える偽の AI。受け取った指示とデータを残す。"""

    def __init__(self, answer_branches: bool = True) -> None:
        super().__init__()
        self.seen: dict[str, list[str]] = {}
        self.answer_branches = answer_branches

    def stream(self, *, system: str, messages, **kwargs):
        text = json.dumps(messages, ensure_ascii=False)
        if "分かれ道" in system:
            self.seen.setdefault("分かれ道", []).append(system + text)
            self.calls.append("分かれ道")
            body = {"答え": []}
            return FakeStream(json.dumps(body, ensure_ascii=False))
        if "見積に載せる工事の行か" in system:
            self.seen.setdefault("線引き", []).append(system + text)
            self.calls.append("線引き")
            return FakeStream(json.dumps({"判定": [{"番号": 1, "判定": "工事の行", "理由": "張替"}]}, ensure_ascii=False))
        if not any(k in system for k in ("読む前の整理", "1 ページだけ", "図面一式に、何が書いてあるか", "仕上表です")):
            n = 2 if "2 ページ" in system else 1
            self.calls.append("理解")
            return FakeStream(json.dumps(_understood_with_socket(n), ensure_ascii=False))
        return super().stream(system=system, messages=messages, **kwargs)


@pytest.fixture()
def machine_output(tmp_path: Path) -> Path:
    p = tmp_path / "機械の出力.json"
    p.write_text(json.dumps({"工事項目": [], "自動確定": {"合計": 0}}), encoding="utf-8")
    return p


@pytest.fixture()
def legend(tmp_path: Path) -> Path:
    p = tmp_path / "凡例.json"
    p.write_text(json.dumps({"binding": "案件の凡例", "work_marks": [{"code": "交換", "meaning": "既存位置に新しく", "source_page": 1}],
                             "symbols": [{"code": "2E", "name": "接地コンセント(2口)", "source_page": 1}]},
                            ensure_ascii=False), encoding="utf-8")
    return p


def _run(tmp_path: Path, name: str, machine_output: Path, client, *extra: str) -> tuple[int, dict]:
    pdf = tmp_path / "図面.pdf"
    if not pdf.exists():
        _pdf(pdf)
    out = tmp_path / name
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output),
                "--answers-dir", str(tmp_path / f"答え_{name}"), "--mode", "精密", *extra], client=client)
    return code, json.loads((out / "下書き.json").read_text(encoding="utf-8"))


def _stable(result: dict, tmp_path: Path, name: str, drop: tuple[str, ...] = VOLATILE) -> str:
    """比べる形: 揺れる欄を外し、通しごとに違う置き場所(出力・答えのフォルダ)の名前を揃える。"""
    text = json.dumps({k: v for k, v in result.items() if k not in drop}, ensure_ascii=False, default=str)
    return text.replace(str(tmp_path / f"答え_{name}"), "<答え>").replace(str(tmp_path / name), "<出力>")


def test_every_flag_is_off_by_default_and_adds_nothing(tmp_path, machine_output):
    code, off = _run(tmp_path, "オフ", machine_output, FlagClient())
    assert code == 0
    assert "旗の部品" not in off
    # 旗オフでは、部品の段の AI を呼ばない
    assert {r["段"] for r in off["AI を呼んだ記録"]["1回ずつ"]} == {"整理", "通読", "読み直し", "理解", "仕上表の原本"}
    # もう一度オフで通すと、時間の欄の他はバイト単位で同じ
    _, again = _run(tmp_path, "オフ2", machine_output, FlagClient())
    timing = ("時間(秒)", "AI を呼んだ記録")  # AI の記録にも秒がある。秒の他(段・鍵・指紋・出どころ)は下で比べる
    assert _stable(off, tmp_path, "オフ", timing) == _stable(again, tmp_path, "オフ2", timing)
    # 並べて呼ぶので記録の並びは終わった順(変更前から揺れる)。並べ直して比べる
    calls = lambda r: sorted((x["段"], x["鍵"], x["指紋"], x["答えの出どころ"]) for x in r["AI を呼んだ記録"]["1回ずつ"])
    assert calls(off) == calls(again)


def test_flags_on_do_not_touch_understanding_or_assembly(tmp_path, machine_output, legend):
    _, off = _run(tmp_path, "オフ", machine_output, FlagClient())
    code, on = _run(tmp_path, "全部", machine_output, FlagClient(), "--with-all", "--legend-lookup", str(legend),
                    "--knowledge", str(KNOWLEDGE))
    assert code == 0
    assert set(on["旗の部品"]["部品"]) == set(flags.FLAGS)
    assert _stable(off, tmp_path, "オフ") == _stable(on, tmp_path, "全部")


def test_flags_on_keep_zero_auto_confirmed_and_mark_what_they_add(tmp_path, machine_output, legend):
    code, on = _run(tmp_path, "全部", machine_output, FlagClient(), "--with-all", "--legend-lookup", str(legend),
                    "--knowledge", str(KNOWLEDGE))
    assert code == 0
    part = on["旗の部品"]
    assert on["機械の検算"]["自動確定"] == 0
    assert part["検算(旗の行を足した)"]["自動確定"] == 0
    added = [x for p in part["部品"].values() for x in p["足したもの"]]
    assert added, "合成の図面でも、記号の数え上げが数量を足すはず"
    for x in added:
        assert x["状態"] in ("推論", "仮説") and x["確度"] in ("中", "低")
    increases = [x for x in added if x.get("数量が増える")]
    assert increases
    for x in increases:
        assert x["数量"] is not None and x["根拠"]["部品"] and x["根拠"]["ページ"]
        assert x["根拠"]["要素"] or x["根拠"].get("輪郭") or x["根拠"].get("AI の根拠") is not None
    sym = part["部品"]["記号を室ごとに数える"]
    socket = [x for x in sym["足したもの"] if x["工事"] == "コンセント 新設"]
    assert socket and socket[0]["数量"] == 2 and socket[0]["状態"] == "仮説" and socket[0]["根拠"]["要素"]
    # 理解の数量は未取得のまま(上書きしていない)
    assert all(it["数量"] is None for it in on["理解"]["項目"] if it["工事"] == "コンセント 新設")
    assert part["まとめ"]["観測・確度高を付けたもの"] == 0
    for q in (q for p in part["部品"].values() for q in p["問い"]):
        assert q["状態"] == "問い" and "推奨" not in json.dumps(q["選択肢"], ensure_ascii=False)


def test_branch_question_never_shows_the_machine_count(tmp_path, machine_output):
    client = FlagClient()
    _, on = _run(tmp_path, "分かれ道", machine_output, client, "--with-branch-questions")
    lamp = next(it for it in on["理解"]["項目"] if it["工事"] == "照明器具 交換")
    assert any(n.startswith("数えた記号の要素は 2 個") for n in lamp["検算"])
    sent = "\n".join(client.seen["分かれ道"])
    assert "数えた記号の要素は" not in sent and "2 台" not in sent
    branch = on["旗の部品"]["部品"]["分かれ道を AI に選択肢で聞く"]
    assert any(b["種類"] == "記号の数え直し" for b in branch["分かれ道"])
    # AI が答えを返さなかった分かれ道は未取得のまま問いに残る(数量は足さない)
    assert all(q["AI の答え"] == "未取得" for q in branch["問い"])
    assert branch["足したもの"] == []


def test_ai_parts_without_a_key_only_write_instructions(tmp_path, machine_output, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pdf = _pdf(tmp_path / "図面.pdf")
    out = tmp_path / "出力"
    run([str(pdf), "--out", str(out), "--machine-output", str(machine_output)], client=FakeClient())
    code = run([str(pdf), "--out", str(out), "--machine-output", str(machine_output), "--with-line-judge",
                "--with-branch-questions"])
    assert code == 0
    r = json.loads((out / "下書き.json").read_text(encoding="utf-8"))
    lj = r["旗の部品"]["部品"]["線引き"]
    assert lj["判定ごと"]["要確認"] == len(r["組み立て"]["内訳の行"]) and lj["判定ごと"]["工事の行ではない"] == 0
    pending = [p.parent.name for p in (out / "AIの答え" / "待っている問い").glob("*/指示.md")]
    assert any(name.startswith("線引き") for name in pending) and any(name.startswith("分かれ道") for name in pending)
    records = [x for x in r["AI を呼んだ記録"]["1回ずつ"] if x["段"] in ("線引き", "分かれ道")]
    assert records and all(x["答えの出どころ"] == "未取得" for x in records)
    # 分かれ道の指示にも、機械が数えた数は書かない
    text = "\n".join(p.read_text(encoding="utf-8") for p in (out / "AIの答え" / "待っている問い").glob("分かれ道*/*"))
    assert "数えた記号の要素は" not in text


def test_line_judge_keeps_every_row(tmp_path, machine_output):
    _, on = _run(tmp_path, "線引き", machine_output, FlagClient(), "--with-line-judge")
    lj = on["旗の部品"]["部品"]["線引き"]
    assert len(lj["判定"]) == len(on["組み立て"]["内訳の行"])
    assert lj["判定"][0]["判定"] == "工事の行" and lj["判定"][0]["線引き"] == "AI"
    # AI が判定しなかった行は落とさず要確認
    assert all(j["判定"] == "要確認" for j in lj["判定"][1:])


def test_knowledge_candidates_stay_candidates():
    understanding = {"項目": [{"id": "u-p1-001", "ページ": 1, "科目": "架空工事", "工事": "飾り柱撤去", "区分": "撤去",
                               "部位": "飾り柱", "何": "飾り柱", "場所": "洋室1", "数量": None}]}
    part = flags.knowledge(understanding, str(KNOWLEDGE))
    assert part["表"]["採否ごと"] == {"候補": 3}
    assert all(a["根拠"]["採否"] == "候補" and a["数量"] is None and a["状態"] == "仮説" for a in part["足したもの"])
    assert part["問い"] and part["問い"][0]["選択肢"] == ["構造体", "化粧だけ", "分からない"]
    # 「天井」は「天井飾り」に掛からない(項目の短い語で知識を引かない)
    ceiling = {"項目": [dict(understanding["項目"][0], 工事="天井クロス張替", 部位="天井", 何="天井", 科目="内装")]}
    assert flags.knowledge(ceiling, str(KNOWLEDGE))["足したもの"] == []
    assert flags.knowledge(understanding, None)["動いたか"].startswith("未取得")


def test_symbol_count_names_from_formula_ids_and_counts_by_room():
    reading = {"読み": {1: {"要素": [
        {"id": "p1-001", "種類": "文字", "内容": "洋室1", "位置": [100, 100, 140, 120]},
        {"id": "p1-002", "種類": "記号", "内容": "円", "位置": [150, 100, 160, 110]},
        {"id": "p1-003", "種類": "記号", "内容": "円", "位置": [170, 100, 180, 110]},
        {"id": "p1-004", "種類": "記号", "内容": "赤い四角に「新」", "位置": [190, 100, 200, 110]},
    ]}}}
    item = {"id": "u-p1-001", "ページ": 1, "要素": ["p1-002", "p1-003", "p1-004"], "式": "記号 2 個を数えた(p1-002, p1-003)",
            "工事": "照明 新設", "何": "照明", "区分": "新設", "場所": "洋室1", "数量": 2.0, "単位": "台"}
    part = flags.symbol_count(reading, {"項目": [item]}, {"原本の行": []})
    rows = part["足したもの"]
    assert len(rows) == 1 and rows[0]["数量"] == 2 and rows[0]["場所"] == "洋室1"
    assert rows[0]["照らし合わせ"] == "一致" and not rows[0]["数量が増える"]
    assert rows[0]["根拠"]["要素"] == ["p1-002", "p1-003"]  # 区分の印(p1-004)は式に無いので数えない


def test_scale_length_does_not_measure_without_a_scale(tmp_path):
    pdf = _pdf(tmp_path / "図面.pdf")
    org = {"ページ": {2: {"種類": "平面図"}}}
    understanding = {"項目": [{"id": "u-p2-001", "ページ": 2, "部位": "床", "単位": "m2", "場所": "洋室1", "数量": None,
                               "工事": "床張替", "何": "床"}]}
    part = flags.scale_length(pdf, org, {"読み": {}}, understanding, {"原本の行": []})
    assert part["縮尺"][0]["縮尺"] == "未取得"
    assert part["足したもの"] == []


def test_killer_question_is_not_connected_and_says_why(tmp_path, machine_output):
    """本番のコードは結合 solver を直接作れない(solver を迂回しない決まり)。だから旗にしない。"""
    assert "キラークエスチョン" not in " ".join(flags.FLAGS)
    assert any("ConsistencySolver" in why for why in flags.NOT_FLAGGED.values())
    _, on = _run(tmp_path, "全部", machine_output, FlagClient(), "--with-all")
    assert on["旗の部品"]["旗の裏でもつながなかった部品"][0]["部品"].startswith("キラークエスチョン")


def test_added_entries_cannot_claim_observed_or_high():
    with pytest.raises(ValueError):
        flags._added("x", work="w", place="p", quantity=1.0, unit="個", state="観測", confidence="中", basis={})
    with pytest.raises(ValueError):
        flags._added("x", work="w", place="p", quantity=1.0, unit="個", state="推論", confidence="高", basis={})
