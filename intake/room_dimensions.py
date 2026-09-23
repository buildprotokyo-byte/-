"""人が室ごとに入れる「縦・横・天井高」を受け取る。

2026-09-23、9周目。**画面(クリックの操作)は作らない。**
`intake/start_kit.py` と同じく、関数の入力として受け取るところまでである。

なぜ「面積」ではなく「縦・横」なのか
------------------------------------
6・7・8 周目で、図面の線をたどって室の面積を出す試みを 3 通り測った
(再現 0 → 0.178 → 0.000)。8 周目のキラークエスチョンで
**この案件の図面には面積を出すのに要る情報が入っていない**と分かった
(`docs/a2_wall_network_report.md`)。

そこで人に入れてもらう案に移ったが、**面積を 1 つもらうだけでは足りない。**
ゴールデンベンチマークの「図形から出す ㎡」22 件のうち、
床面積だけで出るのは 14 件で、残る 8 件(内壁の解体・間仕切り壁の軸組・
内壁ボード貼・壁クロスなど)には**周長と天井高**が要る。

縦と横をもらえば面積も周長も出る。**室ごとに 3 つで 22 件に届く。**

独立性についての但し書き(重要)
------------------------------
縦・横・天井高は、**同じ人が同じ時に入れる 1 つのデータ源**である。
ここから出す床面積・周長・壁面積は 3 つの数量だが、**3 つの独立した証言ではない。**
人が縦と横を取り違えれば面積は変わらず周長も変わらないまま、
壁の位置だけが入れ替わる。**入力の誤りは下流の全部に同じように効く。**
`intake/start_kit.py` の基準点と同じ構図なので、同じように根拠に残す。

刻みで表せない値を、黙って丸めない
----------------------------------
面積の正規形は 1cm²(`arbitration/units.py`)である。
縦 3641mm × 横 2731mm のように、掛けた結果が 1cm² の刻みで表せないことがある。
**そのときは丸めずに、下と上に振った範囲として出す。**
この層が扱うのは寸法までで、数量に直すのは `estimating/from_room_dimensions.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: 人が入れた室の寸法の手法ID。実体はここにあるが、`intake` は `arbitration` を
#: import するので、登録簿側(`arbitration/method_policies.py`)には
#: 文字列として置く(逆向きに import すると循環する)。
METHOD_HUMAN_ROOM_DIMENSIONS = "human_room_dimensions"

#: 寸法をどこで測ったか。**既定は「不明」。**
#: おーちゃんは 2026-09-23 に「面積は芯々で数える」と決めたが、
#: **人がどちらで測って入れたかは、決まりからは分からない。**
#: 既定で「芯々」に倒すと、内法で測った数字が芯々として下流へ流れる。
AreaBasis = Literal["芯々", "内法", "不明"]
AREA_BASES: tuple[AreaBasis, ...] = ("芯々", "内法", "不明")
BASIS_UNKNOWN: AreaBasis = "不明"

#: 室の寸法として受け付ける範囲(mm)。**外れたら止める。**
#: 下限 300mm はパイプスペースなど、上限 50m は体育館などを想定した広めの窓。
#: 窓を広く取るのは、**ありえない値だけを弾く**ためで、
#: もっともらしい取り違え(3640 と 4630 の入れ替え)はここでは捕まえられない。
MIN_DIMENSION_MM = 300.0
MAX_DIMENSION_MM = 50_000.0

#: 天井高として受け付ける範囲(mm)。
MIN_HEIGHT_MM = 1_200.0
MAX_HEIGHT_MM = 10_000.0


class RoomDimensionError(Exception):
    """人が入れた室の寸法が、そのままでは使えない形だった。

    **黙って直さない。** 単位の取り違え(3.64 と 3640)は、直した結果が
    もっともらしい数値になるので、後から気づけない。
    """


@dataclass(frozen=True)
class RoomDimension:
    """1 室ぶんの、人が入れた寸法。**どの欄も任意。**

    入れなかった欄は `None` のままにする。**0 で埋めない。**
    「入れていない」と「0 と入れた」は別である。
    """

    room_name: str
    length_mm: float | None = None
    """縦(mm)。人が図面や現場で測った長い側。**向きの意味は持たせない。**"""

    width_mm: float | None = None
    """横(mm)。"""

    ceiling_height_mm: float | None = None
    """天井高(mm)。**これが無いと壁の数量は出さない。**"""

    area_basis: AreaBasis = BASIS_UNKNOWN
    entered_by: str = ""
    """誰が入れたか。人の入力であることを根拠に残すため。"""

    def __post_init__(self) -> None:
        if not self.room_name.strip():
            raise RoomDimensionError("室の名前を入れてください")
        if self.area_basis not in AREA_BASES:
            raise RoomDimensionError(
                f"寸法の測り方 {self.area_basis!r} は "
                f"{list(AREA_BASES)} のどれかにしてください"
            )
        _check(self.length_mm, "縦", MIN_DIMENSION_MM, MAX_DIMENSION_MM)
        _check(self.width_mm, "横", MIN_DIMENSION_MM, MAX_DIMENSION_MM)
        _check(self.ceiling_height_mm, "天井高", MIN_HEIGHT_MM, MAX_HEIGHT_MM)

    @property
    def has_plan(self) -> bool:
        """縦と横が**両方**入っているか。片方だけでは面積も周長も出ない。"""
        return self.length_mm is not None and self.width_mm is not None

    @property
    def has_walls(self) -> bool:
        """壁の面積が出せるか(縦・横・天井高が揃っている)。"""
        return self.has_plan and self.ceiling_height_mm is not None

    @property
    def perimeter_mm(self) -> float | None:
        """室の周長(mm)。**長方形とみなしている。**

        L 字の室では実際より短く出る。人が入れるのは 2 つの数字だけなので、
        **この手法は原理的に長方形しか表せない。** 未校正にしてある理由の 1 つ。
        """
        if not self.has_plan:
            return None
        return 2.0 * (self.length_mm + self.width_mm)

    def floor_area_cm2_range(self) -> tuple[int, int] | None:
        """床面積を 1cm² きざみの範囲で返す。**丸めない。**

        きざみでちょうど表せるときは下限=上限になる。
        """
        if not self.has_plan:
            return None
        return _mm2_to_cm2_range(self.length_mm * self.width_mm)

    def wall_area_cm2_range(self) -> tuple[int, int] | None:
        """内壁の面積を 1cm² きざみの範囲で返す。**開口は引いていない。**

        引かないのは、建具の大きさがこの入力に入っていないためである。
        **引いていないことを数量の注記に必ず残す。**
        """
        if not self.has_walls:
            return None
        return _mm2_to_cm2_range(self.perimeter_mm * self.ceiling_height_mm)

    def missing(self) -> tuple[str, ...]:
        """入っていない欄の名前。**足りないものを黙らせないため。**"""
        out: list[str] = []
        if self.length_mm is None:
            out.append("縦")
        if self.width_mm is None:
            out.append("横")
        if self.ceiling_height_mm is None:
            out.append("天井高")
        return tuple(out)


def _check(value: float | None, label: str, low: float, high: float) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoomDimensionError(f"{label}は数で入れてください(入った値: {value!r})")
    if value != value or value in (float("inf"), float("-inf")):
        raise RoomDimensionError(f"{label}が数になっていません")
    if value <= 0:
        raise RoomDimensionError(f"{label}は正の数にしてください(入った値: {value})")
    if value < low:
        raise RoomDimensionError(
            f"{label} {value}mm は小さすぎます({low:.0f}mm 未満)。"
            "mm で入っているか確かめてください(3.64 と 3640 の取り違えが起きます)"
        )
    if value > high:
        raise RoomDimensionError(
            f"{label} {value}mm は大きすぎます({high:.0f}mm 超)"
        )


def _mm2_to_cm2_range(area_mm2: float) -> tuple[int, int]:
    """mm² を 1cm² きざみの範囲に直す。**丸めずに下と上へ振る。**"""
    import math

    exact = area_mm2 / 100.0
    low = math.floor(exact)
    high = math.ceil(exact)
    return (low, high)


__all__ = [
    "AREA_BASES",
    "BASIS_UNKNOWN",
    "METHOD_HUMAN_ROOM_DIMENSIONS",
    "RoomDimension",
    "RoomDimensionError",
]
