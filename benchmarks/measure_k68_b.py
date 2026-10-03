"""K-68 B: 質問の作り方を直す。周ごとに測る。**正解は開かない。AI は 1 回も呼ばない。**

基準は `docs/k68_b_questions_criteria.md`(周ごとに、測る前にコミットした)。

使い方(K-61 が保存した出力を読むだけ)::

    PYTHONPATH=. python benchmarks/measure_k68_b.py --round 1 \\
      --runs /mnt/project-files/reports/K-61/結果/P011/full_R1 ... \\
      --out docs/k68_b_questions_result.json

出すのは件数と割合だけ(実案件のため、室名・工事の名前・行の名前は書かない)。
`--out` のファイルがあれば、その周の欄だけを書き換える(ほかの周の数字は残す)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from benchmarks.measure_question_curve import load_run
from draft import uncertainty

K65_RESULT = Path(__file__).resolve().parents[1] / "docs" / "k65_question_curve_result.json"
RESULT = Path(__file__).resolve().parents[1] / "docs" / "k68_b_questions_result.json"


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def round1(drafts: Mapping[str, Mapping[str, Any]], **_: Any) -> dict[str, Any]:
    """周 1: 3 回の割れを K-66 の鍵で揃える。確定・仮説・未確定を旧・新で並べる。"""
    items_by_run = {name: d["理解"]["項目"] for name, d in drafts.items()}
    out: dict[str, Any] = {"回": {}}
    for name, draft in drafts.items():
        other = [v for k, v in items_by_run.items() if k != name]
        row: dict[str, Any] = {"項目数": len(draft["理解"]["項目"])}
        for rule in ("旧", "新"):
            c = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other, rule=rule)
            row[rule] = {
                "3状態": c["3状態の分布"],
                "割れた鍵": c["3回の読みで割れた鍵"],
                "鍵の和": c["鍵の和"],
                "割れた鍵の割合": _ratio(c["3回の読みで割れた鍵"], c["鍵の和"]),
                "全部の回に出た鍵": c["全部の回に出た鍵"],
                "一致の割合(全部の回に出た鍵 ÷ 鍵の和)": _ratio(c["全部の回に出た鍵"], c["鍵の和"]),
                "3回の読みの割れが立った項目": c["信号ごとの件数"]["3回の読みの割れ"],
            }
        flags = {}
        for rule in ("旧", "新"):
            c = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other, rule=rule)
            flags[rule] = {r["id"] for r in c["項目ごと"] if "3回の読みの割れ" in r["信号"]}
        row["割れの立った項目の入れ替わり"] = {
            "旧で割れ・新で割れない": len(flags["旧"] - flags["新"]),
            "旧で割れない・新で割れ": len(flags["新"] - flags["旧"]),
        }
        row["線2: 新の割れた鍵の割合が旧より下がる"] = (
            (row["新"]["割れた鍵の割合"] or 0) < (row["旧"]["割れた鍵の割合"] or 0))
        out["回"][name] = row
    out["線2(5版すべて)"] = all(r["線2: 新の割れた鍵の割合が旧より下がる"] for r in out["回"].values())
    # K-65 と同じ形(5 版をすべて「ほかの回」にする)のほかに、**全部あり版の 3 回だけ**でも並べる。
    full = {k: v for k, v in items_by_run.items() if k.startswith("full")}
    if len(full) >= 2:
        out["全部あり版の3回だけ"] = {}
        for name, items in full.items():
            other = [v for k, v in full.items() if k != name]
            row = {}
            for rule in ("旧", "新"):
                c = uncertainty.classify(items, finish=drafts[name]["仕上表"], other_runs=other, rule=rule)
                row[rule] = {"3状態": c["3状態の分布"], "割れた鍵": c["3回の読みで割れた鍵"],
                             "鍵の和": c["鍵の和"],
                             "割れた鍵の割合": _ratio(c["3回の読みで割れた鍵"], c["鍵の和"]),
                             "全部の回に出た鍵": c["全部の回に出た鍵"],
                             "一致の割合(全部の回に出た鍵 ÷ 鍵の和)": _ratio(c["全部の回に出た鍵"], c["鍵の和"])}
            out["全部あり版の3回だけ"][name] = row
    out["合成の囮と言い換え"] = decoy_check()
    return out


def decoy_check() -> dict[str, Any]:
    """K-66 の囮・言い換えの対を 2 回の読みとして並べ、割れの信号が立つかを数える(合成)。"""
    from sameness.decoys import decoy_pairs, paraphrase_pairs

    def item(name: str, qty: float | None, unit: str) -> dict[str, Any]:
        return {"id": "x", "工事": name, "場所": "室A", "部位": "", "品番": "", "数量": qty, "単位": unit}

    def split(pair: Any) -> bool:
        unit = pair.unit or "m2"
        left = item(pair.left, pair.left_quantity if pair.left_quantity is not None else 10.0, unit)
        right = item(pair.right, pair.right_quantity if pair.right_quantity is not None else 10.0, unit)
        return bool(uncertainty.readings_disagree([[left], [right]], rule="新"))

    decoys = [p for p in decoy_pairs() if p.level == "細目"]
    missed = [p for p in decoys if not split(p)]
    paraphrases = [p for p in paraphrase_pairs() if p.level == "細目"]
    flagged = [p for p in paraphrases if split(p)]
    by_kind: dict[str, int] = {}
    for p in missed:
        by_kind[p.種類] = by_kind.get(p.種類, 0) + 1
    return {
        "囮の対(細目と数量違い)": len(decoys),
        "割れと言えなかった囮": len(missed),
        "割れと言えなかった囮の種類ごと": by_kind,
        "線3: 割れと言えなかった囮が 0 件": not missed,
        "言い換えの対(細目)": len(paraphrases),
        "割れと言った言い換え": len(flagged),
    }


def _k65() -> dict[str, Any]:
    return json.loads(K65_RESULT.read_text(encoding="utf-8"))


def checklists_of(drafts: Mapping[str, Mapping[str, Any]], pdf: Path | None) -> dict[str, Any]:
    """K-67 の工事チェック表(K-65 の測り方と同じ。文字の層だけ読む。AI は呼ばない)。"""
    if pdf is None:
        return {}
    from draft import work_checklist

    out = {}
    for name, draft in drafts.items():
        pages = sorted({int(n) for n in (draft["読む"].get("読み") or {})})
        out[name] = work_checklist.build(draft["理解"]["項目"], pdf=pdf, pages=pages)
    return out


def build_all(drafts: Mapping[str, Mapping[str, Any]], pdf: Path | None, *,
              how: str = "連鎖の金額順", **extra: Any) -> dict[str, dict[str, Any]]:
    """5 版それぞれのカード(K-65 と同じ形: ほかの 4 版を「ほかの回」にする)。"""
    from benchmarks.measure_question_curve import candidates_of
    from draft import questioning

    checklists = checklists_of(drafts, pdf)
    items_by_run = {name: d["理解"]["項目"] for name, d in drafts.items()}
    out = {}
    for name, draft in drafts.items():
        other = [v for k, v in items_by_run.items() if k != name]
        raw = candidates_of(draft, None)
        out[name] = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], other_runs=other, how=how,
                                      checklist=checklists.get(name), **extra)
        out[name]["_候補"] = raw
        out[name]["_ほかの回"] = other
        out[name]["_チェック表"] = checklists.get(name)
        out[name]["枠の理由ごと"] = out[name].get("枠の理由ごと", [])
    return out


def _auto_confirmed(draft: Mapping[str, Any], cards: Sequence[Mapping[str, Any]],
                    answers: Mapping[str, str]) -> dict[str, Any]:
    """答えを戻した下書きを組み立て、本番の入口(機械の検算)に通して自動確定を数える。"""
    from copy import deepcopy

    from draft import answers_io, stages
    from draft.run import machine_check

    understanding = deepcopy(draft["理解"])
    finish = deepcopy(draft["仕上表"])
    applied = answers_io.apply(understanding, finish, cards, answers)
    rows, _ = stages.assembly_rows(understanding["項目"])
    check = machine_check(rows, None, None, "P011")
    return {"自動確定": check["自動確定"], "戻せなかった答え": len(applied["戻せなかった答え"]),
            "外した項目": len(applied["固定の選択肢で外した項目"]) + len(applied["K-64 の口"]["内訳から外した項目"])}


def _first_real(card: Mapping[str, Any]) -> str | None:
    from benchmarks.measure_question_curve import _first_real_option

    return _first_real_option(card)


def made_cards(draft: Mapping[str, Any], built: Mapping[str, Any], pdf_checklist: Mapping[str, Any] | None
               ) -> list[dict[str, Any]]:
    """作ったカード全部(聞く分+「聞かない(影響小)」で黙らせた分)。捨てたカードは入れない。"""
    from draft import cards as cards_mod
    from draft import chain as chain_mod
    from draft.questioning import other_values

    items = draft["理解"]["項目"]
    classified = uncertainty.classify(items, finish=draft["仕上表"], other_runs=built["_ほかの回"])
    graph = chain_mod.build(items, finish=draft["仕上表"])
    bc = cards_mod.build_cards(built["_候補"], items, graph, classified,
                               other_values=other_values(items, built["_ほかの回"]),
                               spot_check_ids=uncertainty.spot_check_ids(classified))
    frames = [c for c in cards_mod.frame_cards(pdf_checklist) if not cards_mod.invalid_reasons(c)]
    return list(bc["カード"]) + frames


def _count(values: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def round2(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 2: 状態・有無・まとめ方・科目を固定の選択肢+「どれでもない」にする。"""
    from copy import deepcopy

    from benchmarks.measure_question_curve import NONE_WORDS
    from draft import answers_io, cards as cards_mod

    k65 = _k65()["回"]
    checklists = checklists_of(drafts, pdf)
    builts = build_all(drafts, pdf)
    out: dict[str, Any] = {"回": {}}
    for name, built in builts.items():
        cards = built["カード"]
        made = made_cards(drafts[name], built, checklists.get(name))
        small = set(built["聞かない(影響小)"])
        fixed = [c for c in made if c["型"] in cards_mod.FIXED_OPTIONS and not cards_mod._is_unreadable(c)]
        not_fixed = [c for c in fixed if c["選択肢"] != cards_mod.fixed_options(c["型"])]
        bad_input = [c for c in made if c.get("数字の入力") or c.get("自由記述") or c.get("推奨")]
        # 固定の選択肢の 1 つ 1 つを答えて、黙って捨てられる答えが無いか(実案件の 5 版で。黙らせた分も含む)。
        refused = 0
        tried = 0
        for c in fixed:
            for choice in c["選択肢"]:
                u, f = deepcopy(drafts[name]["理解"]), deepcopy(drafts[name]["仕上表"])
                res = answers_io.apply(u, f, [c], {c["鍵"]: choice})
                tried += 1
                refused += len(res["戻せなかった答え"])
        first = {c["鍵"]: a for c in cards if (a := _first_real(c)) is not None}
        reals = {c["鍵"]: [o for o in c["選択肢"] if not any(w in o for w in NONE_WORDS)] for c in cards}
        second = {k: v[1] for k, v in reals.items() if len(v) > 1}
        out["回"][name] = {
            "カードの数(聞く分)": {"K-65": k65[name]["カードの数"], "周2": len(cards)},
            "型ごと(聞く分)": {"K-65": k65[name]["型ごと"], "周2": built["型ごと"]},
            "作ったカードの数(聞く分+影響小で黙らせた分)": len(made),
            "作ったカードの型ごと": _count([c["型"] for c in made]),
            "影響小で黙らせたカードの型ごと": _count([c["型"] for c in made if c["鍵"] in small]),
            "固定の型のカード(作った分)": len(fixed),
            "線1: 固定の表と違うカード": len(not_fixed),
            "線2: 数字・自由記述・推奨のあるカード": len(bad_input),
            "線3: 固定の選択肢を 1 つずつ答えた数": tried,
            "線3: 戻せなかった答え": refused,
            "捨てたカードの数": {"K-65": k65[name]["捨てたカードの数"], "周2": len(built["捨てたカード"])},
            "捨てたカードの型と理由ごと": _count([f"{d.get('型')}|{d['理由']}" for d in built["捨てたカード"]]),
            "聞かない(影響小)の数": {"K-65": k65[name]["聞かない(影響小)の数"], "周2": len(small)},
            "線4: 聞くカードに 1 つ目の選択肢で答えて組み立てた自動確定": _auto_confirmed(drafts[name], cards, first),
            "線4: 聞くカードに 2 つ目の選択肢で答えて組み立てた自動確定": _auto_confirmed(drafts[name], cards, second),
        }
    out["線1〜4(5版すべて)"] = {
        "線1": all(r["線1: 固定の表と違うカード"] == 0 for r in out["回"].values()),
        "線2": all(r["線2: 数字・自由記述・推奨のあるカード"] == 0 for r in out["回"].values()),
        "線3": all(r["線3: 戻せなかった答え"] == 0 for r in out["回"].values()),
        "線4": all(r[k]["自動確定"] == 0 for r in out["回"].values() for k in r if k.startswith("線4")),
    }
    return out


