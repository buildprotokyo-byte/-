"""32周目の測定(トライアルB): 鍵の表記ゆれを揃えると、一致は増えるか。偽の一致は出ないか。

基準は `docs/b_item_key_variance_criteria.md`(測る前にコミット済み)。

**実案件も正解ファイルも実図面も使わない。** 公共建築工事内訳書標準書式
(建築工事編・令和7年12月改定)の細目別内訳から、`docs/knowledge/candidates.md`
J 節 3・J 節 4 に写された 35 品目の名前だけを使う。

**揃える処理はこのスクリプトの中だけに書く。** 採用と出ても、この周では本番の
経路に入れない(入れるのは次の周で、再現する失敗テストを先に書いてから)。

実行::

    .venv/bin/python -m benchmarks.measure_item_key_variance
"""

from __future__ import annotations

import argparse
import itertools
import random
import re
import unicodedata

#: 公共建築工事内訳書標準書式の細目別内訳の品目名(docs/knowledge/candidates.md J節3・J節4)。
#: **互いに別の品目である。** だから「同じ鍵になったら偽の一致」と言える。
STANDARD_ITEMS: tuple[str, ...] = (
    "床モルタル撤去", "床タイル撤去", "ビニル床タイル撤去", "ビニル床シート撤去",
    "タイルカーペット撤去", "カーペット撤去", "フリーアクセスフロア撤去",
    "ビニル幅木撤去", "壁ボード撤去", "軽量鉄骨壁下地撤去", "壁紙撤去",
    "コンクリートブロック撤去", "天井合板ボード撤去", "軽量鉄骨天井下地撤去",
    "可動間仕切撤去", "トイレブース撤去", "天井点検口撤去", "ブラインドボックス撤去",
    "床フローリング張り", "畳敷き", "床タイル張り", "床モルタル塗り", "ビニル幅木",
    "壁タイル張り", "壁モルタル塗り",
    "建具撤去", "シャッター撤去", "オーバーヘッドドア撤去",
    "防水保護コンクリート撤去", "防水層撤去", "シーリング撤去", "手すり撤去",
    "笠木撤去", "ルーフドレン撤去", "とい撤去",
)

#: 送り仮名を落とす対応。**測る前に決めた。**
OKURIGANA = (("張り", "張"), ("塗り", "塗"), ("敷き", "敷"))

_SYMBOLS = re.compile(r"[ー―‐\-−ｰ・･/／\\、,.。\s_]+")
_BRACKETED = re.compile(r"[(（\[［{｛<＜【]([^)）\]］}｝>＞】]*)[)）\]］}｝>＞】]")


def t1(name: str) -> str:
    """中黒を 1 つ入れる(真ん中)。"""
    half = len(name) // 2
    return name[:half] + "・" + name[half:]


def _move_head(name: str, size: int) -> str:
    if len(name) <= size:
        return name
    return f"{name[size:]}({name[:size]})"


def t2(name: str) -> str:
    return _move_head(name, 2)


def t3(name: str) -> str:
    return _move_head(name, 3)


def t4(name: str) -> str:
    for long, short in OKURIGANA:
        if name.endswith(long):
            return name[: -len(long)] + short
    return name


def t5(name: str) -> str:
    return name.replace("ー", "−")


def t6(name: str) -> str:
    """**罠。** 末尾の「撤去」を落とす。別の品目になる。"""
    return name[:-2] if name.endswith("撤去") else name


TRUE_VARIANTS = (("T1 中黒", t1), ("T2 先頭2文字を括弧へ", t2), ("T3 先頭3文字を括弧へ", t3),
                 ("T4 送り仮名を落とす", t4), ("T5 長音をハイフンへ", t5))


def n0(text: str) -> str:
    return text


def n1(text: str) -> str:
    out = unicodedata.normalize("NFKC", text)
    out = _SYMBOLS.sub("", out)
    out = "".join(
        chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in out
    )
    return out.upper()


def n2(text: str) -> str:
    """括弧の中身を外に出す(位置を無視する)。"""
    inner = "".join(_BRACKETED.findall(text))
    outer = _BRACKETED.sub("", text)
    return n1(outer + inner)


