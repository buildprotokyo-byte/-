# 抜き取り監査を階層1に広げる — 着手前の読み取りメモ(作りかけ)

2026-09-22。ブランチ `claude/tier1-audit-sampling`、main `7d21245` から。
**実装は未着手。失敗するテストだけ先に置いてある**(`tests/test_tiered_audit.py`)。

## 1. コードを読んで分かったこと(記録と食い違う点)

**抜き取り監査は本番の経路から呼ばれていない。**
`run_provisional_audit` / `collect_tier2_population` / `expand_to_categories` /
`format_report` を呼んでいるのは `tests/` と `benchmarks/` だけで、
`arbitration/inference_orchestrator.py` からも `killer_question/firewall_bridge.py`
からも呼ばれていない(`grep` で確認)。

つまり現状は「階層2の抜き取り監査が**部品として存在する**」段階であって、
推論を1回走らせたときに監査が自動で走る状態ではない。
**階層1に広げても、広がるのは部品の適用範囲であって、運用上の監査件数は
どちらも0のままである。** 本番の経路に挿す作業は別に要る。

階層と action の対応(`arbitration/axis_quality_firewall.py`):
`auto_confirm` = 階層1、`provisional_audit` = 階層2、`requires_review` = 階層3。
階層1にも `confirmed_range` は入るので、監査の材料は揃っている。

## 2. 階層1を母集団に入れると前提がどう変わるか

現状の前提は `collect_tier2_population` の docstring にある
「階層1・階層3を混ぜると的中率が薄まり、階層2の品質が見えなくなる」。

- **薄まりは「1つの的中率に混ぜたとき」にだけ起きる。** 階層ごとに母集団・
  抽出件数・的中率を分けて数えるかぎり、この前提は壊れない。だから設計は
  階層ごとに分けて集計する形にし、**階層をまたいだ的中率を出す入口を作らない。**
- **階層1は人が一度も見ない(`auto_confirm`)。** 階層2は抽出されれば人が見る。
  見逃したときの重さが違うので、抽出率は階層ごとに別に決められる必要がある。
- **階層3は母集団に入れない。** 人が必ず見るので抜き取る意味が無い。
- **v8 4-3節ルール3の拡大は階層をまたぐべき。** 系統誤差は手法から出るので、
  階層2で誤りが出た手法を使っている階層1の要素も疑わしい。
  **返すだけで、自動で階層を下げたりはしない**(既存の性質を保つ)。
- **検出力は階層ごとに計算する。** 合算した母集団で計算すると、実際より高い
  検出力を報告してしまう。階層1の1案件あたりの件数も小さいはずなので、
  [[jidou-sekisan-provisional-audit]] に記録済みの「母集団10〜20件では誤り1件を
  半分以上見逃す」という限界は階層1にもそのまま当てはまる。

## 3. 既定値について

- **階層2の既定は変えない**(抽出率30%・最小5件)。
- **階層1の既定はコードに入れない。** 母集団に入れるには明示的な指定が要る。
  値は PR 本文で提案するだけにする(おーちゃんの判断待ち)。

## 4. 再開するとき最初にやること

`arbitration/provisional_audit.py` に次を足して、置いてあるテストを通す。
`TierAuditPolicy` / `DEFAULT_TIER_POLICIES`(階層2のみ) / `AUDITABLE_TIERS` /
`AuditCandidate.tier` / `AuditFinding.tier` / `AuditLogEntry.tier` /
`TieredAuditReport` / `collect_audit_population` / `run_tiered_audit` /
`format_tiered_report`。
既存の `run_provisional_audit` は階層が混ざった母集団を拒否する。

## 5. 他スレッドとの重なり

別スレッドが `arbitration/` を触っている(群合計制約、ブランチ
`claude/group-total-masking`、`arbitration/group_total.py` 新設と
`firewall_bridge`・`consistency_solver` の変更)。
**この作業が触るのは `arbitration/provisional_audit.py` と `tests/` だけで、
現時点で重なるファイルは無い。**