def sources_of(drafts: Mapping[str, Mapping[str, Any]], paths: Mapping[str, Path] | None,
               pdf: Path | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """機械の値(K-61 が保存した本番の形)と縮尺で換算した値(`draft.flags.scale_length`)。**AI は呼ばない。**"""
    from draft import flags, questioning

    machine, scale, scale_parts = {}, {}, {}
    for name, draft in drafts.items():
        path = (paths or {}).get(name)
        prod = path / "本番の形.json" if path is not None else None
        machine[name] = (questioning.machine_values_from(
            json.loads(prod.read_text(encoding="utf-8")).get("工事項目") or ())
            if prod is not None and prod.exists() else {})
        if pdf is not None:
            # 保存した JSON を読み直すとページの鍵が文字になるので、数に戻す(本番では数のまま)。
            org = {**draft["整理"], "ページ": {int(n): v for n, v in draft["整理"]["ページ"].items()}}
            part = flags.scale_length(pdf, org, draft["読む"], draft["理解"], draft["仕上表"])
            scale_parts[name] = part
            scale[name] = questioning.scale_values_from(part)
        else:
            scale[name] = {}
    return machine, scale, scale_parts


def _quantity_questions(draft: Mapping[str, Any], raw: Sequence[Mapping[str, Any]], other: Sequence[Any],
                        qsources: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """型が `数量` に決まった問い(捨てる前、畳む前)のカード。"""
    from draft import cards as cards_mod
    from draft.questioning import other_values

    items = draft["理解"]["項目"]
    classified = uncertainty.classify(items, finish=draft["仕上表"], other_runs=other)
    uncertain = {r["id"]: r for r in classified["項目ごと"]}
    values = other_values(items, other)
    out = []
    for q in raw:
        card = cards_mod._to_card(q, items, uncertain, {}, values, qsources)
        if card is not None and card["型"] == "数量":
            out.append(card)
    return out


def _real(options: Sequence[str]) -> list[str]:
    from benchmarks.measure_question_curve import NONE_WORDS

    return [o for o in options if not any(w in o for w in NONE_WORDS)]


def round3(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None,
           paths: Mapping[str, Path] | None = None, **_: Any) -> dict[str, Any]:
    """周 3: 数量の型の選択肢を、3 回の値・機械の値・縮尺で換算した値から作る。"""
    from copy import deepcopy

    from draft import answers_io, questioning

    k65 = _k65()["回"]
    machine, scale, scale_parts = sources_of(drafts, paths, pdf)
    builts = build_all(drafts, pdf, machine_values=None, scale_values=None)  # 形だけ(候補とほかの回)
    out: dict[str, Any] = {"回": {}}
    for name, draft in drafts.items():
        items = draft["理解"]["項目"]
        raw, other = builts[name]["_候補"], builts[name]["_ほかの回"]
        qs = questioning.quantity_sources(items, other, machine=machine[name], scale=scale[name])
        old_cards = _quantity_questions(draft, raw, other, None)
        new_cards = _quantity_questions(draft, raw, other, qs)
        old_ok = sum(1 for c in old_cards if _real(c["選択肢"]))
        new_ok = sum(1 for c in new_cards if _real(c["選択肢"]))
        by_source: dict[str, int] = {s: 0 for s in questioning.QUANTITY_SOURCES}
        traced_missing = 0
        option_count = 0
        for c in new_cards:
            for o in _real(c["選択肢"]):
                option_count += 1
                src = c["数量の出どころ"].get(o) or []
                if not src or not all(x.get("辿る") for x in src):
                    traced_missing += 1
                for x in src:
                    by_source[x["出どころ"]] += 1
        all_by_source: dict[str, int] = {s: 0 for s in questioning.QUANTITY_SOURCES}
        for vs in qs.values():
            for v in vs:
                all_by_source[v["出どころ"]] += 1
        no_qty = sum(1 for it in items if it.get("数量") is None)
        zero = sum(1 for vs in qs.values() for v in vs if not v["値"] or v["値"] <= 0)
        bad_input = sum(1 for c in new_cards if c.get("数字の入力") or c.get("自由記述") or c.get("推奨"))
        # 聞くカード(周 3 の作り方)。
        built = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], other_runs=other,
                                  checklist=builts[name].get("_チェック表"),
                                  machine_values=machine[name], scale_values=scale[name])
        asked = built["カード"]
        asked_qty = [c for c in asked if c["型"] == "数量"]
        tried = refused = 0
        for c in asked_qty:
            for choice in c["選択肢"]:
                u, f = deepcopy(draft["理解"]), deepcopy(draft["仕上表"])
                res = answers_io.apply(u, f, [c], {c["鍵"]: choice})
                tried += 1
                refused += len(res["戻せなかった答え"])
        first = {c["鍵"]: a for c in asked if (a := _first_real(c)) is not None}
        auto = _auto_confirmed(draft, asked, first)
        u, f = deepcopy(draft["理解"]), deepcopy(draft["仕上表"])
        first_refused = answers_io.apply(u, f, asked, first)["戻せなかった答え"]
        parts = scale_parts.get(name) or {}
        scales = parts.get("縮尺") or []
        out["回"][name] = {
            "線1: 数量の型の問い(捨てる前・畳む前)": len(new_cards),
            "線1: 値のある問い 旧(K-65 の作り方)": old_ok,
            "線1: 値のある問い 新": new_ok,
            "線1: 割合 旧": _ratio(old_ok, len(old_cards)),
            "線1: 割合 新": _ratio(new_ok, len(new_cards)),
            "値の数(数量の型の問いの選択肢、出どころごと)": by_source,
            "数量が無い項目": no_qty,
            "候補の値が 1 つ以上ある項目(数量が無い項目のうち)": len(qs),
            "候補の値の数(数量が無い項目全部、出どころごと)": all_by_source,
            "縮尺: 平面図のページ": len(scales),
            "縮尺: 決まったページ": sum(1 for x in scales if str(x.get("縮尺", "")).startswith("1/")),
            "縮尺: 値が 1 つに決まった理解の項目": len(scale[name]),
            "縮尺: 照らし合わせ": parts.get("照らし合わせ"),
            "機械の値のある理解の項目": len(machine[name]),
            "線3: 出どころを辿れない選択肢": traced_missing,
            "線3: 選択肢の値の数": option_count,
            "線3: 数量が無いところから作った値(0 以下)": zero,
            "線4: 数字・自由記述・推奨のある数量のカード": bad_input,
            "聞くカード": {"K-65": k65[name]["カードの数"], "周3": len(asked)},
            "聞くカードの型ごと": built["型ごと"],
            "聞く数量のカード": len(asked_qty),
            "線5: 聞く数量のカードの選択肢を 1 つずつ答えた数": tried,
            "線5: そのうち戻せなかった答え": refused,
            "線5: 聞くカード全部に 1 つ目で答えて戻せなかった答え": len(first_refused),
            "線5: 戻せなかった理由ごと": _count([r["理由"] for r in first_refused]),
            "線6: 自動確定": auto,
        }
    rows = out["回"]
    full = [n for n in rows if n.startswith("full")]
    out["線1(全部あり版で 20% 以上、5 版で旧を下回らない)"] = (
        all((rows[n]["線1: 割合 新"] or 0) >= 0.20 for n in full)
        and all((rows[n]["線1: 割合 新"] or 0) >= (rows[n]["線1: 割合 旧"] or 0) for n in rows))
    out["線3"] = all(r["線3: 出どころを辿れない選択肢"] == 0 and r["線3: 数量が無いところから作った値(0 以下)"] == 0
                    for r in rows.values())
    out["線4"] = all(r["線4: 数字・自由記述・推奨のある数量のカード"] == 0 for r in rows.values())
    out["線5"] = all(r["線5: そのうち戻せなかった答え"] == 0 and r["線5: 聞くカード全部に 1 つ目で答えて戻せなかった答え"] == 0
                    for r in rows.values())
    out["線6"] = all(r["線6: 自動確定"]["自動確定"] == 0 for r in rows.values())
    return out


