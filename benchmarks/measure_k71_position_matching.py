"""K-71 作業 2: 位置の重なりで 3 回の行を対応づけたカードを測る。**正解は開かない。AI は 1 回も呼ばない。**

基準は `docs/k71_position_matching_criteria.md`(測る前にコミットした)。

使い方(K-61 が保存した全部あり版の 3 回を読むだけ)::

    PYTHONPATH=. python -m benchmarks.measure_k71_position_matching \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --out docs/k71_position_matching_result.json

出すのは件数と割合だけ(室名・工事名・行の名前は書かない。カードの鍵は名前を含まない記号)。
**答えの方針 A〜D は合成の答えで、正しさの測定ではない。**
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks import measure_k70_split_cards as k70
from draft import position_match as pm
from draft import split_cards

#: 前の周(K-70 作業 3)の数(基準に書いた数。並べるため)。
BEFORE = {"値の割れの鍵": 476, "カードにした鍵": 36, "回の中で対応が 1 つに決まらない": 356,
          "未確定 前→後(方針 A)": [719, 712], "数量が無い項目": 385}
#: 線 2: 囮で誤って対応づけた割合の上限。動かした行がこれ未満なら判定できない。
DECOY_MAX = 0.02
DECOY_MIN_ROWS = 20
#: 線 3: カードにした鍵の割合(K-70 作業 3 の線 2 と同じ)と、前の周の数。
LINE3_MIN_SHARE = 0.50
LINE3_MORE_THAN = 36
#: 線 8: 方針 A の未確定の減り(前の周の 7 を超える)。
LINE8_MIN_DROP = 8
#: 囮 1 のずらす量(幅 2000 画素の画像の座標)。
SHIFT = (300.0, 300.0)
#: 参考に並べるしきい(線とカードは 0.50 だけで決める)。
REFERENCE_THRESHOLDS = (0.3, 0.7)


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def build(runs_dirs: Sequence[Path], pdf: Path | None, *, with_scale: bool = True,
          threshold: float | None = None) -> dict[str, Any]:
    """K-70 作業 3 と同じ材料で、位置の対応づけと単位の同値を入れてカードを作る(**AI は呼ばない**)。"""
    from benchmarks.make_k70_split_cards import build_real

    real = build_real(runs_dirs, pdf, with_scale=with_scale)
    found = split_cards.find_splits(real["runs"], machine=real["machine"], scale=real["scale"],
                                    match="位置", unit_equivalence=True, threshold=threshold)
    base = real["base"]
    real["k70_found"] = real["found"]
    real["found"] = found
    real["ordered"] = split_cards.order(found["カード"], real["runs"], base,
                                        finish=real["drafts"][base]["仕上表"])
    return real


# --- 囮 ------------------------------------------------------------------------


def _shift(box: Sequence[float]) -> list[float]:
    return [box[0] + SHIFT[0], box[1] + SHIFT[1], box[2] + SHIFT[0], box[3] + SHIFT[1]]


def decoy(real: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """囮(基準 2 節)。1 つの回(2 回目、次に 3 回目)の箱だけを動かして、対応づけをやり直す。

    数えるもの: 動かす前に鎖に入っていた行(位置で対応づけた組の行。組ごとに数える)のうち、
    動かした後もほかの回の行と対応づいた行 = 誤って対応づけた行。
    """
    from sameness.rows import row_room

    groups = real["found"]["_位置で対応づけた組"]
    names = list(real["runs"])
    moved = wrong = unmovable = 0
    by_run: dict[str, dict[str, int]] = {}
    for r in range(1, len(names)):
        run_rows = real["runs"][names[r]]
        by_page: dict[Any, list[Mapping[str, Any]]] = {}
        for it in sorted(run_rows, key=lambda x: str(x.get("id"))):
            by_page.setdefault(it.get("ページ"), []).append(it)
        stat = {"動かした行": 0, "誤って対応づけた行": 0, "移せなかった": 0}
        for rows_by_run in groups:
            before = pm.chains(rows_by_run)
            in_chain = [j for j in range(len(rows_by_run[r])) if before["行の行き先"].get((r, j)) == "鎖"]
            if not in_chain:
                continue
            new_rows = [dict(it) for it in rows_by_run[r]]
            targets: list[int] = []
            if kind == "ずらす":
                for j, it in enumerate(new_rows):
                    if pm.box_of(it) is not None:
                        it["囲み"] = _shift(pm.box_of(it)[1])
                targets = in_chain
            else:
                for j in in_chain:
                    it = new_rows[j]
                    room = row_room(it)
                    src = None
                    for other in by_page.get(it.get("ページ"), ()):
                        other_room = row_room(other)
                        if not other_room or not room or other_room == room:
                            continue
                        if pm.overlap(it, other) >= pm.THRESHOLD or pm.box_of(other) is None:
                            continue
                        src = other
                        break
                    if src is None:
                        stat["移せなかった"] += 1
                        continue
                    it["囲み"] = list(pm.box_of(src)[1])
                    targets.append(j)
            trial = [rows_by_run[x] if x != r else new_rows for x in range(len(rows_by_run))]
            after = pm.chains(trial)
            stat["動かした行"] += len(targets)
            stat["誤って対応づけた行"] += sum(1 for j in targets if after["行の行き先"].get((r, j)) == "鎖")
        by_run[names[r]] = stat
        moved += stat["動かした行"]
        wrong += stat["誤って対応づけた行"]
        unmovable += stat["移せなかった"]
    rate = _ratio(wrong, moved)
    verdict = ("判定できない" if moved < DECOY_MIN_ROWS else ("合格" if (rate or 0) <= DECOY_MAX else "不合格"))
    return {"囮": kind, "回ごと": by_run, "動かした行(和)": moved, "誤って対応づけた行(和)": wrong,
            "移せなかった行(和)": unmovable, "誤って対応づけた割合": [wrong, moved, rate], "判定": verdict}


# --- 測る ----------------------------------------------------------------------


def unit_collapse(cards: Sequence[Mapping[str, Any]]) -> int:
    """線 4 の足し: `new_unit` で揃えると実の値が 1 つになる数量のカードの数(1-5 が効いていれば 0)。"""
    return sum(1 for c in cards if c["次元"] == "数量"
               and len(split_cards.same_by_new_unit(split_cards.real_options(c))) < 2)


def keys_summary(found: Mapping[str, Any]) -> dict[str, Any]:
    by_dim = found["入れ先ごとの鍵"]
    value_keys = sum(by_dim[d] for d in split_cards.DIMENSIONS)
    dest = dict(found["割れた鍵の行き先"])
    carded = dest.get("カード", 0)
    dropped = dest.get(split_cards.UNIT_DROPPED, 0)
    reasons = {k: v for k, v in dest.items() if k not in ("カード", split_cards.UNIT_DROPPED)}
    silent = found["割れた鍵"] - (sum(dest.values()) + by_dim[split_cards.PRESENCE_ONLY]
                                + by_dim[split_cards.UNIT_NAME_ONLY])
    reason_groups: dict[str, int] = {}
    for x in found["カードにできなかった組"]:
        reason_groups[x["理由"]] = reason_groups.get(x["理由"], 0) + 1
    by_dim_dest: dict[str, dict[str, int]] = {}
    return {"値の割れの鍵": value_keys, "カードにした鍵": carded, "単位の同値で外した鍵": dropped,
            "カードにできなかった理由(鍵)": dict(sorted(reasons.items())),
            "カードにできなかった理由(組と理由の数)": dict(sorted(reason_groups.items())),
            "どこにも入らなかった鍵": silent, "_by_dim": by_dim_dest}


def measure(real: Mapping[str, Any], *, count: int = 10, machine_check: bool = True,
            reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
    found = real["found"]
    cards = real["ordered"]
    ks = keys_summary(found)
    by_dim = found["入れ先ごとの鍵"]
    old = real.get("k70_found")
    shape = k70.card_shape(cards)
    shape["new_unit で揃えると実の値が 1 つになる数量のカード"] = unit_collapse(cards)
    matching = found["対応づけ"]
    dims_carded: dict[str, int] = {}
    top = cards[:count]
    out: dict[str, Any] = {
        "材料": "K-61 が保存した P011 匿名化 v4 の全部あり版 3 回(基準の回は 1 回目)",
        "重なりの線": {"しきい(面積の重なり率)": pm.THRESHOLD, "同じページ": True, "1対1": "重なり率の大きい順(貪欲)",
                   "同点": "要素の重なりで分け、それでも同じなら対応づけない"},
        "割れた鍵": found["割れた鍵"], "鍵の和": found["鍵の和"],
        "割れた鍵の割合(割れた鍵 ÷ 鍵の和)": _ratio(found["割れた鍵"], found["鍵の和"]),
        "入れ先ごとの鍵": by_dim,
        "割れた鍵の行き先(値の割れ)": found["割れた鍵の行き先"],
        "対応づけ": matching,
        "カード": len(cards),
        "カードの次元ごと": k70._count([c["次元"] for c in cards]),
        "単位の同値で外したカード": len(found["単位の同値で外したカード"]),
        "単位の同値で外したカードの次元ごと": k70._count([c["次元"] for c in found["単位の同値で外したカード"]]),
        **{k: v for k, v in ks.items() if not k.startswith("_")},
        "カードにした鍵 ÷ 値の割れの鍵": [ks["カードにした鍵"], ks["値の割れの鍵"],
                                _ratio(ks["カードにした鍵"], ks["値の割れの鍵"])],
        "選択肢の実の値の数(カードごと)": k70._count([len(split_cards.real_options(c)) for c in cards]),
        "選択肢の値の出どころ(数)": _sources(cards),
        "カードの形": shape,
        "並べ方": {"1つの答えで確定するカード": sum(1 for c in cards if c["1つの答えで確定する行"]),
                "枠ごとのカード": k70._count([c["枠"] for c in cards]),
                "上位 10 枚の次元": k70._count([c["次元"] for c in top]),
                "上位 10 枚の 1 つの答えで確定する": sum(1 for c in top if c["1つの答えで確定する行"])},
        "線6(ii): 実案件の全部の選択肢を 1 つずつ答える": k70.every_option(real, cards),
        "囮1 ずらす": decoy(real, "ずらす"),
        "囮2 別の室へ移す": decoy(real, "別の室へ移す"),
        "動き(全部のカード)": {p: k70.movement(real, cards, p, machine_check=machine_check) for p in k70.POLICIES},
        "動き(PDF の 10 枚)": {p: k70.movement(real, top, p, machine_check=machine_check) for p in k70.POLICIES[:2]},
        "前の周(K-70 作業 3)": BEFORE,
    }
    if old is not None:
        # 前の周のやり方(対応づけ・単位の同値なし)を同じ材料で数え直した数(並べるため)。
        out["前の周のやり方で数え直した数"] = {
            "カード": len(old["カード"]),
            "カードにした鍵": sum(c["割れた鍵の数"] for c in old["カード"]),
            "回の中で対応が 1 つに決まらない(鍵)": sum(x["割れた鍵の数"] for x in old["カードにできなかった組"]
                                          if x["理由"] == split_cards.REASONS[0])}
    if reference:
        out["参考: しきいを変えたとき(線とカードは 0.50 だけで決める)"] = reference
    mv = out["動き(全部のカード)"]
    d1, d2 = out["囮1 ずらす"], out["囮2 別の室へ移す"]
    share = _ratio(ks["カードにした鍵"], ks["値の割れの鍵"]) or 0
    chain_line = matching["鎖の線"]
    out["線"] = {
        "線1 黙って落とさない": ks["どこにも入らなかった鍵"] == 0,
        "線2 囮 ずらす 0.02 以下": d1["判定"],
        "線2 囮 別の室へ移す 0.02 以下": d2["判定"],
        "線3(i) カードにした鍵の割合 0.50 以上": share >= LINE3_MIN_SHARE,
        "線3(ii) カードにした鍵が 36 より多い": ks["カードにした鍵"] > LINE3_MORE_THAN,
        "線4 カードの形": all(v == 0 for k, v in shape.items() if "線4 の補い" not in k),
        "線5 鎖": all(v == 0 for v in chain_line.values()),
        "線6 自動確定 0": all(mv[p].get("自動確定", 0) == 0 for p in k70.POLICIES[:2]),
        "線7 動かない答え": all(mv[p]["どれかの欄が変わった項目"] == 0 and mv[p]["3状態 前→後"][0] == mv[p]["3状態 前→後"][1]
                          and mv[p]["割れた鍵 / 鍵の和 前→後"][0] == mv[p]["割れた鍵 / 鍵の和 前→後"][1]
                          for p in k70.POLICIES[2:]),
        "線8 方針 A の未確定の減り 8 以上": mv[k70.POLICIES[0]]["未確定の減り"] >= LINE8_MIN_DROP,
    }
    return out


def _sources(cards: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out = {s: 0 for s in split_cards.SOURCES}
    for c in cards:
        for o in split_cards.real_options(c):
            for s in c["値の出どころ"][o]:
                out[s["出どころ"]] += 1
    return out


def reference_thresholds(real: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for t in REFERENCE_THRESHOLDS:
        f = split_cards.find_splits(real["runs"], machine=real["machine"], scale=real["scale"],
                                    match="位置", unit_equivalence=True, threshold=t)
        out[str(t)] = {"カード": len(f["カード"]), "割れた鍵の行き先": f["割れた鍵の行き先"]}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--pdf-check", type=Path, default=None)
    a = p.parse_args(argv)
    real = build(a.runs, a.pdf)
    result = measure(real, reference=reference_thresholds(real))
    if a.pdf_check and a.pdf_check.exists():
        result["線9: PDF(実案件の 10 枚)"] = json.loads(a.pdf_check.read_text(encoding="utf-8"))
    result["但し書き"] = ["件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない",
                      "答えの方針は合成で、正しさの測定ではない",
                      "対応づけが本当に同じ物どうしかは、囮で誤りを測るだけ(正解は使っていない)"]
    text = json.dumps(result, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
