"""AI が図面を読んだ答案を、本番の経路が受け取る口(K-49)。

なぜ作るのか
------------
K-46 で線引きだけを AI に替えた本番は数量一致 2 → 3 で止まった。本番の出力で数量を持つ行が
11 行しかなく、線引きは行を通すか外すかを決めるだけで数量を作らないから。一方、AI が 34 ページを
ざっと 1 回読んだ答案は数量一致 18 だった。おーちゃんの決定(K-49):

    部分ごとに替えるのをやめ、AI が 34 ページを読んだ結果をそのまま本番の入力にする。
    機械は測る・数える・検算するに回る。

基準は `docs/k49_ai_reads_all_criteria.md`(測る前にコミット)。

答案の形
--------
::

    {"行": [{"工事": "...", "場所": "...", "数量": 数 または null, "単位": "...",
             "式": "...", "根拠": "..."}],
     "ページごとの所要": [{"ページ": 1, "見たか": true, ...}]}

ここが守ること
--------------
- **形の崩れは黙って捨てない。** 答案全体の形が違えば `AIReadingError` で止める。
  行ごとの崩れ(欄が欠けた・型が違う)は、その行を理由つきで `dropped` に落として数える。
- **数量は答案の数量のまま。null は null のまま(0 にしない)。** 文字の数("6")も数に直さない
  (直すのは答案を書き換えることになるので、理由つきで落とす)。
- **確かさは上げない。** ここは読むだけで、行を確定させる口を持たない。
- AI をその場で呼ぶ役(`AnthropicDrawingReader`)は鍵があるときだけ動く。鍵や部品が無いときは
  止まらずに「読んでいない」答案(行 0 件、理由つき)を返す。
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 答案の行の欄。**全部そろっていなければ、その行は理由つきで落とす。**
FIELD_WORK = "工事"
FIELD_PLACE = "場所"
FIELD_QUANTITY = "数量"
FIELD_UNIT = "単位"
FIELD_FORMULA = "式"
FIELD_BASIS = "根拠"
ROW_FIELDS = (FIELD_WORK, FIELD_PLACE, FIELD_QUANTITY, FIELD_UNIT, FIELD_FORMULA, FIELD_BASIS)

KEY_ROWS = "行"
KEY_PAGES = "ページごとの所要"

#: 答案の状態。
STATUS_READ = "読んだ"
STATUS_NOT_READ = "読んでいない"

#: 指示文(何を読んで何を出すか)。**コードに埋め込まずファイルに置く。**
INSTRUCTIONS_PATH = Path(__file__).with_name("ai_reading_instructions.txt")

#: 既定のモデル。`knowledge/expertise/reader.py` の `AnthropicReader` の既定に合わせる
#: (判定の側は knowledge を import しない決まりなので、値を合わせてテストで見張る)。
DEFAULT_MODEL = "claude-opus-5"
#: モデルを変える環境変数。
MODEL_ENV = "AI_READING_MODEL"
#: ページ画像の長い辺(画素)。**縮小画像でざっと通す**(おーちゃん K-49「3分15秒は実用に耐える」)。
DEFAULT_LONG_EDGE = 1568
LONG_EDGE_ENV = "AI_READING_LONG_EDGE"


class AIReadingError(ValueError):
    """答案全体の形が違う(読み込みで止める)。"""


class AIReaderUnavailable(RuntimeError):
    """AI を呼べない(鍵・部品が無い)。"""


@dataclass(frozen=True)
class AIReadingRow:
    """答案の行 1 つ。**値は答案のまま。**"""

    number: int
    """答案の中の順番(1 始まり。落とした行も数える)。"""
    work: str
    place: str
    quantity: float | int | None
    unit: str
    formula: str
    basis: Any


@dataclass
class AIReading:
    """読み込んだ答案。"""

    rows: list[AIReadingRow]
    dropped: list[dict[str, Any]] = field(default_factory=list)
    """形が崩れて落とした行(番号・理由・元の行)。**黙って捨てない。**"""
    pages: list[Any] = field(default_factory=list)
    """ページごとの所要(答案のまま)。"""
    status: str = STATUS_READ
    reason: str = ""
    """読んでいないときの理由。"""
    reader: str = ""
    """誰の読みか(答案のファイル・モデル名)。行の決め手の証拠になる。"""
    seconds: float | None = None
    """AI が読むのにかかった秒数(その場で呼んだときだけ)。"""

    def summary(self) -> dict[str, Any]:
        return {
            "状態": self.status,
            "理由": self.reason,
            "読み手": self.reader,
            "答案の行": len(self.rows) + len(self.dropped),
            "受け取った行": len(self.rows),
            "数量のある行": sum(1 for r in self.rows if r.quantity is not None),
            "数量が空の行": sum(1 for r in self.rows if r.quantity is None),
            "形が崩れて落とした行": len(self.dropped),
            "落とした行": self.dropped,
            "ページごとの所要": self.pages,
            "AI が読んだ秒数": self.seconds,
        }


def not_read(reason: str, reader: str = "") -> AIReading:
    """「読んでいない」答案。**行は 0 件。止めない。**"""
    return AIReading(rows=[], status=STATUS_NOT_READ, reason=reason, reader=reader)


def _row_problem(row: Any) -> str | None:
    if not isinstance(row, Mapping):
        return "行が辞書ではない"
    missing = [name for name in ROW_FIELDS if name not in row]
    if missing:
        return f"欄が欠けている: {'、'.join(missing)}"
    work = row[FIELD_WORK]
    if not isinstance(work, str) or not work.strip():
        return "工事が空か文字ではない"
    for name in (FIELD_PLACE, FIELD_UNIT, FIELD_FORMULA):
        if row[name] is not None and not isinstance(row[name], str):
            return f"{name}が文字でも null でもない: {row[name]!r}"
    basis = row[FIELD_BASIS]
    if basis is not None and not isinstance(basis, (str, list, Mapping)):
        return f"根拠が文字・一覧・辞書でも null でもない: {basis!r}"
    quantity = row[FIELD_QUANTITY]
    if quantity is not None:
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            return f"数量が数でも null でもない: {quantity!r}"
        if not math.isfinite(quantity):
            return f"数量が有限の数ではない: {quantity!r}"
    return None


def parse_ai_reading(payload: Any, *, reader: str = "") -> AIReading:
    """答案を読み込む。**全体の形が違えば止め、行の崩れは理由つきで落として数える。**"""
    if not isinstance(payload, Mapping):
        raise AIReadingError("答案は JSON のオブジェクト({\"行\": [...], ...})にしてください")
    if KEY_ROWS not in payload:
        raise AIReadingError(f"答案に「{KEY_ROWS}」がありません")
    raw_rows = payload[KEY_ROWS]
    if not isinstance(raw_rows, list):
        raise AIReadingError(f"答案の「{KEY_ROWS}」は一覧(JSON の配列)にしてください")
    pages = payload.get(KEY_PAGES, [])
    if not isinstance(pages, list):
        raise AIReadingError(f"答案の「{KEY_PAGES}」は一覧(JSON の配列)にしてください")
    rows: list[AIReadingRow] = []
    dropped: list[dict[str, Any]] = []
    for number, row in enumerate(raw_rows, 1):
        problem = _row_problem(row)
        if problem is not None:
            dropped.append({"番号": number, "理由": problem, "行": row})
            continue
        rows.append(
            AIReadingRow(
                number=number,
                work=row[FIELD_WORK].strip(),
                place=(row[FIELD_PLACE] or "").strip(),
                quantity=row[FIELD_QUANTITY],
                unit=row[FIELD_UNIT] or "",
                formula=row[FIELD_FORMULA] or "",
                basis=row[FIELD_BASIS] if row[FIELD_BASIS] is not None else "",
            )
        )
    return AIReading(rows=rows, dropped=dropped, pages=list(pages), reader=reader)


def load_ai_reading(path: str | Path | None) -> AIReading:
    """答案のファイルを読む。**渡されなければ「読んでいない」(行 0 件、理由つき)。**"""
    if path is None:
        return not_read("AI の答案のファイルが渡されていない(--ai-reading)")
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AIReadingError(f"答案のファイルが JSON として読めません: {exc}") from exc
    return parse_ai_reading(payload, reader=f"答案のファイル:{source.name}")


# ---------------------------------------------------------------------------
# AI をその場で呼ぶ(鍵があるときだけ)
# ---------------------------------------------------------------------------


def load_instructions(path: str | Path | None = None) -> str:
    return Path(path or INSTRUCTIONS_PATH).read_text(encoding="utf-8")


def render_pages(pdf_path: str | Path, long_edge: int | None = None) -> list[tuple[int, bytes, str]]:
    """PDF の全ページを縮小画像(JPEG)と文字の層にする。返すのは (ページ, 画像, 文字)。"""
    import pymupdf

    edge = int(long_edge or os.environ.get(LONG_EDGE_ENV) or DEFAULT_LONG_EDGE)
    out: list[tuple[int, bytes, str]] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            longest = max(page.rect.width, page.rect.height) or 1.0
            zoom = edge / longest
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            out.append((index + 1, pix.tobytes("jpg", jpg_quality=80), page.get_text("text")))
    return out


def _json_object(text: str) -> Any:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise AIReadingError("AI の答えに JSON のオブジェクトがありません")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIReadingError(f"AI の答えが JSON として読めません: {exc}") from exc


class AnthropicDrawingReader:
    """PDF のページを画像にして Anthropic の API に渡し、答案を返させる。

    **鍵が無ければ作れない**(`AIReaderUnavailable`)。鍵は環境変数 ``ANTHROPIC_API_KEY``
    (または ``ANTHROPIC_AUTH_TOKEN``)、部品は ``anthropic``。モデルは環境変数
    ``AI_READING_MODEL`` で変えられる(既定は `DEFAULT_MODEL`)。
    ``client`` を渡せば鍵を見ない(テストで偽の相手を渡すため)。
    """

    def __init__(
        self,
        model: str | None = None,
        client: Any = None,
        instructions: str | None = None,
        long_edge: int | None = None,
    ) -> None:
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.reader_id = f"anthropic:{self.model}"
        self.instructions = instructions if instructions is not None else load_instructions()
        self.long_edge = long_edge
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise AIReaderUnavailable(
                    "AI を呼ぶ鍵がありません(環境変数 ANTHROPIC_API_KEY が設定されていない)"
                )
            try:
                import anthropic  # noqa: PLC0415  入れていなくてもリポジトリは動く
            except ImportError as exc:
                raise AIReaderUnavailable(
                    "AI を呼ぶ部品(anthropic)が入っていません。pip install anthropic で入ります"
                ) from exc
            client = anthropic.Anthropic()
        self._client = client

    def request_content(self, pages: Sequence[tuple[int, bytes, str]]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for number, image, text in pages:
            content.append({"type": "text", "text": f"--- {number} ページ ---\n文字の層:\n{text}"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(image).decode("ascii"),
                    },
                }
            )
        content.append(
            {"type": "text", "text": f"以上 {len(pages)} ページです。指示の形の JSON だけを答えてください。"}
        )
        return content

    def read(self, pdf_path: str | Path) -> AIReading:
        """読む。**断られた・形が違う答えは「読んでいない」にして理由を書く(止めない)。**"""
        pages = render_pages(pdf_path, self.long_edge)
        started = time.perf_counter()
        # 長い出力になるので流して受ける(SDK の時間切れを避ける)。
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=64000,
                thinking={"type": "adaptive"},
                system=self.instructions,
                messages=[{"role": "user", "content": self.request_content(pages)}],
            ) as stream:
                response = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001  呼び出しの失敗も止めずに理由を残す
            reading = not_read(
                f"AI の呼び出しが失敗した: {type(exc).__name__}: {exc}", reader=self.reader_id
            )
            reading.seconds = round(time.perf_counter() - started, 1)
            return reading
        seconds = round(time.perf_counter() - started, 1)
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            reading = not_read("AI が答えを断った", reader=self.reader_id)
        else:
            text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
            try:
                reading = parse_ai_reading(_json_object(text), reader=self.reader_id)
            except AIReadingError as exc:
                reading = not_read(f"AI の答えの形が違う: {exc}", reader=self.reader_id)
            if stop == "max_tokens":
                cut = "AI の答えが長さの上限で切れた"
                reading.reason = f"{reading.reason} / {cut}" if reading.reason else cut
        reading.seconds = seconds
        return reading


def read_with_api(pdf_path: str | Path, client: Any = None) -> AIReading:
    """AI をその場で呼んで読む。**鍵・部品が無ければ止めずに「読んでいない」。**"""
    try:
        reader = AnthropicDrawingReader(client=client)
    except AIReaderUnavailable as exc:
        return not_read(f"AI を呼べなかった: {exc}")
    return reader.read(pdf_path)
