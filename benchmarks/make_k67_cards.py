"""K-67 6 節(b)(d): **おーちゃんの目で確かめるカード。**1 枚 HTML、確認画面と同じ作り。

2 種類を 1 枚に入れる:

- **未読 10 か所**: 読めていないと機械が言っている場所を図面の上に四角で出し、
  「本当に何か描かれているか / 何も無いか / 分からない」を選んでもらう。
  **「何も無い」が多ければ、読了率は低く出すぎている**(落ちの数え方が厳しすぎる)。
- **候補ページ 10 枠**: 枠ごとに機械が挙げた候補ページを出し、
  「関係している / 関係していない / 分からない」を選んでもらう。

**図面の画像が入るので、リポジトリにも Artifact にも置かない。**共有フォルダだけに置く。

実行::

    PYTHONPATH=. python -m benchmarks.make_k67_cards \
        --pdf /mnt/project-files/anonymized/P011_匿名化v4.pdf \
        --run /mnt/project-files/reports/K-61/結果/P011/full_R1 \
        --out /mnt/project-files/reports/K-67/確認カード_未読10か所と候補10枠.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import random
from pathlib import Path
from typing import Any

SEED = 67
VIEW_WIDTH = 1100


def _crop(pdf: Path, page_no: int, box: list[float], scale_width: int = 2000,
          pad: int = 140) -> tuple[str, list[float]]:
    """未読の場所の周りを切り出した画像と、**切り出しの中での四角の位置(%)**。

    位置の % を呼ぶ側で計算すると、拡大率をかけ忘れて印が本文とずれる
    (K-65 で実際にずれていた)。**画像と印を同じ場所で作る。**
    """
    import pymupdf
    from PIL import Image

    with pymupdf.open(pdf) as doc:
        page = doc.load_page(page_no - 1)
        zoom = scale_width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # **位置は PDF の点(ページは 1190×842 点)なので、拡大率をかけてから切る。**
    # K-65 で見つけた間違い: かけずに切ると、いつもページの左上あたりを切ってしまう。
    a, b, c, d = (v * zoom for v in box)
    # **左右・上下がひっくり返った四角が混ざっている**ので、ここで正しい向きに直す。
    x0, x1 = min(a, c), max(a, c)
    y0, y1 = min(b, d), max(b, d)
    # **紙の外へ出ている四角もある**(ページを回したものや、紙より大きい図形)。
    # 画像の中へ収め、幅・高さが 0 にならないようにする。
    x0 = min(max(x0, 0.0), image.width - 1.0)
    x1 = min(max(x1, x0 + 1.0), float(image.width))
    y0 = min(max(y0, 0.0), image.height - 1.0)
    y1 = min(max(y1, y0 + 1.0), float(image.height))
    left, top = max(int(x0) - pad, 0), max(int(y0) - pad, 0)
    right = min(int(x1) + pad, image.width)
    bottom = min(int(y1) + pad, image.height)
    crop = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    marker = [round((int(x0) - left) / crop.width * 100, 2),
              round((int(y0) - top) / crop.height * 100, 2),
              round((x1 - x0) / crop.width * 100, 2),
              round((y1 - y0) / crop.height * 100, 2)]
    if crop.width > VIEW_WIDTH:
        crop = crop.resize((VIEW_WIDTH, int(crop.height * VIEW_WIDTH / crop.width)))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=70)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return url, marker


def _page_image(pdf: Path, page_no: int) -> str:
    import pymupdf
    from PIL import Image

    with pymupdf.open(pdf) as doc:
        page = doc.load_page(page_no - 1)
        zoom = VIEW_WIDTH / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=60)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>確認カード 未読と候補ページ</title>
<style>
 :root { --bg:#faf9f7; --fg:#1d1b19; --line:#dcd8d2; --card:#fff; --accent:#2d6a4f; --muted:#6b645c; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
   --bg:#17161a; --fg:#eceaf0; --line:#36343c; --card:#201f25; --accent:#7fc0a0; --muted:#9a94a2; } }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--fg); padding:16px;
        font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif; line-height:1.7; }
 main { max-width:860px; margin:0 auto; }
 h1 { font-size:1.1rem; margin:6px 0; }
 p.lead { color:var(--muted); font-size:.9rem; margin:0 0 18px; }
 .bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-bottom:18px; }
 .bar i { display:block; height:100%; background:var(--accent); width:0; transition:width .2s; }
 .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; }
 .num { color:var(--muted); font-size:.8rem; }
 .q { font-size:1.05rem; font-weight:600; margin:10px 0 4px; }
 .meta { color:var(--muted); font-size:.85rem; margin-bottom:12px; }
 figure { margin:0 0 16px; border:1px solid var(--line); border-radius:8px; overflow:hidden; position:relative; }
 figure img { display:block; width:100%; }
 figure u { position:absolute; border:2px solid #e8590c; border-radius:2px; display:block; }
 .choices { display:grid; gap:8px; }
 button { font:inherit; padding:12px 15px; border-radius:8px; border:1px solid var(--line);
          background:transparent; color:var(--fg); cursor:pointer; text-align:left; }
 button:hover { border-color:var(--accent); }
 button b { color:var(--accent); margin-right:8px; }
 textarea { width:100%; height:170px; font-family:ui-monospace,monospace; font-size:.75rem; margin-top:12px;
            background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:10px; }
 table { border-collapse:collapse; width:100%; font-size:.85rem; margin-top:12px; }
 th,td { border-bottom:1px solid var(--line); padding:6px; text-align:left; }
 th { color:var(--muted); }
 .big { font-size:1.4rem; font-weight:700; }
</style></head><body><main>
<h1>確認カード — 未読 10 か所と、候補ページ 10 枠</h1>
<p class="lead">機械が「読めていない」と言っている場所と、「この枠に関係しそう」と言っているページを
確かめてください。20 問です。考えた時間も記録します。</p>
<div class="bar"><i id="bar"></i></div>
<div id="app"></div>
</main>
<script>
const CARDS = /*DATA*/null;
let i = 0, answers = [], started = performance.now(), total = performance.now();
const app = document.getElementById("app"), bar = document.getElementById("bar");

function esc(s) { return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function show() {
  bar.style.width = (i / CARDS.length * 100) + "%";
  if (i >= CARDS.length) return done();
  const c = CARDS[i];
  const mark = c.marker ? `<u style="left:${c.marker[0]}%;top:${c.marker[1]}%;width:${c.marker[2]}%;height:${c.marker[3]}%"></u>` : "";
  app.innerHTML = `<div class="card">
    <div class="num">${i + 1} / ${CARDS.length}　${esc(c.種類)}</div>
    <div class="q">${esc(c.問い)}</div>
    <div class="meta">${esc(c.補足)}</div>
    <figure><img src="${c.画像}" alt="">${mark}</figure>
    <div class="choices">${c.選択肢.map((t, n) =>
      `<button data-n="${n}"><b>${esc(t[0])}</b>${esc(t[1])}</button>`).join("")}</div>
  </div>`;
  started = performance.now();
  app.querySelectorAll("button").forEach(b => b.onclick = () => {
    answers.push({番号: c.番号, 種類: c.種類, 答え: c.選択肢[+b.dataset.n][0],
                  秒: +((performance.now() - started) / 1000).toFixed(2), 中身: c.記録});
    i++; show();
  });
}

function done() {
  bar.style.width = "100%";
  const seconds = (performance.now() - total) / 1000;
  const unread = answers.filter(a => a.種類.startsWith("未読"));
  const frames = answers.filter(a => a.種類.startsWith("候補"));
  const drawn = unread.filter(a => a.答え === "何か描かれている").length;
  const related = frames.filter(a => a.答え === "関係している").length;
  app.innerHTML = `<div class="card">
    <div class="num">おわりました</div>
    <p class="big">未読 ${drawn}/${unread.length} が本当に未読　候補ページ ${related}/${frames.length} が関係している</p>
    <p class="lead">かかった時間 ${seconds.toFixed(0)} 秒(1 問あたり ${(seconds / answers.length).toFixed(1)} 秒)。
    <b>未読で「何も無い」が多ければ、読了率は低く出すぎています</b>(落ちの数え方が厳しすぎる)。</p>
    <textarea readonly>${esc(JSON.stringify({
      未読: {分母: unread.length, 本当に未読: drawn},
      候補ページ: {分母: frames.length, 関係している: related},
      合計の秒: +seconds.toFixed(1), 答え: answers}, null, 1))}</textarea>
    <table><thead><tr><th>#</th><th>種類</th><th>答え</th><th>秒</th></tr></thead><tbody>
    ${answers.map(a => `<tr><td>${a.番号}</td><td>${esc(a.種類)}</td><td>${esc(a.答え)}</td><td>${a.秒}</td></tr>`).join("")}
    </tbody></table>
  </div>`;
}
show();
</script></body></html>
"""

