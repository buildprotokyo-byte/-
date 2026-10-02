"""K-63 3 節(b): 自由記述の工事名を閉じた語彙の細目 id に寄せる(機械、AI を呼ばない)。

行の名前は 3 回で揃わない。その半分近くは言い換え(同じ工事の書き方の違い)なので、
読み方を変えずに名前だけを語彙へ寄せると、回をまたいだ比べ方が揃う。
寄せ先は語彙の id だけで、数量・確度・状態には触らない。当たらない行は X99(その他)のまま残す。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_CLUES = Path(__file__).with_name("vocab") / "手がかり_default.json"
OTHER = "X99"


def _flat(text: str) -> str:
    """表記を揃える。**揃える処理は `sameness/normalize.py` の 1 か所だけ**(K-66 の条件 1)。

    ここに書き写すと「貼→張」を決める場所が 2 か所になり、片方だけ直したときに
    黙って食い違う。P011 の内訳 2,209 行で差し替えて測ったところ、
    **細目 id が変わった行は 0 件**だったので、そのまま 1 か所へ寄せた。
    """
    from sameness.normalize import flatten

    return flatten(text)


def load_clues(path: str | Path | None = None) -> dict[str, list[tuple[list[str], str]]]:
    raw = json.loads(Path(path or DEFAULT_CLUES).read_text(encoding="utf-8"))
    return {k: [([_flat(w) for w in words], code) for words, code in raw[k]] for k in ("撤去", "そのほか")}


def code_of(row: Mapping[str, Any], clues: Mapping[str, Sequence[tuple[list[str], str]]]) -> str:
    """1 行の細目 id。区分か科目が撤去なら撤去の手がかりだけを試す。"""
    text = _flat(" ".join(str(row.get(k) or "") for k in ("工事項目", "摘要", "工事", "何")))
    removal = row.get("区分") == "撤去" or row.get("科目") == "撤去" or "撤去" in text
    for words, code in clues["撤去" if removal else "そのほか"]:
        if all(w in text for w in words):
            return code
    return OTHER


def normalize_rows(rows: Sequence[Mapping[str, Any]], clues=None) -> list[dict[str, Any]]:
    """行を写し、「細目」を足して返す(もとの行は変えない)。"""
    clues = clues or load_clues()
    return [dict(r, 細目=code_of(r, clues)) for r in rows]
