"""機械の段を AI 無しで単独に動かす部品(K-62 の追記 1・2・4)。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from draft import machine
from draft.ai import FolderCaller, make_caller


def _pdf(path: Path, text: str = "洋室1") -> Path:
    doc = pymupdf.open()
    p1 = doc.new_page(width=842, height=595)
    p1.insert_text((100, 100), text, fontname="japan")
    p1.draw_rect(pymupdf.Rect(60, 220, 500, 500))
    p2 = doc.new_page(width=842, height=595)  # 文字の層が空で線だけのページ
    for x in range(60, 700, 40):
        p2.draw_line((x, 200), (x + 30, 200))
    doc.new_page(width=842, height=595)  # 白紙
    doc.save(path)
    return path


def test_machine_does_not_import_the_ai_caller():
    src = Path(machine.__file__).read_text(encoding="utf-8")
    assert "from draft.ai" not in src and "import draft.ai" not in src and "from draft import ai" not in src


def test_prepare_uses_the_cache_dir_given_and_reuses_it(tmp_path):
    pdf = _pdf(tmp_path / "図面.pdf")
    cache = tmp_path / "どこでもよい置き場所"
    first = machine.prepare(pdf, cache)
    d = cache / machine.pdf_fingerprint(pdf)
    assert (d / "文字の層.json").exists() and (d / "ページ").is_dir()
    p1 = first["ページ"]["1"]
    assert p1["語の出どころ"] == "文字の層" and p1["語の数"] >= 1
    assert all(len(w[1]) == 4 for w in p1["語"])
    # 2 回目は作り直さない(画像を消しても文字の層.json から返す)
    for f in (d / "ページ").iterdir():
        f.unlink()
    again = machine.prepare(pdf, cache)
    assert again == first


def test_same_content_gives_same_place_whatever_the_file_name(tmp_path):
    a = _pdf(tmp_path / "a.pdf")
    b = tmp_path / "別の名前.pdf"
    b.write_bytes(a.read_bytes())
    assert machine.case_dir(a, tmp_path / "c") == machine.case_dir(b, tmp_path / "c")


def test_cache_dir_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(machine.CACHE_ENV, str(tmp_path / "環境変数の置き場所"))
    assert machine.cache_root(None) == tmp_path / "環境変数の置き場所"
    assert machine.cache_root(str(tmp_path / "引数")) == tmp_path / "引数"


def test_empty_text_layer_is_not_counted_as_zero_words(tmp_path):
    pdf = _pdf(tmp_path / "図面.pdf")
    r = machine.prepare(pdf, tmp_path / "c")
    p2 = r["ページ"]["2"]
    assert p2["語の出どころ"] == machine.OCR_NOT_ASKED
    assert p2["語の数"] is None  # 0 ではなく未取得
    assert r["ページ"]["3"]["白紙"] is True


def test_missing_ocr_engine_is_written_as_not_installed(tmp_path, monkeypatch):
    import axes.image_axis.ocr_backends as backends

    monkeypatch.setattr(backends, "is_rapidocr_available", lambda: False)
    pdf = _pdf(tmp_path / "図面.pdf")
    r = machine.prepare(pdf, tmp_path / "c", ocr=True)
    p2 = r["ページ"]["2"]
    assert p2["語の出どころ"].startswith(machine.OCR_MISSING)
    assert p2["語の数"] is None


def test_misses_counts_from_any_reading(tmp_path):
    pdf = _pdf(tmp_path / "図面.pdf")
    reading = {"読み": {"1": {"ページ": 1, "要素": [
        {"id": "p1-001", "種類": "図", "内容": "四角", "位置": [100, 500, 1200, 1700], "確かさ": "読めた"}]}}}
    r = machine.misses(pdf, reading, [1])
    assert set(r["合計"]) >= {"数える図形", "落ちた", "落ちた率", "囮が拾えた"}
    assert r["合計"]["数える図形"] > 0


def test_assemble_keeps_unknown_quantities_unknown():
    items = [{"id": "u1", "ページ": 1, "要素": ["p1-001"], "位置": None, "囲み": None, "読み取った値": "洋室1", "何": "床",
              "部位": "床", "場所": "洋室1", "区分": "張替", "工事": "床 張替", "科目": "内装", "品番": "FL-1",
              "数量": None, "単位": "m2", "式": None, "状態": "未取得", "確度": "中", "根拠の種類": "図面",
              "理由": "", "選択肢": [], "検算": []}]
    r = machine.assemble({"理解": {"項目": items}})
    assert r["内訳の行"] and all(row["数量"] is None for row in r["内訳の行"])


def test_cli_prepare_and_assemble_run_without_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pdf = _pdf(tmp_path / "図面.pdf")
    assert machine.main(["prepare", str(pdf), "--cache-dir", str(tmp_path / "c")]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["ページ"] == 3
    draft = tmp_path / "下書き.json"
    draft.write_text(json.dumps({"理解": {"項目": []}}), encoding="utf-8")
    out = tmp_path / "組み立て.json"
    assert machine.main(["assemble", "--draft", str(draft), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["内訳の行"] == []


def test_auto_confirmation_makes_the_command_fail(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path / "図面.pdf")
    d = machine.case_dir(pdf, tmp_path / "c")
    (d / "機械の出力.json").write_text(json.dumps({"工事項目": [], "自動確定": {"合計": 1}}), encoding="utf-8")
    assert machine.main(["read", str(pdf), "--cache-dir", str(tmp_path / "c")]) == 2


def test_ai_backend_can_be_switched_to_local_without_calling_the_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("DRAFT_AI_BACKEND", "local")
    assert isinstance(make_caller(tmp_path / "答え"), FolderCaller)
    monkeypatch.setenv("DRAFT_AI_BACKEND", "どこか")
    with pytest.raises(ValueError):
        make_caller(tmp_path / "答え")
