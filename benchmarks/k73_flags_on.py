"""K-73 作業1: 保存された `下書き.json` から、K-63 の 6 部品の旗を**全部オン**にした出力を作る。**AI は呼ばない。**

基準は `docs/k73_flags_sum_criteria.md` 2 節(測る前にコミット)。

使い方::

    python -m benchmarks.k73_flags_on --draft 回/下書き.json --pdf 図面.pdf --out 出力フォルダ \
        [--machine-output machine_v4.json] [--legend-lookup 対照表.json] [--knowledge 知識の表.json] [--pages ページの画像フォルダ]

- 保存された「整理」「読む」「理解」「仕上表」「組み立て」をそのまま使い、`draft.run._flag_parts` を全部オンで呼ぶ。
- AI を呼ぶ部品(分かれ道・線引き)は**空の答えのフォルダ**で動かす(指示を書き出すだけ。答えが無いので効かない)。
- 出力 = 元の `下書き.json` に「旗の部品」の欄だけを足したもの。**ほかの欄は変えない**(変わっていないことを確かめて書く)。
- 自動確定が 1 件でも出たら終了コード 2。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

#: 旗オンで足す欄。これ以外の欄は元と同じでなければならない。
FLAG_FIELD = "旗の部品"
AI_PARTS = ("分かれ道を AI に選択肢で聞く", "線引き")
"""AI を呼ぶ部品。答えが無いので効かない(「AI が要るので効かない」)。"""


def _int_keys(table: Mapping[str, Any]) -> dict[Any, Any]:
    return {int(k) if isinstance(k, str) and k.isdigit() else k: v for k, v in table.items()}


def restore(draft: Mapping[str, Any]) -> tuple[dict, dict, dict, dict, dict]:
    """JSON で文字になったページ番号の鍵を数に戻す(一本道の中と同じ形にする)。"""
    org = dict(draft["整理"])
    org["ページ"] = _int_keys(org.get("ページ") or {})
    reading = dict(draft["読む"])
    for key in ("読み", "ページ"):
        if isinstance(reading.get(key), Mapping):
            reading[key] = _int_keys(reading[key])
    return org, reading, dict(draft["理解"]), dict(draft["仕上表"]), dict(draft["組み立て"])


def flags_on(draft: Mapping[str, Any], pdf: Path, pages_dir: Path, *, machine_output: str | None,
             legend: str | None, knowledge: str | None, case_id: str = "P011", parallel: int = 4) -> dict[str, Any]:
    """旗の部品の欄を作って返す。AI は空の答えのフォルダの口だけ(呼ばない)。"""
    from draft import flags as fp
    from draft import stages
    from draft.ai import FolderCaller
    from draft.pages import render
    from draft.run import _flag_parts

    org, reading, understanding, finish, assembly = restore(draft)
    with tempfile.TemporaryDirectory() as empty:
        caller = FolderCaller(Path(empty) / "AIの答え")
        ctx = stages.Context(pdf=pdf, pages=render(pdf, pages_dir), caller=caller, parallel=parallel)
        args = argparse.Namespace(legend_lookup=legend, knowledge=knowledge, no_machine_check=machine_output is None,
                                  machine_output=machine_output, case_id=case_id)
        started = time.perf_counter()
        out = _flag_parts(list(fp.FLAGS), args, ctx, org, reading, understanding, finish, assembly, None)
        out["作り方(K-73)"] = {
            "元": "保存された 下書き.json(整理・読む・理解・仕上表・組み立てをそのまま使った)",
            "AI": f"呼んでいない(空の答えのフォルダ。書き出した指示 {len(caller.pending_written)} 件は捨てた)",
            "AI が要るので効かない部品": list(AI_PARTS),
            "止まった所": ctx.stops,
            "秒": round(time.perf_counter() - started, 1),
        }
    return out


def unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """「旗の部品」以外の欄で、元と違うものの名前。"""
    keys = (set(before) | set(after)) - {FLAG_FIELD}
    return sorted(k for k in keys if json.dumps(before.get(k), ensure_ascii=False, sort_keys=True, default=str)
                  != json.dumps(after.get(k), ensure_ascii=False, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--draft", type=Path, required=True)
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--machine-output", default=None)
    p.add_argument("--legend-lookup", default=None)
    p.add_argument("--knowledge", default=None)
    p.add_argument("--pages", type=Path, default=None, help="ページの画像のフォルダ(既定は --out の中)")
    p.add_argument("--case-id", default="P011")
    a = p.parse_args(argv)
    text = a.draft.read_text(encoding="utf-8")
    draft = json.loads(text)
    work = json.loads(text)  # 部品に渡す写し(元と比べて、ほかの欄が変わっていないことを確かめるため)
    if FLAG_FIELD in draft:
        print(f"元の 下書き.json に既に「{FLAG_FIELD}」があります(旗オフの出力ではない)", file=sys.stderr)
        return 3
    a.out.mkdir(parents=True, exist_ok=True)
    part = flags_on(work, a.pdf, a.pages or (a.out / "ページ"), machine_output=a.machine_output,
                    legend=a.legend_lookup, knowledge=a.knowledge, case_id=a.case_id)
    result = dict(work)
    result[FLAG_FIELD] = json.loads(json.dumps(part, ensure_ascii=False, default=str))
    changed = unchanged(draft, result)
    if changed:
        print(f"旗の部品以外の欄が変わった: {changed}", file=sys.stderr)
        return 4
    (a.out / "下書き.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    auto = (part.get("検算(旗の行を足した)") or {}).get("自動確定")
    print(json.dumps({"出力": str(a.out), "旗の行を足した自動確定": auto,
                      "まとめ": part.get("まとめ"), "止まった所": part["作り方(K-73)"]["止まった所"]},
                     ensure_ascii=False, default=str))
    return 2 if isinstance(auto, int) and auto > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
