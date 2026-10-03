"""K-73 作業 3(a): カードの対応づけに「読んだ文字が同じ」を足して測る。

**正解は開かない。AI は 1 回も呼ばない。**基準は `docs/k73_card_text_criteria.md`(測る前にコミットした)。

使い方(K-61 が保存した全部あり版の 3 回を読むだけ)::

    PYTHONPATH=. python -m benchmarks.measure_k73_card_text \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --out docs/k73_card_text_result.json

出すのは件数と割合だけ。**読んだ文字(要素の ``内容``)は 1 つも書き出さない**(線 10 で確かめる)。
"""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from benchmarks import measure_k72_card_element as k72
from draft import position_match as pm
from draft import split_cards

#: 前の周(K-72 作業 A)の数(基準に書いた数。並べるため)。
BEFORE = {"囮1 ずらす": [12, 907, 0.0132], "囮2 別の室へ移す": [5, 748, 0.0067],
          "囮1' ずらす(要素ごと)": [3, 907, 0.0033], "囮2' 別の室へ移す(要素ごと)": [17, 748, 0.0227],
          "カードにした鍵": 73, "カード": 64, "単位の同値で外した鍵": 28,
          "カードにできなかった理由(鍵)": {"値が 1 つしか無い": 202, "位置の重なる行がほかの回に無い": 167,
                                   "位置は重なるが要素が重ならない": 6, "同点で決まらない": 4},
          "未確定 前→後(方針 A)": [719, 714], "数量が無い項目": 385}
DECOYS = ("囮1 ずらす", "囮2 別の室へ移す", "囮1' ずらす(要素ごと)", "囮2' 別の室へ移す(要素ごと)")


def build(runs_dirs: Sequence[Path], pdf: Path | None, *, with_scale: bool = True) -> dict[str, Any]:
    """K-72 と同じ材料で、要素の重なりに「読んだ文字が同じ」を足してカードを作る(**AI は呼ばない**)。"""
    real = k72.build(runs_dirs, pdf, with_scale=with_scale)
    real["k72_found"] = real["found"]
    real["k72_ordered"] = real["ordered"]
    found = split_cards.find_splits(real["runs"], machine=real["machine"], scale=real["scale"], match="位置",
                                    unit_equivalence=True, elements=real["elements"], text_match=True)
    base = real["base"]
    real["found"] = found
    real["ordered"] = split_cards.order(found["カード"], real["runs"], base, finish=real["drafts"][base]["仕上表"])
    return real


# --- 読んだ文字の数(中身は書かない) ---------------------------------------------


