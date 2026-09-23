"""複数の経路の出力を突き合わせる(トライアルB「多方向からのアプローチ」)。

同じ対象を、上からも下からも、横からも、後ろからも解いて、最後に突き合わせる。
**この層がやるのは突き合わせだけで、値を選ばない。**

**何を出すか**

- **一致** … 2 つ以上の経路が同じことを言った
- **食い違い** … 2 つ以上の経路が違うことを言った。**どちらも選ばない**
- **片方にしか無いもの** … 1 つの経路だけが見つけた。**捨てない**
  (漏れの発見はここで起きる)

**一致が根拠を強めるのは、経路が本当に独立している場合だけ**

図面 PDF は 1 ファイル = 1 つのデータ源である。同じ PDF を線から読んでも
表から読んでも、独立した 2 つの証言にはならない。だから独立の数え方は
`arbitration/axis_quality_firewall.AxisEvidence.independence_key` と**同じ規則**
(元データの指紋があればそれ、無ければ source_id)を使う。
**ここで別の数え方を作らない。** 作ると、片方だけ甘い経路ができる。

人の入力(スタートキット)は図面とは別のデータ源なので、
**2 つ目の独立した軸になりうる**。ただしどの手法も未校正なので、
独立の数が 2 になっても、この層は**何も自動で確定させない。**
確定させるかどうかは仲裁層が決める。

**この層が決めないこと**

- どちらの値が正しいか(食い違いは食い違いのまま残す)
- 片方にしか無いものを採るかどうか(そのまま出して、下流に渡す)
- 階層(独立の数を報告するだけで、階層は仲裁層が決める)
"""


from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

#: 経路の種類。マスタープロンプト 3 節の並びをそのまま使う。
PathKind = Literal[
    "上から",   # 要約資料・メニューから(設計者の意図)
    "下から",   # 図面の線・記号・文字から(観測)
    "横から",   # 表(建具表、仕上表、器具表)から
    "別の角度", # 別の図面(展開図、天井伏図、電気図、解体図)
    "後ろから", # 見積の行の型から逆算(正解は見ない。書式と一般知識だけ)
    "外から",   # 公開されている基準・施工要領の知識から
]

PATH_KINDS: tuple[PathKind, ...] = ("上から", "下から", "横から", "別の角度", "後ろから", "外から")


@dataclass(frozen=True)
class PathItem:
    """1 つの経路が出した 1 件。**ほかの経路の出力を見ずに作られたもの。**"""

    path_id: str
    """経路の名前。"""

    path_kind: PathKind

    source_fingerprint: str
    """その経路が**どのデータから**読んだか。独立性はこれで数える。

    同じ PDF から読んだ経路は、手法が違っても同じ指紋になる。
    """

    item_key: str
    """突き合わせの鍵(見積の行の名前など)。**表記ゆれは呼ぶ側で揃える。**"""

    item_category: str = ""
    """行の種類(記号を数える行・形から出す行など)。得意分野の表に使う。"""

    value_range: tuple[float, float] | None = None
    """値。**値を出さない経路(「この行があるはず」だけ言う経路)では None。**"""

    unit: str | None = None

    evidence: str = ""
    """どこを見たか。**根拠の無い件は突き合わせに入れない。**"""

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id は空にできません")
        if self.path_kind not in PATH_KINDS:
            raise ValueError(f"知らない経路の種類です: {self.path_kind}")
        if not self.source_fingerprint:
            raise ValueError(
                "source_fingerprint は空にできません"
                "(どのデータから来たかが分からないと、独立かどうかを数えられません)"
            )
        if not self.item_key:
            raise ValueError("item_key は空にできません")
        if not self.evidence:
            raise ValueError(
                f"{self.item_key}: 根拠が空です。"
                "根拠の無い件は突き合わせに入れません(位置を必須にした決まり)"
            )
        if self.value_range is not None:
            lower, upper = self.value_range
            if lower > upper:
                raise ValueError(f"{self.item_key}: 値の下限が上限を超えています")
            if not self.unit:
                raise ValueError(f"{self.item_key}: 値があるのに単位がありません")


@dataclass(frozen=True)
class ReconciledItem:
    """1 つの鍵について突き合わせた結果。"""

    item_key: str
    status: Literal["一致", "食い違い", "片方にしか無い"]
    items: tuple[PathItem, ...]

    independent_source_count: int
    """**独立したデータ源の数。** 同じ指紋の経路はまとめて 1 と数える。"""

    path_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def strengthens(self) -> bool:
        """一致が根拠を強めるか。

        **一致していて、かつ独立したデータ源が 2 つ以上あるときだけ True。**
        それでも自動確定はしない(どの手法も未校正のため)。
        """
        return self.status == "一致" and self.independent_source_count >= 2

    @property
    def agreed_range(self) -> tuple[float, float] | None:
        """一致したときの重なり。**食い違いでは None**(片方を選ばないため)。"""
        if self.status == "食い違い":
            return None
        ranges = [item.value_range for item in self.items if item.value_range is not None]
        if not ranges:
            return None
        return (max(r[0] for r in ranges), min(r[1] for r in ranges))


