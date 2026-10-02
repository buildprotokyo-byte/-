"""K-66 3 節(c): **おーちゃんの目で確かめる 40 組**を、確認画面と同じ作りのカードにする。

選択肢は `同じ` / `違う` / `上位・下位`(+ `分からない`)。1 組ごとの所要時間を記録し、
最後に部品の判定との一致率を出す。**部品の判定は、答え終わるまで見せない。**

**実案件も正解ファイルも使わない。**40 組は `sameness/decoys.py` の囮と言い換えから
決まった種で選ぶ(同じ種なら毎回同じ 40 組)。だから結果を比べられる。

実行::

    python -m benchmarks.make_sameness_cards --out /mnt/project-files/reports/K-66/判定の確認_40組.html
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sameness import compare
from sameness.decoys import decoy_pairs, paraphrase_pairs

SEED = 66
COUNT = 40


def build_pairs(count: int = COUNT, seed: int = SEED) -> list[dict[str, object]]:
    """囮と言い換えを混ぜて `count` 組選ぶ。**半分ずつに近くなるように取る。**"""
    # **数量違いの囮は出さない。**カードの問いは「同じ工事か」なので、おーちゃんは
    # 名前を見て「同じ」と答えるのが正しく(数量は別に判定する)、食い違いとして数えると
    # 測り方の方が間違っていることになる。数量の判定は囮の測定で測る。
    decoys = [p for p in decoy_pairs() if not p.unit]
    paras = list(paraphrase_pairs())
    rng = random.Random(seed)
    rng.shuffle(decoys)
    rng.shuffle(paras)
    half = count // 2
    chosen = decoys[:half] + paras[: count - half]
    rng.shuffle(chosen)

    rows: list[dict[str, object]] = []
    for index, pair in enumerate(chosen, 1):
        verdict = compare(pair.left, pair.right, level=pair.level)
        value = verdict.value
        rows.append({
            "番号": index,
            "左": pair.left,
            "右": pair.right,
            "段": pair.level,
            "部品の判定": value,
            "部品の理由": verdict.reason,
            "部品は同じと見たか": bool(verdict.hit),
            "作った種類": pair.種類,
        })
    return rows


TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>判定の確認 40 組</title>
<style>
 :root { --bg:#faf9f7; --fg:#1d1b19; --line:#dcd8d2; --card:#fff; --accent:#2d6a4f; --muted:#6b645c; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
   --bg:#17161a; --fg:#eceaf0; --line:#36343c; --card:#201f25; --accent:#7fc0a0; --muted:#9a94a2; } }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--fg); font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;
        line-height:1.7; padding:16px; }
 main { max-width:720px; margin:0 auto; }
 h1 { font-size:1.15rem; margin:8px 0 4px; }
 p.lead { color:var(--muted); font-size:.9rem; margin:0 0 20px; }
 .bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-bottom:20px; }
 .bar i { display:block; height:100%; background:var(--accent); width:0; transition:width .2s; }
 .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; }
 .num { color:var(--muted); font-size:.8rem; letter-spacing:.04em; }
 .pair { display:grid; gap:10px; margin:14px 0 22px; }
 .side { border:1px solid var(--line); border-radius:8px; padding:12px 14px; font-size:1.05rem; font-weight:600; }
 .vs { text-align:center; color:var(--muted); font-size:.8rem; }
 .level { display:inline-block; background:var(--line); color:var(--fg); border-radius:999px;
          padding:2px 10px; font-size:.75rem; }
 .choices { display:grid; gap:8px; }
 button { font:inherit; padding:13px 16px; border-radius:8px; border:1px solid var(--line);
          background:transparent; color:var(--fg); cursor:pointer; text-align:left; }
 button:hover { border-color:var(--accent); }
 button b { color:var(--accent); margin-right:8px; }
 table { border-collapse:collapse; width:100%; font-size:.85rem; margin-top:14px; }
 th,td { border-bottom:1px solid var(--line); padding:7px 6px; text-align:left; vertical-align:top; }
 th { color:var(--muted); font-weight:600; }
 .ng { color:#b3261e; font-weight:700; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .ng { color:#ff8a80; } }
 textarea { width:100%; height:150px; font-family:ui-monospace,monospace; font-size:.75rem;
            background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:10px; }
 .big { font-size:1.6rem; font-weight:700; }
</style></head><body><main>
<h1>判定の確認 40 組</h1>
<p class="lead">2 つが同じ工事かどうかを選んでください。40 組です。考えた時間も記録します。
機械の判定は、全部答えたあとで出ます。</p>
<div class="bar"><i id="bar"></i></div>
<div id="app"></div>
</main>
<script>
const PAIRS = /*DATA*/null;
const CHOICES = [["同じ","同じ工事だと思う"],["違う","別の工事だと思う"],
                 ["上位・下位","片方がもう片方を含む(大きい・小さい)"],["分からない","判断できない"]];
let i = 0, answers = [], started = performance.now(), total = performance.now();
const app = document.getElementById("app"), bar = document.getElementById("bar");

function show() {
  bar.style.width = (i / PAIRS.length * 100) + "%";
  if (i >= PAIRS.length) return done();
  const p = PAIRS[i];
  app.innerHTML = `<div class="card">
    <div class="num">${i + 1} / ${PAIRS.length}　<span class="level">${p["段"]}</span></div>
    <div class="pair">
      <div class="side">${esc(p["左"])}</div>
      <div class="vs">と</div>
      <div class="side">${esc(p["右"])}</div>
    </div>
    <div class="choices">${CHOICES.map((c, n) =>
      `<button data-n="${n}"><b>${c[0]}</b>${c[1]}</button>`).join("")}</div>
  </div>`;
  started = performance.now();
  app.querySelectorAll("button").forEach(b => b.onclick = () => {
    answers.push({番号: PAIRS[i]["番号"], 答え: CHOICES[+b.dataset.n][0],
                  秒: +((performance.now() - started) / 1000).toFixed(2)});
    i++; show();
  });
}

function esc(s) { return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function done() {
  bar.style.width = "100%";
  const seconds = (performance.now() - total) / 1000;
  let agree = 0, counted = 0;
  const rows = PAIRS.map((p, n) => {
    const a = answers[n], mine = p["部品は同じと見たか"] ? "同じ" : null;
    const same = a.答え === "同じ";
    const match = (p["部品は同じと見たか"] === same);
    if (a.答え !== "分からない") { counted++; if (match) agree++; }
    return `<tr><td>${p["番号"]}</td><td>${esc(p["左"])}<br>${esc(p["右"])}</td>
      <td>${a.答え}</td><td>${esc(p["部品の判定"])}</td>
      <td class="${match ? "" : "ng"}">${match ? "一致" : "食い違い"}</td>
      <td>${a.秒}</td><td>${esc(p["部品の理由"])}</td></tr>`;
  }).join("");
  app.innerHTML = `<div class="card">
    <div class="num">おわりました</div>
    <p class="big">${counted ? Math.round(agree / counted * 100) : 0}% が機械の判定と一致</p>
    <p class="lead">かかった時間 ${seconds.toFixed(0)} 秒(1 組あたり ${(seconds / PAIRS.length).toFixed(1)} 秒)。
    「分からない」を選んだ ${PAIRS.length - counted} 組は分母に入れていません。</p>
    <p class="lead"><b>食い違った組が、直すべき所です。</b>下の枠の中身をそのままコピーして返してください。</p>
    <textarea readonly>${esc(JSON.stringify({
      一致率: counted ? agree / counted : null, 分母: counted, 一致: agree,
      合計の秒: +seconds.toFixed(1), 答え: answers,
      食い違い: PAIRS.map((p, n) => ({番号: p["番号"], おーちゃん: answers[n].答え,
        部品: p["部品の判定"], 理由: p["部品の理由"]}))
        .filter((r, n) => (PAIRS[n]["部品は同じと見たか"]) !== (answers[n].答え === "同じ"))
    }, null, 1))}</textarea>
    <table><thead><tr><th>#</th><th>対</th><th>おーちゃん</th><th>部品</th><th></th><th>秒</th><th>部品の理由</th></tr></thead>
    <tbody>${rows}</tbody></table>
  </div>`;
}
show();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-66 判定の確認カード 40 組")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=COUNT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    rows = build_pairs(args.count, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        TEMPLATE.replace("/*DATA*/null", json.dumps(rows, ensure_ascii=False)), encoding="utf-8"
    )
    same = sum(1 for r in rows if r["部品は同じと見たか"])
    print(f"書いた: {args.out}({len(rows)} 組。部品が「同じ」と見たのは {same} 組)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
