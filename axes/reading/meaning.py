"""値に付ける「意味」の4欄。

おーちゃんの原則2(`docs/principles/start_kit.md`)。

    読み取った値には、必ず意味を付ける：それは何か、どこのものか、
    現況か計画か、目的にどう関係するか
    意味が付けられない値は、数量に使わない

2026-09-22 の判断(status.md 判断待ち21)で、**新しく書くコードでは4欄を
今から必須**と決まった。既存のコードは修正のついでに順に入れる。
このモジュールはその4欄の置き場である。

ここが守ること
--------------
1. **4欄すべてを引数で受け取る。既定値を持たせない。** 既定を持たせると、
   付け忘れが「付けた」ことになって黙って下流へ行く。
2. **埋められないことを、埋めたことにしない。** 目的(原則3-2)を受け取る場所は
   このリポジトリにまだ無いので、`purpose_link` は誰も本当には埋められない。
   そこに作った文字列を入れる代わりに、`PURPOSE_UNLINKED` という**明示の印**を
   置き、`is_complete` を False にする。
3. **判定はしない。** この4欄は値の意味を運ぶだけで、階層にも確定にも効かない。

**原則との食い違いを1つ、そのまま残しておく(2026-09-22):** 原則2の2行目は
「意味が付けられない値は、数量に使わない」である。`PURPOSE_UNLINKED` を許すのは
その行を緩めている。緩めずに必須にすると、目的の受け皿ができるまで
**この経路から出る数量が0件になる**(`docs/principles/queue3_redesign.md` 6節)。
原則5の「前提が揃うまで止めるのではなく、何に基づくかを区別して出す」と
正面からぶつかるところなので、**どちらを採るかはおーちゃんの判断**である。
判断が出たら、ここと `axes/image_axis/pdf_dimensions.py` を直す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 目的にどう関係するかが、まだ誰も埋められないことの印。
#:
#: **「関係が無い」ではない。** 目的(原則3-2)を受け取る場所がリポジトリに
#: 無いので、埋めようがないという意味である。
PURPOSE_UNLINKED = "目的未受領"

#: 現況か計画か。`intake/start_kit.py` の `PageDeclaration.phase` と同じ語を使う。
#: ``"不明"`` は**人が宣言していない**という意味で、「どちらでもない」ではない。
PHASES: tuple[str, ...] = ("現況", "計画", "解体", "不明")


class MeaningError(ValueError):
    """意味の4欄が揃っていない、または知らない語が入っている。"""


@dataclass(frozen=True)
class Meaning:
    """1 つの値に付ける意味。**4欄すべてを明示して作る。**"""

    what: str
    """それは何か。例: ``図面に記入された寸法``。"""

    where: str
    """どこのものか。例: ``ページ1 x=200.0,y=700.0 - x=406.4,y=700.0``。

    **ページ番号だけでは足りない。** 図面の中のどこを指しているかが要る。
    """

    phase: str
    """現況か計画か解体か。人が宣言していなければ ``"不明"``。"""

    purpose_link: str
    """目的にどう関係するか。埋められないときは `PURPOSE_UNLINKED`。"""

    def __post_init__(self) -> None:
        for name in ("what", "where", "phase", "purpose_link"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MeaningError(
                    f"意味の欄 {name} が空です(4欄すべてを明示する約束です)"
                )
        if self.phase not in PHASES:
            raise MeaningError(
                f"知らない phase です: {self.phase!r}(使えるのは {list(PHASES)})"
            )

    @property
    def is_complete(self) -> bool:
        """4欄が本当に埋まっているか。

        `PURPOSE_UNLINKED` と ``phase == "不明"`` は**埋まっていない扱い**にする。
        原則2の「意味が付けられない値」に当たるのがどれかを、後から数えられる
        ようにしておくため。
        """
        return self.purpose_link != PURPOSE_UNLINKED and self.phase != "不明"

    def as_dict(self) -> dict[str, Any]:
        """根拠として `provenance` に載せる形。"""
        return {
            "what": self.what,
            "where": self.where,
            "phase": self.phase,
            "purpose_link": self.purpose_link,
            "is_complete": self.is_complete,
        }
