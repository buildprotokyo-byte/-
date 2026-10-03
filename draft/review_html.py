"""確認画面(HTML 1 枚)を作る(K-61)。左に図面、右に一覧。どちらを押しても相互に光る。

- 表示の切り替え: 認識(読んだ要素)/ 理解(項目)/ 組み立て(内訳)/ 仕上表 / 質問 / 材料 / 時間
- 根拠の種類で色分け(図面から読んだ・凡例から・公開基準から推論・仮に置いた・人の回答)
- 状態(観測・推論・仮説・問い)と確度(高・中・低)
- ページごとの確認の負荷(落ちた率・問いの数・確度「低」の数)。落ちの多いページには「読み落としの可能性が高い」
- 画像は埋め込む(1 ファイルで開ける)。**実図面の画像が入るので、リポジトリにもArtifactにも置かない。**
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Mapping

VIEW_WIDTH = 1400


def _image_data(path: Path) -> tuple[str, int, int]:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = VIEW_WIDTH / im.width
        im = im.resize((VIEW_WIDTH, int(im.height * scale)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), im.width, im.height


def build(result: Mapping[str, Any], page_images: Mapping[int, Path], out: Path) -> Path:
    pages = []
    for n, path in sorted(page_images.items()):
        src, w, h = _image_data(path)
        pages.append({"n": n, "src": src, "w": w, "h": h})
    data = json.dumps({"pages": pages, "result": result}, ensure_ascii=False)
    out.write_text(TEMPLATE.replace("/*DATA*/null", data), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>積算の下書き 確認画面</title>
<style>
:root{--bg:#f6f6f4;--panel:#fff;--ink:#1f2328;--muted:#646a73;--line:#d9dbe0;--hi:#ffd400;
--c-read:#2563eb;--c-legend:#16a34a;--c-infer:#ea580c;--c-put:#9333ea;--c-human:#0d9488;--c-rec:#64748b;--c-miss:#dc2626}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#16181c;--panel:#1f2228;--ink:#e8eaed;--muted:#9aa1ab;--line:#353a42}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,"Hiragino Sans","Noto Sans JP",sans-serif;background:var(--bg);color:var(--ink)}
header{padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:17px;margin:0 0 4px}.warn{color:var(--c-miss);font-weight:600}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}.tabs button{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:4px 10px;border-radius:6px;cursor:pointer}
.tabs button.on{background:var(--ink);color:var(--panel)}
main{display:grid;grid-template-columns:minmax(0,3fr) minmax(320px,2fr);gap:0;height:calc(100vh - 120px)}
@media (max-width:900px){main{grid-template-columns:1fr;height:auto}}
#left{overflow:auto;border-right:1px solid var(--line);display:flex;flex-direction:column}
#pagebar{display:flex;gap:4px;flex-wrap:wrap;padding:6px 8px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:2}
#pagebar button{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:4px;padding:2px 6px;cursor:pointer;font-size:12px}
#pagebar button.on{outline:2px solid var(--ink)}#pagebar .load1{background:#fff4c2;color:#000}#pagebar .load2{background:#ffd0c2;color:#000}#pagebar .load3{background:#ff9f8a;color:#000}
#pageinfo{padding:4px 10px;font-size:13px;color:var(--muted)}
#stage{position:relative;margin:0 8px 16px}#stage img{width:100%;display:block}#stage svg{position:absolute;inset:0;width:100%;height:100%}
#stage rect{fill:transparent;stroke-width:2;cursor:pointer;vector-effect:non-scaling-stroke}#stage rect.hl{stroke:var(--hi)!important;stroke-width:4;fill:rgba(255,212,0,.25)}
#right{overflow:auto;padding:8px 12px}
.row{border:1px solid var(--line);border-left:5px solid var(--c-rec);background:var(--panel);border-radius:6px;padding:6px 8px;margin:6px 0;cursor:pointer}
.row.hl{outline:3px solid var(--hi)}.row .sub{color:var(--muted);font-size:12px}.row details{margin-top:4px}
.b{display:inline-block;font-size:11px;border-radius:4px;padding:0 5px;margin-right:4px;border:1px solid var(--line)}
.legend span{display:inline-block;margin-right:10px;font-size:12px}.legend i{display:inline-block;width:10px;height:10px;margin-right:3px;border-radius:2px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid var(--line);padding:3px 5px;text-align:left;vertical-align:top}
.unk{color:var(--c-miss)}.diff{background:rgba(255,212,0,.35)}
</style></head><body>
<header><h1>積算の下書き 確認画面</h1><div id="summary"></div>
<div class="legend" id="legend"></div><div class="tabs" id="tabs"></div></header>
<main><section id="left"><div id="pagebar"></div><div id="pageinfo"></div><div id="stage"><img id="img" alt="図面"><svg id="svg"></svg></div></section>
<section id="right"><div id="list"></div></section></main>
<script>
const DATA=/*DATA*/null;const R=DATA.result;
const COLORS={"図面から読んだ":"var(--c-read)","凡例から":"var(--c-legend)","公開基準から推論":"var(--c-infer)","仮に置いた":"var(--c-put)","人の回答":"var(--c-human)"};
const VIEWS=[["認識","認識(読んだ要素)"],["理解","理解(項目)"],["組み立て","組み立て(内訳)"],["仕上表","仕上表"],["質問","質問"],["材料","材料表"],["時間","時間"]];
let view="理解",page=null,selected=null;
const pages=Object.fromEntries(DATA.pages.map(p=>[p.n,p]));
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const unk=v=>(v===null||v===undefined||v==="")?'<span class="unk">未取得</span>':esc(v);
function pageMeta(n){return (R["読む"]&&R["読む"]["ページ"]||{})[n]||{}}
function kindOf(n){return ((R["整理"]||{})["ページ"]||{})[n]||{}}
function loadOf(n){const m=pageMeta(n),items=(R["理解"]||{})["項目"]||[];
 const q=items.filter(i=>i["ページ"]==n&&i["状態"]=="問い").length,low=items.filter(i=>i["ページ"]==n&&i["確度"]=="低").length;
 const miss=m["落ちた率"];let s=(miss??0.5)*10+q+low*0.5;return {miss,q,low,score:s}}
function summary(){const s=R["まとめ"]||{};let h=`<div>${esc(s["案件"]||"")} ・ 段階 <b>${esc(s["段階"]||"")}</b> ・ 自動確定 <b>${esc(s["自動確定"])}</b> 件 ・ 原本の仕上表: <b>${esc((R["仕上表"]||{})["原本"]||"未取得")}</b>${((R["仕上表"]||{})["本来の仕上表ではないページ"]||[]).map(x=>`(${esc(x["ページ"])}ページは本来の仕上表ではない: ${esc(x["図面"])})`).join("")} ・ 原価表: <b>${esc(s["原価表"]||"未取得")}</b></div>`;
 const w=s["精度の注意"]||[];if(w.length)h+=`<div class="warn">精度が上がっていません: ${w.map(esc).join(" / ")}</div>`;
 const st=R["止まった所"]||[];if(st.length)h+=`<div class="warn">欠けたまま進んだ所: ${st.map(x=>esc(x["段"]+": "+x["止まった所"])).join(" / ")}</div>`;
 document.getElementById("summary").innerHTML=h;
 document.getElementById("legend").innerHTML=Object.entries(COLORS).map(([k,c])=>`<span><i style="background:${c}"></i>${k}</span>`).join("")+'<span><i style="background:var(--c-rec)"></i>読んだ要素</span><span><i style="background:var(--c-miss)"></i>読めなかった所</span>';
 document.getElementById("tabs").innerHTML=VIEWS.map(([k,l])=>`<button data-v="${k}" class="${k==view?"on":""}">${l}</button>`).join("");
 document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{view=b.dataset.v;selected=null;matSel=null;render()})}
function pagebar(){document.getElementById("pagebar").innerHTML=DATA.pages.map(p=>{const l=loadOf(p.n);const c=l.score>=6?"load3":l.score>=3?"load2":l.score>=1.5?"load1":"";
 return `<button class="${c} ${p.n==page?"on":""}" data-p="${p.n}" title="落ちた率 ${l.miss==null?"未取得":(l.miss*100).toFixed(1)+"%"} / 問い ${l.q} / 確度低 ${l.low}">${p.n}</button>`}).join("");
 document.querySelectorAll("#pagebar button").forEach(b=>b.onclick=()=>{page=+b.dataset.p;render()})}
function boxesFor(){const out=[];const rd=(R["読む"]||{})["読み"]||{};
 if(view=="認識"){for(const e of ((rd[page]||{})["要素"]||[]))if(e["位置"])out.push({id:e.id,b:e["位置"],c:"var(--c-rec)"});
  for(const [i,u] of ((rd[page]||{})["分からなかったもの"]||[]).entries())if(u["位置"])out.push({id:"u"+i,b:u["位置"],c:"var(--c-miss)"})}
 else if(view=="理解"||view=="組み立て"||view=="材料"||view=="時間"){for(const it of ((R["理解"]||{})["項目"]||[]))if(it["ページ"]==page)for(const b of it["位置"])out.push({id:it.id,b,c:COLORS[it["人の回答"]?"人の回答":it["根拠の種類"]]||"var(--c-put)"})}
 else if(view=="仕上表"){for(const [i,c] of (((R["仕上表"]||{})["照らし合わせ"])||[]).entries()){for(const p of c["原本の位置"]||[])if(p["ページ"]==page&&p["位置"])out.push({id:"f"+i,b:p["位置"],c:"var(--c-read)"});
  for(const r of c["ひな型の根拠"]||[]){const it=itemById(r["項目"]);if(it&&it["ページ"]==page)for(const b of it["位置"])out.push({id:"f"+i,b,c:COLORS[it["根拠の種類"]]})}}}
 else if(view=="質問"){for(const q of allQuestions())for(const p of q["位置"]||[])if(p["ページ"]==page&&p["位置"])out.push({id:q["鍵"],b:p["位置"],c:"var(--c-miss)"})}
 return out}
let matSel=null;let _items=null;function itemById(id){if(!_items){_items={};for(const it of ((R["理解"]||{})["項目"]||[]))_items[it.id]=it}return _items[id]}
function allQuestions(){const s=(R["質問"]||{})["段階ごと"]||{};return s[(R["まとめ"]||{})["段階"]||"通常"]||[]}
function stage(){const p=pages[page];const img=document.getElementById("img");img.src=p.src;const svg=document.getElementById("svg");
 const sc=p.w/2000;svg.setAttribute("viewBox",`0 0 ${p.w} ${p.h}`);
 svg.innerHTML=boxesFor().map(x=>`<rect data-id="${esc(x.id)}" x="${x.b[0]*sc}" y="${x.b[1]*sc}" width="${Math.max(2,(x.b[2]-x.b[0])*sc)}" height="${Math.max(2,(x.b[3]-x.b[1])*sc)}" style="stroke:${x.c}" class="${linked(x.id)?"hl":""}"></rect>`).join("");
 svg.querySelectorAll("rect").forEach(r=>r.onclick=e=>{e.stopPropagation();selected=r.dataset.id;render(true)});
 const m=pageMeta(page),k=kindOf(page),l=loadOf(page);
 document.getElementById("pageinfo").innerHTML=`${page}ページ: ${esc(k["種類"]||"未取得")} ${esc(k["描かれているもの"]||"")} ・ 落ちた率 ${l.miss==null?'<span class="unk">未取得</span>':(l.miss*100).toFixed(1)+"%"}${m["読み直した"]?"(読み直した)":""} ・ 問い ${l.q} ・ 確度低 ${l.low}`+(m["読み落としの可能性が高い"]?' <span class="warn">読み落としの可能性が高い</span>':"")}
function linked(id){if(!selected)return false;if(id==selected)return true;
 const row=currentRows().find(r=>r.key==selected);return row?row.ids.includes(id):false}
function badge(t){return `<span class="b">${esc(t)}</span>`}
function currentRows(){const rows=[];const rd=(R["読む"]||{})["読み"]||{};
 if(view=="認識"){for(const e of ((rd[page]||{})["要素"]||[]))rows.push({key:e.id,ids:[e.id],page,html:`${badge(e["種類"])}${esc(e["内容"])}<div class="sub">${esc(e.id)} ・ 確かさ ${esc(e["確かさ"])}</div>`,c:"var(--c-rec)"});
  for(const [i,u] of ((rd[page]||{})["分からなかったもの"]||[]).entries())rows.push({key:"u"+i,ids:["u"+i],page,html:`<span class="warn">読めなかった所</span> ${esc(u["理由"])}`,c:"var(--c-miss)"})}
 else if(view=="理解"){for(const it of ((R["理解"]||{})["項目"]||[])){const c=COLORS[it["人の回答"]?"人の回答":it["根拠の種類"]];
  rows.push({key:it.id,ids:[it.id],page:it["ページ"],c,html:`${badge(it["状態"])}${badge("確度 "+it["確度"])}<b>${esc(it["工事"]||it["何"])}</b> ・ ${esc(it["場所"])} ・ ${unk(it["数量"])} ${esc(it["単位"])}
  <div class="sub">${it["ページ"]}ページ ・ ${esc(it["根拠の種類"])} ・ ${esc(it["科目"])}${it["検算"].length?' ・ <span class="warn">検算: '+it["検算"].map(esc).join(" / ")+"</span>":""}</div>
  <details><summary>中身</summary><div>読み取った値(書かれたまま): ${esc(it["読み取った値"])}</div><div>何: ${esc(it["何"])}</div><div>部位: ${esc(it["部位"])} ・ 区分: ${esc(it["区分"])} ・ 品番: ${esc(it["品番"])}</div><div>式: ${esc(it["式"])}</div><div>理由: ${esc(it["理由"])}</div><div>根拠の要素: ${it["要素"].map(esc).join(", ")}</div>${it["選択肢"].length?"<div>選択肢: "+it["選択肢"].map(esc).join(" / ")+"</div>":""}${it["人の回答"]?"<div>人の回答: "+esc(it["人の回答"])+"</div>":""}</details>`})}}
 else if(view=="組み立て"){const bd=(R["組み立て"]||{})["内訳の行"]||[];for(const [i,r] of bd.entries()){const it=itemById(r["項目"][0])||{};
  rows.push({key:"a"+i,ids:r["項目"],page:it["ページ"],c:"var(--c-rec)",html:`${badge(r["科目"]||"科目未定")}${r["区分"]?badge(r["区分"]):""}<b>${esc(r["工事項目"])}</b> ${esc(r["摘要"])} ・ ${esc(r["場所"]||"場所未確定")} ・ ${unk(r["数量"])} ${esc(r["単位"])}${r["メモ"]?'<div class="sub">'+esc(r["メモ"])+"</div>":""}`})}}
 else if(view=="仕上表"){const f=R["仕上表"]||{};for(const [i,c] of (f["照らし合わせ"]||[]).entries()){const d=["違う","近い","原本のみ","ひな型のみ"].includes(c["照らし合わせ"]);
  const ids=(c["ひな型の根拠"]||[]).map(r=>r["項目"]);ids.push("f"+i);
  const pg=((c["原本の位置"]||[])[0]||{})["ページ"]||((c["ひな型の根拠"]||[])[0]||{})["ページ"];
  rows.push({key:"f"+i,ids,page:pg,c:d?"var(--c-miss)":"var(--c-rec)",html:`<b>${esc(c["室"])} ・ ${esc(c["部位"])}</b> ${badge(c["照らし合わせ"])}<table><tr><th>原本(画像を正とする)</th><th>ひな型(図面の読み)</th></tr><tr><td class="${d?"diff":""}">${unk(c["原本"])}</td><td class="${d?"diff":""}">${unk(c["ひな型"])}<div class="sub">確度 ${esc(c["ひな型の確度"])} ・ 根拠 ${(c["ひな型の根拠"]||[]).map(r=>esc(r["資料の種類"]+" "+r["ページ"]+"ページ")).join(", ")}</div></td></tr></table>${c["人の回答"]?"人の回答: "+esc(c["人の回答"]):""}`})}}
 else if(view=="質問"){const q=R["質問"]||{};for(const x of allQuestions())rows.push({key:x["鍵"],ids:[x["鍵"],...(x["関係する項目"]||[])],page:(x["見る所"]||[])[0],c:"var(--c-miss)",html:`<b>${esc(x["番号"])}</b> ${badge(x["種類"])}${badge(x["科目"])}${esc(x["問い"])}<ol>${x["選択肢"].map(o=>"<li>"+esc(o)+"</li>").join("")}</ol><div class="sub">見る所: ${(x["見る所"]||[]).join(", ")}ページ ・ 回答の鍵: ${esc(x["鍵"])}</div>`})}
 else if(view=="材料"){const mats=((R["組み立て"]||{})["材料表"])||[];const sel=matSel==null?null:+matSel.slice(1);
  for(const [i,m] of mats.entries()){if(sel!=null&&i!=sel)continue;rows.push({key:"m"+i,ids:m["項目"],page:(itemById(m["項目"][0])||{})["ページ"],c:"var(--c-rec)",html:`${badge(m["科目"]||"科目未定")}<b>${esc(m["品番"])}</b> ${esc(m["名称"])} ・ 合計(分かった分) ${unk(m["数量の合計(分かった分)"])} ${esc(m["単位"])}${m["未取得の件数"]?' ・ <span class="unk">未取得 '+m["未取得の件数"]+" 件(合計に入れていない)</span>":""}<div class="sub">場所: ${m["場所"].map(esc).join(", ")}${sel==null?"(押すと関わる行だけを抜き出す)":"(もう一度押すと全部に戻る)"}</div>`})}
  if(sel!=null&&mats[sel]){const ids=new Set(mats[sel]["項目"]);
   for(const [i,r] of (((R["組み立て"]||{})["内訳の行"])||[]).entries())if(r["項目"].some(x=>ids.has(x))){const it=itemById(r["項目"][0])||{};rows.push({key:"a"+i,ids:r["項目"],page:it["ページ"],c:"var(--c-put)",html:`${badge("内訳")}<b>${esc(r["工事項目"])}</b> ${esc(r["摘要"])} ・ ${esc(r["場所"]||"場所未確定")} ・ ${unk(r["数量"])} ${esc(r["単位"])}`})}
   for(const id of ids){const it=itemById(id);if(it)rows.push({key:it.id,ids:[it.id],page:it["ページ"],c:COLORS[it["根拠の種類"]]||"var(--c-put)",html:`${badge("項目")}${badge("確度 "+it["確度"])}${esc(it["工事"]||it["何"])} ・ ${esc(it["場所"])} ・ ${unk(it["数量"])} ${esc(it["単位"])}<div class="sub">${it["ページ"]}ページ ・ ${esc(it["根拠の種類"])}</div>`})}}}
 else if(view=="時間"){for(const [i,t] of (((R["組み立て"]||{})["時間"])||[]).entries()){const r=((R["組み立て"]||{})["内訳の行"]||[])[i]||{};rows.push({key:"t"+i,ids:r["項目"]||[],page:(itemById((r["項目"]||[])[0])||{})["ページ"],c:"var(--c-rec)",html:`<b>${esc(t["工事項目"])}</b> ${esc(t["場所"])} ・ 数量 ${unk(t["数量"])} ${esc(t["単位"])}<div class="sub">1人1日あたり ${esc(t["1人1日あたり"])} ・ 人日 ${esc(t["人日"])} ・ 時間 ${esc(t["時間"])} ・ 作業費 ${esc(t["作業費"])}</div>`})}}
 return rows}
function list(){const rows=currentRows();const el=document.getElementById("list");
 let head="";if(view=="質問"){const q=R["質問"]||{};head=`<div class="sub">並べ方: ${esc(q["並べ方"])} ・ 推奨は付けていない ・ 答えは「回答の鍵: 選んだ選択肢の文字」の JSON で戻す</div>`}
 if(view=="材料")head='<div class="sub">材料発注表の並び(科目 → 品番)。数量が未取得のものは合計に入れない。同じ並びの CSV は 材料発注表.csv。</div>';
 if(view=="時間")head='<div class="sub">歩掛(職人1人1日あたりの進み方)は公開基準に無いので空。入力があれば人日・時間・作業費を計算する。</div>';
 el.innerHTML=head+(rows.length?"":'<div class="sub">このページ・この表示には行がありません(未取得の段は上の注意を見てください)</div>')+rows.map(r=>`<div class="row ${linked(r.key)||r.ids.includes(selected)?"hl":""}" data-k="${esc(r.key)}" data-p="${r.page??""}" style="border-left-color:${r.c}">${r.html}</div>`).join("");
 el.querySelectorAll(".row").forEach(d=>d.onclick=e=>{if(e.target.closest("details"))return;if(view=="材料"&&/^m\d+$/.test(d.dataset.k))matSel=matSel==d.dataset.k?null:d.dataset.k;selected=d.dataset.k;if(d.dataset.p)page=+d.dataset.p;render()});
 const h=el.querySelector(".row.hl");if(h)h.scrollIntoView({block:"nearest"})}
function render(){summary();pagebar();stage();list()}
page=(R["整理"]||{})["読む順"]?.[0]||DATA.pages[0].n;render();
</script></body></html>
"""
