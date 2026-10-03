"""K-65 の 7: 本物の人で測るための質問 10 問(1 枚の HTML)。

使い方。

    PYTHONPATH=. python benchmarks/make_k65_question_html.py \
      --pdf /mnt/project-files/anonymized/P011_匿名化v4.pdf \
      --run /mnt/project-files/reports/K-61/結果/P011/full_R1 \
      --out /mnt/project-files/reports/K-65/質問10問.html

携帯で答えられる大きさ。タップで答え、最後に JSON が出る。
記録: 1 問の所要時間 / 迷った質問 / 図面全体を開いた回数 / カードだけで答えられなかった質問。

**画像が入るので共有フォルダにだけ置く**(リポジトリには入れない)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.make_k67_cards import _crop, _page_image

TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>質問 10 問</title>
<style>
 :root { --bg:#faf9f7; --fg:#1d1b19; --line:#dcd8d2; --card:#fff; --accent:#2d6a4f; --muted:#6b645c; }
 * { box-sizing:border-box; }
 body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
        font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; line-height:1.7; }
 h1 { font-size:19px; margin:0 0 4px; }
 .note { color:var(--muted); font-size:13px; margin:0 0 20px; }
 .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
         padding:14px; margin-bottom:18px; }
 .no { font-size:12px; color:var(--muted); }
 .q { font-size:17px; font-weight:700; margin:4px 0 10px; }
 .shot { position:relative; border:1px solid var(--line); border-radius:8px; overflow:hidden;
         margin-bottom:10px; }
 .shot img { width:100%; display:block; }
 .shot u { position:absolute; border:3px solid #e07a17; border-radius:3px; }
 .meter { background:#f4f2ee; border-radius:8px; padding:9px 11px; font-size:13px;
          color:var(--muted); margin-bottom:10px; }
 .meter b { color:var(--fg); }
 button.opt { display:block; width:100%; text-align:left; padding:13px 14px; margin-bottom:8px;
              font-size:16px; border:1px solid var(--line); border-radius:9px; background:#fff;
              color:var(--fg); }
 button.opt.on { border-color:var(--accent); background:#eaf3ee; font-weight:700; }
 .row { display:flex; gap:8px; flex-wrap:wrap; margin-top:4px; }
 .row button { flex:1 1 46%; padding:10px; font-size:13px; border:1px solid var(--line);
               border-radius:9px; background:#fff; color:var(--muted); }
 .row button.on { border-color:#b4652a; color:#b4652a; font-weight:700; }
 .full { display:none; margin-top:10px; }
 .full img { width:100%; border:1px solid var(--line); border-radius:8px; }
 #out { width:100%; height:200px; font-family:ui-monospace,monospace; font-size:11px;
        border:1px solid var(--line); border-radius:8px; padding:8px; }
 #done { padding:14px; font-size:16px; width:100%; border:0; border-radius:9px;
         background:var(--accent); color:#fff; font-weight:700; }
</style></head><body>
<h1>質問 10 問</h1>
<p class="note">カードだけで答えてください。図面全体を開かずに答えられるかを測っています。
分からなければ「不明」や「どれでもない」を選んでかまいません。
最後に出る文字を、そのままおーちゃんに渡してください。</p>
<div id="cards"></div>
<button id="done">答えを書き出す</button>
<textarea id="out" readonly placeholder="ここに結果が出ます"></textarea>
<script>
const CARDS = __CARDS__;
const state = CARDS.map(() => ({選択肢:null, 迷った:false, カードだけでは無理:false, 開いた:0, 秒:0}));
const started = CARDS.map(() => null);
const root = document.getElementById("cards");
CARDS.forEach((c, i) => {
  const el = document.createElement("div");
  el.className = "card";
  const mark = c.marker ? `<u style="left:${c.marker[0]}%;top:${c.marker[1]}%;width:${c.marker[2]}%;height:${c.marker[3]}%"></u>` : "";
  el.innerHTML = `<div class="no">${i + 1} / ${CARDS.length}・${c.型}${c.印 ? "・" + c.印 : ""}</div>
    <div class="q">${c.問い}</div>
    <div class="shot">${mark}<img src="${c.画像}" alt=""></div>
    <div class="meter">${c.メーター}</div>
    <div class="opts"></div>
    <div class="row">
      <button class="maybe">迷った</button>
      <button class="hard">カードだけでは答えられない</button>
      <button class="open">図面全体を見る</button>
    </div>
    <div class="full"><img src="${c.全体}" alt=""></div>`;
  const opts = el.querySelector(".opts");
  c.選択肢.forEach(o => {
    const b = document.createElement("button");
    b.className = "opt"; b.textContent = o;
    b.onclick = () => {
      if (started[i] === null) started[i] = Date.now();
      state[i].選択肢 = o;
      state[i].秒 = Math.round((Date.now() - (started[i] || Date.now())) / 1000);
      opts.querySelectorAll("button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
    };
    opts.appendChild(b);
  });
  el.querySelector(".maybe").onclick = e => {
    state[i].迷った = !state[i].迷った; e.target.classList.toggle("on");
  };
  el.querySelector(".hard").onclick = e => {
    state[i].カードだけでは無理 = !state[i].カードだけでは無理; e.target.classList.toggle("on");
  };
  el.querySelector(".open").onclick = () => {
    const f = el.querySelector(".full");
    f.style.display = f.style.display === "block" ? "none" : "block";
    if (f.style.display === "block") state[i].開いた += 1;
  };
  root.appendChild(el);
  if (started[i] === null) started[i] = Date.now();
});
document.getElementById("done").onclick = () => {
  const rows = CARDS.map((c, i) => ({鍵: c.鍵, 型: c.型, 選択肢: state[i].選択肢, 秒: state[i].秒,
    迷った: state[i].迷った, カードだけでは答えられない: state[i].カードだけでは無理,
    図面全体を開いた回数: state[i].開いた}));
  document.getElementById("out").value = JSON.stringify({案件: "__CASE__", 回答: rows}, null, 1);
};
</script></body></html>
"""


