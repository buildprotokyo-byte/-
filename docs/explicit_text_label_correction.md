# 正解データの「印字をそのまま読む(explicit_text)」の印の訂正(2026-09-23)

おーちゃんの指示(K-07 2 番、2026-09-23 22:45): 「7件を「波及」に数え直してください。
正解ファイルの印も直してください。直した記録を残し、誰がいつ直したか書いてください。
あわせて、過去の報告書(docs/a1_*、docs/a2_*)の explicit_text 24行も同じ取り違いが
ないか調べてください。あれば同じように直してください。」

## 何が違っていたか

正解データ(P011)で `expected_source_type` が `explicit_text` の行は **24 行**ある
(a1・a2 の報告が使った 24 行と同じもの)。1 行ずつ図面の埋め込み文字と突き合わせた結果:

| 分け方 | 行数 |
|---|---|
| 同じ工事が図面に印字されている(印どおり) | 14 |
| 工事名が印字されておらず、きっかけから推し量る行(**波及**) | 9 |
| どこに印字があるか辿れない | 1 |

**行の一覧と引用はリポジトリに書かない**(実見積から作ったデータのため)。
共有フォルダの `reports/印字をそのまま読む_24行.md` にある。

## 正解ファイルをどう直したか

- 9 行の `expected_source_type` を `explicit_text` から **`propagation`(波及)** に変えた。
- 辿れない 1 行は印を変えず、注記だけ足した(おーちゃんの判断待ち)。
- 各行に `expected_source_type_history`(変える前・後・いつ・誰が・指示・理由)、
  ファイルの先頭に `corrections` を足した。**直す前の版は封印フォルダに残した。**
  変更の記録は共有フォルダの `.sealed/golden_history/変更記録.md`。
- 直したのは Claude(K-06・K-07 を受けたスレッド)、2026-09-23。
- 読み方の比較の採点をこの版でやり直し、結果がバイト単位で同じことを確かめた
  (採点はこの印を使っていない)。

## 過去の報告への影響(測り直していない)

直した後は `explicit_text` が **15 行**(24 − 9)になり、`propagation` の 9 行は、
今の測定スクリプトの「別の種類の行」(`OTHER_KINDS` など)に**入らない**。
そのため、`explicit_text` を分母に使った次の数字は、**直す前の印で測ったもの**である。
**数字は書き換えていない。**測り直すかどうかは別に決める。

| 報告 | explicit_text の使い方 | 判定に効いていたか |
|---|---|---|
| `a1_repeated_symbol_report.md` 1 節 | 6 本とも落ちた 39 行の内訳「印字をそのまま読む 7 / 24」 | 内訳の表。直すと **印字 0 / 15、波及 7 / 9** |
| `a1_symbol_alias_report.md` 対照 5 | 誤爆 4 / 24 | 対照の最大値(◯) |
| `a1_knowledge_vocabulary_report.md` 対照 2 | 誤爆 4 / 24 | 対照の最大値(◯) |
| `a1_drawing_vocabulary_result.json` | 内訳 8 / 24 | 参考 |
| `a1_vocabulary_intersection_result.json` | 内訳 2 / 24 | 参考 |
| `a2_finish_schedule_vocabulary_report.md` D2・D3 | 誤爆 0.417・0.583 | **いちばん高い誤爆で、判定(×)を決めていた** |
| `a2_where_the_words_live_report.md` Z3 | 誤爆 0.292 | いちばん高い誤爆(通る) |
| `a2_knowledge_area_vocabulary_report.md` | 誤爆 7 / 24 | 対照 |
| `a2_area_row_coverage_report.md` 対照 1 | 誤爆 0.125 | 最大ではない(最大は standard_rule) |
| `a2_drawing_area_vocabulary_result.json`・`a2_vocabulary_narrowing_result.json` | 誤爆の比較と分け方 | 対照 |
| `golden_eval_result.json` | 種類別の集計 | 参考 |

基準の文書(`*_criteria.md`)は測る前に固定したものなので、書き換えない。