UNREAD_CHOICES = [["何か描かれている", "機械の言うとおり、ここには何かある(=本当に未読)"],
                  ["何も無い", "ここには何も描かれていない(=落ちの数え方が厳しすぎる)"],
                  ["分からない", "切り出した絵では判断できない"]]
FRAME_CHOICES = [["関係している", "この枠の工事に関係するページだ"],
                 ["関係していない", "この枠とは関係ない"],
                 ["分からない", "判断できない"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-67 確認カード")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--unread", type=int, default=10)
    parser.add_argument("--frames", type=int, default=10)
    args = parser.parse_args(argv)

    from draft.readthrough import readthrough
    from draft.work_checklist import build as checklist_build

    draft = json.loads((args.run / "下書き.json").read_text(encoding="utf-8"))
    reading = {int(k): v for k, v in (draft["読む"].get("読み") or {}).items()}
    pages = sorted(reading)
    rt = readthrough(args.pdf, reading, pages)
    checklist = checklist_build(draft["理解"]["項目"], pdf=args.pdf, pages=pages)

    rng = random.Random(SEED)
    # 未読は大きいものから偏らないよう、種を固定してばらして選ぶ
    unread = list(rt["未読マップ"])
    rng.shuffle(unread)
    picked = unread[: args.unread]

    cards: list[dict[str, Any]] = []
    for index, row in enumerate(picked, 1):
        pad = 140
        img, marker = _crop(args.pdf, row["ページ"], row["位置"], pad=pad)
        cards.append({
            "番号": index, "種類": "未読の確認",
            "問い": f"{row['ページ']} ページのこの四角の中に、何か描かれていますか",
            "補足": f"機械は読めていないと言っている。種類 {row['種類']}・大きさ {row['大きさ']} 画素"
                    + (f"・文字「{row['文字']}」" if row["文字"] else ""),
            "画像": img,
            "marker": marker,
            "選択肢": UNREAD_CHOICES,
            "記録": {"ページ": row["ページ"], "図形の番号": row["図形の番号"], "種類": row["種類"]},
        })

    frames = [f for f in checklist["枠"] if f["分からないこと"]["候補ページ"]][: args.frames]
    for index, frame in enumerate(frames, len(cards) + 1):
        top = frame["分からないこと"]["候補ページ"][0]
        words = "、".join(dict.fromkeys(k["語"] for k in top["きっかけ"]))
        cards.append({
            "番号": index, "種類": "候補ページの確認",
            "問い": f"「{frame['枠']}」の工事に、{top['ページ']} ページは関係していますか",
            "補足": f"きっかけになった語: {words}(当たった語 {top['当たった語の数']} 個)。"
                    f"この枠の状態は「{frame['状態']}」",
            "画像": _page_image(args.pdf, top["ページ"]),
            "marker": None,
            "選択肢": FRAME_CHOICES,
            "記録": {"枠": frame["枠"], "ページ": top["ページ"], "語": words},
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(TEMPLATE.replace("/*DATA*/null", json.dumps(cards, ensure_ascii=False)), encoding="utf-8")
    print(f"書いた: {args.out}(未読 {len(picked)} 問・候補ページ {len(frames)} 問)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
