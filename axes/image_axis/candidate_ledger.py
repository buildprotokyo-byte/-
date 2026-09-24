"""画像軸: 意味を当てない**候補台帳**(K-23)。

**何のための部品か**

いままでの経路は、図形を拾う段と「それが何か」を当てる段が一体だった。
繰り返す図形を群にする経路(`pdf_repeated_symbols.py`)は縮尺が読めないと
動かないので、同じ図面でも**ページによって 674 群になったり 0 件になったり**した。
0 件は「記号が無い」ではなく「縮尺が読めなかった」だった。

既存の積算プログラム(SUGORAKU)を読んだ報告(K-23)の結論は、
**毎回同じ固定処理で候補を拾い、意味を当てるのは後段に分ける**こと。
この部品はその前段だけを受け持つ。

- **全ページに同じ処理を掛ける。** 縮尺・ページの種類・凡例の有無で処理を変えない。
- **意味を当てない。** 線・閉領域・小輪郭を、形の事実(位置・長さ・面積)だけで保存する。
- **座標はページ全体を 0〜1000 に揃える。** ベクターとラスターで同じ形式。
  横と縦で 1 単位の長さは違う(A3 横なら横 1 単位 ≒ 1.19pt、縦 1 単位 ≒ 0.84pt)。
  元に戻すための紙の大きさはページの記録に残す。
- **上限**(線 1,800・閉領域 300・小輪郭 600)を超えたら決まった順で残し、
  **上限に当たったことを必ず記録する。**
- **表題欄を本文より先に読む**(`read_title_block`)。
- **品質ゲート**(`gate_page`)が passed / needs_review / blocked を付ける。
  読めなかったページを「候補 0 個」と書かない。
- **キャッシュ**(`LedgerCache`)はページの画像と設定から SHA-256 の鍵を作る。

**この部品がしないこと(重要)**

- **候補の数を数量に使わない。** 小輪郭には文字・家具・ハッチングの切れ端が入る。
  「小輪郭が 600 個」は「記号が 600 個」ではない。
- 本番の取り込み経路(`intake/drawing_intake.py`)からは呼ばれていない。
  判定にも繋がっていない。
- OCR を掛けない。文字の層が無いページは「文字が読めない」として blocked になる。

**回転したページ**

PDF のページには表示の回転が付いていることがある(P011 匿名化v2 では 6・18・22 ページ)。
`get_drawings()` と `get_text("words")` が返す座標は**回転前**なので、
そのまま使うと縦横が入れ替わり、0〜1000 の外に出る。ここでは
`page.rotation_matrix` で表示の向きに直してから揃える。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import pymupdf

from axes.image_axis.pdf_vector_symbols import parse_scale_text

#: 台帳の作り方を変えたら上げる。キャッシュの鍵に入るので、古い結果が使い回されない。
LEDGER_VERSION = "k23-1"

#: 座標を揃える幅。ページ全体が 0〜NORM になる。
NORM = 1000.0

Source = Literal["vector", "raster", "none"]
GateStatus = Literal["passed", "needs_review", "blocked"]


@dataclass(frozen=True)
class LedgerSettings:
    """台帳の固定処理の設定。**全ページで同じ値を使う。** 値はキャッシュの鍵に入る。

    既定値は K-23 に書かれた上限と、SUGORAKU の手順(CLAHE → Canny → HoughLinesP)を
    こちらで具体化したもの。**実図面で校正した値ではない。**
    """

    max_lines: int = 1800
    max_regions: int = 300
    max_small: int = 600

    #: ラスターは長いほうの辺をこの画素数に揃えてから処理する(紙の大きさで結果が変わらないように)。
    raster_long_side_px: int = 3000
    clahe_clip: float = 2.0
    clahe_tile: int = 8
    canny_low: int = 50
    canny_high: int = 150
    hough_threshold: int = 80
    hough_max_gap_px: int = 3

    #: ここから下は 0〜1000 の単位。ベクターとラスターで同じ値を使う。
    min_line_length: float = 10.0
    """これより短い直線は線として残さない。"""
    small_max_side: float = 20.0
    """外接矩形の長いほうの辺がこれ以下なら小輪郭。"""
    small_min_side: float = 1.0
    """これより小さいものは点の汚れとして落とす(落とした数は記録する)。"""
    region_min_area: float = 100.0
    """閉領域として残す最小の面積(単位²)。"""

    #: 画像がページのこの割合以上を覆っていたら、そのページはラスターとして処理する。
    raster_coverage: float = 0.5

    #: 表題欄として切り出す場所(0〜1000)。下端の帯と右下の区画。
    title_bottom_from: float = 880.0
    title_right_from_x: float = 600.0
    title_right_from_y: float = 600.0

    def fingerprint(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


# ---------------------------------------------------------------------------
# 台帳の中身
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineCandidate:
    x0: float
    y0: float
    x1: float
    y1: float
    length: float


@dataclass(frozen=True)
class BoxCandidate:
    """閉領域・小輪郭の共通の形。外接矩形と面積だけで、意味は持たない。"""

    x0: float
    y0: float
    x1: float
    y1: float
    area: float


@dataclass(frozen=True)
class KindCount:
    found: int
    """固定処理が見つけた数。"""
    kept: int
    """上限で残した数。"""
    limit: int
    cap_hit: bool
    """見つけた数が上限を超えたか。超えたぶんは台帳に入っていない。"""


@dataclass(frozen=True)
class TitleBlock:
    """表題欄から読んだもの。**読めなかった欄は None**(空文字で埋めない)。"""

    read: bool
    """表題欄の場所に文字の層があったか。"""
    drawing_name: str | None
    drawing_number: str | None
    scale_text: str | None
    scale_denominator: float | None
    scale_stated_none: bool
    """「NON SCALE」「-」のように、縮尺が無いと書いてある。"""
    revision_date: str | None
    labels_seen: tuple[str, ...]
    """見つかった見出しの種類(name / number / scale / date)。"""


@dataclass(frozen=True)
class PageGate:
    status: GateStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PageLedger:
    page_index: int
    width_pt: float
    """表示の向き(回転後)の紙の幅。0〜1000 から元に戻すときに使う。"""
    height_pt: float
    rotation: int
    source: Source
    has_text: bool
    image_coverage: float
    """埋め込み画像がページを覆う割合。"""
    unprocessed_images: bool
    """ベクターとして処理したページに、台帳に入っていない画像が残っているか。"""
    title: TitleBlock | None
    lines: tuple[LineCandidate, ...]
    regions: tuple[BoxCandidate, ...]
    small: tuple[BoxCandidate, ...]
    counts: dict[str, KindCount]
    dropped_specks: int
    """小さすぎて点の汚れとして落とした数。"""
    duplicate_lines: int
    """座標まで同じ線を 1 本にまとめたときに減らした数(閉じた線の戻り、重ね描き)。"""
    error: str | None
    gate: PageGate
    cache_key: str | None = None
    from_cache: bool = False

    def content_digest(self) -> str:
        """残した候補の中身(座標まで)の指紋。3 回処理して同じかを比べるのに使う。"""
        payload = {
            "lines": [asdict(c) for c in self.lines],
            "regions": [asdict(c) for c in self.regions],
            "small": [asdict(c) for c in self.small],
            "counts": {k: asdict(v) for k, v in sorted(self.counts.items())},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["content_digest"] = self.content_digest()
        return data

    @staticmethod
    def from_dict(data: dict) -> "PageLedger":
        data = dict(data)
        data.pop("content_digest", None)
        title = data.get("title")
        return PageLedger(
            page_index=data["page_index"],
            width_pt=data["width_pt"],
            height_pt=data["height_pt"],
            rotation=data["rotation"],
            source=data["source"],
            has_text=data["has_text"],
            image_coverage=data["image_coverage"],
            unprocessed_images=data["unprocessed_images"],
            title=None
            if title is None
            else TitleBlock(**{**title, "labels_seen": tuple(title["labels_seen"])}),
            lines=tuple(LineCandidate(**c) for c in data["lines"]),
            regions=tuple(BoxCandidate(**c) for c in data["regions"]),
            small=tuple(BoxCandidate(**c) for c in data["small"]),
            counts={k: KindCount(**v) for k, v in data["counts"].items()},
            dropped_specks=data["dropped_specks"],
            duplicate_lines=data["duplicate_lines"],
            error=data["error"],
            gate=PageGate(data["gate"]["status"], tuple(data["gate"]["reasons"])),
            cache_key=data.get("cache_key"),
            from_cache=data.get("from_cache", False),
        )


@dataclass(frozen=True)
class DocumentLedger:
    pdf_path: str
    settings: LedgerSettings
    pages: tuple[PageLedger, ...]

    def gate_summary(self) -> dict[str, int]:
        out = {"passed": 0, "needs_review": 0, "blocked": 0}
        for page in self.pages:
            out[page.gate.status] += 1
        return out


# ---------------------------------------------------------------------------
# 品質ゲート
# ---------------------------------------------------------------------------

REASON_ERROR = "処理が止まった"
REASON_EMPTY = "描画も画像も無い"
REASON_NO_TEXT = "文字が読めない(文字の層が無く、OCRを掛けていない)"
REASON_NO_SCALE = "縮尺が取れていない"
REASON_NO_TITLE = "表題欄から図面名称も図番も取れていない"
REASON_CAP = "上限に当たった(台帳に入っていない候補がある)"
REASON_ZERO = "候補が0件"
REASON_UNPROCESSED_IMAGES = "台帳に入っていない画像がある"


def gate_page(
    *,
    error: str | None,
    source: Source,
    has_text: bool,
    title: TitleBlock | None,
    counts: dict[str, KindCount],
    unprocessed_images: bool,
) -> PageGate:
    """ページの事実だけから passed / needs_review / blocked を決める。

    - **blocked** … このページは後段(意味を当てる段)に渡しても何も決められない。
      処理が止まった・中身が無い・文字が読めない。
    - **needs_review** … 候補は取れたが、欠けているものがある。
    - **passed** … 欠けているものが見つからなかった。**正しいという意味ではない。**

    **候補が 0 件のページは passed にしない。** 0 件は「対象が無い」とは限らないので、
    人が見るまで 0 として扱わせない。
    """
    if error is not None:
        return PageGate("blocked", (REASON_ERROR,))
    if source == "none":
        return PageGate("blocked", (REASON_EMPTY,))

    reasons: list[str] = []
    if not has_text:
        reasons.append(REASON_NO_TEXT)
    scale_ok = title is not None and (
        title.scale_denominator is not None or title.scale_stated_none
    )
    if not scale_ok:
        reasons.append(REASON_NO_SCALE)
    if title is None or (title.drawing_name is None and title.drawing_number is None):
        reasons.append(REASON_NO_TITLE)
    if any(c.cap_hit for c in counts.values()):
        reasons.append(REASON_CAP)
    if sum(c.kept for c in counts.values()) == 0:
        reasons.append(REASON_ZERO)
    if unprocessed_images:
        reasons.append(REASON_UNPROCESSED_IMAGES)

    if not has_text:
        return PageGate("blocked", tuple(reasons))
    if reasons:
        return PageGate("needs_review", tuple(reasons))
    return PageGate("passed", ())


# ---------------------------------------------------------------------------
# 表題欄
# ---------------------------------------------------------------------------

_LABELS: dict[str, re.Pattern[str]] = {
    "name": re.compile(r"^(図面名称|図面名|図名|DRAWING\s*TITLE|TITLE)$", re.I),
    "number": re.compile(r"^(図面番号|図番|DWG\.?\s*NO\.?|SHEET\s*NO\.?)$", re.I),
    "scale": re.compile(r"^(縮尺|尺度|SCALE)$", re.I),
    "date": re.compile(r"^(日付|作成日|年月日|年/月/日|改訂日|改訂|DATE)$", re.I),
}
_ANY_LABEL = re.compile("|".join(p.pattern.strip("^$") for p in _LABELS.values()), re.I)
_NO_SCALE_RE = re.compile(r"(NON\s*-?\s*SCALE|^NON$|N\.?\s*S\.?$|^[-‐ー―—－]$|なし|無し)", re.I)
#: 縮尺として読む語の形。**語の頭が 1/ で始まるもの**に限る(「2021/1/21」の中の「1/21」を
#: 縮尺として拾わないため)。「S=1/50」の形も受ける。
_SCALE_WORD_RE = re.compile(r"^(?:S\s*[=＝]\s*|A\d\s*[：:]\s*)?1\s*[/:／：]\s*\d{1,4}(?!\s*[/／]\s*\d)", re.I)
_DATE_RE = re.compile(
    r"((?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*[./月\-]\s*\d{1,2}\s*日?"
    r"|(?:R|令和)\s*\d{1,2}\s*[./年]\s*\d{1,2}\s*[./月]\s*\d{1,2}\s*日?"
    r"|(?<![\d/])\d{2}/\d{1,2}/\d{1,2}(?![\d/]))"
)


def _date_key(text: str) -> tuple[int, int, int] | None:
    """日付の表記を (年, 月, 日) にする。並べて最新を選ぶためだけに使う。"""
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if len(numbers) < 3:
        return None
    year, month, day = numbers[:3]
    if "令和" in text or text.strip().upper().startswith("R"):
        year += 2018
    elif year < 100:
        year += 2000
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


@dataclass(frozen=True)
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1e-6)


def _page_words(page: pymupdf.Page) -> list[_Word]:
    """文字を表示の向きに直し、0〜1000 に揃えて返す。"""
    matrix = page.rotation_matrix
    width, height = page.rect.width, page.rect.height
    out: list[_Word] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        rect = pymupdf.Rect(x0, y0, x1, y1) * matrix
        rect.normalize()
        out.append(
            _Word(
                rect.x0 / width * NORM,
                rect.y0 / height * NORM,
                rect.x1 / width * NORM,
                rect.y1 / height * NORM,
                text.strip(),
            )
        )
    return out


def _in_title_area(word: _Word, settings: LedgerSettings) -> bool:
    if word.cy >= settings.title_bottom_from:
        return True
    cx = (word.x0 + word.x1) / 2
    return cx >= settings.title_right_from_x and word.cy >= settings.title_right_from_y


def _valid_value(kind: str, text: str) -> bool:
    """その見出しの値として成り立つ文字列か。成り立たないものは値にしない。"""
    if not text or _ANY_LABEL.fullmatch(text):
        return False
    if kind == "scale":
        return bool(_SCALE_WORD_RE.match(text)) or bool(_NO_SCALE_RE.search(text))
    if kind == "date":
        return bool(_DATE_RE.search(text))
    if kind == "number":
        return len(text) <= 16 and any(ch.isdigit() for ch in text)
    # 図面名称: 縮尺や日付の表記そのものは名称にしない。
    return parse_scale_text(text) is None and not _DATE_RE.fullmatch(text)


def _value_after(label: _Word, words: list[_Word], kind: str) -> str | None:
    """見出しの右か右下にある、いちばん近い値を読む。

    表題欄は「見出しの右隣」とは限らず、P011 匿名化v2 では値が見出しの右下に
    半行ずれて置かれている。そこで、見出しの右端から近い順に見て、
    **その見出しの値として成り立つもの**(縮尺なら 1/50 の形、図番なら数字を含む短い語)
    だけを取る。成り立つものが近くに無ければ None(推測で埋めない)。
    """
    anchor_x, anchor_y = label.x1, label.cy
    candidates = []
    for w in words:
        if w is label:
            continue
        dy = w.cy - label.cy
        if w.x0 < label.x0 - 10 or w.x0 - label.x1 > 150:
            continue
        if dy < -max(label.h, w.h) * 0.6 or dy > 40:
            continue
        if not _valid_value(kind, w.text):
            continue
        distance = math.hypot(max(w.x0 - anchor_x, 0.0), w.cy - anchor_y)
        candidates.append((round(distance, 3), round(w.y0, 3), round(w.x0, 3), w))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[:3])
    first = candidates[0][3]
    # 同じ行で右に続く語を、間が大きく空くまでつなぐ(名称が 2 語に割れている場合)。
    parts = [first]
    if kind == "name":
        followers = sorted(
            (
                w
                for w in words
                if w.x0 > first.x1 - 0.5
                and abs(w.cy - first.cy) <= max(first.h, w.h) * 0.5
                and _valid_value(kind, w.text)
            ),
            key=lambda w: w.x0,
        )
        for w in followers:
            if w.x0 - parts[-1].x1 > max(parts[-1].h, w.h) * 1.5:
                break
            parts.append(w)
    return " ".join(p.text for p in parts)


def read_title_block(words: list[_Word], settings: LedgerSettings) -> TitleBlock:
    """表題欄(下端と右下)だけを見て、図面名称・図番・縮尺・改訂日を読む。

    **見出し(「図面名称」「縮尺」など)を手がかりにする。** 見出しが無い欄は読まない。
    図番らしい文字列を本文から探して当てることはしない(本文の建具記号などと区別できない)。
    """
    area = [w for w in words if w.text and _in_title_area(w, settings)]
    if not area:
        return TitleBlock(False, None, None, None, None, False, None, ())

    values: dict[str, str | None] = {"name": None, "number": None, "scale": None, "date": None}
    seen: list[str] = []
    # 位置の順に見て、同じ見出しが複数あれば最初(上・左)のものを使う。決まった順にするため。
    for word in sorted(area, key=lambda w: (round(w.y0, 1), round(w.x0, 1))):
        for kind, pattern in _LABELS.items():
            if pattern.fullmatch(word.text):
                if kind not in seen:
                    seen.append(kind)
                if values[kind] is None:
                    values[kind] = _value_after(word, area, kind)

    scale_text = values["scale"]
    parsed = (
        parse_scale_text(scale_text) if scale_text and _SCALE_WORD_RE.match(scale_text) else None
    )
    if parsed is None and scale_text is None:
        # 見出しが無いときだけ、表題欄の中で「1/50」の形で始まる語を探す。
        for word in sorted(area, key=lambda w: (round(w.y0, 1), round(w.x0, 1))):
            if _SCALE_WORD_RE.match(word.text):
                parsed = parse_scale_text(word.text)
                break
    stated_none = parsed is None and scale_text is not None and bool(
        _NO_SCALE_RE.search(scale_text)
    )

    # 改訂日: 表題欄の中の日付の形をした語のうち、いちばん新しいもの
    # (改訂の履歴が表になっていることが多いので、見出しの隣の 1 つに決めない)。
    dated = []
    for word in area:
        match = _DATE_RE.search(word.text)
        if match is None:
            continue
        key = _date_key(match.group(0))
        if key is not None:
            dated.append((key, round(word.y0, 1), round(word.x0, 1), match.group(0)))
    revision = max(dated)[3] if dated else None

    return TitleBlock(
        read=True,
        drawing_name=values["name"],
        drawing_number=values["number"],
        scale_text=parsed.source_text if parsed else scale_text,
        scale_denominator=parsed.denominator if parsed else None,
        scale_stated_none=stated_none,
        revision_date=revision,
        labels_seen=tuple(seen),
    )


# ---------------------------------------------------------------------------
# 候補を拾う(ベクター)
# ---------------------------------------------------------------------------


def _r(value: float) -> float:
    """座標の丸め。0.1 単位にしてから比べるので、浮動小数の末尾で結果が揺れない。"""
    return round(float(value), 1)


def _straight_edges(items: list) -> list[tuple[pymupdf.Point, pymupdf.Point]]:
    """描画の要素から直線の辺を取り出す。

    PyMuPDF は 4 本の線で閉じた長方形を ``re``(矩形)や ``qu``(四辺形)として返す。
    壁や枠の多くはこの形で入っているので、``l``(線分)だけを見ると線がほとんど
    取れない。ラスターの HoughLinesP は長方形の辺も線として拾うので、
    ベクターでも辺を線として数え、2 つの経路の意味を揃える。ベジェ曲線は線にしない。
    """
    edges: list[tuple[pymupdf.Point, pymupdf.Point]] = []
    for item in items:
        kind = item[0]
        if kind == "l":
            edges.append((pymupdf.Point(item[1]), pymupdf.Point(item[2])))
        elif kind == "re":
            rect = pymupdf.Rect(item[1])
            corners = [rect.tl, rect.tr, rect.br, rect.bl]
            edges.extend(zip(corners, corners[1:] + corners[:1]))
        elif kind == "qu":
            quad = item[1]
            corners = [quad.ul, quad.ur, quad.lr, quad.ll]
            edges.extend(zip(corners, corners[1:] + corners[:1]))
    return edges


def _vector_candidates(
    page: pymupdf.Page, settings: LedgerSettings
) -> tuple[list[LineCandidate], list[BoxCandidate], list[BoxCandidate], int]:
    matrix = page.rotation_matrix
    sx = NORM / page.rect.width
    sy = NORM / page.rect.height

    def norm(point: pymupdf.Point) -> tuple[float, float]:
        p = point * matrix
        return p.x * sx, p.y * sy

    lines: list[LineCandidate] = []
    regions: list[BoxCandidate] = []
    small: list[BoxCandidate] = []
    specks = 0
    for drawing in page.get_drawings():
        rect = pymupdf.Rect(drawing["rect"]) * matrix
        rect.normalize()
        x0, y0 = rect.x0 * sx, rect.y0 * sy
        x1, y1 = rect.x1 * sx, rect.y1 * sy
        w, h = x1 - x0, y1 - y0
        side = max(w, h)
        items = drawing["items"]
        if side < settings.small_min_side:
            specks += 1
            continue
        if side <= settings.small_max_side:
            small.append(BoxCandidate(_r(x0), _r(y0), _r(x1), _r(y1), _r(w * h)))
            continue
        closed = bool(drawing.get("closePath")) or drawing.get("fill") is not None or any(
            item[0] in ("re", "qu") for item in items
        )
        if closed and w * h >= settings.region_min_area:
            regions.append(BoxCandidate(_r(x0), _r(y0), _r(x1), _r(y1), _r(w * h)))
        for a, b in _straight_edges(items):
            ax, ay = norm(a)
            bx, by = norm(b)
            length = math.hypot(bx - ax, by - ay)
            if length < settings.min_line_length:
                continue
            # 向きを揃える(同じ線を逆向きに描いても同じ候補になるように)。
            if (bx, by) < (ax, ay):
                ax, ay, bx, by = bx, by, ax, ay
            lines.append(LineCandidate(_r(ax), _r(ay), _r(bx), _r(by), _r(length)))
    return lines, regions, small, specks


# ---------------------------------------------------------------------------
# 候補を拾う(ラスター)
# ---------------------------------------------------------------------------


def render_gray(page: pymupdf.Page, long_side_px: int) -> np.ndarray:
    """ページを表示の向きで、長いほうの辺が ``long_side_px`` 画素のグレースケールにする。"""
    long_side_pt = max(page.rect.width, page.rect.height)
    zoom = long_side_px / long_side_pt
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.stride)
    return image[:, : pixmap.width].copy()


def raster_candidates(
    gray: np.ndarray, settings: LedgerSettings
) -> tuple[list[LineCandidate], list[BoxCandidate], list[BoxCandidate], int]:
    """グレースケール → CLAHE → Canny → HoughLinesP → 輪郭抽出。意味は当てない。"""
    height, width = gray.shape
    sx = NORM / width
    sy = NORM / height
    clahe = cv2.createCLAHE(
        clipLimit=settings.clahe_clip, tileGridSize=(settings.clahe_tile, settings.clahe_tile)
    )
    enhanced = clahe.apply(gray)
    edges = cv2.Canny(enhanced, settings.canny_low, settings.canny_high)

    # 0〜1000 の最小の長さを、画素に直す(縦横の短いほうの換算で。短い線を落としすぎないように)。
    min_len_px = max(1, int(round(settings.min_line_length / max(sx, sy))))
    segments = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=settings.hough_threshold,
        minLineLength=min_len_px,
        maxLineGap=settings.hough_max_gap_px,
    )
    lines: list[LineCandidate] = []
    if segments is not None:
        for ax, ay, bx, by in np.asarray(segments).reshape(-1, 4):
            ax, ay, bx, by = ax * sx, ay * sy, bx * sx, by * sy
            length = math.hypot(bx - ax, by - ay)
            if length < settings.min_line_length:
                continue
            if (bx, by) < (ax, ay):
                ax, ay, bx, by = bx, by, ax, ay
            lines.append(LineCandidate(_r(ax), _r(ay), _r(bx), _r(by), _r(length)))

    # 輪郭: 縁を 1 画素ふくらませて切れ目をつなぎ、外側と穴を分けて取る。
    closed_edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, hierarchy = cv2.findContours(closed_edges, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    regions: list[BoxCandidate] = []
    small: list[BoxCandidate] = []
    specks = 0
    if hierarchy is not None:
        for contour, (_next, _prev, _child, parent) in zip(contours, np.asarray(hierarchy).reshape(-1, 4)):
            x, y, w, h = cv2.boundingRect(contour)
            x0, y0, x1, y1 = x * sx, y * sy, (x + w) * sx, (y + h) * sy
            side = max(x1 - x0, y1 - y0)
            if side < settings.small_min_side:
                specks += 1
                continue
            if parent < 0:
                # 外側の輪郭(線の塊そのもの)。小さいものだけを小輪郭にする。
                if side <= settings.small_max_side:
                    small.append(
                        BoxCandidate(_r(x0), _r(y0), _r(x1), _r(y1), _r((x1 - x0) * (y1 - y0)))
                    )
                continue
            # 穴の輪郭 = 線で囲まれた内側。これを閉領域とする。
            area = cv2.contourArea(contour) * sx * sy
            if side > settings.small_max_side and area >= settings.region_min_area:
                regions.append(BoxCandidate(_r(x0), _r(y0), _r(x1), _r(y1), _r(area)))
    return lines, regions, small, specks


# ---------------------------------------------------------------------------
# 上限
# ---------------------------------------------------------------------------


def _cap_lines(found: list[LineCandidate], limit: int) -> tuple[tuple[LineCandidate, ...], KindCount]:
    ordered = sorted(found, key=lambda c: (-c.length, c.y0, c.x0, c.y1, c.x1))
    kept = tuple(ordered[:limit])
    return kept, KindCount(len(found), len(kept), limit, len(found) > limit)


def _cap_boxes(found: list[BoxCandidate], limit: int) -> tuple[tuple[BoxCandidate, ...], KindCount]:
    """大きいものから残す。同じ大きさは位置の順。**どの順で残すかは固定**なので毎回同じになる。"""
    ordered = sorted(found, key=lambda c: (-c.area, c.y0, c.x0, c.y1, c.x1))
    kept = tuple(ordered[:limit])
    return kept, KindCount(len(found), len(kept), limit, len(found) > limit)


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------


class LedgerCache:
    """ページの台帳をファイルに置いて使い回す。

    鍵 = SHA-256(台帳の版 + 設定 + 表示の向きに直したページ画像の画素 + 紙の大きさ)。
    画像が 1 画素でも違えば、設定が 1 つでも違えば、別の鍵になる。
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(gray: np.ndarray, page: pymupdf.Page, settings: LedgerSettings) -> str:
        digest = hashlib.sha256()
        digest.update(LEDGER_VERSION.encode())
        digest.update(settings.fingerprint().encode())
        digest.update(f"{gray.shape}|{page.rect.width:.3f}|{page.rect.height:.3f}".encode())
        digest.update(np.ascontiguousarray(gray).tobytes())
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> PageLedger | None:
        path = self._path(key)
        if not path.exists():
            return None
        return PageLedger.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, key: str, ledger: PageLedger) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _image_coverage(page: pymupdf.Page) -> float:
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        covered += pymupdf.Rect(info["bbox"]).get_area()
    return min(covered / page_area, 1.0)


