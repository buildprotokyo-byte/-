"""K-72 作業 A: 位置の重なりに加えて要素の重なりも見て、3 回の行を対応づけたカードを測る。

**正解は開かない。AI は 1 回も呼ばない。**基準は `docs/k72_card_element_criteria.md`(測る前にコミットした)。

使い方(K-61 が保存した全部あり版の 3 回を読むだけ)::

    PYTHONPATH=. python -m benchmarks.measure_k72_card_element \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --out docs/k72_card_element_result.json

出すのは件数と割合だけ(室名・工事名・行の名前は書かない)。**答えの方針 A〜D は合成の答えで、正しさの測定ではない。**
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks import measure_k71_position_matching as k71
from draft import position_match as pm
from draft import split_cards

#: 前の周(K-71 作業 2)の数(基準に書いた数。並べるため)。
BEFORE = {"囮1 ずらす": [17, 920, 0.0185], "囮2 別の室へ移す": [18, 756, 0.0238],
          "カードにした鍵": 76, "カード": 68, "単位の同値で外した鍵": 28,
          "カードにできなかった理由(鍵)": {"値が 1 つしか無い": 205, "位置の重なる行がほかの回に無い": 167,
                                   "同点で決まらない": 4},
          "未確定 前→後(方針 A)": [719, 714], "数量が無い項目": 385}
DECOY_MAX = k71.DECOY_MAX
DECOY_MIN_ROWS = k71.DECOY_MIN_ROWS
SHIFT = k71.SHIFT
#: 参考に並べる大きさの許容(線とカードは 2 倍 = 0.5 だけで決める)。
REFERENCE_SIZE_RATIOS = {"1.5倍": 1 / 1.5, "3倍": 1 / 3}


def element_indexes(drafts: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[tuple[int, str], Mapping[str, Any]]]:
    return {n: pm.element_index((d.get("読む") or {}).get("読み") or {}) for n, d in drafts.items()}


def build(runs_dirs: Sequence[Path], pdf: Path | None, *, with_scale: bool = True) -> dict[str, Any]:
    """K-71 作業 2 と同じ材料で、要素の重なりを足してカードを作る(**AI は呼ばない**)。"""
    from benchmarks.make_k70_split_cards import build_real

    real = build_real(runs_dirs, pdf, with_scale=with_scale)
    real["elements"] = element_indexes(real["drafts"])
    kw = {"machine": real["machine"], "scale": real["scale"], "match": "位置", "unit_equivalence": True}
    real["k70_found"] = real["found"]
    real["k71_found"] = split_cards.find_splits(real["runs"], **kw)
    found = split_cards.find_splits(real["runs"], elements=real["elements"], **kw)
    base = real["base"]
    real["found"] = found
    real["ordered"] = split_cards.order(found["カード"], real["runs"], base,
                                        finish=real["drafts"][base]["仕上表"])
    return real


# --- 囮 ------------------------------------------------------------------------


def _center(box: Sequence[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _move(box: Sequence[float], dx: float, dy: float) -> list[float]:
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


def _move_elements(row: dict[str, Any], index: dict[tuple[int, str], Mapping[str, Any]],
                   dx: float, dy: float) -> None:
    """その行の要素を、その行のためだけに写して動かす(同じ要素を持つほかの行は動かさない)。"""
    page = int(row["ページ"])
    new_ids = []
    for eid in row.get("要素") or ():
        e = index.get((page, str(eid)))
        if e is None:
            continue
        nid = f"{eid}#囮{row.get('id')}"
        index[(page, nid)] = {**e, "id": nid, "位置": _move([float(v) for v in e["位置"]], dx, dy)}
        new_ids.append(nid)
    row["要素"] = new_ids


def decoy(real: Mapping[str, Any], kind: str, *, with_elements: bool, element_check: bool = True,
          check_text: bool = False) -> dict[str, Any]:
    """囮(基準 2 節)。1 つの回(2 回目、次に 3 回目)の行を動かして、対応づけをやり直す。

    ``with_elements`` が偽なら K-71 と同じ形(行の箱だけ)。真なら要素の箱も同じだけ動かす(2-2)。
    ``element_check`` が偽なら位置だけで対応づける(K-71 のやり方。並べるため)。
    ``check_text`` が真なら、読んだ文字が同じことも見る(K-73 作業 3(a))。動かした要素の文字は元のまま写す。
    """
    from sameness.rows import row_room

    found = real["found"]
    groups = found["_位置で対応づけた組"]
    dims = found.get("_位置で対応づけた組の次元") or ["数量"] * len(groups)
    names = list(real["runs"])
    indexes = [real["elements"].get(n) or {} for n in names] if element_check else None
    moved = wrong = unmovable = 0
    by_run: dict[str, dict[str, int]] = {}

    def run_chains(rows_by_run, dim, idx):
        if idx is None:
            return pm.chains(rows_by_run)
        return pm.chains(rows_by_run, elements=idx, check_room=dim != "室", check_text=check_text)

    for r in range(1, len(names)):
        run_rows = real["runs"][names[r]]
        by_page: dict[Any, list[Mapping[str, Any]]] = {}
        for it in sorted(run_rows, key=lambda x: str(x.get("id"))):
            by_page.setdefault(it.get("ページ"), []).append(it)
        stat = {"動かした行": 0, "誤って対応づけた行": 0, "移せなかった": 0}
        for rows_by_run, dim in zip(groups, dims):
            before = run_chains(rows_by_run, dim, indexes)
            in_chain = [j for j in range(len(rows_by_run[r])) if before["行の行き先"].get((r, j)) == "鎖"]
            if not in_chain:
                continue
            new_rows = [dict(it) for it in rows_by_run[r]]
            new_index = dict(indexes[r]) if (indexes is not None and with_elements) else None
            targets: list[int] = []
            if kind == "ずらす":
                for it in new_rows:
                    if pm.box_of(it) is not None:
                        it["囲み"] = _move(pm.box_of(it)[1], *SHIFT)
                        if new_index is not None:
                            _move_elements(it, new_index, *SHIFT)
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
                    if with_elements:
                        own = pm.box_of(it)[1]
                        (cx, cy), (tx, ty) = _center(own), _center(pm.box_of(src)[1])
                        it["囲み"] = _move(own, tx - cx, ty - cy)
                        if new_index is not None:
                            _move_elements(it, new_index, tx - cx, ty - cy)
                    else:
                        it["囲み"] = list(pm.box_of(src)[1])
                    targets.append(j)
            trial = [rows_by_run[x] if x != r else new_rows for x in range(len(rows_by_run))]
            trial_idx = None
            if indexes is not None:
                trial_idx = [indexes[x] if (x != r or new_index is None) else new_index for x in range(len(names))]
            after = run_chains(trial, dim, trial_idx)
            stat["動かした行"] += len(targets)
            stat["誤って対応づけた行"] += sum(1 for j in targets if after["行の行き先"].get((r, j)) == "鎖")
        by_run[names[r]] = stat
        moved += stat["動かした行"]
        wrong += stat["誤って対応づけた行"]
        unmovable += stat["移せなかった"]
    rate = k71._ratio(wrong, moved)
    verdict = ("判定できない" if moved < DECOY_MIN_ROWS else ("合格" if (rate or 0) <= DECOY_MAX else "不合格"))
    return {"囮": kind, "要素ごと動かす": with_elements, "要素の重なりを見る": element_check,
            "読んだ文字を見る": check_text,
            "回ごと": by_run, "動かした行(和)": moved, "誤って対応づけた行(和)": wrong,
            "移せなかった行(和)": unmovable, "誤って対応づけた割合": [wrong, moved, rate], "判定": verdict}


# --- 測る ----------------------------------------------------------------------


def reference_sizes(real: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for label, ratio in REFERENCE_SIZE_RATIOS.items():
        f = split_cards.find_splits(real["runs"], machine=real["machine"], scale=real["scale"], match="位置",
                                    unit_equivalence=True, elements=real["elements"], size_ratio=ratio)
        out[label] = {"カード": len(f["カード"]), "割れた鍵の行き先": f["割れた鍵の行き先"]}
    return out


def measure(real: Mapping[str, Any], *, check_text: bool = False) -> dict[str, Any]:
    out = k71.measure(real)
    # K-71 の囮(位置だけ)は捨て、この周の囮に替える。
    out.pop("囮1 ずらす")
    out.pop("囮2 別の室へ移す")
    out["前の周(K-70 作業 3)"] = k71.BEFORE
    out["前の周(K-71 作業 2)"] = BEFORE
    out["要素の重なり"] = {"同じページ": True, "同じ室": "行の室の鍵(室の組では見ない)", "同じ種類": True,
                     "近い大きさ": f"幅・高さそれぞれ 小さい方÷大きい方 {pm.SIZE_RATIO} 以上",
                     "要素の箱が重なる": "重なった面積 > 0", "行の要素の組のうち": "1 組以上"}
    decoys = {
        "囮1 ずらす": decoy(real, "ずらす", with_elements=False, check_text=check_text),
        "囮2 別の室へ移す": decoy(real, "別の室へ移す", with_elements=False, check_text=check_text),
        "囮1' ずらす(要素ごと)": decoy(real, "ずらす", with_elements=True, check_text=check_text),
        "囮2' 別の室へ移す(要素ごと)": decoy(real, "別の室へ移す", with_elements=True, check_text=check_text),
    }
    out.update(decoys)
    out["位置だけ(K-71 のやり方)で同じ囮"] = {
        "囮1 ずらす": decoy(real, "ずらす", with_elements=False, element_check=False)["誤って対応づけた割合"],
        "囮2 別の室へ移す": decoy(real, "別の室へ移す", with_elements=False, element_check=False)["誤って対応づけた割合"],
        "囮1' ずらす(要素ごと)": decoy(real, "ずらす", with_elements=True, element_check=False)["誤って対応づけた割合"],
        "囮2' 別の室へ移す(要素ごと)":
            decoy(real, "別の室へ移す", with_elements=True, element_check=False)["誤って対応づけた割合"],
    }
    old = real.get("k71_found")
    if old is not None:
        out["K-71 のやり方で数え直した数(同じ材料)"] = {
            "カード": len(old["カード"]), "割れた鍵の行き先": old["割れた鍵の行き先"],
            "対応づけの行の行き先": (old["対応づけ"] or {}).get("行の行き先")}
    lines = out.pop("線")
    lines.pop("線2 囮 ずらす 0.02 以下", None)
    lines.pop("線2 囮 別の室へ移す 0.02 以下", None)
    out["線"] = {
        "線1 黙って落とさない": lines.pop("線1 黙って落とさない"),
        "線2 囮 ずらす 0.02 以下": decoys["囮1 ずらす"]["判定"],
        "線2 囮 別の室へ移す 0.02 以下": decoys["囮2 別の室へ移す"]["判定"],
        "線2' 囮 ずらす(要素ごと) 0.02 以下": decoys["囮1' ずらす(要素ごと)"]["判定"],
        "線2' 囮 別の室へ移す(要素ごと) 0.02 以下": decoys["囮2' 別の室へ移す(要素ごと)"]["判定"],
        **lines,
    }
    carded = out["カードにした鍵"]
    out["K-71 より減ったか(線ではない)"] = {
        "カードにした鍵": [BEFORE["カードにした鍵"], carded, carded < BEFORE["カードにした鍵"]],
        "カード": [BEFORE["カード"], out["カード"], out["カード"] < BEFORE["カード"]]}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    real = build(a.runs, a.pdf)
    result = measure(real)
    result["参考: 大きさの許容を変えたとき(線とカードは 2 倍だけで決める)"] = reference_sizes(real)
    result["但し書き"] = ["件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない",
                      "答えの方針は合成で、正しさの測定ではない",
                      "対応づけが本当に同じ物どうしかは、囮で誤りを測るだけ(正解は使っていない)",
                      "PDF の 10 枚は、この周では作り直していない"]
    text = json.dumps(result, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
