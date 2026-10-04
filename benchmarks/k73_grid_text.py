"""K-73 作業 3(b): 機械の罫線を足す条件を「AI の文字の中身が一致する」に厳しくした周の、前 → 後と囮。

基準は `docs/k73_grid_text_criteria.md`(測る前にコミット済み)。前 = K-72 の後(``text_match=False``、位置の証拠)、
後 = 中身の証拠。囮は 7 つ(囮 5 = でたらめに置いた箱を標準の囮にした。囮 6・7 は中身の条件を試す強い囮)。

**4 種類それぞれ、分子(拾えた)と分母(数える・全部)を前後で別々に出す**(「分母から外した」と「拾えた」を見分けるため)。
**新しく AI を呼ばない。出すのは件数と割合だけ**(図面の文字・AI が読んだ文字は出さない)。

    PYTHONPATH=. python -m benchmarks.k73_grid_text --pdf P011_匿名化v4.pdf --run .../full_R1 --out 周_R1.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 囮 7(内容をページの中で入れ替える)の種。
SHUFFLE_SEED = 73
TEXT_KINDS = ("文字", "数字")
SETTINGS = {"前": {"text_match": False}, "後": {"text_match": True}}


def random_boxes_with_content(reading: Mapping[int, Mapping[str, Any]], pdf: Path) -> dict[int, dict[str, Any]]:
    """囮 6: 囮 5 と同じ置き方・同じ種(72)のでたらめな箱に、その元の要素の「内容」をそのまま付けた版。"""
    from benchmarks.k72_readrate_guard import random_boxes

    boxes = random_boxes(reading, pdf)
    out: dict[int, dict[str, Any]] = {}
    for number, entry in boxes.items():
        originals = [e for e in (reading.get(number) or {}).get("要素", []) if e.get("位置")]
        assert len(originals) == len(entry["要素"])
        out[number] = {"要素": [{**box, "内容": src.get("内容")} for box, src in zip(entry["要素"], originals)]}
    return out


def shuffled_content(reading: Mapping[int, Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """囮 7: 位置は本物のまま、「文字」「数字」の要素の内容をページの中ででたらめに並べ替えた版(種 73)。"""
    rng = random.Random(SHUFFLE_SEED)
    out: dict[int, dict[str, Any]] = {}
    for number, entry in sorted(reading.items()):
        elements = [dict(e) for e in (entry or {}).get("要素", []) if e.get("位置")]
        idx = [i for i, e in enumerate(elements) if str(e.get("種類") or "") in TEXT_KINDS]
        texts = [elements[i].get("内容") for i in idx]
        rng.shuffle(texts)
        for i, t in zip(idx, texts):
            elements[i]["内容"] = t
        out[number] = {"要素": elements}
    return out


def fractions(result: Mapping[str, Any]) -> dict[str, Any]:
    """4 種類(+ 点は参考)の分子・分母・割合。"""
    kinds = result["種類ごと(重なりなし)"]
    slices = result["別の切り口(重なる)"]
    ink = result["墨の量で見た読了率"]

    def row(num, den):
        return {"分子(拾えた)": num, "分母": den, "読了率": round(num / den, 4) if den else None}

    text = kinds.get("文字") or {}
    sym = kinds.get("記号") or {}
    table = slices.get("表") or {}
    line = ink.get("線(長さ)") or {}
    dots = kinds.get("点・小さい図形") or {}
    return {
        "文字・数字": row(text.get("拾えた", 0), text.get("数える", 0)),
        "記号": row(sym.get("拾えた", 0), sym.get("数える", 0)),
        "表": {**row(table.get("拾えた", 0), table.get("数える", 0)), "測れないページ": table.get("測れないページ")},
        "線(長さ)": row(line.get("拾えた", 0.0), line.get("全部", 0.0)),
        "点・小さい図形(数、参考。合否に入れない)": row(dots.get("拾えた", 0), dots.get("数える", 0)),
        "合否": result["合否"]["合否"],
        "機械が足した罫線": result.get("機械が読んだ罫線(表)", 0),
    }


def table_shares(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """表ごとの証拠の割合(ページの番号・語の数・割合・足した数だけ)。"""
    rows = []
    for p in result["ページごと"]:
        for note in (p.get("機械が読んだ罫線(表)") or {}).get("表ごと", []):
            rows.append({"ページ": p["ページ"], **{k: v for k, v in note.items() if k != "足さなかった理由"}})
    return rows


def gains(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for name in ("文字・数字", "記号", "表", "線(長さ)"):
        b, a = before[name], after[name]
        out[name] = {
            "上がり幅": None if a["読了率"] is None or b["読了率"] is None else round(a["読了率"] - b["読了率"], 4),
            "分子の差": round(a["分子(拾えた)"] - b["分子(拾えた)"], 1),
            "分母の差": round(a["分母"] - b["分母"], 1),
        }
    return out


def measure(pdf: Path, reading: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    from benchmarks.k71_readrate_round import table_boxes
    from benchmarks.k72_readrate_guard import candidate_table_boxes, random_boxes
    from benchmarks.measure_readthrough import big_boxes, scatter
    from draft.readthrough import readthrough

    pages = sorted(reading)

    def both(rd: Mapping[int, Mapping[str, Any]], with_tables: bool = False) -> dict[str, Any]:
        got: dict[str, Any] = {}
        for label, kw in SETTINGS.items():
            result = readthrough(pdf, rd, pages, with_unread=False, **kw)
            got[label] = fractions(result)
            if with_tables:
                got[label]["表ごとの証拠"] = table_shares(result)
        got["前→後"] = gains(got["前"], got["後"])
        return got

    out: dict[str, Any] = {"本物": both(reading, with_tables=True)}
    decoys = {"囮1 でたらめに置き直した版": scatter(reading, pdf),
              "囮2 ページを覆う大きい箱で囲んだ版": big_boxes(reading, pdf),
              "囮3 守りを通った表の四角を表の箱で囲んだだけの版": table_boxes(reading, pdf),
              "囮4 守りの前の候補の表を表の箱で囲んだだけの版": candidate_table_boxes(reading, pdf),
              "囮5 でたらめに置いた箱(標準)": random_boxes(reading, pdf),
              "囮6 でたらめに置いた箱に本当の内容を付けた版": random_boxes_with_content(reading, pdf),
              "囮7 位置は本物・内容をページの中で入れ替えた版": shuffled_content(reading)}
    for name, fake in decoys.items():
        out[name] = both(fake)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-73 作業 3(b) 機械の罫線の中身の証拠の前後と囮")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    a = parser.parse_args(argv)
    from benchmarks.measure_readthrough import load_reading

    result = {"回": a.run.name, **measure(a.pdf, load_reading(a.run))}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
