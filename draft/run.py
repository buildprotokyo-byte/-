"""図面一式を渡すと、確認できる下書きが出る(K-61)。

使い方::

    python -m draft.run 図面.pdf --out 出力フォルダ [--answers-dir AIの答えのフォルダ]
        [--mode 概算|通常|精密] [--answers 人の答え.json] [--cost-table 原価表.json]
        [--labor 歩掛.json] [--machine-output 機械の出力.json] [--parallel 8] [--case-id P011]

出すもの(``--out`` の中):
- ``下書き.json`` … 全部の段の出力(止まった所・時間・AI を呼んだ回数と費用の概算を含む)
- ``確認画面.html`` … 左に図面、右に一覧。ブラウザで開く(1 ファイル)
- ``待っている問い/`` … 鍵が無くて呼べなかった AI の指示(答えを置けば次の通しで使う。``--answers-dir`` の中)

**止まらない。** 段が失敗しても「止まった所」に書いて次の段へ進む。**自動確定が 1 件でも出たら、終了コード 2 で止める。**
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from draft import review_html, stages
from draft.ai import DEFAULT_PARALLEL, MODEL_ENV, DEFAULT_MODEL, make_caller
from draft.pages import render

#: いまの部品のうち、この一本道につないでいないもの(理由つき)。
NOT_CONNECTED = [
    ("迷った所だけ選択肢で AI に聞く(K-57 周 2 の分かれ道)",
     "分かれ道を見つける部品がリポジトリに無い(周 2 は作業用の書き捨て)。周 2 では寸法の分かれ道 57 件のうち 52 件で "
     "AI は機械と同じ答えで、聞く値打ちがあったのは機械の答えが無い 5 件だけだった"),
    ("記号を室ごとに数える(K-55 positioned_symbol_count)",
     "周 1 の採点(パソコン側)が済んでいない。ここでは理解の段で AI が記号を数え、機械が要素の数と照らす(検算)"),
    ("線引き(K-46 line_judge)", "理解の段で AI が項目にするかどうかを決めるので、二重に掛けない"),
    ("凡例の対照表(legend_lookup)", "対照表はリポジトリの外にある。理解の段に凡例のページの画像を渡している"),
    ("知識の表(knowledge/table.py)", "表の中身がまだ入っていない(K-17: 知識は「まだ入れていない」)"),
    ("キラークエスチョン(killer_question)",
     "効きの大きさで問いを選ぶ部品は群合計の制約が要り、この一本道の項目には制約が無い。質問は原本との違い・"
     "読めなかった所・決められなかった所から作り、科目の順に並べた"),
    ("縮尺で長さを測る", "測った長さを室と工事に結ぶ段がまだ無い(K-59 で後に回した)"),
]


def _load_json(path: str | None) -> Any:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _guarded(ctx: stages.Context, name: str, fn: Callable[[], Any], fallback: Any) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001  段の失敗で止めない
        ctx.stop(name, f"{type(exc).__name__}: {exc}")
        ctx.state.setdefault("例外", []).append({"段": name, "traceback": traceback.format_exc(limit=5)})
        return fallback
    finally:
        ctx.timings[f"段: {name}"] = round(time.perf_counter() - started, 1)


def machine_reading(pdf: Path, case_id: str, work: Path) -> dict[str, Any]:
    """機械の読み(app.run の 7 つの段、台帳は飛ばす)。**AI の段と並べて最初から動かす。**"""
    import app

    started = time.perf_counter()
    machine = app.run(pdf, case_id=case_id, answers_path=work / "機械の問いの答え.json", build_ledger_stage=False)
    return {
        "工事項目": [line.as_answer_row(i) for i, line in enumerate(machine.lines, 1)],
        "自動確定": dict(machine.auto_confirmed),
        "出どころ": "この場で機械が読んだ(app.run、AI の段と並べて動かした)",
        "秒": round(time.perf_counter() - started, 1),
    }


def machine_check(rows: Sequence[Mapping[str, Any]], machine_output: str | None, machine: Mapping[str, Any] | None,
                  case_id: str, out_path: Path | None = None) -> dict[str, Any]:
    """内訳の行を本番の入口(app.run_ai_reading)に通し、機械の検算と自動確定の数を得る。"""
    import app
    from intake.ai_reading import parse_ai_reading

    reading = parse_ai_reading(
        {"行": [{"工事": r["工事項目"], "場所": r["場所"], "数量": r["数量"], "単位": r["単位"],
                  "式": r.get("メモ", ""), "根拠": ",".join(r["項目"])} for r in rows]},
        reader="K-61 一本道の理解の段",
    )
    machine_rows, machine_auto, source = None, None, "機械の検算を動かしていない"
    if machine_output:
        payload = json.loads(Path(machine_output).read_text(encoding="utf-8"))
        machine_rows = list(payload.get("工事項目", []))
        machine_auto = {k: int(v) for k, v in (payload.get("自動確定") or {}).items() if k != "合計"}
        source = f"前に出した機械の出力: {Path(machine_output).name}"
    elif machine is not None:
        machine_rows = list(machine["工事項目"])
        machine_auto = {k: int(v) for k, v in machine["自動確定"].items() if k != "合計"}
        source = machine["出どころ"]
    result = app.run_ai_reading(reading, case_id=case_id, machine_rows=machine_rows, machine_source=source,
                                machine_auto_confirmed=machine_auto)
    reasons = [line.extra.get("要確認の理由", []) for line in result.lines]
    if out_path is not None:
        # パソコン側の採点(K-49・K-57 と同じ道具)がそのまま読める形でも書く。
        out_path.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {
        "自動確定": result.auto_confirmed_total,
        "自動確定の内訳": result.auto_confirmed,
        "機械の検算": {k: v for k, v in (result.machine_check or {}).items() if k != "引けなかった機械の行"},
        "行ごとの要確認": reasons,
    }


#: 通読の 1 回に渡すページ数(K-61 の判断 1「分ける」)。P011 は 1 ページの読みが平均 1.6〜2.1 万字あり、
#: 34 ページを 1 回で読ませると 1 回の出力の上限を超える。3 ページなら上限の内に収まる見込み(パソコン側で確かめる)。
DEFAULT_PASS1_BATCH = 3


def run(argv: Sequence[str] | None = None, client: Any = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.add_argument("--answers-dir", default=None, help="AI の答えと待っている問いを置くフォルダ(既定は --out の中)")
    p.add_argument("--case-id", default="案件")
    p.add_argument("--mode", default="通常", choices=tuple(stages.MODES))
    p.add_argument("--answers", default=None, help="人の答え {回答の鍵: 選んだ選択肢の文字}")
    p.add_argument("--cost-table", default=None, help="原価表 {品番または工事: 単価}(無ければ未取得)")
    p.add_argument("--labor", default=None, help="歩掛 {工事項目: {1人1日あたり, 日当}}(無ければ未入力)")
    p.add_argument("--machine-output", default=None, help="前に出した機械の出力(無ければこの場で機械を動かす)")
    p.add_argument("--no-machine-check", action="store_true", help="機械の検算を飛ばす(自動確定の数も未取得になる)")
    p.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    p.add_argument("--pass1-batch", type=int, default=DEFAULT_PASS1_BATCH,
                   help=f"通読の 1 回に渡すページ数(既定 {DEFAULT_PASS1_BATCH}。K-61 の判断 1 で分ける。0 は全ページを 1 回で)")
    a = p.parse_args(argv)

    started = time.perf_counter()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    answers_dir = Path(a.answers_dir) if a.answers_dir else out / "AIの答え"
    caller = make_caller(answers_dir, client=client)
    import os

    model = getattr(caller, "model", None) or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    t = time.perf_counter()
    pdf = Path(a.pdf)
    page_infos = render(pdf, out / "ページ")
    ctx = stages.Context(pdf=pdf, pages=page_infos, caller=caller, parallel=a.parallel, pass1_batch=a.pass1_batch)
    ctx.timings["段: ページを画像にする"] = round(time.perf_counter() - t, 1)
    machine_future = None
    pool = None
    if not a.no_machine_check and not a.machine_output:
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        machine_future = pool.submit(machine_reading, pdf, a.case_id, out)
    cost_table = _load_json(a.cost_table)
    human = _load_json(a.answers) or {}

    org = _guarded(ctx, "整理", lambda: stages.organize(ctx),
                   {"出どころ": stages.UNKNOWN, "ページ": {}, "読む順": [p.number for p in page_infos]})
    for pinfo in page_infos:
        org["ページ"].setdefault(pinfo.number, {"種類": stages.UNKNOWN, "描かれているもの": "", "担当": "AI", "理由": ""})
    reading = _guarded(ctx, "読む", lambda: stages.read(ctx, org),
                       {"読み": {}, "ページ": {}, "通読の落ち": stages.UNKNOWN, "読み直した後の落ち": stages.UNKNOWN,
                        "読み直したページ": [], "読み直しが未取得のページ": []})
    understanding = _guarded(ctx, "理解", lambda: stages.understand(ctx, org, reading),
                             {"項目": [], "決められなかった要素": [], "未取得のページ": []})
    finish = _guarded(ctx, "仕上表", lambda: stages.finish_schedule(ctx, org, understanding),
                      {"原本": stages.UNKNOWN, "原本のページ": [], "原本の行": [], "原本の読めなかった所": [],
                       "ひな型": [], "照らし合わせ": []})
    round_trip = None
    if human:
        round_trip = _guarded(ctx, "答えを戻す", lambda: stages.apply_answers(understanding, finish, human), None)
    qs = _guarded(ctx, "質問", lambda: stages.questions(understanding, finish, reading, cost_table, human,
                                                                (round_trip or {}).get("戻した鍵")),
                  {"段階ごと": {}, "候補の数": {}, "並べ方": stages.UNKNOWN})

    def assemble() -> dict[str, Any]:
        from estimating.breakdown import build_breakdown

        rows, conflicts = stages.assembly_rows(understanding["項目"])
        return {
            "内訳の行": rows,
            "内訳": build_breakdown(rows).as_dict(),
            "ページで数量が違う": conflicts,
            "材料表": stages.materials(understanding["項目"]),
            "時間": stages.labor(rows, _load_json(a.labor)),
            "段階ごとの出力": stages.mode_outputs(rows, understanding["項目"]),
        }

    assembly = _guarded(ctx, "組み立て", assemble, {"内訳の行": [], "内訳": {}, "材料表": [], "時間": []})
    check = None
    if not a.no_machine_check:
        def checked() -> dict[str, Any]:
            machine = machine_future.result() if machine_future is not None else None
            if machine is not None:
                ctx.timings["機械の読み(AI と並べて動かした)"] = machine["秒"]
            return machine_check(assembly["内訳の行"], a.machine_output, machine, a.case_id, out / "本番の形.json")

        check = _guarded(ctx, "機械の検算", checked, None)
    if pool is not None:
        pool.shutdown(wait=False)
    auto = check["自動確定"] if check else stages.UNKNOWN

    warn = []
    flagged = [n for n, v in reading["ページ"].items() if v.get("読み落としの可能性が高い")]
    if flagged:
        warn.append(f"読み落としの可能性が高いページ {sorted(flagged)}")
    if understanding["未取得のページ"]:
        warn.append(f"理解が未取得のページ {understanding['未取得のページ']}")
    if finish["原本"] != "原本あり":
        warn.append(f"仕上表: {finish['原本']}(ひな型だけ)")
    for np_ in finish.get("本来の仕上表ではないページ", []):
        warn.append(f"仕上表の原本の {np_['ページ']} ページは本来の仕上表ではない({np_['図面']})")
    warn.append("数量・照らし合わせは採点していない(正解を使う測定はパソコン側)")
    items = understanding["項目"]
    result = {
        "まとめ": {
            "案件": a.case_id,
            "段階": a.mode,
            "段階の目安": stages.MODES[a.mode],
            "自動確定": auto,
            "原価表": "あり" if cost_table else stages.UNKNOWN,
            "概要の別紙": stages.UNKNOWN,
            "精度の注意": warn,
            "項目の数": len(items),
            "数量のある項目": sum(1 for it in items if it["数量"] is not None),
            "状態ごと": {s: sum(1 for it in items if it["状態"] == s) for s in stages.STATES},
            "確度ごと": {c: sum(1 for it in items if it["確度"] == c) for c in stages.CONFIDENCE},
            "根拠の種類ごと": {b: sum(1 for it in items if it["根拠の種類"] == b) for b in stages.BASIS_KINDS},
            "検算で食い違った項目": sum(1 for it in items if it["検算"]),
        },
        "止まった所": ctx.stops,
        "段の中の例外": ctx.state.get("例外", []),
        "整理": org,
        "読む": reading,
        "理解": understanding,
        "仕上表": finish,
        "質問": qs,
        "答えの往復": round_trip,
        "組み立て": assembly,
        "機械の検算": check,
        "つなげなかった部品": [{"部品": n, "理由": w} for n, w in NOT_CONNECTED],
        "時間(秒)": ctx.timings,
        "AI を呼んだ記録": {"まとめ": caller.summary(model), "1回ずつ": [r.as_dict() for r in caller.records]},
        "並列数の上限": a.parallel,
    }
    ctx.timings["通し全体(壁時計)"] = round(time.perf_counter() - started, 1)
    (out / "下書き.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    try:
        review_html.build(json.loads(json.dumps(result, ensure_ascii=False, default=str)),
                          {pi.number: pi.image for pi in page_infos}, out / "確認画面.html")
    except Exception as exc:  # noqa: BLE001
        ctx.stop("確認画面", f"{type(exc).__name__}: {exc}")
        (out / "下書き.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    caller.retire_stale()
    pending = sorted((answers_dir / "待っている問い").glob("*/指示.md")) if (answers_dir / "待っている問い").exists() else []
    print(json.dumps({"出力": str(out), "自動確定": auto, "止まった所": len(ctx.stops), "待っている問い": len(pending),
                      "AI": caller.summary(model)["答えの出どころ"]}, ensure_ascii=False))
    return 2 if isinstance(auto, int) and auto > 0 else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
