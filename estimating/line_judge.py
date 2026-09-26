"""行が「見積に載せる工事の行か」の線引き(K-46)。**判定する役はここ 1 か所に置く。**

なぜここに置くのか
------------------
K-45 で、この線引きを機械(工事の語の一覧=案 B、「改修」入り)と AI で比べ、
AI が明確に強かった(あるべき行を通した数 92 対 109、囮を通した数 6 対 5、
3 回とも同じ数)。K-46 でおーちゃんが「読む・判断する仕事は AI に寄せる。
線引きは AI。本番の経路を AI に替える」と決めた。

それまで本番(`app.py`)は線を引かずに全部の行を候補として出し、線は採点の側で
案 B の語の一覧で引いていた。ここで線引きを本番の中の 1 つの役にし、差し替え
られる形にする。

役(どれも ``judge(rows)`` で、行ごとに `LineVerdict` を返す)
--------------------------------------------------------------
- `AIJudgmentFileJudge` … **本番の既定。**AI が出した判定のファイルを読む。
  形は ``[{"番号": n, "判定": "工事の行"|"工事の行ではない", "理由": "..."}]``。
  番号の代わりに ``"工事"``(と ``"場所"``)を書けば、文字で引く。
- `AnthropicLineJudge` … AI をその場で呼ぶ。**鍵が無ければ作れない**
  (`LineJudgeUnavailable`)。鍵やネットワークはこのリポジトリでは足さない。
- `WordListJudge` … いままでの工事の語の一覧(案 B、「改修」入り)。比べるために残す。

守ること
--------
- **判定が無い行は落とさない。**AI の判定が渡されていない行・判定が無い行は
  「要確認」として残す(K-42 の「少しずれたものを捨てない」)。語の一覧で黙って
  落とすこともしない。
- 「工事の行ではない」とされた行も**消さない。**見積の行から外し、理由つきで残す。
- **何も確定させない。**「工事の行」と判定されても、行の確かさは上げない。
- どの役が判定したか(AI/語の一覧/判定なし)を、行の根拠に残す。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

#: 判定。
VERDICT_WORK = "工事の行"
VERDICT_NOT_WORK = "工事の行ではない"
VERDICT_NEEDS_CHECK = "要確認"
VERDICTS = (VERDICT_WORK, VERDICT_NOT_WORK, VERDICT_NEEDS_CHECK)

#: 判定した役。
JUDGE_AI = "AI"
JUDGE_WORDS = "語の一覧"
JUDGE_NONE = "判定なし"

#: 語の一覧(案 B)。`docs/k42_electrical_count_remeasure_criteria.md` の一覧に、
#: K-42(a) で「改修」を足したもの。**比べるために残す。本番の既定ではない。**
WORK_WORDS: tuple[str, ...] = (
    "新設", "新", "交換", "交", "移設", "移", "脱着", "脱", "撤去", "撤", "取付",
    "取り付け", "設置", "増設", "張", "貼", "組", "造作", "補修", "処分", "搬出",
    "養生", "塗装", "盛替", "復旧", "切替", "接続", "更新", "研磨", "清掃", "剥がし",
    "解体", "充填", "敷設", "配線", "配管", "改修",
)

REASON_NO_AI_JUDGMENT = "AI の判定が渡されていない行。落とさずに要確認として残した"
REASON_AI_GAVE_NOTHING = "AI の判定にこの行が無い。落とさずに要確認として残した"


class LineJudgeError(ValueError):
    """判定のファイルの形が違う。"""


class LineJudgeUnavailable(RuntimeError):
    """AI を呼べない(鍵・部品が無い)。"""


def _key(text: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(text or "")).split())


@dataclass(frozen=True)
class LineText:
    """判定する役に渡す 1 行の文字。**数量や金額は渡さない。**"""

    number: int
    work: str
    place: str


@dataclass(frozen=True)
class LineVerdict:
    verdict: str
    judge: str
    reason: str

    def as_evidence(self) -> dict[str, str]:
        return {"線引き": self.judge, "判定": self.verdict, "理由": self.reason}


class LineJudge(Protocol):
    name: str

    def judge(self, rows: Sequence[LineText]) -> list[LineVerdict]: ...


# ---------------------------------------------------------------------------
# 語の一覧(案 B)
# ---------------------------------------------------------------------------


class WordListJudge:
    """工事の語が 1 つでも入っていれば工事の行、無ければ工事の行ではない(案 B)。"""

    name = JUDGE_WORDS

    def __init__(self, words: Iterable[str] = WORK_WORDS) -> None:
        self.words = tuple(_key(w) for w in words if _key(w))

    def judge(self, rows: Sequence[LineText]) -> list[LineVerdict]:
        out: list[LineVerdict] = []
        for row in rows:
            text = _key(row.work)
            hit = next((w for w in self.words if w in text), None)
            if hit:
                out.append(LineVerdict(VERDICT_WORK, JUDGE_WORDS, f"工事の語「{hit}」を含む"))
            else:
                out.append(LineVerdict(VERDICT_NOT_WORK, JUDGE_WORDS, "工事の語を含まない"))
        return out


# ---------------------------------------------------------------------------
# AI の判定のファイル(本番の既定)
# ---------------------------------------------------------------------------


def _entry_verdict(entry: Mapping[str, Any]) -> str:
    verdict = entry.get("判定")
    if verdict not in (VERDICT_WORK, VERDICT_NOT_WORK):
        raise LineJudgeError(
            f"判定は「{VERDICT_WORK}」か「{VERDICT_NOT_WORK}」で書いてください: {verdict!r}"
        )
    return str(verdict)


class AIJudgmentFileJudge:
    """AI が出した判定を読む役。**渡されていなければ、全部の行を要確認にする。**

    引き方(上から順に当てる):

    1. ``"工事"`` と ``"場所"`` の両方が書かれた判定 … 行の工事・場所の文字で引く。
    2. ``"工事"`` だけの判定 … 行の工事の文字で引く(場所を問わない)。
    3. ``"番号"`` だけの判定 … 行の番号(本番の出力の番号、1 始まり)で引く。

    文字は NFKC にして空白を除いて比べる。**同じ文字に食い違う判定があれば
    読み込みで止める**(どちらかを黙って選ばない)。
    """

    name = JUDGE_AI

    def __init__(self, entries: Sequence[Mapping[str, Any]] | None = None, source: str = "") -> None:
        self.given = entries is not None
        self.source = source
        self._by_text: dict[tuple[str, str], tuple[str, str]] = {}
        self._by_work: dict[str, tuple[str, str]] = {}
        self._by_number: dict[int, tuple[str, str]] = {}
        for entry in entries or ():
            if not isinstance(entry, Mapping):
                raise LineJudgeError(f"判定の 1 つが辞書ではありません: {entry!r}")
            verdict = _entry_verdict(entry)
            value = (verdict, str(entry.get("理由") or ""))
            if "工事" in entry and entry.get("場所") is not None:
                self._put(self._by_text, (_key(entry["工事"]), _key(entry["場所"])), value)
            elif "工事" in entry:
                self._put(self._by_work, _key(entry["工事"]), value)
            elif "番号" in entry:
                self._put(self._by_number, int(entry["番号"]), value)
            else:
                raise LineJudgeError(f"判定に「番号」も「工事」もありません: {dict(entry)!r}")

    @staticmethod
    def _put(table: dict, key: Any, value: tuple[str, str]) -> None:
        if key in table and table[key][0] != value[0]:
            raise LineJudgeError(f"同じ行に食い違う判定があります: {key!r}")
        table.setdefault(key, value)

    @classmethod
    def from_path(cls, path: str | Path | None) -> "AIJudgmentFileJudge":
        if path is None:
            return cls(None)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, Mapping):
            data = data.get("判定", data.get("行"))
        if not isinstance(data, list):
            raise LineJudgeError("判定のファイルは判定の一覧(JSON の配列)にしてください")
        return cls(data, source=str(path))

    def _lookup(self, row: LineText) -> tuple[str, str] | None:
        return (
            self._by_text.get((_key(row.work), _key(row.place)))
            or self._by_work.get(_key(row.work))
            or self._by_number.get(row.number)
        )

    def judge(self, rows: Sequence[LineText]) -> list[LineVerdict]:
        out: list[LineVerdict] = []
        for row in rows:
            if not self.given:
                why = f"{REASON_NO_AI_JUDGMENT}({self.source})" if self.source else REASON_NO_AI_JUDGMENT
                out.append(LineVerdict(VERDICT_NEEDS_CHECK, JUDGE_NONE, why))
                continue
            found = self._lookup(row)
            if found is None:
                out.append(LineVerdict(VERDICT_NEEDS_CHECK, JUDGE_NONE, REASON_AI_GAVE_NOTHING))
            else:
                out.append(LineVerdict(found[0], JUDGE_AI, found[1]))
        return out


# ---------------------------------------------------------------------------
# AI をその場で呼ぶ(鍵があるときだけ)
# ---------------------------------------------------------------------------

#: K-45 の T3 で AI に渡した決まりと同じ中身。
AI_INSTRUCTIONS = (
    "改装工事の図面から読み取った行が並んでいます(工事・場所)。1 行ずつ、"
    "見積に載せる工事の行か(何かを新設・撤去・交換・取付などする工事を表しているか)を判定してください。\n"
    "- 「可動棚6枚」のように工事の動詞が無くても、何かを取り付ける・作ることが読み取れれば工事の行とする。\n"
    "- 室名だけ、寸法だけ、「取付位置施主確認」のような指示・メモ、「床張方向」のような作図の注記は"
    "工事の行ではない。\n"
    '答えは JSON の配列だけ: [{"番号": n, "判定": "工事の行"|"工事の行ではない", "理由": "短く"}]'
)

DEFAULT_MODEL = "claude-opus-5"


def _json_array(text: str) -> list[Any]:
    match = re.search(r"\[.*\]", text, flags=re.S)
    if not match:
        raise LineJudgeError("AI の答えに JSON の配列がありません")
    data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise LineJudgeError("AI の答えが配列ではありません")
    return data


class AnthropicLineJudge:
    """AI をその場で呼んで判定させる。**鍵が無ければ作れない**(`LineJudgeUnavailable`)。

    答えに無い行・形の違う答えの行は、ファイルの役と同じく要確認として残す。
    """

    name = JUDGE_AI

    def __init__(self, model: str | None = None, client: Any = None) -> None:
        self.model = model or os.environ.get("LINE_JUDGE_MODEL") or DEFAULT_MODEL
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise LineJudgeUnavailable(
                    "AI を呼ぶ鍵がありません(環境変数 ANTHROPIC_API_KEY が設定されていない)"
                )
            try:
                import anthropic  # noqa: PLC0415  入れていなくてもリポジトリは動く
            except ImportError as exc:
                raise LineJudgeUnavailable(
                    "AI を呼ぶ部品(anthropic)が入っていません。pip install anthropic で入ります"
                ) from exc
            client = anthropic.Anthropic()
        self._client = client

    def judge(self, rows: Sequence[LineText]) -> list[LineVerdict]:
        if not rows:
            return []
        payload = [{"番号": r.number, "工事": r.work, "場所": r.place} for r in rows]
        response = self._client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=AI_INSTRUCTIONS,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        entries: list[Mapping[str, Any]] = []
        for entry in _json_array(text):
            if isinstance(entry, Mapping) and "番号" in entry and entry.get("判定") in (
                VERDICT_WORK,
                VERDICT_NOT_WORK,
            ):
                entries.append({"番号": entry["番号"], "判定": entry["判定"], "理由": entry.get("理由", "")})
        return AIJudgmentFileJudge(entries).judge(rows)


# ---------------------------------------------------------------------------
# 選ぶ
# ---------------------------------------------------------------------------

JUDGE_CHOICE_AI_FILE = "ai"
JUDGE_CHOICE_AI_API = "ai-api"
JUDGE_CHOICE_WORDS = "words"
JUDGE_CHOICES = (JUDGE_CHOICE_AI_FILE, JUDGE_CHOICE_AI_API, JUDGE_CHOICE_WORDS)


def make_line_judge(choice: str = JUDGE_CHOICE_AI_FILE, judgments: str | Path | None = None) -> LineJudge:
    """本番の既定は AI の判定(ファイル)。渡されていなければ全部の行が要確認になる。

    ``ai-api`` で鍵が無いときは止めずに、判定なし(要確認)の役に戻す。**落とさない。**
    """
    if choice == JUDGE_CHOICE_WORDS:
        return WordListJudge()
    if choice == JUDGE_CHOICE_AI_API:
        try:
            return AnthropicLineJudge()
        except LineJudgeUnavailable as exc:
            return AIJudgmentFileJudge(None, source=f"AI を呼べなかった: {exc}")
    if choice == JUDGE_CHOICE_AI_FILE:
        return AIJudgmentFileJudge.from_path(judgments)
    raise LineJudgeError(f"知らない線引きの役です: {choice!r}(選べるのは {', '.join(JUDGE_CHOICES)})")
