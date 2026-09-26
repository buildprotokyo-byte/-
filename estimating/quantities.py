"""当てはめの入力になる「数量 1 件」。

入口(`intake/drawing_intake.py`)の `DrawingFinding` をそのまま使わずに
中立の形を置くのは、数量の出どころが図面だけではないからである。
人が入れた前提(スタートキット)も、将来つなぐ別のデータ源も、同じ形に
そろえてからこの層に入れる。**`intake/` には手を入れない。**
変換は `estimating/from_intake.py` 側が持つ。

ここが守ること
--------------
1. **単位は作った時点で検査する。** 解釈できない単位の数量は作らせない。
   下流で黙って落ちるより、入口で止めたほうが原因が分かる。
2. **確定しているかどうかは、この層が決め直さない。** 仲裁層が出した
   `action` と `confirmed_range` をそのまま運ぶ。`is_confirmed` は
   その2つが揃ったときだけ真になる。
3. **読めなかった属性は入れない。** 空文字や既定値で埋めない。属性が
   無いことと、属性が空であることは別である。
4. **何に基づくかを持つ。** 原則5(`docs/principles/start_kit.md`)の
   確定／推論に基づく／仮説に基づく／現地確認が必要。決め方は
   `estimating/basis.py` にあり、**この層は仲裁層の判定を読むだけで
   やり直さない。**
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from arbitration.units import UnitError, canonical_unit, normalise_range
from axes.reading.meaning import Meaning
from estimating.basis import basis_for
from estimating.decisive import DecisiveReason, decisive_reasons_for

#: 対象名の中で「種類」と「個別の鍵」を分ける印。
#: 入口が `建具数量::AW-1` `開き戸::ページ1` の形で出している。
TARGET_SEPARATOR = "::"


class QuantityError(Exception):
    """数量として受け付けられなかった。"""


def split_target(target: str) -> tuple[str, str | None]:
    """対象名を(種類, 鍵)に分ける。

    規則は種類のほうに当てる。`建具数量::AW-1` の全文に当てると、建具番号が
    1 つ増えるたびに規則を書き足すことになる。
    """
    if TARGET_SEPARATOR in target:
        kind, key = target.split(TARGET_SEPARATOR, 1)
        return kind, key
    return target, None


def normalise_text(value: str) -> str:
    """属性の突き合わせに使う正規化。

    全角と半角の違い(`ＳＤ` と `SD`)だけで規則が当たらなくなるのを防ぐ。
    **表記をそろえるだけで、意味は変えない。** 元の文字列は
    `QuantityItem.attributes` に入っているものがそのまま残る。
    """
    return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True)
class QuantityItem:
    """当てはめの対象になる数量 1 件。"""

    target: str
    """入口が付けた対象名。追跡のためそのまま持つ。"""

    value_range: tuple[float, float]
    """読めた値。1 点で読めたなら下限=上限。**宣言した単位のまま。**"""

    unit: str
    """印字どおりの表記(`箇所` `㎡`)。`arbitration/units.py` が解釈する。"""

    method_id: str
    source_kind: str = "drawing"
    axis_id: str = "image"

    tier: int | None = None
    """仲裁層が出した階層。まだ仲裁にかけていなければ None。"""

    action: str | None = None
    """`auto_confirm` / `requires_review` など。"""

    confirmed_range: tuple[int, int] | None = None
    """**正規形単位の整数**。確定していなければ None。"""

    derivation: str = "read"
    """仲裁層と同じ由来(`read` / `derived` / `assumed`)。

    **入口の読みからそのまま運ぶ。** ここで作り直すと、一般則で補った値が
    図面から読んだ値に化ける。
    """

    derivation_basis: tuple[str, ...] = ()
    """`derivation="derived"` のとき、計算の根拠にした値それぞれの由来。"""

    premise_ids: tuple[str, ...] = ()
    """この値が寄りかかっている案件の前提。**仮説かどうかは問わない。**"""

    hypothesis_premise_ids: tuple[str, ...] = ()
    """そのうち、**仮説として置かれた**前提。基づきを「仮説に基づく」にする。

    原則5「仮説に基づく行は、どの仮説に依存しているかを記録し、仮説が
    変わったら連動して見直す」のための記録である。
    """

    site_survey_reason: str | None = None
    """図面からは決められない理由。**根拠の無い「現地確認が必要」は作らない。**

    原則8(おーちゃんの回答): システムが根拠を付けて提案し、人が確認する。
    """

    # -- **決め手の証拠。** 札そのものではなく、証拠のほうを持つ。 -----------
    # 札を持たせる形にすると、証拠の無い札を手で付けられてしまう。
    # 札は `decisive` が `estimating/decisive.py` の 1 か所で作る。

    knowledge_rule_ids: tuple[str, ...] = ()
    """効いた知識のルール(図面に現れない行の決まり・基準・ガイドライン・波及)。"""

    agreeing_paths: tuple[str, ...] = ()
    """矛盾なく一致した経路(手法)。**仲裁層の記録から運ぶ。数え直さない。**"""

    paths_independent: bool | None = None
    """その経路が独立したデータ源だったか。**分からなければ None。**"""

    question_id: str = ""
    """人のどの回答から来たか。"""

    summary_item: str = ""
    """要約資料(メニュー)のどの項目から探し当てたか。"""

    attributes: Mapping[str, str] = field(default_factory=dict)
    """規則が条件に使える属性(建具表の種別など)。**読めたものだけ。**"""

    meaning: Meaning | None = None
    """原則2の意味の4欄(`axes/reading/meaning.py`)。**入口から運ぶだけ。**

    ここで作らないのは、意味を知っているのは読んだ側だけだからである。
    **None は「意味が無い」ではなく「入口がまだ付けていない」。**
    値を見て後から組み立てると、読めていない欄を埋めることになる。
    """

    provenance: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    """この数量について記録しておくこと(属性が食い違った、など)。"""

    def __post_init__(self) -> None:
        if not self.target:
            raise QuantityError("target は空にできません")
        try:
            canonical_unit(self.unit)
            normalise_range(self.unit, self.value_range[0], self.value_range[1])
        except UnitError as error:
            raise QuantityError(
                f"{self.target} の単位または値が受け付けられません: {error}"
            ) from error
        if self.derivation not in ("read", "derived", "assumed"):
            raise QuantityError(
                f"{self.target} の由来 {self.derivation!r} は知らない値です"
                "(read / derived / assumed のどれか)"
            )
        if self.derivation != "derived" and self.derivation_basis:
            raise QuantityError(
                f"{self.target}: 根拠の由来を持てるのは derived のときだけです"
            )
        unknown_hypotheses = set(self.hypothesis_premise_ids) - set(self.premise_ids)
        if unknown_hypotheses:
            raise QuantityError(
                f"{self.target}: 仮説として挙げた前提が、依存する前提に入っていません: "
                + "、".join(sorted(unknown_hypotheses))
            )
        for name, value in self.attributes.items():
            if not isinstance(value, str) or not value.strip():
                raise QuantityError(
                    f"{self.target} の属性 {name} が文字列ではありません"
                    "(読めなかった属性は入れないでください)"
                )

    @property
    def kind(self) -> str:
        return split_target(self.target)[0]

    @property
    def phase(self) -> str | None:
        """現況か計画か。**意味の欄からだけ読む。対象名から切り出さない。**

        `不明` は「決まっていない」という記録なので、そのまま返す。
        **意味そのものが付いていなければ None**(`不明` ですらない)。
        """
        return self.meaning.phase if self.meaning is not None else None

    @property
    def key(self) -> str | None:
        return split_target(self.target)[1]

    @property
    def canonical_unit(self) -> str:
        return canonical_unit(self.unit)

    @property
    def canonical_range(self) -> tuple[int, int]:
        """読めた値を正規形の整数に直したもの。"""
        return normalise_range(self.unit, self.value_range[0], self.value_range[1])[1]

    @property
    def is_confirmed(self) -> bool:
        """**仲裁層が自動確定したか。** この層は判定をやり直さない。"""
        return self.action == "auto_confirm" and self.confirmed_range is not None

    @property
    def effective_derivation(self) -> str:
        """根拠まで遡った由来。

        `axes/reading/protocol.py` と同じ規則で、**計算した値も、根拠に
        「一般則で補った」が1つでもあれば「一般則で補った」に落ちる。**
        """
        if self.derivation == "derived" and "assumed" in self.derivation_basis:
            return "assumed"
        return self.derivation

    @property
    def decisive(self) -> tuple[DecisiveReason, ...]:
        """**この数量が出た決め手。**(`estimating/decisive.py` の 5 種類)

        **空は「決め手が無い」**であって「観測だけで出た」ではない。
        一般則で補った値のように、名乗れる証拠が無いものは空のまま残る。
        埋めずに `lines_without_reason()` で名指しする。
        """
        return decisive_reasons_for(
            effective_derivation=self.effective_derivation,
            source_kind=self.source_kind,
            knowledge_rule_ids=self.knowledge_rule_ids,
            agreeing_paths=self.agreeing_paths,
            paths_independent=self.paths_independent,
            question_id=self.question_id,
            summary_item=self.summary_item,
        )

    @property
    def basis(self) -> str:
        """この値が何に基づくか(原則5の4つ)。**弱いほうが勝つ。**"""
        return basis_for(
            is_confirmed=self.is_confirmed,
            effective_derivation=self.effective_derivation,
            hypothesis_premise_ids=tuple(self.hypothesis_premise_ids),
            site_survey_reason=self.site_survey_reason,
        )

    def attribute(self, name: str) -> str | None:
        return self.attributes.get(name)