def _meter_text(card: dict[str, Any]) -> str:
    m = card.get("メーター") or {}
    items = m.get("決める項目数") or {}
    money = m.get("決める金額")
    money_text = ("金額 未取得(原価表なし)" if not isinstance(money, dict)
                  else f"金額の総額比 {money.get('総額比')}")
    return (f"この答えが決める内訳の行: <b>{items.get('合計', 0)} 行</b>"
            f"(直接 {items.get('直接', 0)}・連鎖 {items.get('連鎖', 0)})・{money_text}<br>"
            f"目安の回答時間 <b>{m.get('回答時間の見積(秒)', '—')} 秒</b>(未較正)<br>"
            "「決める」は欄が埋まることで、確定になることではありません")


def _spread(cards: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """型を順番に 1 つずつ取る(順位は型の中で保つ)。"""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        buckets.setdefault(c["型"], []).append(c)
    out: list[dict[str, Any]] = []
    while len(out) < count and any(buckets.values()):
        for key in list(buckets):
            if buckets[key] and len(out) < count:
                out.append(buckets[key].pop(0))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K-65 質問 10 問")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--case-id", default="P011 匿名化v4")
    a = p.parse_args(argv)

    from draft import questioning, stages, work_checklist

    draft = json.loads((a.run / "下書き.json").read_text(encoding="utf-8"))
    pages = sorted({int(n) for n in (draft["読む"].get("読み") or {})})
    checklist = work_checklist.build(draft["理解"]["項目"], pdf=a.pdf, pages=pages)
    raw = stages.question_candidates(draft["理解"], draft["仕上表"], draft["読む"], None)
    built = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], checklist=checklist)

    # **型がばらけるように選ぶ。**順位の上から取ると 10 問とも同じ型になった
    # (P011 では上位が全部「仕様書と図面のどちらを採るか」)。
    # 1 つの型ばかりでは、型ごとの回答時間も、答えやすさの差も測れない。
    picked = _spread(built["カード"], a.count)
    cards: list[dict[str, Any]] = []
    for c in picked:
        page = c.get("ページ")
        box = (c.get("位置") or [{}])[0].get("位置")
        if box and page:
            image, marker = _crop(a.pdf, int(page), list(box))
        elif page:
            image, marker = _page_image(a.pdf, int(page)), None
        else:
            continue
        cards.append({
            "鍵": c["鍵"], "型": c["型"], "問い": c["問い"], "選択肢": c["選択肢"],
            "画像": image, "marker": marker,
            "全体": _page_image(a.pdf, int(page)),
            "メーター": _meter_text(c),
            "印": "抜き取り" if c.get("抜き取り") else ("枠の問い" if c.get("枠の問い") else ""),
        })

    html = (TEMPLATE.replace("__CARDS__", json.dumps(cards, ensure_ascii=False))
            .replace("__CASE__", a.case_id))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding="utf-8")
    print(f"書いた: {a.out}({len(cards)} 問)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