def n3(text: str) -> str:
    """文字を並べ替えて比べる(語順を完全に無視する)。"""
    return "".join(sorted(n2(text)))


NORMALISERS = (("N0 何もしない", n0), ("N1 記号と仮名を揃える", n1),
               ("N2 括弧を外に出す", n2), ("N3 語順を無視する", n3))


def measure(norm) -> dict:
    keys = {name: norm(name) for name in STANDARD_ITEMS}

    matched = 0
    total = 0
    per_variant: dict[str, tuple[int, int]] = {}
    for label, make in TRUE_VARIANTS:
        got = 0
        count = 0
        for name in STANDARD_ITEMS:
            variant = make(name)
            if variant == name:
                continue  # その品目には当てはまらない変形。分母に入れない。
            count += 1
            if norm(variant) == keys[name]:
                got += 1
        per_variant[label] = (got, count)
        matched += got
        total += count

    collisions = 0
    pairs = 0
    for a, b in itertools.combinations(STANDARD_ITEMS, 2):
        pairs += 1
        if keys[a] == keys[b]:
            collisions += 1

    trapped = 0
    traps = 0
    for name in STANDARD_ITEMS:
        stripped = t6(name)
        if stripped == name:
            continue
        traps += 1
        if any(norm(stripped) == keys[other] for other in STANDARD_ITEMS):
            trapped += 1

    return {
        "A1": (matched, total), "per_variant": per_variant,
        "A2": (collisions, pairs), "A3": (trapped, traps),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    print(f"品目: {len(STANDARD_ITEMS)} / 品目の組: "
          f"{len(STANDARD_ITEMS) * (len(STANDARD_ITEMS) - 1) // 2}")

    results = {}
    for label, norm in NORMALISERS:
        r = measure(norm)
        results[label] = r
        a1, a1n = r["A1"]
        a2, a2n = r["A2"]
        a3, a3n = r["A3"]
        print(f"\n=== {label} ===")
        print(f"A1 拾えた真の一致: {a1} / {a1n} = {a1 / a1n:.3f}")
        for v, (got, count) in r["per_variant"].items():
            print(f"      {v}: {got} / {count}")
        print(f"A2 偽の一致(別の品目どうし): {a2} / {a2n} = {a2 / a2n:.4f}")
        print(f"A3 罠(撤去を落としたもの): {a3} / {a3n}")

    print("\n=== 対照 ===")
    base_a1 = results["N0 何もしない"]["A1"]
    print(f"対照1 N0 の A1: {base_a1[0]} / {base_a1[1]}"
          f" = {base_a1[0] / base_a1[1]:.3f}(揃えないと拾えない、の確認)")

    rng = random.Random(args.seed)
    letters = "床壁天井撤去張塗敷タイルカーペットビニルボード軽量鉄骨"
    for label, norm in NORMALISERS:
        fakes = []
        for _ in range(10):
            fake = [
                "".join(rng.choice(letters) for _ in range(len(name)))
                for name in STANDARD_ITEMS
            ]
            keys = [norm(f) for f in fake]
            fakes.append(sum(1 for a, b in itertools.combinations(keys, 2) if a == b))
        print(f"対照2 {label}: でたらめな 35 個での偽の一致 10 回: {fakes}"
              f" / 本物 {results[label]['A2'][0]}")

    repeats = [measure(n3)["A2"][0] for _ in range(3)]
    print(f"対照3 反復 3 回(N3 の A2): {repeats}")

    print("\n=== まとめ ===")
    for label, _ in NORMALISERS:
        r = results[label]
        a1 = r["A1"][0] / r["A1"][1]
        better = a1 > base_a1[0] / base_a1[1]
        clean = r["A2"][0] == 0 and r["A3"][0] == 0
        verdict = "採用" if better and clean else "保留" if better else "不採用"
        print(f"   {label}: A1 {a1:.3f} / A2 {r['A2'][0]} / A3 {r['A3'][0]}"
              f" → **{verdict}**")


if __name__ == "__main__":
    main()
