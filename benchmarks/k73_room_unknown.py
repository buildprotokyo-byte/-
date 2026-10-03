"""K-73 作業 4: 図面に室の寸法が無い行の「分からない」と室ごとの 1 問を、K-61 の 3 回の出力で数える(AI は呼ばない)。

基準は `docs/k73_room_unknown_criteria.md`(測る前にコミットした)。**寸法・数量は 1 つも作らない。正解は開かない。**
リポジトリに書くのは件数・割合・室の番号・ページの種類だけ。室ごとの探した結果の写しと携帯の PDF は共有フォルダにだけ置く。

使い方::

    PYTHONPATH=. python -m benchmarks.k73_room_unknown \\
      --pdf <匿名化 v4 の PDF> --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --k37-rooms <reports/K-37/p011_rooms.json> --sides <reports/K-72/作業C/k37_室の辺の写し.json> \\
      --out docs/k73_room_unknown_result.json --shared <reports/K-73/作業4>
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from draft import room_unknown as ru

K72_C = {"full_R1": 131, "full_R2": 117, "full_R3": 100}


def summarize(name: str, sec: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    rows = sec["行"]
    complete = sum(1 for r in rows if r["分からない"].startswith(ru.REASON) and r["数量"] is None
                   and any(x["図面に無い辺"] for x in r["室"])
                   and all(set(x["探したページ"]) == set(ru.SEARCH_KINDS) for x in r["室"] if x["図面に無い辺"]))
    verdicts = {k: dict(Counter(room["探したページ"][k]["結果"] for room in sec["室"])) for k in ru.SEARCH_KINDS}
    qs = sec["問いの候補"]
    return {
        "C の行": sec["C の行"],
        "K-72 の C と同じ": sec["C の行"] == K72_C.get(name),
        "理由と 4 つの種類が付いた行": complete,
        "理由と 4 つの種類が付いた割合": round(complete / len(rows), 4) if rows else None,
        "数量が None のままの行": sum(1 for r in rows if r["数量"] is None),
        "天井高も無い行": sum(1 for r in rows if ru.NO_CEILING in r["分からない"]),
        "上限の側(開口を引かない)の印": sum(1 for r in rows if r.get("上限の側")),
        "問いを出す室": len(sec["室"]),
        "数字の入力を認める室": [r["室"] for r in sec["室"] if r["数字の入力を認める"]],
        "数字の入力を認めない室": [r["室"] for r in sec["室"] if not r["数字の入力を認める"]],
        "種類ごとの結果(室の数)": verdicts,
        "室ごとの結果": {str(r["室"]): {k: r["探したページ"][k]["結果"] for k in ru.SEARCH_KINDS} for r in sec["室"]},
        "段階ごとの問いの数": {lvl: len(v) for lvl, v in sec["段階ごと"].items()},
        "通常で聞く室": [q["室"] for q in sec["段階ごと"]["通常"]],
        "メーター": {str(q["室"]): {
            "この答えで確定する行数": q["メーター"]["この答えで確定する行数"],
            "ほかの室の答えも要る行数": q["メーター"]["ほかの室の答えも要る行数"],
            "この室の C の行": q["メーター"]["この室の C の行"],
            "確定しない理由": q["メーター"]["確定しない理由"],
            "金額の割合": q["メーター"]["金額の割合"],
            "行数の割合(金額ではない)": q["メーター"]["行数の割合(金額ではない)"],
            "回答時間の見積(秒)": q["メーター"]["回答時間の見積(秒)"],
            "数字の入力欄": q["数字の入力"], "選択肢の数": len(q["選択肢"]),
        } for q in qs},
        "答えが戻ったとき(構造)": sec["答えが戻ったとき(構造)"],
        "書き出した寸法": sec["書き出した寸法"],
        "自動確定(出力のまま。この周は確定させない)": draft["まとめ"].get("自動確定"),
        "理解の項目": len(draft["理解"]["項目"]),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-73 作業 4(AI を呼ばない)")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--k37-rooms", type=Path, required=True)
    p.add_argument("--sides", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--shared", type=Path, required=True)
    a = p.parse_args(argv)
    k37 = json.loads(a.k37_rooms.read_text(encoding="utf-8"))
    sides = json.loads(a.sides.read_text(encoding="utf-8"))
    drawing = ru.Drawing(a.pdf, sides.get("基準の寸法"))
    runs, details = {}, {}
    for d in a.runs:
        draft = json.loads((d / "下書き.json").read_text(encoding="utf-8"))
        sec = ru.run(a.pdf, draft["整理"], draft["理解"], k37, sides, drawing=drawing)
        runs[d.name] = summarize(d.name, sec, draft)
        details[d.name] = sec
        if d.name == a.runs[0].name:
            pdf = ru.card_pdf(sec["段階ごと"]["精密"], a.shared / f"室の寸法の問い_{d.name}_精密.pdf")
    a.shared.mkdir(parents=True, exist_ok=True)
    (a.shared / "k73_作業4_室ごとの探した結果.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    result = {
        "材料": "K-61 の P011 全部あり版 3 回の下書き・匿名化 v4 の PDF・K-37 の室・K-72 の辺の写し",
        "AI を呼んだ回数": 0,
        "書き出した寸法・数量": 0,
        "回ごと": runs,
        "携帯で答える PDF": {"ファイル": pdf.name, "ページ": len(details[a.runs[0].name]["段階ごと"]["精密"]),
                         "大きさ(バイト)": pdf.stat().st_size},
    }
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
