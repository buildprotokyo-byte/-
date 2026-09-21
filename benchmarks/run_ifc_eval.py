"""ステップ3・4: 読み取り結果 → IFC 空間階層 → 矛盾検出、の一気通しの効果測定。

何を測るか
----------
劣化した図面では、前処理の選び方によって記号(開き戸・窓)の線が失われます
(benchmarks/run_vtracer_eval.py で実測済み)。問題は、**失われたことが読み取り側から
は分からない**ことです。v8 の「穴1: 情報の欠落」がそのまま「穴2: 尤もらしい誤り」に
転化する、という構図です。

ここでは、失われた記号が IFC の空間階層に載せた時点で**明示的な矛盾として表に出るか**
を測ります。「部屋には出入口がある」という絶対ルールに照らせば、戸を読み落とした部屋は
``space_without_door`` として必ず上がるはずです。

記号認識について(正直な制約)
------------------------------
Grounding DINO の重みが取得できないため、**記号の認識そのものは行っていません**。
代わりに「正解の記号位置に、前処理後のマスクでインクがどれだけ残っているか」を測り、
劣化のない図面での残り方と比べて一定割合を下回ったものを「読めなかった」と扱います。
つまりここで測っているのは認識器の性能ではなく、**前処理が記号を消したかどうかと、
消えたことを絶対ルール軸が検出できるか**です。

実行: ``python -m benchmarks.run_ifc_eval``
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from axes.absolute_rule_axis.ifc_containment import ElementReading, audit, build_from_reading
from axes.image_axis.vtracer_vectorizer import binarize_classical, vectorize
from benchmarks.synthetic_plans import (
    ALL_LEVELS,
    DegradationLevel,
    make_plan,
    wall_ink_mask,
    symbol_ink_masks,
)

#: 記号の線の位置をこれだけ膨らませた範囲を「その記号の領域」とみなす。
#: medium / heavy では図面が少し回転するため、その分の余裕。
SYMBOL_DILATION = 9

#: 壁のインクをこれだけ膨らませた範囲は、記号の判定から除く(壁のインクを
#: 記号が残っている証拠として数えないため)。
WALL_DILATION = 3

#: 正解の記号インク量に対する残存率がこれを下回ったら「読めなかった」とみなす。
READABLE_RATIO = 0.5

#: 残存率がこれを超えたら、その領域はノイズで埋まっていて記号かどうか判断できない
#: とみなし、やはり「読めなかった」扱いにする(v8 の「棄権」に相当)。
NOISE_RATIO = 2.0


def _ellipse(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))


@dataclass(frozen=True)
class SymbolProbe:
    """記号 1 つ分の判定領域と、そこにあるべきインク量(正解値)。"""

    region: np.ndarray
    expected_ink: int


def _symbol_probes() -> dict[int, SymbolProbe]:
    """記号ごとの判定領域を作る。

    判定領域 = 記号の線を膨らませた範囲 − 壁のインクを膨らませた範囲。
    壁を除くのは、記号が消えていても近くの壁のインクを「記号が残っている」と
    数えてしまわないようにするためです。分母は**正解の記号インク量**であり、
    前処理後の画像ではありません(前処理側を基準にすると、最初から記号を消す
    前処理が「何も失っていない」ように見えてしまう)。
    """
    walls = cv2.dilate(wall_ink_mask(), _ellipse(WALL_DILATION)) > 0
    probes: dict[int, SymbolProbe] = {}
    for index, ink in symbol_ink_masks().items():
        region = (cv2.dilate(ink, _ellipse(SYMBOL_DILATION)) > 0) & ~walls
        probes[index] = SymbolProbe(region=region, expected_ink=int(((ink > 0) & region).sum()))
    return probes


def classical_mask(image: np.ndarray, ksize: int) -> np.ndarray:
    return binarize_classical(image, ksize)


def vtracer_mask(image: np.ndarray, filter_speckle: int = 4) -> np.ndarray:
    ink = cv2.bitwise_not(binarize_classical(image, 1))
    return vectorize(ink, filter_speckle=filter_speckle).to_mask()


FRONT_ENDS = {
    "古典(median=1)": lambda img: classical_mask(img, 1),
    "古典(median=3)": lambda img: classical_mask(img, 3),
    "大津→VTracer(fs=4)": lambda img: vtracer_mask(img, 4),
}


@dataclass
class IfcEvalResult:
    level: DegradationLevel
    front_end: str
    doors_read: int
    windows_read: int
    findings: dict[str, int]

    @property
    def symbols_lost(self) -> int:
        return (4 - self.doors_read) + (4 - self.windows_read)

    @property
    def caught(self) -> int:
        """読み落とした記号のうち、絶対ルール軸が矛盾として拾えた数。"""
        return self.findings.get("space_without_door", 0) + self.findings.get(
            "space_without_window", 0
        )


def evaluate(level: DegradationLevel, front_end: str, seed: int = 7) -> IfcEvalResult:
    probes = _symbol_probes()
    plan = make_plan(level, seed=seed)
    mask = FRONT_ENDS[front_end](plan.image) > 0

    elements: list[ElementReading] = [
        ElementReading(f"壁{i + 1}", "wall", "1階") for i in range(len(plan.walls))
    ]

    doors_read = windows_read = 0
    for index, symbol in enumerate(plan.symbols):
        probe = probes[index]
        ink = int((mask & probe.region).sum())
        ratio = (ink / probe.expected_ink) if probe.expected_ink else 0.0
        if not READABLE_RATIO <= ratio <= NOISE_RATIO:
            continue  # 読めなかった / ノイズで判断できない → 要素として登録しない
        elements.append(
            ElementReading(f"{symbol.kind}{index + 1}", symbol.kind, symbol.room)
        )
        if symbol.kind == "door":
            doors_read += 1
        else:
            windows_read += 1

    rooms = [room.name for room in plan.rooms]
    report = audit(build_from_reading(rooms, elements))

    return IfcEvalResult(
        level=level,
        front_end=front_end,
        doors_read=doors_read,
        windows_read=windows_read,
        findings=report.summary(),
    )


def main(seed: int = 7) -> list[IfcEvalResult]:
    print("読み取り → IFC 空間階層 → 矛盾検出\n")
    print("正解: 開き戸 4、窓 4、部屋 4(各室に戸 1・窓 1 ずつ)\n")

    header = (
        f"{'劣化':<8}{'前処理':<22}{'戸':>4}{'窓':>4}"
        f"{'見落とし':>10}{'検出した矛盾':>14}"
    )
    print(header)
    print("-" * 70)

    results: list[IfcEvalResult] = []
    for level in ALL_LEVELS:
        for front_end in FRONT_ENDS:
            result = evaluate(level, front_end, seed)
            results.append(result)
            print(
                f"{level:<8}{front_end:<22}{result.doors_read:>4}{result.windows_read:>4}"
                f"{result.symbols_lost:>10}{result.caught:>14}"
            )
        print()

    total_lost = sum(r.symbols_lost for r in results)
    total_caught = sum(r.caught for r in results)
    print(
        f"合計: 見落とした記号 {total_lost} 件のうち、"
        f"絶対ルール軸が矛盾として拾えたのは {total_caught} 件"
    )
    if total_lost:
        print(f"検出率: {total_caught / total_lost * 100:.1f}%")
    return results


if __name__ == "__main__":
    main()
