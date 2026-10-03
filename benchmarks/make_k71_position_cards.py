"""K-71 作業 2: 位置の重なりで対応づけた割れた値のカード 10 枚を、PDF と HTML にする。

基準は `docs/k71_position_matching_criteria.md`。作り方(PDF・HTML・確かめ)は K-70 作業 3 と同じ
(`benchmarks.make_k70_split_cards`)。カードの作り方だけが違う(位置の対応づけ・単位の同値)。

    PYTHONPATH=. python -m benchmarks.make_k71_position_cards \\
      --pdf <匿名化 v4 の PDF> --runs <...>/full_R1 <...>/full_R2 <...>/full_R3 --out-dir <共有フォルダの K-71>

**切り抜きの画像と名前が入るので、出したファイルは共有フォルダにだけ置く**(リポジトリには入れない)。
**AI は呼ばない。正解は開かない。**
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks import make_k70_split_cards as k70
from benchmarks.measure_k71_position_matching import build
from draft import split_cards

STEM = "割れた値のカード10枚_位置で対応"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-71 作業 2 位置で対応づけた割れた値のカード(PDF と HTML)")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--check-out", type=Path, default=None)
    a = p.parse_args(argv)
    real = build(a.runs, a.pdf)
    shown = k70.with_images(split_cards.numbered(real["ordered"], a.count), a.pdf)
    pdf_out = a.out_dir / f"{STEM}.pdf"
    html_out = a.out_dir / f"{STEM}.html"
    info = k70.render_pdf(shown, pdf_out, title="割れた値のカード(位置で対応)")
    k70.render_html(shown, html_out, title="割れた値のカード(位置で対応)")
    check = k70.check_pdf(pdf_out, shown)
    key_out = a.out_dir / f"{STEM}_番号と鍵.json"
    key_out.write_text(json.dumps({"カード": [{"番号": c["番号"], "鍵": c["鍵"], "選択肢の数": len(c["選択肢"])}
                                            for c in shown]}, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.check_out:
        a.check_out.write_text(json.dumps({"作った": info, "確かめ": check}, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(json.dumps({"PDF": str(pdf_out), "HTML": str(html_out), "作った": info, "確かめ": check},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
