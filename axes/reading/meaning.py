"""読み取った値に付ける**意味**の 4 欄(最上位の原則 2)。

原則(`docs/principles/start_kit.md` 2節)はこう書かれている。

> 読み取った値には、必ず意味を付ける：それは何か、どこのものか、
> 現況か計画か、目的にどう関係するか
> 意味が付けられない値は、数量に使わない

おーちゃんの回答9(2026-09-22)により、**新しく書くコードではこの 4 欄を
今から必須にする。** 既存のコードは修正のついでに順に入れる。

4 欄
----
``what``         … それは何か(``縮尺`` ``専有延床面積`` ``建具の数量`` など)
``where``        … どこのものか(``ページ12の表題欄`` ``建具表 WD-01 の行`` など)
``phase``        … 現況か計画か(`PHASES` のどれか)
``purpose_link`` … 目的にどう関係するか

**空文字で埋めることはできない。** 4 欄のどれも、値を入れないまま
作ることはできない(`ValueError`)。

まだ埋められない欄の扱い
------------------------
`phase` と `purpose_link` は、いまの実装では埋まらないことがある。

- `phase` … 人がページの種類を宣言していない図面では、そのページが現況か
  計画かは決まらない。**図面の文字からは決まらない**ので推測しない。
  この場合は `PHASE_UNKNOWN`(``不明``)を明示的に入れる。
- `purpose_link` … 2つに分かれる。
  - 目的そのものが渡されていない … `PURPOSE_UNESTABLISHED`(``目的未確立``)
  - 目的は渡されているが、**その目的と個々の値を結び付ける経路が無い**
    … `PURPOSE_RECEIVED_UNLINKED`(``目的受領済み・結び付けは未実装``)

  目的の受け取り口(`intake/start_kit.py` の `Purpose`)は 2026-09-22 に入った
  ので、いまは「受け皿が無い」と「結び付けが無い」を分けて記録できる。
  **方向性の自由記述を `purpose_link` に写さない。** 写すのは結び付けではない
  (原則 3-2 の二段階目が未実装)。

**どちらも「埋め忘れ」ではなく「まだ決まっていない」という記録である。**
`is_complete` が False の値は、原則どおりなら数量に使えない。
**ただし、この層ではまだ止めていない。** いま止めると、この経路から出る
数量が 0 件になる(`docs/principles/queue3_redesign.md` 6節)。
いつ止めるかは、目的の受け取り口ができてからの判断になる
(`docs/ocr_scanned_pages_report.md` の判断待ち)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: ``phase`` に入れてよい値。
PHASE_EXISTING = "現況"
PHASE_PLANNED = "計画"
PHASE_DEMOLITION = "解体"
PHASE_UNKNOWN = "不明"

PHASES: frozenset[str] = frozenset(
    {PHASE_EXISTING, PHASE_PLANNED, PHASE_DEMOLITION, PHASE_UNKNOWN}
)

#: 目的そのものが渡されていないことを表す、`purpose_link` の値。
PURPOSE_UNESTABLISHED = "目的未確立"

#: 目的は渡されているが、この値との結び付けが未実装であることを表す値。
#:
#: 原則 3-2 の二段階目(AI が図面を読んだ後に詳細な目的を組み立て、人が確認する)が
#: 入るまで、個々の値が目的のどこに効くのかは決められない。
PURPOSE_RECEIVED_UNLINKED = "目的受領済み・結び付けは未実装"

#: 結び付けが済んでいないことを表す値の全部。`is_complete` の判定に使う。
#: **ここに漏れがあると、決まっていない値が「意味が付いた」ことになる。**
PURPOSE_PLACEHOLDERS: frozenset[str] = frozenset(
    {PURPOSE_UNESTABLISHED, PURPOSE_RECEIVED_UNLINKED}
)


@dataclass(frozen=True)
class Meaning:
    """読み取った値 1 つに付ける意味。**4 欄すべて必須。**"""

    what: str
    where: str
    phase: str
    purpose_link: str

    def __post_init__(self) -> None:
        for field_name in ("what", "where", "phase", "purpose_link"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"意味の欄 {field_name} が空です。"
                    "埋められない欄は、空にせず「不明」「目的未確立」のように"
                    "決まっていないことを書いてください"
                )
        if self.phase not in PHASES:
            raise ValueError(
                f"phase は {sorted(PHASES)} のどれかである必要があります: {self.phase!r}"
            )

    @property
    def is_complete(self) -> bool:
        """原則 2 の「意味が付けられた」状態か。

        `phase` が ``不明``、`purpose_link` が `PURPOSE_PLACEHOLDERS` の
        どれかのあいだは False。
        **False でもこの層は値を止めない**(モジュール冒頭)。
        """
        return (
            self.phase != PHASE_UNKNOWN
            and self.purpose_link not in PURPOSE_PLACEHOLDERS
        )

    @property
    def unresolved(self) -> tuple[str, ...]:
        """まだ決まっていない欄の名前。"""
        out: list[str] = []
        if self.phase == PHASE_UNKNOWN:
            out.append("phase")
        if self.purpose_link in PURPOSE_PLACEHOLDERS:
            out.append("purpose_link")
        return tuple(out)

    def as_dict(self) -> dict[str, Any]:
        """証拠(`provenance`)にそのまま載せる形。"""
        return {
            "what": self.what,
            "where": self.where,
            "phase": self.phase,
            "purpose_link": self.purpose_link,
            "is_complete": self.is_complete,
            "unresolved": list(self.unresolved),
        }