def _one_edit(a: str, b: str) -> bool:
    """1 文字の置き換え・足し・引きで同じになるか。"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def text_counts(real: Mapping[str, Any]) -> dict[str, Any]:
    """位置で重なる行の対(重なり率 0.50 以上)の、1〜5 を満たす要素の組を、読んだ文字で分けて数える(種類ごと)。

    参考の数(仮の判断 0-2)。線は変えない。数えるのは件数だけ。
    """
    found = real["found"]
    groups = found["_位置で対応づけた組"]
    dims = found.get("_位置で対応づけた組の次元") or ["数量"] * len(groups)
    names = list(real["runs"])
    idx = [real["elements"].get(n) or {} for n in names]
    seen: set[tuple[Any, ...]] = set()
    by_kind: dict[str, dict[str, int]] = {}
    rows = {"重なる行の対": 0, "1〜5 を満たす組がある": 0, "そのうち文字が同じ組もある": 0}
    from sameness.rows import row_room

    for rows_by_run, dim in zip(groups, dims):
        n = len(rows_by_run)
        for r in range(n):
            for s in range(r + 1, n):
                for a in rows_by_run[r]:
                    for b in rows_by_run[s]:
                        if pm.overlap(a, b) < pm.THRESHOLD:
                            continue
                        rows["重なる行の対"] += 1
                        if dim != "室" and (row_room(a) or "") != (row_room(b) or ""):
                            continue
                        any_shape = any_text = False
                        for x_id in a.get("要素") or ():
                            for y_id in b.get("要素") or ():
                                page = int(a["ページ"])
                                x, y = idx[r].get((page, str(x_id))), idx[s].get((page, str(y_id)))
                                if x is None or y is None or not pm.same_element(x, y):
                                    continue
                                any_shape = True
                                tx, ty = pm.normalized_text(x.get("内容")), pm.normalized_text(y.get("内容"))
                                same = bool(tx) and tx == ty
                                any_text |= same
                                key = (r, s, page, str(x_id), str(y_id))
                                if key in seen:
                                    continue
                                seen.add(key)
                                k = by_kind.setdefault(str(x.get("種類")), {"同じ": 0, "1 文字違い": 0,
                                                                           "それより違う": 0, "空": 0})
                                if same:
                                    k["同じ"] += 1
                                elif not tx or not ty:
                                    k["空"] += 1
                                elif _one_edit(tx, ty):
                                    k["1 文字違い"] += 1
                                else:
                                    k["それより違う"] += 1
                        rows["1〜5 を満たす組がある"] += any_shape
                        rows["そのうち文字が同じ組もある"] += any_text
    return {"行の対(3 回の組み合わせの延べ)": rows,
            "1〜5 を満たす要素の組(種類ごと。読んだ文字で分けた件数)": dict(sorted(by_kind.items()))}


def _strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _strings(v)


#: 要素の種類(コードが決めた語)。
KINDS = ("文字", "線", "数字", "記号", "図", "表", "写真")


def fixed_vocabulary() -> frozenset[str]:
    """コードと会社の語彙で決まっている語(図面から読んだ文字ではない)。線 10 の数から外す。

    次元の名前・入れ先・要素の種類・工事チェック表の枠の名前。これらが図面の要素の ``内容`` と偶然同じでも、
    書き出したのは図面の文字ではなく、決まった語。数字だけの文字列(件数の表の鍵)も外す。
    """
    from draft import work_checklist

    words = set(split_cards.DIMENSIONS) | {split_cards.PRESENCE_ONLY, split_cards.UNIT_NAME_ONLY} | set(KINDS)
    words |= {str(f.get("名前")) for f in work_checklist.load_frames() if f.get("名前")} | {"その他"}
    return frozenset(words)


def leaks_text(out: Any, indexes: Mapping[str, Mapping[tuple[int, str], Mapping[str, Any]]],
               allowed: frozenset[str] = frozenset()) -> int:
    """結果の中の文字列(鍵と値)のうち、要素の ``内容``(そのまま・正規化したもの)と一致するものの数(線 10)。

    ``allowed``(決まった語)と、数字だけの文字列は数えない。
    """
    texts: set[str] = set()
    for idx in indexes.values():
        for e in idx.values():
            raw = e.get("内容")
            if isinstance(raw, str) and raw.strip():
                texts.add(raw)
                texts.add(pm.normalized_text(raw))
    texts.discard("")
    return sum(1 for s in _strings(out)
               if s not in allowed and not s.isdigit() and (s in texts or pm.normalized_text(s) in texts))


# --- 測る ----------------------------------------------------------------------


def _not_worse(now: Sequence[Any], before: Sequence[Any]) -> str:
    wrong, moved = now[0], now[1]
    if moved < k72.DECOY_MIN_ROWS:
        return "判定できない"
    ok = Fraction(wrong, moved) <= Fraction(before[0], before[1]) and Fraction(wrong, moved) <= Fraction(k72.DECOY_MAX)
    return "合格" if ok else "不合格"


def measure(real: Mapping[str, Any]) -> dict[str, Any]:
    out = k72.measure(real, check_text=True)
    out.pop("前の周(K-71 作業 2)", None)
    out.pop("K-71 より減ったか(線ではない)", None)
    out["前の周(K-72 作業 A)"] = BEFORE
    out["読んだ文字が同じ"] = {"正規化": "NFKC → 空白を全部除く → casefold", "比べ方": "空でなく完全一致",
                         "かける種類": "全部の種類", "1〜5 と同じ要素の組で満たす": True}
    out["K-72 のやり方(1〜5)で同じ材料の囮"] = {
        name: k72.decoy(real, kind, with_elements=w, check_text=False)["誤って対応づけた割合"]
        for name, kind, w in (("囮1 ずらす", "ずらす", False), ("囮2 別の室へ移す", "別の室へ移す", False),
                              ("囮1' ずらす(要素ごと)", "ずらす", True),
                              ("囮2' 別の室へ移す(要素ごと)", "別の室へ移す", True))}
    old = real.get("k72_found")
    if old is not None:
        ks = k72.k71.keys_summary(old)
        out["K-72 のやり方で数え直した数(同じ材料)"] = {
            "カード": len(old["カード"]), "カードにした鍵": ks["カードにした鍵"], "割れた鍵の行き先": old["割れた鍵の行き先"],
            "対応づけの行の行き先": (old["対応づけ"] or {}).get("行の行き先")}
    out["読んだ文字の数(参考。線は変えない)"] = text_counts(real)
    old_lines = out.pop("線")
    rate = {n: out[n]["誤って対応づけた割合"] for n in DECOYS}
    lines = {
        "線1 黙って落とさない": old_lines["線1 黙って落とさない"],
        "線2 囮2' 別の室へ移す(要素ごと) 0.02 以下": out["囮2' 別の室へ移す(要素ごと)"]["判定"],
        "線3 囮1 ずらす K-72 以下・0.02 以下": _not_worse(rate["囮1 ずらす"], BEFORE["囮1 ずらす"]),
        "線3 囮2 別の室へ移す K-72 以下・0.02 以下": _not_worse(rate["囮2 別の室へ移す"], BEFORE["囮2 別の室へ移す"]),
        "線3 囮1' ずらす(要素ごと) K-72 以下・0.02 以下":
            _not_worse(rate["囮1' ずらす(要素ごと)"], BEFORE["囮1' ずらす(要素ごと)"]),
    }
    for k, v in old_lines.items():
        if not k.startswith(("線1 ", "線2 ", "線2' ")):
            n, rest = k.split(" ", 1)
            num = n[1:]
            head = num.rstrip("()iv")
            new = {"3": "4", "4": "5", "5": "6", "6": "7", "7": "8", "8": "9"}.get(head, head)
            lines[f"線{new}{num[len(head):]} {rest}"] = v
    carded = out["カードにした鍵"]
    out["K-72 より減ったか(線ではない)"] = {
        "カードにした鍵": [BEFORE["カードにした鍵"], carded, carded < BEFORE["カードにした鍵"]],
        "カード": [BEFORE["カード"], out["カード"], out["カード"] < BEFORE["カード"]]}
    out["線"] = lines
    lines["線10 読んだ文字を書き出さない"] = leaks_text(out, real["elements"], fixed_vocabulary()) == 0
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    real = build(a.runs, a.pdf)
    result = measure(real)
    result["但し書き"] = ["件数と割合だけ。読んだ文字は書き出していない。正解は開いていない。AI は 1 回も呼んでいない",
                      "答えの方針は合成で、正しさの測定ではない",
                      "対応づけが本当に同じ物どうしかは、囮で誤りを測るだけ(正解は使っていない)",
                      "PDF の 10 枚は、この周では作り直していない"]
    if leaks_text(result, real["elements"], fixed_vocabulary()):
        raise SystemExit("読んだ文字が結果に入った。書き出さない")
    text = json.dumps(result, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