@dataclass(frozen=True)
class Reconciliation:
    """突き合わせ全体の結果。"""

    items: tuple[ReconciledItem, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def agreed(self) -> tuple[ReconciledItem, ...]:
        return tuple(item for item in self.items if item.status == "一致")

    def conflicting(self) -> tuple[ReconciledItem, ...]:
        """**どちらも選ばない。** 人に確かめてもらう。"""
        return tuple(item for item in self.items if item.status == "食い違い")

    def single_path(self) -> tuple[ReconciledItem, ...]:
        """**その経路だけが見つけたもの。捨てない。**"""
        return tuple(item for item in self.items if item.status == "片方にしか無い")

    def strengthened(self) -> tuple[ReconciledItem, ...]:
        """独立した経路が 2 つ以上そろった一致。**それでも自動確定はしない。**"""
        return tuple(item for item in self.items if item.strengthens)

    def summary(self) -> str:
        lines = [
            f"突き合わせた鍵: {len(self.items)} 件",
            f"一致: {len(self.agreed())} 件"
            f"(うち独立した経路が 2 つ以上: {len(self.strengthened())} 件)",
            f"食い違い(どちらも選ばない): {len(self.conflicting())} 件",
            f"片方にしか無いもの(捨てない): {len(self.single_path())} 件",
        ]
        return "\n".join(lines + list(self.notes))


def reconcile(
    items: Iterable[PathItem],
    relative_tolerance: float = 0.05,
) -> Reconciliation:
    """経路ごとの出力を突き合わせる。**値は選ばない。**

    ``relative_tolerance`` は、値が「同じことを言っている」とみなす幅。
    既定は中心値の検査の連続量と同じ ±5%。**ここで新しい値を作らない。**

    結果は入力の順番に依存しない(同じ入力なら同じ出力)。
    """
    if relative_tolerance < 0:
        raise ValueError("許容差は 0 以上でなければなりません")

    grouped: dict[str, list[PathItem]] = defaultdict(list)
    for item in items:
        grouped[item.item_key].append(item)

    out: list[ReconciledItem] = []
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda i: (i.path_id, i.source_fingerprint))
        path_ids = tuple(sorted({item.path_id for item in group}))
        independent = len({item.source_fingerprint for item in group})

        if len(path_ids) == 1:
            out.append(
                ReconciledItem(
                    item_key=key,
                    status="片方にしか無い",
                    items=tuple(group),
                    independent_source_count=independent,
                    path_ids=path_ids,
                    reason=f"{path_ids[0]} だけが見つけた。**捨てない**",
                )
            )
            continue

        if _values_agree(group, relative_tolerance):
            same_source = independent < len(path_ids)
            reason = f"{len(path_ids)} 経路が同じことを言った"
            if same_source:
                reason += (
                    "。ただし**同じデータ源から来ているので、独立した証言ではない**"
                    f"(独立したデータ源は {independent} つ)"
                )
            out.append(
                ReconciledItem(
                    item_key=key,
                    status="一致",
                    items=tuple(group),
                    independent_source_count=independent,
                    path_ids=path_ids,
                    reason=reason,
                )
            )
        else:
            out.append(
                ReconciledItem(
                    item_key=key,
                    status="食い違い",
                    items=tuple(group),
                    independent_source_count=independent,
                    path_ids=path_ids,
                    reason="経路によって違うことを言っている。**どちらも選ばない**",
                )
            )
    return Reconciliation(items=tuple(out))


def _values_agree(group: Sequence[PathItem], relative_tolerance: float) -> bool:
    """値が重なっているか。

    **値を出さない経路は「食い違っていない」として扱う**
    (「この行があるはず」とだけ言う経路は、値については何も主張していない)。
    """
    ranges = [item.value_range for item in group if item.value_range is not None]
    if len(ranges) < 2:
        return True
    units = {item.unit for item in group if item.value_range is not None}
    if len(units) > 1:
        return False  # 単位が違うものを突き合わせない
    lower = max(r[0] for r in ranges)
    upper = min(r[1] for r in ranges)
    if lower <= upper:
        return True
    # 端どうしが許容差の中なら一致とみなす。
    reference = max(abs(upper), abs(lower), 1e-9)
    return (lower - upper) / reference <= relative_tolerance


def path_specialties(items: Iterable[PathItem]) -> dict[str, Counter]:
    """**どの経路が、どの種類の行を見つけたか**の表。

    マスタープロンプト 3 節の「経路ごとの得意分野の表」。
    経路を増やすかどうかを決める材料になる。
    """
    out: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        out[item.path_id][item.item_category or "種類なし"] += 1
    return dict(out)


def coverage_by_path(items: Iterable[PathItem]) -> dict[str, set[str]]:
    """経路ごとに、見つけた鍵の集合。単独の成績と合わせた成績を比べるため。"""
    out: dict[str, set[str]] = defaultdict(set)
    for item in items:
        out[item.path_id].add(item.item_key)
    return dict(out)
