# 外部取得の可否(実測、2026-09-22)

## 結論

この作業環境(claude-code-remoteのクラウドセッション)から**直接アクセス(curl / WebFetch)できるのは
`e-stat.go.jp` と `mlit.go.jp`(国土交通省本体・官庁営繕とも)の2ドメインのみ**。それ以外は
組織のegressポリシーで403(`connect_rejected`、`gateway answered 403 to CONNECT`)になり、回避できない。

実際に403で拒否されたドメイン(実測分のみ): `laws.e-gov.go.jp`(e-Gov法令検索)、`www.e-gov.go.jp`、
`www.mhlw.go.jp`(厚労省)、`www.env.go.jp`(環境省)、`www.meti.go.jp`(経産省)、`www.soumu.go.jp`(総務省)、
`www.fdma.go.jp`(消防庁)、`www.metro.tokyo.lg.jp`・`www.toshiseibi.metro.tokyo.lg.jp`(東京都)、
`www.city.yokohama.lg.jp`、`www.ndl.go.jp`(国立国会図書館)、`ja.wikipedia.org`、
日本建築学会・建設物価調査会・経済調査会・公共建築協会・日本建設業連合会・建築士会連合会・
建築技術教育普及センター・マンションリフォーム推進協議会・リノベーション協議会・住宅リフォーム推進協議会・
建設副産物リサイクル広報推進会議・石綿対策全国連絡会議、TOTO・LIXIL・Panasonic・YKKAPの各サイト。

## 使えた代替手段

- **`WebSearch`ツールはこの制限を受けない。** 遮断されているドメインを含め、どのサイトの検索結果
  (タイトル・URL・要約)でも取得できる。全文は取得できないため、条番号など本文の詳細は検索結果の
  要約止まりになる。
- **`mlit.go.jp`配下のPDFはWebFetchで実体を取得できる**(1〜2MB級でも成功)。WebFetch自体の要約は
  PDFの文字レイヤーの圧縮方式により失敗することがあったが、その場合もPDF実体はローカルに保存される
  ([`/root/.claude/projects/.../tool-results/webfetch-*.pdf`])。`pip install pdfminer.six`
  (要 `cffi`/`cryptography` の再インストール、環境の`cryptography`が壊れていたため)でテキスト抽出できた。
  `poppler-utils`(`pdftoppm`、Readツールのページレンダリングに必要)はaptが403で入らない。
- **WebFetchには10MB(`maxContentLength`)の上限がある。** それを超えるPDF(実測: 11MBの公共建築改修
  工事標準仕様書で発生)は`maxContentLength size of 10485760 exceeded`で失敗する。**`mlit.go.jp`配下
  なら`curl -o <path> <url>`で直接ダウンロードでき、この上限を回避できる。** 大きいPDFは最初から
  curlで取りに行く方が早い。
- **300頁級の大きいPDFは、目次(TOC)だけで数十頁に及ぶことがあり、`extract_text(maxpages=N)`で
  読める範囲を目次だけで使い切ることがある。** 目的の章の本文が何頁目にあるかは事前に分からないため、
  `page_numbers=range(a,b)`で範囲を変えながら本文中の見出し(TOCと同じ文字列が2回目に出る場所)を
  `grep`で探すのが実用的だった。

## 今後この作業を再開するときの方針

1. まず`mlit.go.jp`配下を当たる(特に官庁営繕の技術基準ページ群)。本文まで読める数少ない一次資料。
2. それ以外は`WebSearch`で書誌情報(発行元・文書名・版・URL)を集め、[sources.md](./sources.md)に記録する。
   本文が必要な箇所は「取得すべき資料」として残し、実際の取得は届く環境(おーちゃんの手元など)に委ねる。
3. `e-Gov法令検索`(法令の一次ソース)が遮断されているのは大きな制約。法令の条文確認はWebSearchの要約と
   二次情報(行政書士事務所の解説記事等)止まりになるため、**条文を正確に引く必要がある場面ではこの環境の
   結果を鵜呑みにしない**こと。