def _build_page(
    page: pymupdf.Page, settings: LedgerSettings, gray: np.ndarray | None
) -> PageLedger:
    width, height = page.rect.width, page.rect.height
    title: TitleBlock | None = None
    lines: list[LineCandidate] = []
    regions: list[BoxCandidate] = []
    small: list[BoxCandidate] = []
    specks = 0
    source: Source = "none"
    has_text = False
    coverage = 0.0
    unprocessed = False
    error: str | None = None
    try:
        # 表題欄を本文より先に読む。
        words = _page_words(page)
        has_text = any(w.text for w in words)
        title = read_title_block(words, settings)

        coverage = _image_coverage(page)
        drawing_count = len(page.get_drawings())
        image_count = len(page.get_images(full=True))
        if coverage >= settings.raster_coverage:
            source = "raster"
        elif drawing_count:
            source = "vector"
            unprocessed = image_count > 0 and coverage > 0.05
        elif image_count:
            source = "raster"

        if source == "vector":
            lines, regions, small, specks = _vector_candidates(page, settings)
        elif source == "raster":
            if gray is None:
                gray = render_gray(page, settings.raster_long_side_px)
            lines, regions, small, specks = raster_candidates(gray, settings)
    except Exception as exc:  # noqa: BLE001  止まったことを記録して次のページへ進む
        error = f"{type(exc).__name__}: {exc}"
        lines, regions, small = [], [], []

    unique_lines = sorted(set(lines), key=lambda c: (c.y0, c.x0, c.y1, c.x1))
    duplicates = len(lines) - len(unique_lines)
    kept_lines, line_count = _cap_lines(unique_lines, settings.max_lines)
    kept_regions, region_count = _cap_boxes(regions, settings.max_regions)
    kept_small, small_count = _cap_boxes(small, settings.max_small)
    counts = {"lines": line_count, "regions": region_count, "small": small_count}
    gate = gate_page(
        error=error,
        source=source,
        has_text=has_text,
        title=title,
        counts=counts,
        unprocessed_images=unprocessed,
    )
    return PageLedger(
        page_index=page.number,
        width_pt=round(width, 2),
        height_pt=round(height, 2),
        rotation=page.rotation,
        source=source,
        has_text=has_text,
        image_coverage=round(coverage, 4),
        unprocessed_images=unprocessed,
        title=title,
        lines=kept_lines,
        regions=kept_regions,
        small=kept_small,
        counts=counts,
        dropped_specks=specks,
        duplicate_lines=duplicates,
        error=error,
        gate=gate,
    )


