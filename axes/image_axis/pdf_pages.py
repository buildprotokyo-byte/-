"""画像軸: 本物の図面 PDF をラスター画像として取り込む入口。

2026-09-21 時点まで、このリポジトリには **PDF を読むコードが1行も無く**、
画像軸に入るのは `benchmarks/synthetic_plans.py` が生成する合成図面だけでした。
実案件の図面(P011案件 `設計図面_匿名化済み.pdf` など)を評価するには、
まず「PDF のページ → 画像軸が食べられる 2 値化前のグレースケール」に落とす
経路が要ります。本モジュールがその1段目です。

**このモジュールが意図的にやらないこと(重要)**

`PdfPage.mm_per_pixel` が返すのは **紙の上の 1 ピクセルが何ミリか**であって、
**図面が表している実寸が何ミリか**ではありません。両者は図面の縮尺
(1:50, 1:100 など)だけ食い違い、その縮尺は表題欄の文字列か寸法線として
図面に書かれています。つまり縮尺の確定には**文章軸の読み取りが要る**のに、
文章軸はこのリポジトリに未実装です(v8 2章の軸カタログ参照)。

そこで `drawing_mm_per_pixel` は **既定で None** とし、呼び出し側が縮尺を
明示的に与えたときだけ実寸を返します。紙のスケールを実寸として黙って
流用すると、1:100 の図面では **100 倍ずれた長さ**が「もっともらしい数値」
として下流に入ります。これは v8 8章の穴2(集計表を見ても気づけない誤り)
そのものであり、2026-09-21 に修理した単位の取り違え
(`docs/top_priority_unit_safety_defect.md`)と同じ型の事故です。
既定値を与えないことで、縮尺を意識しない呼び出しが黙って通るのを防ぎます。

**ページの中身を偽らないこと**

`PdfPage.content_kind` は、そのページが

- ``"vector"``  … 線や文字がベクターとして入っている(CAD からの出力)
- ``"raster"``  … 紙をスキャンした画像が1枚貼ってあるだけ
- ``"mixed"``   … 両方ある
- ``"empty"``   … 描画オブジェクトが無い

のどれなのかを実測して返します。段階Aで測った劣化耐性は
**スキャン図面(raster)を想定した劣化モデル**の上の数字なので、
対象がベクターなのかスキャンなのかを取り違えると、報告した数字が
そのまま当てはまるかどうかの前提が崩れます。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pymupdf

#: PDF の座標単位(ポイント)からミリメートルへの換算。1 pt = 1/72 inch。
MM_PER_POINT = 25.4 / 72.0

#: 既定のラスター化解像度。線画図面の細線(窓の 1px 線など)を潰さない程度に高く、
#: かつ A1 サイズでもメモリに載る値として選んだ。用が合わなければ呼び出し側で上書きする。
DEFAULT_DPI = 200

ContentKind = Literal["vector", "raster", "mixed", "empty"]


@dataclass(frozen=True)
class PdfPage:
    """PDF の 1 ページをラスター化した結果と、そのページについて実測した事実。"""

    page_index: int
    """0 始まりのページ番号。"""

    image: np.ndarray
    """グレースケール(uint8)。2 値化はしていない。"""

    dpi: int

    paper_width_mm: float
    paper_height_mm: float

    content_kind: ContentKind
    """ページの中身の実測。モジュール冒頭の説明を参照。"""

    vector_draw_count: int
    """ベクターの描画オブジェクト(線・曲線・塗り)の個数。"""

    embedded_image_count: int
    """ページに貼られているラスター画像の枚数。"""

    text: str
    """ページに埋め込まれている文字列。**文章軸が未実装なので誰も消費しない。**"""

    text_span_count: int
    """埋め込み文字列の断片数。0 ならスキャン画像か、文字がアウトライン化されている。"""

    @property
    def mm_per_pixel(self) -> float:
        """**紙の上の** 1 ピクセルあたりのミリメートル。実寸ではない。"""
        return 25.4 / self.dpi

    def drawing_mm_per_pixel(self, scale_denominator: float | None = None) -> float | None:
        """図面が表している実寸の 1 ピクセルあたりミリメートル。

        `scale_denominator` は縮尺の分母(1:100 なら 100.0)。**既定は None** で、
        与えられなければ None を返します。返り値が None であることは
        「縮尺が未確定」という情報であって、0 でも 1 でもありません。
        呼び出し側が None のまま長さを計算しないよう、ここでは推測しません。
        """
        if scale_denominator is None:
            return None
        if scale_denominator <= 0:
            raise ValueError("縮尺の分母は正の数である必要があります")
        return self.mm_per_pixel * scale_denominator


def _classify(page: pymupdf.Page) -> tuple[ContentKind, int, int]:
    """ページがベクターかスキャンかを、描画オブジェクトの実数から判定する。"""
    vector_count = len(page.get_drawings())
    image_count = len(page.get_images(full=True))
    if vector_count and image_count:
        kind: ContentKind = "mixed"
    elif vector_count:
        kind = "vector"
    elif image_count:
        kind = "raster"
    else:
        kind = "empty"
    return kind, vector_count, image_count


def rasterize(
    pdf_path: str | Path,
    dpi: int = DEFAULT_DPI,
    pages: range | None = None,
) -> list[PdfPage]:
    """PDF を開いて、各ページをグレースケール画像にして返す。

    `pages` を与えると、そのページ番号(0 始まり)だけを処理します。
    """
    if dpi <= 0:
        raise ValueError("dpi は正の整数である必要があります")

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF が見つかりません: {path}")

    out: list[PdfPage] = []
    with pymupdf.open(path) as doc:
        indices = range(doc.page_count) if pages is None else pages
        for index in indices:
            if not 0 <= index < doc.page_count:
                raise IndexError(f"ページ {index} は存在しません(全 {doc.page_count} ページ)")
            page = doc.load_page(index)
            kind, vector_count, image_count = _classify(page)

            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width
            )

            text = page.get_text("text")
            # 空白だけの断片は数えない。スキャン図面と「文字が少しだけある図面」を
            # 取り違えると、文章軸が使えるかどうかの判断を誤る。
            span_count = len([token for token in text.split() if token])

            out.append(
                PdfPage(
                    page_index=index,
                    image=image.copy(),
                    dpi=dpi,
                    paper_width_mm=page.rect.width * MM_PER_POINT,
                    paper_height_mm=page.rect.height * MM_PER_POINT,
                    content_kind=kind,
                    vector_draw_count=vector_count,
                    embedded_image_count=image_count,
                    text=text,
                    text_span_count=span_count,
                )
            )
    return out


def describe(pages: list[PdfPage]) -> str:
    """ページごとの実測を人が読める表にする。評価レポートにそのまま貼るための出力。"""
    lines = [
        "| ページ | 用紙(mm) | 中身 | ベクター描画数 | 埋め込み画像 | 文字断片 |",
        "|---|---|---|---|---|---|",
    ]
    for page in pages:
        lines.append(
            f"| {page.page_index + 1} "
            f"| {page.paper_width_mm:.0f} x {page.paper_height_mm:.0f} "
            f"| {page.content_kind} "
            f"| {page.vector_draw_count} "
            f"| {page.embedded_image_count} "
            f"| {page.text_span_count} |"
        )
    return "\n".join(lines)
