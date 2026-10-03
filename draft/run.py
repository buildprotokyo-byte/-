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

旗(K-63、既定はすべてオフ。オフなら出力は今までと同じ)::

    --with-symbol-count  --with-scale-length  --with-legend-lookup [--legend-lookup 対照表.json]
    --with-branch-questions  --with-line-judge  --with-knowledge [--knowledge 知識の表.json]
    --with-all(キラークエスチョンは旗の裏でもつないでいない。draft/flags.py の NOT_FLAGGED)

旗オンの部品は ``下書き.json`` の「旗の部品」の欄にだけ書く(理解・組み立ての数量は書き換えない)。
部品が足した行は組み立ての行と一緒に機械の検算へもう一度通し、自動確定が出たら終了コード 2 で止める。
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


SOURCE_PRESENT, SOURCE_ABSENT = "あり", "なし"


def source_presence(org: Mapping[str, Any], finish: Mapping[str, Any], cost_table: Any, stage: int = 3) -> dict[str, str]:
    """資料の有無(K-68 C 周 1)。**分からないものは「なし」と言い切らず「未取得」と書く。**

    - 仕上表: 原本ありなら「あり」。原本のページがあって書き写しが無ければ「未取得」。ページが無いとき、整理が AI なら「なし」、
      整理が未取得(文字の層の語で探しただけ)なら「未取得」。段階 1 は仕上表の段を動かさないので「未取得」。
    - 仕様書: 整理の段(AI)の種類に「仕様書」のページがあれば「あり」、無ければ「なし」。整理が未取得なら「未取得」。
    - 原価表: 渡されれば「あり」、無ければ「未取得」(今までの「原価表: 未取得」と同じ)。
    """
    unknown = stages.UNKNOWN
    from_ai = org.get("出どころ") == "AI"
    status = finish.get("原本")
    if stage < 2:
        finish_state = f"{unknown}(段階 1 なので仕上表の段を動かしていない)"
    elif status == "原本あり":
        finish_state = SOURCE_PRESENT
    elif finish.get("原本のページ"):
        finish_state = f"{unknown}(原本のページはあるが書き写しが未取得)"
    else:
        finish_state = SOURCE_ABSENT if from_ai else f"{unknown}(整理が未取得。文字の層の語では見つからなかった)"
    if from_ai:
        spec_state = SOURCE_PRESENT if stages.pages_of_kind(org, "仕様書") else SOURCE_ABSENT
    else:
        spec_state = f"{unknown}(整理が未取得)"
    return {"仕上表": finish_state, "仕様書": spec_state, "原価表": SOURCE_PRESENT if cost_table else unknown}


#: 通読の 1 回に渡すページ数(K-61 の判断 1「分ける」)。P011 は 1 ページの読みが平均 1.6〜2.1 万字あり、
#: 34 ページを 1 回で読ませると 1 回の出力の上限を超える。3 ページなら上限の内に収まる見込み(パソコン側で確かめる)。
DEFAULT_PASS1_BATCH = 3

#: 旗の部品の表のパスを渡す環境変数(K-63)。表はリポジトリの外に置く。
LEGEND_ENV = "DRAFT_LEGEND_LOOKUP"
KNOWLEDGE_ENV = "DRAFT_KNOWLEDGE"