def build_ledger(
    pdf_path: str | Path,
    *,
    pages: range | list[int] | None = None,
    settings: LedgerSettings | None = None,
    cache: LedgerCache | None = None,
) -> DocumentLedger:
    """PDF の全ページ(または指定ページ)に同じ固定処理を掛けて台帳を作る。

    ``cache`` を渡すと、ページごとに鍵を作って使い回す。鍵を作るためにページを
    画像にするので、キャッシュが無いときより 1 ページあたりの前処理は増える。
    """
    settings = settings or LedgerSettings()
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF が見つかりません: {path}")
    out: list[PageLedger] = []
    with pymupdf.open(path) as doc:
        indices = range(doc.page_count) if pages is None else pages
        for index in indices:
            if not 0 <= index < doc.page_count:
                raise IndexError(f"ページ {index} は存在しません(全 {doc.page_count} ページ)")
            page = doc.load_page(index)
            if cache is None:
                out.append(_build_page(page, settings, None))
                continue
            gray = render_gray(page, settings.raster_long_side_px)
            key = LedgerCache.key(gray, page, settings)
            hit = cache.get(key)
            if hit is not None:
                out.append(_replace_cache_fields(hit, key, True))
                continue
            built = _replace_cache_fields(_build_page(page, settings, gray), key, False)
            cache.put(key, built)
            out.append(built)
    return DocumentLedger(str(path), settings, tuple(out))


def _replace_cache_fields(ledger: PageLedger, key: str, from_cache: bool) -> PageLedger:
    from dataclasses import replace

    return replace(ledger, cache_key=key, from_cache=from_cache)
