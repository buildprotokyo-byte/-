"""K-70 作業 3: 割れた値のカード 10 枚を、携帯で開ける PDF と HTML にする。

基準は `docs/k70_split_value_cards_criteria.md`(測る前にコミットした)。

使い方(K-61 が保存した全部あり版の 3 回を読むだけ。**AI は呼ばない。正解は開かない。**)::

    PYTHONPATH=. python -m benchmarks.make_k70_split_cards \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --out-dir <共有フォルダの K-70>

**切り抜きの画像と名前が入るので、出したファイルは共有フォルダにだけ置く**(リポジトリには入れない)。

- PDF: A4 縦・字は大きめ(本文 12pt 以上)・1 ページに 1〜2 枚・切り抜きを埋め込む・選択肢に番号。
  答えは「1-3、2-1 …」(カードの番号 - 選択肢の番号)を文字で返してもらう。
- HTML: 同じカード・同じ問い・同じ選択肢と番号。タップすると同じ形の文字ができる。
- **どの値が何回目の読みか・機械の値かは書かない**(機械の推す答えは見せない)。見込みも出さない。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

#: A4 縦(点)。
A4 = (595.0, 842.0)
MARGIN = 36.0
#: 字の大きさ(点)。**本文はどれも 12pt 以上**(基準の線 10)。
SIZE_TITLE = 20.0
SIZE_NO = 18.0
SIZE_Q = 16.0
SIZE_OPT = 16.0
SIZE_NOTE = 12.0
#: 1 ページのカードの数の上限。
PER_PAGE = 2
#: 切り抜きの高さの上限(点)。1 枚なら大きく、2〜3 枚なら小さく。
IMG_H = {1: 330.0, 2: 230.0, 3: 170.0}

FOOTER = "答え方: カードの番号-選択肢の番号(例 1-3、2-1)"
HOW_TO_ANSWER = "答え方: 「カードの番号-選択肢の番号」を、、で区切って文字で返してください(例: 1-3、2-1、3-4)"

#: 日本語の字の候補(上から探す)。``K70_FONT`` で指定もできる。見つからなければ PyMuPDF の内蔵の日本語の字。
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
)


def font_path() -> str | None:
    env = os.environ.get("K70_FONT")
    for p in ([env] if env else []) + list(FONT_CANDIDATES):
        if p and Path(p).exists():
            return p
    return None


# --- 切り抜き(K-65 で直した版を使う) -----------------------------------------


def crop_image(pdf: Path, page_no: int, box_px: Sequence[float]) -> tuple[bytes, list[float]]:
    """切り抜きの JPEG と、切り抜きの中の四角の位置(%)。

    **位置は K-61 の読みの座標(幅 2000 画素の画像)**なので、PDF の点に戻してから、K-65 で直した
    `benchmarks.make_k67_cards._crop`(拡大率・ひっくり返った四角・紙の外の四角を直した版)に渡す。
    回転のあるページも、K-61 が描いた画像と同じ向き(`page.rect` は回転の後の大きさ)。
    """
    import pymupdf

    from benchmarks.make_k67_cards import _crop
    from draft.pages import WIDTH_PX

    with pymupdf.open(pdf) as doc:
        width = doc.load_page(page_no - 1).rect.width
    zoom = WIDTH_PX / width
    box_pt = [float(v) / zoom for v in box_px]
    url, marker = _crop(pdf, page_no, box_pt, scale_width=WIDTH_PX)
    return base64.b64decode(url.split(",", 1)[1]), marker


def with_images(cards: Sequence[Mapping[str, Any]], pdf: Path) -> list[dict[str, Any]]:
    """番号つきのカードに切り抜きの画像を付ける。"""
    out = []
    for c in cards:
        card = dict(c)
        card["画像"] = []
        for crop in c.get("切り抜き") or ():
            jpeg, marker = crop_image(pdf, int(crop["ページ"]), crop["位置"])
            card["画像"].append({"jpeg": jpeg, "marker": marker, "ページ": crop["ページ"]})
        out.append(card)
    return out


# --- PDF -------------------------------------------------------------------


class _Writer:
    def __init__(self, doc: Any, font_file: str | None) -> None:
        import pymupdf

        self.doc = doc
        self.font_file = font_file
        self.fontname = "jp" if font_file else "japan"
        self.font = pymupdf.Font(fontfile=font_file) if font_file else pymupdf.Font("japan")
        self.page = None
        self.y = MARGIN

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=A4[0], height=A4[1])
        if self.font_file:
            self.page.insert_font(fontname=self.fontname, fontfile=self.font_file)
        self.y = MARGIN
        self.page.insert_text((MARGIN, A4[1] - 18), FOOTER, fontname=self.fontname,
                              fontsize=SIZE_NOTE, color=(0.35, 0.35, 0.35))

    def wrap(self, text: str, size: float, width: float) -> list[str]:
        lines: list[str] = []
        for para in str(text).split("\n"):
            import re

            line = ""
            # 英数字と「-」「.」の並び(「1-3」「12.5m2」)は途中で折り返さない。
            for ch in re.findall(r"[0-9A-Za-z.\-]+|.", para):
                if line and self.font.text_length(line + ch, fontsize=size) > width:
                    lines.append(line)
                    line = ch
                else:
                    line += ch
            lines.append(line)
        return lines

    def text_height(self, text: str, size: float, width: float) -> float:
        return len(self.wrap(text, size, width)) * size * 1.45

    def write(self, text: str, size: float, *, x: float = MARGIN, width: float | None = None,
              color: tuple[float, float, float] = (0, 0, 0)) -> None:
        width = width or (A4[0] - x - MARGIN)
        for line in self.wrap(text, size, width):
            self.y += size * 1.2
            self.page.insert_text((x, self.y), line, fontname=self.fontname, fontsize=size, color=color)
            self.y += size * 0.25


def _image_size(img: Mapping[str, Any], count: int, shrink: float = 1.0) -> tuple[float, float]:
    import pymupdf

    pix = pymupdf.Pixmap(img["jpeg"])
    w, h = float(pix.width), float(pix.height)
    max_w = A4[0] - 2 * MARGIN
    max_h = IMG_H.get(count, IMG_H[3]) * shrink
    scale = min(max_w / w, max_h / h)
    return w * scale, h * scale


def _card_height(wr: _Writer, card: Mapping[str, Any], shrink: float = 1.0) -> float:
    width = A4[0] - 2 * MARGIN
    h = SIZE_NO * 1.6 + wr.text_height(card["問い"], SIZE_Q, width) + 6
    imgs = card.get("画像") or ()
    for img in imgs:
        h += _image_size(img, len(imgs), shrink)[1] + SIZE_NOTE * 1.6 + 4
    for opt in card["番号つきの選択肢"]:
        h += wr.text_height(f"{opt['番号']}. {opt['文字']}", SIZE_OPT, width - 12) + 4
    return h + 24


def render_pdf(cards: Sequence[Mapping[str, Any]], out: Path, *, title: str = "割れた値のカード",
               font_file: str | None = None) -> dict[str, Any]:
    """カードを PDF にする。``cards`` は `split_cards.numbered` の形に `画像` を付けたもの。"""
    import pymupdf

    font_file = font_file if font_file is not None else font_path()
    doc = pymupdf.open()
    wr = _Writer(doc, font_file)
    wr.new_page()
    wr.write(title, SIZE_TITLE)
    wr.write(f"カード {len(cards)} 枚。切り抜きのオレンジの四角の所を見て、選択肢の番号を 1 つ選んでください。"
             "分からなければ「分からない」、どれも違えば「どれでもない」の番号を選んでください。", SIZE_NOTE + 1)
    wr.write(HOW_TO_ANSWER, SIZE_NOTE + 1, color=(0.1, 0.35, 0.2))
    wr.y += 10
    on_page = 0
    width = A4[0] - 2 * MARGIN
    for card in cards:
        room = A4[1] - MARGIN - 20 - wr.y
        shrink = 1.0
        if on_page == 0 and _card_height(wr, card) > room:
            # 表紙(答え方)の下にも 1 枚は載せる。載らなければ切り抜きを少しだけ小さくする(半分まで)。
            while shrink > 0.5 and _card_height(wr, card, shrink) > room:
                shrink -= 0.05
            if _card_height(wr, card, shrink) > room:
                shrink = 1.0
        need = _card_height(wr, card, shrink)
        if on_page >= PER_PAGE or need > room:
            wr.new_page()
            on_page = 0
            shrink = 1.0
        wr.write(f"カード {card['番号']}", SIZE_NO, color=(0.1, 0.35, 0.2))
        wr.write(card["問い"], SIZE_Q)
        wr.y += 6
        imgs = card.get("画像") or ()
        for img in imgs:
            w, h = _image_size(img, len(imgs), shrink)
            rect = pymupdf.Rect(MARGIN, wr.y, MARGIN + w, wr.y + h)
            wr.page.insert_image(rect, stream=img["jpeg"])
            wr.page.draw_rect(rect, color=(0.8, 0.8, 0.8), width=0.5)
            m = img.get("marker")
            if m:
                mr = pymupdf.Rect(rect.x0 + w * m[0] / 100, rect.y0 + h * m[1] / 100,
                                  rect.x0 + w * (m[0] + m[2]) / 100, rect.y0 + h * (m[1] + m[3]) / 100)
                wr.page.draw_rect(mr, color=(0.88, 0.48, 0.09), width=2)
            wr.y += h
            wr.write(f"{img['ページ']} ページ(オレンジの四角)", SIZE_NOTE, color=(0.35, 0.35, 0.35))
            wr.y += 4
        for opt in card["番号つきの選択肢"]:
            wr.write(f"{opt['番号']}. {opt['文字']}", SIZE_OPT, x=MARGIN + 12, width=width - 12)
            wr.y += 4
        wr.y += 10
        wr.page.draw_line((MARGIN, wr.y), (A4[0] - MARGIN, wr.y), color=(0.75, 0.75, 0.75), width=0.7)
        wr.y += 10
        on_page += 1
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001 — 字を小さくできなくても PDF は使える
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, garbage=3, deflate=True)
    pages = doc.page_count
    doc.close()
    return {"ページ数": pages, "字を埋め込んだか": bool(font_file), "カード": len(cards)}


def check_pdf(path: Path, cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """PDF を PyMuPDF で開き直して確かめる(基準の線 10)。"""
    import pymupdf

    out: dict[str, Any] = {"開けた": False}
    with pymupdf.open(path) as doc:
        out["開けた"] = True
        out["ページ数"] = doc.page_count
        out["A4縦"] = all(abs(p.rect.width - A4[0]) < 1 and abs(p.rect.height - A4[1]) < 1 for p in doc)
        text = "".join(p.get_text() for p in doc)
        # 折り返しの改行・字の中の空白を外して比べる。IPA の字は「-」を取り出すと U+00AD になるので「-」に戻す。
        norm = lambda s: "".join(str(s).replace("\xad", "-").split())  # noqa: E731
        flat = norm(text)
        sizes = [s["size"] for p in doc for b in p.get_text("dict")["blocks"] for l in b.get("lines", ())
                 for s in l.get("spans", ()) if s.get("text", "").strip()]
        out["いちばん小さい字(pt)"] = round(min(sizes), 1) if sizes else None
        out["画像の数"] = sum(len(p.get_images()) for p in doc)
        per_page = [sum(1 for c in cards if f"カード{c['番号']}" in [norm(ln) for ln in p.get_text().splitlines()])
                    for p in doc]
        out["1ページのカードの数"] = per_page
        out["見つからなかった問い"] = sum(1 for c in cards if norm(c["問い"]) not in flat)
        out["見つからなかった選択肢"] = sum(1 for c in cards for o in c["番号つきの選択肢"]
                                  if norm(f"{o['番号']}.{o['文字']}") not in flat)
        out["答え方が書いてある"] = norm("1-3、2-1") in flat
    return out


# --- HTML ------------------------------------------------------------------

HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>割れた値のカード</title>
<style>
 :root { --bg:#faf9f7; --fg:#1d1b19; --line:#dcd8d2; --card:#fff; --accent:#2d6a4f; --muted:#6b645c; --on:#eaf3ee; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
   --bg:#17161a; --fg:#eceaf0; --line:#36343c; --card:#201f25; --accent:#7fc0a0; --muted:#9a94a2; --on:#23392f; } }
 :root[data-theme="dark"] { --bg:#17161a; --fg:#eceaf0; --line:#36343c; --card:#201f25; --accent:#7fc0a0; --muted:#9a94a2; --on:#23392f; }
 * { box-sizing:border-box; }
 body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
        font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; line-height:1.7; }
 h1 { font-size:20px; margin:0 0 4px; }
 .note { color:var(--muted); font-size:14px; margin:0 0 18px; }
 .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:18px; }
 .no { font-size:18px; font-weight:700; color:var(--accent); }
 .q { font-size:17px; font-weight:700; margin:4px 0 10px; }
 .shot { position:relative; border:1px solid var(--line); border-radius:8px; overflow:hidden; margin-bottom:4px; }
 .shot img { width:100%; display:block; }
 .shot u { position:absolute; border:3px solid #e07a17; border-radius:3px; }
 .cap { font-size:13px; color:var(--muted); margin-bottom:10px; }
 button.opt { display:block; width:100%; text-align:left; padding:13px 14px; margin-bottom:8px; font-size:16px;
              border:1px solid var(--line); border-radius:9px; background:var(--card); color:var(--fg); }
 button.opt.on { border-color:var(--accent); background:var(--on); font-weight:700; }
 #out { width:100%; font-size:18px; padding:10px; border:1px solid var(--line); border-radius:8px;
        background:var(--card); color:var(--fg); }
 #copy { margin-top:8px; padding:12px; font-size:16px; width:100%; border:0; border-radius:9px;
         background:var(--accent); color:#fff; font-weight:700; }
</style></head><body>
<h1>__TITLE__</h1>
<p class="note">切り抜きのオレンジの四角の所を見て、選択肢を 1 つ選んでください。分からなければ「分からない」、
どれも違えば「どれでもない」を選んでください。__HOW__</p>
<div id="cards"></div>
<p class="note">選ぶと、下に答えの文字ができます。そのまま貼って返してください。</p>
<input id="out" readonly placeholder="1-3、2-1 …">
<button id="copy">答えの文字をコピー</button>
<script>
const CARDS = __CARDS__;
const picked = {};
const root = document.getElementById("cards");
function render() {
  document.getElementById("out").value = CARDS.filter(c => picked[c.番号]).map(c => c.番号 + "-" + picked[c.番号]).join("、");
}
CARDS.forEach(c => {
  const el = document.createElement("div");
  el.className = "card";
  const shots = c.画像.map(i => {
    const m = i.marker ? `<u style="left:${i.marker[0]}%;top:${i.marker[1]}%;width:${i.marker[2]}%;height:${i.marker[3]}%"></u>` : "";
    return `<div class="shot">${m}<img src="${i.src}" alt=""></div><div class="cap">${i.ページ} ページ(オレンジの四角)</div>`;
  }).join("");
  el.innerHTML = `<div class="no">カード ${c.番号}</div><div class="q"></div>${shots}<div class="opts"></div>`;
  el.querySelector(".q").textContent = c.問い;
  const opts = el.querySelector(".opts");
  c.選択肢.forEach(o => {
    const b = document.createElement("button");
    b.className = "opt"; b.textContent = o.番号 + ". " + o.文字;
    b.onclick = () => { picked[c.番号] = o.番号;
      opts.querySelectorAll("button").forEach(x => x.classList.remove("on")); b.classList.add("on"); render(); };
    opts.appendChild(b);
  });
  root.appendChild(el);
});
document.getElementById("copy").onclick = () => {
  const t = document.getElementById("out"); t.select();
  try { navigator.clipboard.writeText(t.value); } catch (e) { document.execCommand("copy"); }
};
</script></body></html>
"""