def run(argv: Sequence[str] | None = None, client: Any = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.add_argument("--answers-dir", default=None, help="AI の答えと待っている問いを置くフォルダ(既定は --out の中)")
    p.add_argument("--case-id", default="案件")
    p.add_argument("--mode", default="通常", choices=tuple(stages.MODES))
    p.add_argument("--answers", default=None, help="人の答え {回答の鍵: 選んだ選択肢の文字}")
    p.add_argument("--cost-table", default=None, help="原価表(JSON {品番または工事: 単価} か行の並び、または CSV: 工事・品番・単位・単価・数量)。"
                   "質問の並べ方と内訳との比べにだけ使い、正解にはしない(無ければ未取得)")
    p.add_argument("--labor", default=None, help="歩掛 {工事項目: {1人1日あたり, 日当}}(無ければ未入力)")
    p.add_argument("--machine-output", default=None, help="前に出した機械の出力(無ければこの場で機械を動かす)")
    p.add_argument("--no-machine-check", action="store_true", help="機械の検算を飛ばす(自動確定の数も未取得になる)")
    p.add_argument("--cache-dir", default=None,
                   help="機械の読みを置いて使い回す場所(既定は環境変数 DRAFT_CACHE_DIR。どちらも無ければ使い回さない)")
    p.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    p.add_argument("--pass1-batch", type=int, default=DEFAULT_PASS1_BATCH,
                   help=f"通読の 1 回に渡すページ数(既定 {DEFAULT_PASS1_BATCH}。K-61 の判断 1 で分ける。0 は全ページを 1 回で)")
    p.add_argument("--text-instead-of-image", action="store_true",
                   help="表・仕様書のページで文字の層があれば、画像を送らず位置つきの文字で読ませる(K-62 の手段 a、未採用)")
    p.add_argument("--stage", type=int, default=3, choices=(1, 2, 3),
                   help="K-67 5 節。1 台帳(読了率と未読マップまで。理解・組み立ての AI を呼ばない)/ "
                        "2 概略書(+ 理解・仕上表・工事チェック表・質問)/ 3 下書き(全部。既定)")
    p.add_argument("--design", default="V1", choices=("V1", "V2", "V3"),
                   help="読みの設計(K-63)。V1 は今まで通り(既定)。V2 は目的から探す型、V3 は閉じた語彙型")
    p.add_argument("--vocab", default=None,
                   help="V3 の語彙のファイル(既定は環境変数 DRAFT_VOCAB、無ければ draft/vocab/default.json)")
    p.add_argument("--batch", action="store_true",
                   help="評価用の回だけ: 段ごとの呼び出しをまとめて送る(即時でない処理方式、半額。結果は最長 24 時間後)")
    p.add_argument("--batch-poll-seconds", type=float, default=30.0, help="まとめて送ったものの終わりを見に行く間隔(秒)")
    from draft import flags as flag_parts

    for part, flag in flag_parts.FLAGS.items():
        p.add_argument(flag, action="store_true", help=f"旗(既定はオフ): {part}をつなぐ(K-63)。出力は「旗の部品」の欄だけ")
    p.add_argument("--with-all", action="store_true", help="旗を全部オンにする(K-63 の比べる回)")
    p.add_argument("--ocr", default=None,
                   help="OCR の結果の JSON(K-64。文字の層が無いページだけ文字の層の代わりに使う。既定は環境変数 DRAFT_OCR)")
    p.add_argument("--with-page-confidence", action="store_true",
                   help="旗(既定はオフ、K-64): 読めた割合が基準未満のページでは確度「高」を出さない(理解の確度を書き換える)")
    p.add_argument("--legend-lookup", default=None,
                   help=f"凡例の対照表の JSON(--with-legend-lookup で使う。既定は環境変数 {LEGEND_ENV})")
    p.add_argument("--knowledge", default=None,
                   help=f"知識の表の JSON(--with-knowledge で使う。既定は環境変数 {KNOWLEDGE_ENV})")
    a = p.parse_args(argv)
    on = [part for part, flag in flag_parts.FLAGS.items() if a.with_all or getattr(a, flag_parts.dest(flag))]

    started = time.perf_counter()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    answers_dir = Path(a.answers_dir) if a.answers_dir else out / "AIの答え"
    caller = make_caller(answers_dir, client=client, batch=a.batch, poll_seconds=a.batch_poll_seconds)
    import os

    model = getattr(caller, "model", None) or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    t = time.perf_counter()
    pdf = Path(a.pdf)
    page_infos = render(pdf, out / "ページ")
    ctx = stages.Context(pdf=pdf, pages=page_infos, caller=caller, parallel=a.parallel, pass1_batch=a.pass1_batch)
    ctx.timings["段: ページを画像にする"] = round(time.perf_counter() - t, 1)
    ocr_info = None
    ocr_path = a.ocr or os.environ.get("DRAFT_OCR")
    if ocr_path:
        from draft import ocr as ocr_part

        def take_ocr() -> dict[str, Any]:
            loaded = ocr_part.load_ocr(ocr_path)
            info = ocr_part.apply_ocr(page_infos, loaded)
            ctx.ocr = {n: loaded[n] for n in info["OCR を使ったページ"]}
            return {"ファイル": Path(ocr_path).name, **info}

        ocr_info = _guarded(ctx, "OCR の結果を受け取る", take_ocr, None)
    machine_future = None
    pool = None
    if a.stage < 3:
        # **段階 1・2 は組み立てまで行かないので、機械の検算を動かさない。**費用を段ごとに分けるため。
        a.no_machine_check = True
    if not a.no_machine_check and not a.machine_output:
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        import os as _os

        from draft import machine as machine_part

        if a.cache_dir or _os.environ.get(machine_part.CACHE_ENV):
            machine_future = pool.submit(machine_part.machine_read, pdf, a.cache_dir, a.case_id)
        else:
            machine_future = pool.submit(machine_reading, pdf, a.case_id, out)
    from draft.cost_table import compare as compare_cost, load_cost_table

    cost_table = load_cost_table(a.cost_table)
    human = _load_json(a.answers) or {}

    org = _guarded(ctx, "整理", lambda: stages.organize(ctx),
                   {"出どころ": stages.UNKNOWN, "ページ": {}, "読む順": [p.number for p in page_infos]})
    for pinfo in page_infos:
        org["ページ"].setdefault(pinfo.number, {"種類": stages.UNKNOWN, "描かれているもの": "", "担当": "AI", "理由": ""})
    if a.text_instead_of_image:
        ctx.text_only = stages.text_only_pages(ctx, org)
    empty_reading = {"読み": {}, "ページ": {}, "通読の落ち": stages.UNKNOWN, "読み直した後の落ち": stages.UNKNOWN,
                     "読み直したページ": [], "読み直しが未取得のページ": []}
    empty_understanding = {"項目": [], "決められなかった要素": [], "未取得のページ": []}
    design_info = None
    transcribed = None
    if a.design == "V1":
        reading = _guarded(ctx, "読む", lambda: stages.read(ctx, org), empty_reading)
        if a.stage >= 2:
            understanding = _guarded(ctx, "理解", lambda: stages.understand(ctx, org, reading), empty_understanding)
        else:
            understanding = dict(empty_understanding, 段階="段階 1 なので理解の AI を呼んでいない")
    else:
        from draft import designs

        if a.design == "V2":
            fn = lambda: designs.v2_read_understand(ctx, org)  # noqa: E731
        else:
            transcribed = _guarded(ctx, "仕上表の原本", lambda: stages.finish_original(ctx, org), None)
            vocab = designs.load_vocab(a.vocab)
            fn = lambda: designs.v3_read_understand(ctx, org, transcribed or {}, vocab)  # noqa: E731
        design_info, reading, understanding = _guarded(ctx, f"読みと理解({a.design})", fn,
                                                       (None, empty_reading, empty_understanding))
    page_conf = None
    if a.with_page_confidence:
        page_conf = _guarded(ctx, "確度に読めた割合", lambda: stages.cap_by_page_readability(understanding["項目"], reading),
                             None)
    empty_finish = {"原本": stages.UNKNOWN, "原本のページ": [], "原本の行": [], "原本の読めなかった所": [],
                    "ひな型": [], "照らし合わせ": []}
    finish = (
        _guarded(ctx, "仕上表", lambda: stages.finish_schedule(ctx, org, understanding, transcribed), empty_finish)
        if a.stage >= 2 else dict(empty_finish, 段階="段階 1 なので仕上表の段を動かしていない")
    )
    round_trip = None
    if human:
        round_trip = _guarded(ctx, "答えを戻す", lambda: stages.apply_answers(understanding, finish, human), None)
    empty_questions = {"段階ごと": {}, "候補の数": {}, "並べ方": stages.UNKNOWN}
    qs = (
        _guarded(ctx, "質問", lambda: stages.questions(understanding, finish, reading, cost_table, human,
                                                       (round_trip or {}).get("戻した鍵")), empty_questions)
        if a.stage >= 2 else dict(empty_questions, 段階="段階 1 なので質問の段を動かしていない")
    )

    def assemble() -> dict[str, Any]:
        from estimating.breakdown import build_breakdown

        rows, conflicts = stages.assembly_rows(understanding["項目"])
        return {
            "内訳の行": rows,
            "内訳": build_breakdown(rows).as_dict(),
            "ページで数量が違う": conflicts,
            "材料表": stages.materials(understanding["項目"]),
            "時間": stages.labor(rows, _load_json(a.labor)),
            "段階ごとの出力": stages.mode_outputs(rows, understanding["項目"],
                                                stages.big_kamoku(understanding["項目"], cost_table)),
            **({"原価表との比べ": compare_cost(rows, cost_table)} if cost_table else {}),
            **({"外した行": [{"項目": it["id"], "工事": it["工事"], "場所": it["場所"], "数量": it["数量"],
                             "理由": it["外す"]} for it in understanding["項目"] if it.get("外す")]}
               if any(it.get("外す") for it in understanding["項目"]) else {}),
        }

    empty_assembly = {"内訳の行": [], "内訳": {}, "材料表": [], "時間": []}
    assembly = (
        _guarded(ctx, "組み立て", assemble, empty_assembly) if a.stage >= 3
        else dict(empty_assembly, 段階=f"段階 {a.stage} なので組み立ての段を動かしていない")
    )
    check = None
    machine_seen: list[Any] = []
    if not a.no_machine_check:
        def checked() -> dict[str, Any]:
            machine = machine_future.result() if machine_future is not None else None
            machine_seen.append(machine)
            if machine is not None:
                ctx.timings["機械の読み(AI と並べて動かした)"] = machine["秒"]
            return machine_check(assembly["内訳の行"], a.machine_output, machine, a.case_id, out / "本番の形.json")

        check = _guarded(ctx, "機械の検算", checked, None)
    flag_out = None
    if on:
        flag_out = _flag_parts(on, a, ctx, org, reading, understanding, finish, assembly,
                              machine_seen[0] if machine_seen else None)
    if pool is not None:
        pool.shutdown(wait=False)
    auto = check["自動確定"] if check else stages.UNKNOWN
    flag_auto = (flag_out or {}).get("検算(旗の行を足した)", {}).get("自動確定")

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

    # --- K-67: 読了率と未読マップ(段階 1 から出す。最初の画面になるもの) ---
    from draft import readthrough as rt_part
    from draft import scorecard as card_part
    from draft import search_check
    from draft import work_checklist

    # 読みが未取得のページ(通読の答えが無い)は読了率・検索で 0 と数えない(K-68 C 周 1)。
    unobtained_pages = sorted(int(n) for n, v in (reading.get("読み") or {}).items() if "注" in v)
    read_pages = sorted(int(n) for n in (reading.get("読み") or {}) if int(n) not in set(unobtained_pages))
    if unobtained_pages:
        warn.insert(0, f"読みが未取得のページ {len(unobtained_pages)}(読了率・検索に 0 として入れていない。未取得のまま)")
    t = time.perf_counter()
    rt = _guarded(ctx, "読了率",
                  lambda: rt_part.readthrough(pdf, {int(n): v for n, v in (reading.get("読み") or {}).items()},
                                              read_pages, unobtained=unobtained_pages),
                  None)
    ctx.timings["段: 読了率"] = round(time.perf_counter() - t, 1)
    if rt and rt.get("案件全体の警告"):
        warn.insert(0, rt["案件全体の警告"])

    t = time.perf_counter()
    search = _guarded(ctx, "検索の正答率",
                      lambda: search_check.score(search_check.build_questions(pdf, read_pages),
                                                 {int(n): v for n, v in (reading.get("読み") or {}).items()}),
                      None)
    ctx.timings["段: 検索の正答率"] = round(time.perf_counter() - t, 1)

    # --- K-67: 工事チェック表(段階 2 から) ---
    sources = source_presence(org, finish, cost_table, a.stage)
    missing_sources = [f"{name}{'なし' if state == SOURCE_ABSENT else '未取得'}"
                       for name, state in sources.items() if state != SOURCE_PRESENT]
    red_pages = [p_["ページ"] for p_ in ((rt or {}).get("ページごと") or []) if p_.get("信号") == rt_part.RED]
    red_pages += [n for n in unobtained_pages if n not in red_pages]
    checklist = None
    if a.stage >= 2:
        t = time.perf_counter()
        checklist = _guarded(ctx, "工事チェック表",
                             lambda: work_checklist.build(items, pdf=pdf, pages=read_pages,
                                                          missing_sources=missing_sources,
                                                          unread_pages=red_pages),
                             None)
        ctx.timings["段: 工事チェック表"] = round(time.perf_counter() - t, 1)

    card = _guarded(ctx, "採点表",
                    lambda: card_part.build(readthrough=rt, search=search, checklist=checklist,
                                            finish=finish, items=items,
                                            timings={"読む": ctx.timings.get("段: 読む")}),
                    None)
    result = {
        "まとめ": {
            "案件": a.case_id,
            "段階": a.mode,
            "段階の目安": stages.MODES[a.mode],
            "自動確定": auto,
            "原価表": f"あり({len(cost_table['行'])} 行、{cost_table['形']})" if cost_table else stages.UNKNOWN,
            "資料の有無": sources,
            "概要の別紙": stages.UNKNOWN,
            "精度の注意": warn,
            "項目の数": len(items),
            "数量のある項目": sum(1 for it in items if it["数量"] is not None),
            "状態ごと": {s: sum(1 for it in items if it["状態"] == s) for s in stages.STATES},
            "確度ごと": {c: sum(1 for it in items if it["確度"] == c) for c in stages.CONFIDENCE},
            "根拠の種類ごと": {b: sum(1 for it in items if it["根拠の種類"] == b) for b in stages.BASIS_KINDS},
            "検算で食い違った項目": sum(1 for it in items if it["検算"]),
            "下書き": "下書き(人が直す前提)",
            "段階(K-67)": f"段階 {a.stage} {card_part.STAGES[a.stage]}",
            "採点表の見出し": card_part.headline(card) if card else stages.UNKNOWN,
            "読了率": (rt or {}).get("読了率", stages.UNKNOWN),
            # K-68 C 周 1: 読了率は読みが取れたページだけで数える。分母を並べて、少ないページの高い数字に見えないようにする。
            "読みが取れたページ": f"{len(read_pages)} / {len(read_pages) + len(unobtained_pages)}",
        },
        "止まった所": ctx.stops,
        "段の中の例外": ctx.state.get("例外", []),
        "整理": org,
        **({"読みの設計": {"案": a.design, "中身": design_info}} if a.design != "V1" else {}),
        "読む": reading,
        "読了率": rt,
        "検索の正答率": search,
        "理解": understanding,
        **({"工事チェック表": checklist} if checklist is not None else {}),
        "採点表": card,
        **({"確度に読めた割合": page_conf} if a.with_page_confidence else {}),
        **({"OCR": ocr_info} if ocr_path else {}),
        "仕上表": finish,
        "質問": qs,
        "答えの往復": round_trip,
        "組み立て": assembly,
        "機械の検算": check,
        "つなげなかった部品": [{"部品": n, "理由": w} for n, w in NOT_CONNECTED],
        **({"旗の部品": flag_out} if flag_out is not None else {}),
        "時間(秒)": ctx.timings,
        "AI を呼んだ記録": {"まとめ": caller.summary(model), "1回ずつ": [r.as_dict() for r in caller.records]},
        "並列数の上限": a.parallel,
    }
    ctx.timings["通し全体(壁時計)"] = round(time.perf_counter() - started, 1)
    (out / "下書き.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "材料発注表.csv").write_text(stages.order_sheet_csv(assembly.get("材料表") or []), encoding="utf-8-sig")
    try:
        review_html.build(json.loads(json.dumps(result, ensure_ascii=False, default=str)),
                          {pi.number: pi.image for pi in page_infos}, out / "確認画面.html")
    except Exception as exc:  # noqa: BLE001
        ctx.stop("確認画面", f"{type(exc).__name__}: {exc}")
        (out / "下書き.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    caller.retire_stale()
    pending = sorted((answers_dir / "待っている問い").glob("*/指示.md")) if (answers_dir / "待っている問い").exists() else []
    print(json.dumps({"出力": str(out), "自動確定": auto, "止まった所": len(ctx.stops), "待っている問い": len(pending),
                      "AI": caller.summary(model)["答えの出どころ"],
                      "未取得の内訳": caller.summary(model)["未取得の内訳"],
                      **({"旗": on, "旗の行を足した自動確定": flag_auto} if on else {})}, ensure_ascii=False))
    if isinstance(flag_auto, int) and flag_auto > 0:
        return 2
    return 2 if isinstance(auto, int) and auto > 0 else 0


def _flag_parts(on: Sequence[str], a: argparse.Namespace, ctx: stages.Context, org: Mapping[str, Any],
                reading: Mapping[str, Any], understanding: Mapping[str, Any], finish: Mapping[str, Any],
                assembly: Mapping[str, Any],
                machine: Mapping[str, Any] | None) -> dict[str, Any]:
    """旗オンの部品を動かす(K-63 4 節)。**理解・組み立ての数量は書き換えない。** 部品の失敗は「止まった所」に書いて次へ。"""
    import os

    from draft import flags as fp

    legend = a.legend_lookup or os.environ.get(LEGEND_ENV)
    know = a.knowledge or os.environ.get(KNOWLEDGE_ENV)
    calls: dict[str, Callable[[], dict[str, Any]]] = {
        "記号を室ごとに数える": lambda: fp.symbol_count(reading, understanding, finish),
        "縮尺で長さを測る": lambda: fp.scale_length(ctx.pdf, org, reading, understanding, finish),
        "凡例の対照表": lambda: fp.legend_lookup(ctx.pdf, org, reading, understanding, legend),
        "分かれ道を AI に選択肢で聞く": lambda: fp.branch_questions(ctx, understanding, assembly),
        "線引き": lambda: fp.line_judge(ctx, assembly),
        "知識の表": lambda: fp.knowledge(understanding, know),
    }
    parts: dict[str, Any] = {}
    for name in fp.FLAGS:
        if name not in on:
            continue
        part = _guarded(ctx, f"旗: {name}", calls[name], None)
        parts[name] = part if part is not None else {"旗": fp.FLAGS[name], "動いたか": "止まった(「止まった所」を見る)",
                                                      "足したもの": [], "問い": []}
    out: dict[str, Any] = {"部品": parts, "まとめ": fp.overview(parts),
                           "旗の裏でもつながなかった部品": [{"部品": k, "理由": v} for k, v in fp.NOT_FLAGGED.items()]}
    rows = fp.increase_rows(parts)
    if a.no_machine_check:
        out["検算(旗の行を足した)"] = {"自動確定": stages.UNKNOWN, "注": "機械の検算を飛ばした"}
    elif rows:
        res = _guarded(ctx, "旗: 機械の検算", lambda: machine_check(
            list(assembly["内訳の行"]) + rows, a.machine_output, machine, a.case_id), None)
        out["検算(旗の行を足した)"] = ({"自動確定": res["自動確定"], "自動確定の内訳": res["自動確定の内訳"], "足した行": len(rows)}
                                 if res else {"自動確定": stages.UNKNOWN, "注": "検算が止まった"})
    else:
        out["検算(旗の行を足した)"] = {"自動確定": 0, "足した行": 0, "注": "数量が増えた行が無いので、組み立ての検算と同じ"}
    return out


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
