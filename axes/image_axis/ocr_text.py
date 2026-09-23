"""画像軸: **スキャンされたページから文字を読む**層(OCR)。

なぜ要るのか
------------
2026-09-22 時点で、このリポジトリが図面の文字から読めるものは全部
`page.get_text()`(PDF に埋め込まれた文字)から来ていた。表題欄の縮尺
(`pdf_vector_symbols.extract_scale`)、面積の記載(`find_area_labels`)、
建具表・内装仕上表(`pdf_tables` → `schedule_tables`)のどれもである。

**実案件 P011 の図面は 34 ページ全部がスキャン画像で、埋め込み文字が
1 文字も無い**(`docs/real_drawing_eval_report.md`)。つまりこの経路は
実案件で 1 件も動かない。本番の入口(`intake/drawing_intake.py`)も、
ラスターのページを「未対応」として記録するところまでしかしていない。
このモジュールはその穴を開ける 1 段目である。

実測して分かったこと(設計の理由)
--------------------------------
合成したスキャン(`tests/scan_fixtures.py` と同じ作り)に本物の OCR を
掛けて測った結果が `docs/ocr_scanned_pages_report.md` にある。要点は 3 つ。

1. **確信度は文字化けの見張りにならない。** 中国語・英語のモデルは
   ``種別`` を ``种别``(確信度 0.99)、``引戸`` を ``引户``(0.97)と読んだ。
   確信度で足切りしても、この種の化けは 1 件も止まらない。
2. **モデルによって、強いところが正反対だった。** 中国語・英語のモデルは
   数字(``1650`` ``2000`` ``WD-01``)をすべて正しく読み、日本語の語を
   化けさせた。日本語のモデルは語(``種別`` ``引戸`` ``開き戸``
   ``専有延床面積``)をすべて正しく読み、数字を ``16.0`` ``２ｏｏ``
   ``ｓｓ・ｓ４`` のように壊した。**片方だけを信じると、もっともらしい
   誤りが黙って下流に入る。**
3. **語が丸ごと消えることがある。** 見出しの ``高さ`` は 4 回の試行すべてで
   返ってこなかった。数量の ``1`` が消えた回もある。**「読めた語の一覧」は
   「書いてあることの一覧」ではない。**

この層が守ること
----------------
1. **位置はページ座標(ポイント)で返す。** 既存の読み取り経路が全部
   ポイントで位置を持っているので、ここだけ画素で返すと突き合わせられない。
2. **確信度の低い読みは既定値で埋めず落とし、落としたことを残す**
   (`OcrPage.dropped`)。ただし上の 1 のとおり、**これは安全網ではない。**
3. **エンジンを 2 つ以上渡したときは、全部が同じに読んだ語だけを通す。**
   食い違ったものは `OcrPage.conflicts` に残す。**どちらかを選ばない。**
   選べる根拠がこの場に無く、間違ったほうを選ぶと誤りが黙って下流に入る。
4. **読めた語が 0 件であることを「文字が無い」と言わない**(`notes`)。
5. **エンジンが 1 つも無ければ例外**(`OcrUnavailable`)。黙って空を返すと
   「掛けたが何も無かった」と区別できない。

このモジュールがやらないこと
----------------------------
- **傾き補正・二値化・ノイズ除去をしない。** 前処理を入れると「どの前処理が
  効いたか」を測る話が増えるうえ、いまはその測り方が無い。エンジンに素の
  グレースケールを渡す。
- **文字の化けを直さない。** ``引户`` を ``引戸`` に寄せる変換は書かない。
  よく似た字への寄せは、**本当に ``户`` と書いてある図面**を壊す。
  幅寄せ(全角の数字・英字・記号 → 半角)だけを `normalize_ocr_text` で行う。
- **罫線を見つけて表の升目を組むことはしない。** スキャンの建具表を表として
  読むには画像から罫線を検出する実装が要る。これは次の段で、まだ無い。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import numpy as np
import pymupdf

#: OCR に渡す既定の解像度。実案件のスキャン図面は 200〜300dpi で作られる
#: ことが多い。**低いほうへ落とすと細い文字が消える**ので、元の画像より
#: 高めに取る。実測では 200 と 300 で読めた語の数はほとんど変わらなかった。
OCR_DPI = 300

#: 既定の確信度の足切り。
#:
#: **これは安全網ではない。** 実測した化け方(``種別`` → ``种别``)は確信度
#: 0.99 で返ってきた。ここで落とせるのは、罫線や汚れを文字と見た類の
#: 明らかな屑だけである。値を上げても文字化けは止まらない。
DEFAULT_MIN_CONFIDENCE = 0.5

#: 別のエンジンの読みを「同じ場所の読み」とみなすための重なり(IoU)。
#: 検出の枠はエンジンごとに少しずれるので、完全一致は求められない。
#: **実測で校正した値ではない。** 低くすると隣の語同士を同じ語として
#: 突き合わせてしまうので、半分以上重なることを条件にしてある。
DEFAULT_AGREEMENT_IOU = 0.5

#: 全角 → 半角に寄せる文字。**数字・英字・記号だけ。**
#: ``㎡`` や仮名・漢字は触らない(NFKC を丸ごと掛けると ``㎡`` が ``m2`` に、
#: ``㎜`` が ``mm`` になり、図面に書かれていた単位の記号が消える)。
_FULLWIDTH = (
    "０１２３４５６７８９"
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "－．，／：；（）＋＝％＃＊＠　"
)
_HALFWIDTH = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "-.,/:;()+=%#*@ "
)
_WIDTH_TABLE = str.maketrans(_FULLWIDTH, _HALFWIDTH)


class OcrUnavailable(RuntimeError):
    """OCR のエンジンが使えない。**空の結果で代用しない。**"""


@dataclass(frozen=True)
class OcrWord:
    """エンジンが返した語 1 つ。座標は**画素**。"""

    text: str
    """読んだ文字。**そのまま。** 整形はこちらでしない。"""

    rect_px: tuple[float, float, float, float]
    """画像の中の外接矩形(左, 上, 右, 下)。"""

    confidence: float
    """0.0〜1.0。エンジンが確信度を出さないなら 1.0 ではなく実際の値を入れる。"""


@runtime_checkable
class OcrBackend(Protocol):
    """OCR エンジンの口。**この口さえ満たせば差し替えられる。**"""

    name: str
    model_id: str

    def recognize(self, image: np.ndarray, *, dpi: int) -> Sequence[OcrWord]:
        ...


@dataclass(frozen=True)
class OcrSpan:
    """読めた語 1 つ。座標は**ページ座標(ポイント)**。"""

    text: str
    """読んだ文字。複数のエンジンで突き合わせた場合は 1 つ目のエンジンの文字。"""

    normalized: str
    """幅寄せしたもの(`normalize_ocr_text`)。突き合わせと数値の解釈に使う。"""

    rect_pt: tuple[float, float, float, float]
    confidence: float
    """突き合わせた場合は**いちばん低いエンジンの値**。平均は取らない。"""

    engines: tuple[str, ...]
    readings: tuple[tuple[str, str], ...]
    """``(エンジン名, 読んだ文字)`` の全部。根拠としてそのまま運ぶ。"""


@dataclass(frozen=True)
class OcrConflict:
    """エンジンどうしで食い違った読み。**どちらも採らない。**"""

    rect_pt: tuple[float, float, float, float]
    readings: tuple[tuple[str, str], ...]
    reason: str


@dataclass(frozen=True)
class OcrDrop:
    """足切りで落とした読み。**落としたことを残す。**"""

    text: str
    rect_pt: tuple[float, float, float, float]
    confidence: float
    engine: str
    reason: str


@dataclass(frozen=True)
class OcrPage:
    """1 ページを OCR に掛けた結果。**読めなかったことも入っている。**"""

    page_index: int
    """0 始まり。"""

    dpi: int
    engines: tuple[str, ...]
    models: tuple[str, ...]
    spans: tuple[OcrSpan, ...]
    conflicts: tuple[OcrConflict, ...]
    dropped: tuple[OcrDrop, ...]
    min_confidence: float
    notes: tuple[str, ...] = ()

    @property
    def cross_checked(self) -> bool:
        """2 つ以上のエンジンで突き合わせたか。"""
        return len(self.engines) >= 2

    def lines(self) -> list[tuple[tuple[float, float, float, float], str]]:
        """近い高さにある語を 1 行にまとめて、左から並べる。

        表題欄のように「ラベルと数値が横に並ぶ」記載を、既存の正規表現
        (`pdf_vector_symbols`)にそのまま掛けられる形にするため。
        **行の組み立てはここだけで、意味づけはしない。**
        """
        remaining = sorted(self.spans, key=lambda span: (span.rect_pt[1], span.rect_pt[0]))
        out: list[tuple[tuple[float, float, float, float], str]] = []
        current: list[OcrSpan] = []
        for span in remaining:
            if not current:
                current = [span]
                continue
            height = max(current[0].rect_pt[3] - current[0].rect_pt[1], 1.0)
            same_line = abs(span.rect_pt[1] - current[0].rect_pt[1]) <= height * 0.6
            if same_line:
                current.append(span)
            else:
                out.append(_join_line(current))
                current = [span]
        if current:
            out.append(_join_line(current))
        return out

    def text(self) -> str:
        """行に組んだ文字列。**位置が要らない読み取りのため。**"""
        return "\n".join(text for _, text in self.lines())


def _join_line(spans: list[OcrSpan]) -> tuple[tuple[float, float, float, float], str]:
    ordered = sorted(spans, key=lambda span: span.rect_pt[0])
    rect = (
        min(span.rect_pt[0] for span in ordered),
        min(span.rect_pt[1] for span in ordered),
        max(span.rect_pt[2] for span in ordered),
        max(span.rect_pt[3] for span in ordered),
    )
    return rect, " ".join(span.text for span in ordered)


def normalize_ocr_text(text: str) -> str:
    """全角の数字・英字・記号を半角に寄せる。**それ以外は触らない。**

    OCR は同じ図面の同じ文字を、回によって全角と半角のどちらでも返す
    (実測: ``１６5０`` ``95．54``)。数値として解釈するときと、
    エンジンどうしを突き合わせるときに、この違いだけで別物にならないようにする。

    **似た字への寄せはしない。** ``户`` → ``戸`` のような変換は、本当に
    ``户`` と書いてある図面を壊すうえ、どの字をどの字に寄せてよいかの
    根拠がこの場に無い。
    """
    return text.translate(_WIDTH_TABLE)


def recognize_page(
    pdf_path: str | Path,
    page_index: int,
    *,
    backends: Sequence[OcrBackend],
    dpi: int = OCR_DPI,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    agreement_iou: float = DEFAULT_AGREEMENT_IOU,
) -> OcrPage:
    """ページを画像にして OCR に掛け、読めた語をページ座標で返す。

    `backends` を 2 つ以上渡すと、**全部が同じに読んだ語だけ**が `spans` に入る。
    食い違った語は `conflicts` に入り、どちらも採らない。
    """
    if not backends:
        raise OcrUnavailable(
            "OCR のエンジンが 1 つも渡されていません。"
            "空の結果を返すと「掛けたが何も無かった」と区別できないため、ここで止めます"
        )
    if dpi <= 0:
        raise ValueError("dpi は正の整数である必要があります")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence は 0.0〜1.0 である必要があります")

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF が見つかりません: {path}")

    with pymupdf.open(path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(
                f"ページ {page_index} は存在しません(全 {doc.page_count} ページ)"
            )
        page = doc.load_page(page_index)
        rect = pymupdf.Rect(page.rect)
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width
        ).copy()

    # 画素 → ポイント。ページの原点が (0,0) でない PDF があるので、
    # 倍率だけでなく原点も持ってくる。
    scale_x = rect.width / pixmap.width if pixmap.width else 0.0
    scale_y = rect.height / pixmap.height if pixmap.height else 0.0

    def to_pt(rect_px: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = rect_px
        return (
            rect.x0 + x0 * scale_x,
            rect.y0 + y0 * scale_y,
            rect.x0 + x1 * scale_x,
            rect.y0 + y1 * scale_y,
        )

    dropped: list[OcrDrop] = []
    per_engine: list[list[tuple[str, tuple[float, float, float, float], float]]] = []
    engines: list[str] = []
    models: list[str] = []
    for backend in backends:
        name = getattr(backend, "name", backend.__class__.__name__)
        engines.append(name)
        models.append(getattr(backend, "model_id", "unknown"))
        kept: list[tuple[str, tuple[float, float, float, float], float]] = []
        for word in backend.recognize(image, dpi=dpi):
            rect_pt = to_pt(word.rect_px)
            if not word.text.strip():
                continue
            if word.confidence < min_confidence:
                dropped.append(
                    OcrDrop(
                        text=word.text,
                        rect_pt=rect_pt,
                        confidence=word.confidence,
                        engine=name,
                        reason=(
                            f"確信度 {word.confidence:.2f} が足切り "
                            f"{min_confidence:.2f} に届かないため採らなかった"
                        ),
                    )
                )
                continue
            kept.append((word.text, rect_pt, word.confidence))
        per_engine.append(kept)

    spans, conflicts = _cross_check(per_engine, engines, agreement_iou)

    notes: list[str] = []
    if len(engines) < 2:
        notes.append(
            "エンジンが 1 つなので、読みの突き合わせをしていない。"
            "**この読みは 1 つの手法の主張であって、確かめられた値ではない**"
        )
    if not spans:
        notes.append(
            "読めた語が 0 件。**ページに文字が無いという意味ではない。**"
            "スキャンの質・文字の大きさ・エンジンの向き不向きのどれでも 0 件になる"
        )
    if conflicts:
        notes.append(
            f"エンジンどうしで食い違った読みが {len(conflicts)} 件ある。"
            "どちらも採っていない"
        )
    if dropped:
        notes.append(f"確信度の足切りで落とした読みが {len(dropped)} 件ある")

    return OcrPage(
        page_index=page_index,
        dpi=dpi,
        engines=tuple(engines),
        models=tuple(models),
        spans=tuple(spans),
        conflicts=tuple(conflicts),
        dropped=tuple(dropped),
        min_confidence=min_confidence,
        notes=tuple(notes),
    )


def _cross_check(
    per_engine: list[list[tuple[str, tuple[float, float, float, float], float]]],
    engines: list[str],
    agreement_iou: float,
) -> tuple[list[OcrSpan], list[OcrConflict]]:
    """エンジンどうしの読みを突き合わせる。

    1 つ目のエンジンの語を基準にして、他のエンジンの中で**いちばん重なる語**を
    探す。全部が同じ文字(幅寄せ後)なら通し、1 つでも違えば食い違いにする。
    どのエンジンにも対応が無かった語も食い違いとして残す。
    """
    if not per_engine:
        return [], []

    base = per_engine[0]
    others = per_engine[1:]
    if not others:
        return (
            [
                OcrSpan(
                    text=text,
                    normalized=normalize_ocr_text(text),
                    rect_pt=rect,
                    confidence=confidence,
                    engines=(engines[0],),
                    readings=((engines[0], text),),
                )
                for text, rect, confidence in base
            ],
            [],
        )

    spans: list[OcrSpan] = []
    conflicts: list[OcrConflict] = []
    matched_indexes: list[set[int]] = [set() for _ in others]

    for text, rect, confidence in base:
        readings = [(engines[0], text)]
        confidences = [confidence]
        agreed = True
        for offset, other in enumerate(others):
            best_index, best_iou = -1, 0.0
            for index, (_, other_rect, _) in enumerate(other):
                score = _iou(rect, other_rect)
                if score > best_iou:
                    best_index, best_iou = index, score
            if best_index < 0 or best_iou < agreement_iou:
                readings.append((engines[offset + 1], ""))
                agreed = False
                continue
            matched_indexes[offset].add(best_index)
            other_text, _, other_confidence = other[best_index]
            readings.append((engines[offset + 1], other_text))
            confidences.append(other_confidence)
            if normalize_ocr_text(other_text) != normalize_ocr_text(text):
                agreed = False

        if agreed:
            spans.append(
                OcrSpan(
                    text=text,
                    normalized=normalize_ocr_text(text),
                    rect_pt=rect,
                    confidence=min(confidences),
                    engines=tuple(engines),
                    readings=tuple(readings),
                )
            )
        else:
            missing = [name for name, value in readings if not value]
            conflicts.append(
                OcrConflict(
                    rect_pt=rect,
                    readings=tuple(readings),
                    reason=(
                        f"{'、'.join(missing)} に対応する読みが無いため採らなかった"
                        if missing
                        else "エンジンによって読みが違うため、どちらも採らなかった"
                    ),
                )
            )

    # どの基準の語にも対応しなかった、他のエンジンだけが見た語。
    for offset, other in enumerate(others):
        for index, (text, rect, _) in enumerate(other):
            if index in matched_indexes[offset]:
                continue
            conflicts.append(
                OcrConflict(
                    rect_pt=rect,
                    readings=((engines[offset + 1], text), (engines[0], "")),
                    reason=f"{engines[0]} に対応する読みが無いため採らなかった",
                )
            )
    return spans, conflicts


def _iou(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area_first = max(first[2] - first[0], 0.0) * max(first[3] - first[1], 0.0)
    area_second = max(second[2] - second[0], 0.0) * max(second[3] - second[1], 0.0)
    union = area_first + area_second - intersection
    return intersection / union if union > 0 else 0.0