def round4(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 4: 見る所が 4 ページ以上の問いを捨てず、先頭 3 か所+「他◯か所」で出す。

    機械の値・縮尺の値は渡さない(周 3 で、聞くカードを 1 枚も変えなかったため。縮尺は 1 版 1 分半かかる)。
    """
    from draft import cards as cards_mod

    before = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}
    prev2 = (before.get("周2") or {}).get("回") or {}
    prev3 = (before.get("周3") or {}).get("回") or {}
    checklists = checklists_of(drafts, pdf)
    builts = build_all(drafts, pdf)
    out: dict[str, Any] = {"回": {}}
    for name, built in builts.items():
        asked = built["カード"]
        made = made_cards(drafts[name], built, checklists.get(name))
        small = set(built["聞かない(影響小)"])
        # 同じ鍵の問いの候補が 2 つ以上あることがある(見る所が違う)ので、鍵ごとに全部の見る所を持つ。
        raw_pages: dict[str, list[list[int]]] = {}
        for q in built["_候補"]:
            raw_pages.setdefault(q["鍵"], []).append(sorted(cards_mod._page_numbers(q.get("見る所"))))
        for c in cards_mod.frame_cards(checklists.get(name)):
            raw_pages.setdefault(c["鍵"], []).append(sorted([*c["見る所"], *c["他の見る所"]]))
        lost = [c["鍵"] for c in made if c["鍵"] in raw_pages
                and sorted([*c["見る所"], *(c.get("他の見る所") or ())]) not in raw_pages[c["鍵"]]]
        dup_keys = sum(1 for v in raw_pages.values() if len(v) > 1)
        label_bad = [c["鍵"] for c in made if (c.get("他の見る所") or c.get("他◯か所"))
                     and c.get("他◯か所") != f"他{len(c.get('他の見る所') or ())}か所"]
        long_ = [c for c in made if c.get("他の見る所")]
        dropped_long = [d for d in built["捨てたカード"] if "ページ以上" in d["理由"]]
        first = {c["鍵"]: a for c in asked if (a := _first_real(c)) is not None}
        out["回"][name] = {
            "線1: 見る所が 4 ページ以上で捨てたカード": len(dropped_long),
            "捨てたカード 周2→周4": {"周2": (prev2.get(name) or {}).get("捨てたカードの数", {}).get("周2"),
                                "周4": len(built["捨てたカード"])},
            "捨てたカードの型と理由ごと": _count([f"{d.get('型')}|{d['理由']}" for d in built["捨てたカード"]]),
            "線2: 画面に出す見る所が 4 ページ以上のカード": sum(1 for c in made if len(c["見る所"]) >= cards_mod.MAX_PAGES),
            "線2: 他◯か所を付けたカード(作った分)": len(long_),
            "線2: 他◯か所を付けたカードの型ごと": _count([c["型"] for c in long_]),
            "線2: 他◯か所の数が合わないカード": len(label_bad),
            "線2: ページを落としたカード": len(lost),
            "同じ鍵の問いの候補が 2 つ以上ある鍵": dup_keys,
            "他の見る所のページ数(最小・最大)": ([min(len(c["他の見る所"]) for c in long_),
                                      max(len(c["他の見る所"]) for c in long_)] if long_ else None),
            "聞くカード 周3→周4": {"周3": (prev3.get(name) or {}).get("聞くカード", {}).get("周3"), "周4": len(asked)},
            "聞くカードの型ごと 周3→周4": {"周3": (prev3.get(name) or {}).get("聞くカードの型ごと"),
                                    "周4": built["型ごと"]},
            "聞く他◯か所のカード": sum(1 for c in asked if c.get("他の見る所")),
            "作ったカード 周2→周4": {"周2": (prev2.get(name) or {}).get("作ったカードの数(聞く分+影響小で黙らせた分)"),
                                "周4": len(made)},
            "聞かない(影響小) 周2→周4": {"周2": (prev2.get(name) or {}).get("聞かない(影響小)の数", {}).get("周2"),
                                    "周4": len(small)},
            "影響小で黙らせた他◯か所のカード": sum(1 for c in long_ if c["鍵"] in small),
            "線4: 自動確定": _auto_confirmed(drafts[name], asked, first),
        }
    rows = out["回"]
    out["線1"] = all(r["線1: 見る所が 4 ページ以上で捨てたカード"] == 0 for r in rows.values())
    out["線2"] = all(r["線2: 画面に出す見る所が 4 ページ以上のカード"] == 0 and r["線2: 他◯か所の数が合わないカード"] == 0
                    and r["線2: ページを落としたカード"] == 0 for r in rows.values())
    out["線4"] = all(r["線4: 自動確定"]["自動確定"] == 0 for r in rows.values())
    return out


def _wrong_answers(draft: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], other: Sequence[Any],
                   checklist: Mapping[str, Any] | None, *, share: float, n: int = 40,
                   seed: int = 65) -> dict[str, Any]:
    """K-65 と同じ作り方の間違い(1 つ目の選択肢を「正しい」とし、``share`` を別の選択肢に替える)。"""
    import random

    from draft import answers_io

    chosen = list(cards[:n])
    right = {c["鍵"]: a for c in chosen if (a := _first_real(c)) is not None}
    rng = random.Random(seed)
    keys = sorted(right)
    wrong_keys = rng.sample(keys, max(1, int(len(keys) * share))) if keys else []
    wrong = dict(right)
    for key in wrong_keys:
        card = next(c for c in chosen if c["鍵"] == key)
        others = [o for o in card["選択肢"] if o != right[key]]
        if others:
            wrong[key] = others[0]
    answered = [{"鍵": k, "選択肢": v} for k, v in wrong.items()]
    items = draft["理解"]["項目"]
    new = answers_io.contradictions(answered, chosen, items, other_runs=other, checklist=checklist)
    old = answers_io.contradictions(answered, chosen, items, types="K-65")
    return {
        "間違えた割合": share, "答えた問数": len(right), "間違えた問数": len(wrong_keys),
        "間違えた答えの型ごと": _count([next(c for c in chosen if c["鍵"] == k)["型"] for k in wrong_keys]),
        "K-65 の 3 型": answers_io.wrong_answer_detection(answered, wrong_keys, old),
        "周5(7 型)": answers_io.wrong_answer_detection(answered, wrong_keys, new),
        "見つけた矛盾の型ごと": _count([c["型"] for c in new]),
        "自動確定": _auto_confirmed(draft, chosen, wrong)["自動確定"],
    }


def round5(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 5: 矛盾検出の型を足し、間違った答えの検出率を測り直す(カードは周 4 の作り方)。"""
    checklists = checklists_of(drafts, pdf)
    builts = build_all(drafts, pdf)
    k65 = _k65()["回"]
    out: dict[str, Any] = {"回": {}}
    for name, built in builts.items():
        out["回"][name] = {
            "K-65 の数字(そのときのカード)": [x["矛盾の検出"] for x in k65[name]["間違えた答え"]],
            "周5": [_wrong_answers(drafts[name], built["カード"], built["_ほかの回"], checklists.get(name), share=s)
                   for s in (0.1, 0.2)],
        }
    tot = {"K-65 の 3 型": [0, 0, 0, 0], "周5(7 型)": [0, 0, 0, 0]}
    for r in out["回"].values():
        for x in r["周5"]:
            for arm in tot:
                d = x[arm]
                tot[arm][0] += d["矛盾で見つけた"]
                tot[arm][1] += d["間違えた答え"]
                tot[arm][2] += d["誤って挙げた"]
                tot[arm][3] += d["間違えていない答え"]
    out["5版×2割合のまとめ"] = {
        arm: {"見つけた": v[0], "間違えた答え": v[1], "検出率": _ratio(v[0], v[1]),
              "誤って挙げた": v[2], "間違えていない答え": v[3], "誤検出率": _ratio(v[2], v[3])}
        for arm, v in tot.items()}
    m = out["5版×2割合のまとめ"]["周5(7 型)"]
    out["線1(検出率 0.30 以上)"] = (m["検出率"] or 0) >= 0.30
    out["線2(誤検出率 0.10 以下)"] = m["誤検出率"] is not None and m["誤検出率"] <= 0.10
    out["線3(自動確定 0)"] = all(x["自動確定"] == 0 for r in out["回"].values() for x in r["周5"])
    return out


def round6(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 6: 工事チェック表の「分からない理由」から質問を作る(機械の値・縮尺の値は渡さない。周 4 と同じ)。"""
    from copy import deepcopy

    from draft import answers_io

    before = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}
    prev4 = (before.get("周4") or {}).get("回") or {}
    checklists = checklists_of(drafts, pdf)
    builts = build_all(drafts, pdf)
    out: dict[str, Any] = {"回": {}}
    for name, built in builts.items():
        asked = built["カード"]
        checklist = checklists.get(name) or {}
        records = built["枠の理由ごと"]
        have = {(r["枠"], r["理由"]) for r in records}
        want = [(f["枠"], r) for f in checklist.get("枠") or () if f.get("状態") != "確認できた"
                for r in (f.get("分からないこと") or {}).get("理由") or ()]
        silent = [w for w in want if w not in have]
        made_keys = {k for r in records for k in r["作った鍵"]}
        reason_cards = [c for c in asked if c["鍵"] in made_keys]
        tried = refused = 0
        for c in reason_cards:
            for choice in c["選択肢"]:
                u, f = deepcopy(drafts[name]["理解"]), deepcopy(drafts[name]["仕上表"])
                tried += 1
                refused += len(answers_io.apply(u, f, [c], {c["鍵"]: choice})["戻せなかった答え"])
        by_reason: dict[str, dict[str, Any]] = {}
        for r in records:
            row = by_reason.setdefault(r["理由"], {"理由の数": 0, "問いにした": 0, "作れなかった": 0, "カード": set(),
                                               "作れなかった理由": {}})
            row["理由の数"] += 1
            if r["作った鍵"]:
                row["問いにした"] += 1
                row["カード"].update(r["作った鍵"])
            else:
                row["作れなかった"] += 1
            for why in r["作れなかった理由"]:
                w = why if "個に候補の値が無い" not in why else "数量の無い項目に候補の値が無い"
                row["作れなかった理由"][w] = row["作れなかった理由"].get(w, 0) + 1
        for row in by_reason.values():
            row["カード"] = len(row["カード"])
        qty = [c for c in reason_cards if c["型"] == "数量"]
        first = {c["鍵"]: a for c in asked if (a := _first_real(c)) is not None}
        out["回"][name] = {
            "確認できなかった枠": sum(1 for f in checklist.get("枠") or () if f.get("状態") != "確認できた"),
            "理由の和": len(want),
            "線1: 問いにも作れなかった理由にもならなかった理由": len(silent),
            "理由ごと": by_reason,
            "線2: 数量の根拠が足りないから作った数量のカード": len(qty),
            "線3: 理由から作ったカードの選択肢を 1 つずつ答えた数": tried,
            "線3: 戻せなかった答え": refused,
            "理由から作った聞くカードの型ごと": _count([c["型"] for c in reason_cards]),
            "聞くカード 周4→周6": {"周4": ((prev4.get(name) or {}).get("聞くカード 周3→周4") or {}).get("周4"),
                               "周6": len(asked)},
            "聞くカードの型ごと": built["型ごと"],
            "線5: 自動確定": _auto_confirmed(drafts[name], asked, first),
        }
    rows = out["回"]
    full = [n for n in rows if n.startswith("full")]
    hidden = [n for n in rows if not n.startswith("full")]
    base = min(rows[n]["聞くカード 周4→周6"]["周6"] for n in full) if full else None
    out["線1"] = all(r["線1: 問いにも作れなかった理由にもならなかった理由"] == 0 for r in rows.values())
    out["線2"] = all(rows[n]["線2: 数量の根拠が足りないから作った数量のカード"] >= 1 for n in full)
    out["線3"] = all(r["線3: 戻せなかった答え"] == 0 for r in rows.values())
    out["線4"] = {"全部あり版のいちばん少ない聞くカード": base,
                 "隠した版の聞くカード": {n: rows[n]["聞くカード 周4→周6"]["周6"] for n in hidden},
                 "合否": all(rows[n]["聞くカード 周4→周6"]["周6"] >= base for n in hidden) if base is not None else None}
    out["線5"] = all(r["線5: 自動確定"]["自動確定"] == 0 for r in rows.values())
    return out


def _after(draft: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], answers: Mapping[str, str],
           other: Sequence[Any], *, machine: bool = False) -> dict[str, Any]:
    """答えを本番の口(`answers_io.apply`)で戻した後の 3 状態。``machine`` なら自動確定も数える。"""
    from copy import deepcopy

    from draft import answers_io, stages

    understanding = deepcopy(draft["理解"])
    finish = deepcopy(draft["仕上表"])
    applied = answers_io.apply(understanding, finish, cards, answers)
    c = uncertainty.classify(understanding["項目"], finish=finish, other_runs=other)
    out = {"3状態": c["3状態の分布"], "未確定の項目": {r["id"] for r in c["項目ごと"] if r["3状態"] == uncertainty.UNSETTLED},
           "戻せなかった答え": len(applied["戻せなかった答え"])}
    if machine:
        from draft.run import machine_check

        rows, _ = stages.assembly_rows(understanding["項目"])
        out["自動確定"] = machine_check(rows, None, None, "P011")["自動確定"]
    return out


COUNTS = (0, 3, 5, 10, 20, 40, 60)


def round7(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 7: 質問の曲線とメーターの較正を、本番の口で答えを戻して測り直す(カードは周 6 の作り方)。"""
    k65 = _k65()["回"]
    by_how = {how: build_all(drafts, pdf, how=how) for how in ("連鎖の金額順", "ランダム")}
    out: dict[str, Any] = {"回": {}}
    for name, draft in drafts.items():
        row: dict[str, Any] = {"曲線": {}}
        for how, builts in by_how.items():
            built = builts[name]
            asked, other = built["カード"], built["_ほかの回"]
            base = _after(draft, asked, {}, other)
            points = []
            for k in [*COUNTS, len(asked)]:
                chosen = asked[:k]
                answers = {c["鍵"]: a for c in chosen if (a := _first_real(c)) is not None}
                got = _after(draft, asked, answers, other, machine=True)
                points.append({"問数": min(k, len(asked)), "答えた数": len(answers),
                               "戻せなかった答え": got["戻せなかった答え"], "3状態(後)": got["3状態"],
                               "未確定が減った数": len(base["未確定の項目"]) - len(got["未確定の項目"]),
                               "自動確定": got["自動確定"]})
            row["曲線"][how] = {"聞くカード": len(asked), "3状態(前)": base["3状態"], "点": points}
        built = by_how["連鎖の金額順"][name]
        asked, other = built["カード"], built["_ほかの回"]
        base = _after(draft, asked, {}, other)["未確定の項目"]
        cal_rows = []
        for c in asked[:60]:
            pick = _first_real(c)
            if pick is None:
                continue
            now = _after(draft, asked, {c["鍵"]: pick}, other)["未確定の項目"]
            actual = len(base - now)
            promised = c["メーター"]["決める項目数"]["合計"]
            cal_rows.append({"型": c["型"], "見込み": promised, "実際": actual,
                             "ずれ": abs(promised - actual) / promised if promised else None})
        gaps = sorted(r["ずれ"] for r in cal_rows if r["ずれ"] is not None)
        median = round(gaps[len(gaps) // 2], 4) if gaps else None
        by_type = {}
        for t in sorted({r["型"] for r in cal_rows}):
            g = sorted(r["ずれ"] for r in cal_rows if r["型"] == t and r["ずれ"] is not None)
            if g:
                by_type[t] = {"問数": len(g), "ずれの中央値": round(g[len(g) // 2], 4)}
        calibration = {"測った問数": len(cal_rows), "見込みのある問": len(gaps),
                       "見込みの合計": sum(r["見込み"] for r in cal_rows), "実際の合計": sum(r["実際"] for r in cal_rows),
                       "ずれの中央値": median, "実際が 0 だった問": sum(1 for r in cal_rows if r["実際"] == 0),
                       "型ごと": by_type}
        from draft import questioning

        rebuilt = questioning.build({"候補": built["_候補"]}, draft["理解"], draft["仕上表"], other_runs=other,
                                    checklist=built.get("_チェック表"), calibration=calibration)
        row["較正"] = {"K-65": {"ずれの中央値": k65[name]["メーターの較正"]["ずれの中央値"],
                              "見込みの合計": k65[name]["メーターの較正"]["見込みの合計"],
                              "実際の合計": k65[name]["メーターの較正"]["実際の合計"]},
                     "周7": calibration,
                     "見込みを画面に出すか": rebuilt["見込みを画面に出すか"],
                     "線2: 画面に出す見込みのあるカード": sum(1 for c in rebuilt["カード"]
                                                    if c["メーター"].get("画面に出す見込み") is not None)}
        k65_curve = k65[name]["曲線"]["連鎖の金額順"]["行"]
        row["K-65 の曲線(連鎖の金額順、未確定が減った数)"] = {str(x["問数"]): x["未確定が減った数"] for x in k65_curve}
        out["回"][name] = row
    rows = out["回"]
    full = [n for n in rows if n.startswith("full")]
    out["線1"] = all(rows[n]["曲線"]["連鎖の金額順"]["点"][-1]["未確定が減った数"]
                    > max(rows[n]["K-65 の曲線(連鎖の金額順、未確定が減った数)"].values()) for n in full)
    out["線2"] = all((r["較正"]["線2: 画面に出す見込みのあるカード"] == 0)
                    if (r["較正"]["周7"]["ずれの中央値"] is None or r["較正"]["周7"]["ずれの中央値"] > 0.20) else True
                    for r in rows.values())
    out["線3"] = all(p["自動確定"] == 0 for r in rows.values() for c in r["曲線"].values() for p in c["点"])
    return out


ROUNDS: dict[int, Callable[..., dict[str, Any]]] = {1: round1, 2: round2, 3: round3, 4: round4, 5: round5,
                                                    6: round6, 7: round7}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    drafts = {path.name: load_run(path) for path in a.runs}
    result = ROUNDS[a.round](drafts, pdf=a.pdf, paths={path.name: path for path in a.runs})
    whole: dict[str, Any] = {}
    if a.out and a.out.exists():
        whole = json.loads(a.out.read_text(encoding="utf-8"))
    whole["但し書き"] = "件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない"
    whole[f"周{a.round}"] = result
    text = json.dumps(whole, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=sorted)[:6000])


if __name__ == "__main__":
    main()