def html_cards(cards: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """HTML に入れるカード(**値の出どころ・見込みは入れない**。PDF と同じ番号・問い・選択肢)。"""
    out = []
    for c in cards:
        out.append({
            "番号": c["番号"], "問い": c["問い"],
            "選択肢": [{"番号": o["番号"], "文字": o["文字"]} for o in c["番号つきの選択肢"]],
            "画像": [{"src": "data:image/jpeg;base64," + base64.b64encode(i["jpeg"]).decode("ascii"),
                    "marker": i.get("marker"), "ページ": i["ページ"]} for i in c.get("画像") or ()],
        })
    return out


def render_html(cards: Sequence[Mapping[str, Any]], out: Path, *, title: str = "割れた値のカード") -> None:
    data = json.dumps(html_cards(cards), ensure_ascii=False).replace("</", "<\\/")
    text = (HTML.replace("__CARDS__", data).replace("__TITLE__", title).replace("__HOW__", HOW_TO_ANSWER))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def cards_in_html(path: Path) -> list[dict[str, Any]]:
    """HTML に入れたカード(テストで PDF と比べるため)。"""
    text = path.read_text(encoding="utf-8")
    start = text.index("const CARDS = ") + len("const CARDS = ")
    end = text.index(";\nconst picked")
    return json.loads(text[start:end].replace("<\\/", "</"))


# --- 実案件 -----------------------------------------------------------------


def build_real(runs_dirs: Sequence[Path], pdf: Path | None, *, with_scale: bool = True) -> dict[str, Any]:
    """K-61 が保存した 3 回から、割れた値のカードを作って並べる(**AI は呼ばない**)。"""
    from draft import flags, questioning, split_cards

    drafts = {p.name: json.loads((p / "下書き.json").read_text(encoding="utf-8")) for p in runs_dirs}
    runs = {n: d["理解"]["項目"] for n, d in drafts.items()}
    machine, scale = {}, {}
    for p in runs_dirs:
        prod = p / "本番の形.json"
        machine[p.name] = (questioning.machine_values_from(
            json.loads(prod.read_text(encoding="utf-8")).get("工事項目") or ()) if prod.exists() else {})
        if pdf is not None and with_scale:
            d = drafts[p.name]
            org = {**d["整理"], "ページ": {int(n): v for n, v in d["整理"]["ページ"].items()}}
            scale[p.name] = questioning.scale_values_from(
                flags.scale_length(pdf, org, d["読む"], d["理解"], d["仕上表"]))
        else:
            scale[p.name] = {}
    base = runs_dirs[0].name
    found = split_cards.find_splits(runs, machine=machine, scale=scale)
    ordered = split_cards.order(found["カード"], runs, base, finish=drafts[base]["仕上表"])
    return {"drafts": drafts, "runs": runs, "machine": machine, "scale": scale, "base": base,
            "found": found, "ordered": ordered}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-70 作業 3 割れた値のカード(PDF と HTML)")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--by-frame", action="store_true", help="工事チェック表の枠ごとにまとめて並べる")
    p.add_argument("--no-scale", action="store_true")
    p.add_argument("--check-out", type=Path, default=None, help="PDF の確かめの結果(件数だけ)を書く先")
    a = p.parse_args(argv)

    from draft import split_cards

    real = build_real(a.runs, a.pdf, with_scale=not a.no_scale)
    shown = with_images(split_cards.numbered(real["ordered"], a.count, group_by_frame=a.by_frame), a.pdf)
    pdf_out = a.out_dir / "割れた値のカード10枚.pdf"
    html_out = a.out_dir / "割れた値のカード10枚.html"
    info = render_pdf(shown, pdf_out)
    render_html(shown, html_out)
    check = check_pdf(pdf_out, shown)
    # 答えを戻すときに使う、番号とカードの鍵の対応(名前は入れない)。
    key_out = a.out_dir / "割れた値のカード10枚_番号と鍵.json"
    key_out.write_text(json.dumps({"カード": [{"番号": c["番号"], "鍵": c["鍵"], "選択肢の数": len(c["選択肢"])}
                                            for c in shown]}, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.check_out:
        a.check_out.write_text(json.dumps({"作った": info, "確かめ": check}, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(json.dumps({"PDF": str(pdf_out), "HTML": str(html_out), "作った": info, "確かめ": check},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
