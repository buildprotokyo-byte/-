"""機械の段だけを、AI を呼ばずに単独で動かす(K-62 の追記 1・4)。

マザー PC(Windows 11)・WSL・Docker・将来のサーバーのどこでも同じに動くように、
**場所は決め打ちしない**。入力・出力・キャッシュの置き場所は、すべて引数か環境変数で渡す。

    python -m draft.machine prepare  図面.pdf --cache-dir <置き場所> [--ocr]
    python -m draft.machine read     図面.pdf --cache-dir <置き場所> [--case-id P011]
    python -m draft.machine misses   図面.pdf --reading 下書き.json --out 落ち.json [--cache-dir ...]
    python -m draft.machine assemble --draft 下書き.json --out 組み立て.json [--labor 歩掛.json]

キャッシュ
----------
``<置き場所>/<PDF の中身の指紋 12 桁>/`` に置く。同じ PDF なら 2 回目からは作り直さない
(ファイル名や置き場所が変わっても、中身が同じなら同じ指紋)。置き場所の既定は環境変数
``DRAFT_CACHE_DIR``、無ければ今いるフォルダの ``draft_cache``。

段ごとの中身
------------
- prepare: ページを画像にする・文字の層(位置つきの語)を取り出す・白紙を見分ける。
  文字の層が空のページは ``--ocr`` のとき OCR に掛ける。**OCR のエンジンが入っていなければ
  「OCR 未導入」と書き、読めた語を 0 とは書かない。**
- read: 機械の読み(``app.run`` の 7 つの段。台帳は飛ばす)。一本道の検算の相手になる。
- misses: 読み(AI の読みでも何でも)の四角で、図形がどれだけ拾えたかを数える(囮も同じ数え方)。
- assemble: 理解の項目から、内訳の行・材料表・時間を組み立てる(数量の無いものは未取得のまま)。

**どの段も AI を呼ばない**(``draft.ai`` を読み込まない)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

CACHE_ENV = "DRAFT_CACHE_DIR"
DEFAULT_CACHE_DIR = "draft_cache"
OCR_MISSING = "OCR 未導入"
OCR_NOT_ASKED = "OCR を掛けていない(--ocr を付けていない)"


def cache_root(arg: str | None) -> Path:
    return Path(arg or os.environ.get(CACHE_ENV) or DEFAULT_CACHE_DIR)


def pdf_fingerprint(pdf: str | Path) -> str:
    h = hashlib.sha256()
    with open(pdf, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def case_dir(pdf: str | Path, cache: str | Path | None) -> Path:
    d = cache_root(str(cache) if cache else None) / pdf_fingerprint(pdf)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def _ocr_words(pdf: Path, number: int) -> tuple[list[list[Any]], str]:
    """文字の層が空のページを OCR に掛ける。エンジンが無ければ語を返さず、そのことを書く。"""
    try:
        from axes.image_axis.ocr_backends import RapidOcrBackend, is_rapidocr_available
        from axes.image_axis.ocr_text import recognize_page
    except Exception as exc:  # noqa: BLE001
        return [], f"{OCR_MISSING}({type(exc).__name__})"
    if not is_rapidocr_available():
        return [], OCR_MISSING
    page = recognize_page(pdf, number - 1, backends=[RapidOcrBackend()])
    return [[s.text, [round(v, 1) for v in s.bbox]] for s in page.spans], "OCR で読んだ(RapidOCR、既定のモデル)"


def prepare(pdf: str | Path, cache: str | Path | None = None, ocr: bool = False) -> dict[str, Any]:
    """ページの画像・文字の層・白紙の見分け。**キャッシュにあれば作り直さない。**"""
    import pymupdf

    from draft.pages import WIDTH_PX, render

    pdf = Path(pdf)
    d = case_dir(pdf, cache)
    out = d / "文字の層.json"
    if out.exists() and not ocr:
        return json.loads(out.read_text(encoding="utf-8"))
    started = time.perf_counter()
    infos = render(pdf, d / "ページ")
    pages: dict[str, Any] = {}
    with pymupdf.open(pdf) as doc:
        for info in infos:
            page = doc.load_page(info.number - 1)
            zoom = WIDTH_PX / page.rect.width
            words = [[w[4], [round(w[0] * zoom, 1), round(w[1] * zoom, 1), round(w[2] * zoom, 1), round(w[3] * zoom, 1)]]
                     for w in page.get_text("words")]
            source = "文字の層"
            if not words and not info.blank:
                if ocr:
                    words, source = _ocr_words(pdf, info.number)
                else:
                    source = OCR_NOT_ASKED
            pages[str(info.number)] = {
                "白紙": info.blank,
                "画像": str(info.image),
                "縮小画像": str(info.thumb),
                "語の出どころ": source,
                # OCR を掛けていない・入っていないページは「語が 0」ではなく未取得(None)
                "語の数": len(words) if source == "文字の層" or source.startswith("OCR で読んだ") else None,
                "語": words,
                "線や図形の数": len(page.get_drawings()),
            }
    result = {"PDF": pdf.name, "指紋": pdf_fingerprint(pdf), "ページ": pages,
              "秒": round(time.perf_counter() - started, 1), "座標": f"幅 {WIDTH_PX} 画素の画像の座標"}
    _write(out, result)
    return result


def machine_read(pdf: str | Path, cache: str | Path | None = None, case_id: str = "案件") -> dict[str, Any]:
    """機械の読み(``app.run``)。一本道の検算の相手。**キャッシュにあれば動かさない。**"""
    pdf = Path(pdf)
    d = case_dir(pdf, cache)
    out = d / "機械の出力.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    import app

    started = time.perf_counter()
    machine = app.run(pdf, case_id=case_id, answers_path=d / "機械の問いの答え.json", build_ledger_stage=False)
    result = {
        "工事項目": [line.as_answer_row(i) for i, line in enumerate(machine.lines, 1)],
        "自動確定": dict(machine.auto_confirmed),
        "出どころ": f"機械の読み(app.run、台帳は飛ばした。{pdf.name})",
        "秒": round(time.perf_counter() - started, 1),
    }
    _write(out, result)
    return result


def _reading_of(payload: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """下書き.json(「読む」→「読み」)でも、{"読み": {...}} でも、{"ページ": [...]} でも受ける。"""
    if "読む" in payload:
        payload = payload["読む"]
    if "読み" in payload:
        return {int(k): v for k, v in payload["読み"].items()}
    from draft.stages import page_entries

    return page_entries(payload)


def misses(pdf: str | Path, reading: Mapping[str, Any], pages: Sequence[int] | None = None) -> dict[str, Any]:
    from draft.stages import _totals, measure_misses

    r = _reading_of(reading)
    targets = sorted(pages or r)
    per_page = measure_misses(Path(pdf), r, targets)
    return {"ページごと": per_page, "合計": _totals(per_page)}


def assemble(draft: Mapping[str, Any], labor: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from draft import stages

    items = draft["理解"]["項目"]
    rows, conflicts = stages.assembly_rows(items)
    return {"内訳の行": rows, "ページで数量が違う": conflicts, "材料表": stages.materials(items),
            "時間": stages.labor(rows, labor), "段階ごとの出力": stages.mode_outputs(rows, items)}


def _load(path: str | None) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "read", "misses"):
        s = sub.add_parser(name)
        s.add_argument("pdf")
        s.add_argument("--cache-dir", default=None, help=f"キャッシュの置き場所(既定は環境変数 {CACHE_ENV}、無ければ ./{DEFAULT_CACHE_DIR})")
        s.add_argument("--out", default=None, help="結果の JSON を書く先(無ければ画面に要約だけ)")
    sub.choices["prepare"].add_argument("--ocr", action="store_true", help="文字の層が空のページを OCR に掛ける")
    sub.choices["read"].add_argument("--case-id", default="案件")
    sub.choices["misses"].add_argument("--reading", required=True, help="読み(下書き.json など)")
    sub.choices["misses"].add_argument("--pages", default=None, help="数えるページ(例 7,8,13)。無ければ読みにある全ページ")
    s = sub.add_parser("assemble")
    s.add_argument("--draft", required=True, help="下書き.json(理解の項目を使う)")
    s.add_argument("--labor", default=None)
    s.add_argument("--out", default=None)
    a = p.parse_args(argv)

    if a.cmd == "prepare":
        result = prepare(a.pdf, a.cache_dir, a.ocr)
        summary = {"ページ": len(result["ページ"]),
                   "語の出どころ": {src: sum(1 for v in result["ページ"].values() if v["語の出どころ"] == src)
                               for src in {v["語の出どころ"] for v in result["ページ"].values()}},
                   "置き場所": str(case_dir(a.pdf, a.cache_dir))}
    elif a.cmd == "read":
        result = machine_read(a.pdf, a.cache_dir, a.case_id)
        summary = {"工事項目": len(result["工事項目"]), "自動確定": result["自動確定"], "置き場所": str(case_dir(a.pdf, a.cache_dir))}
    elif a.cmd == "misses":
        pages = [int(x) for x in a.pages.split(",")] if a.pages else None
        result = misses(a.pdf, _load(a.reading), pages)
        summary = result["合計"]
    else:
        result = assemble(_load(a.draft), _load(a.labor))
        summary = {"内訳の行": len(result["内訳の行"]), "材料表": len(result["材料表"])}
    if a.out:
        _write(Path(a.out), result)
    print(json.dumps(summary, ensure_ascii=False, default=str))
    auto = summary.get("自動確定") if isinstance(summary, dict) else None
    if isinstance(auto, Mapping) and int(auto.get("合計", 0) or 0) > 0:
        return 2  # 自動確定が 1 件でも出たら、呼んだ側が止められるように 0 以外で返す
    return 0


if __name__ == "__main__":
    sys.exit(main())
