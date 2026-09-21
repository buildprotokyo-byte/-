# 段階B追加検証2：実運用入口→軸品質ファイアウォール→Z3→エスカレーション統合試験

## 総合判定

**条件付き合格**。

実装済みのPython APIとJSON入口では、全入力が単一オーケストレーターを通り、信頼済みsource登録・methodポリシー照合、軸品質ファイアウォール、Z3、3段階判定、イベント生成、ルート決定まで一貫して処理される。固定15ケース、異常入力、入口迂回、1000試行、静的検査はすべて成功した。

条件付きの理由は、既存Webアプリなど実際の業務入口が推論処理へまだ接続されていないこと、深掘り先が契約スタブであること、trace冪等性がプロセス内メモリだけで永続化されていないことである。

## 実行環境とGit状態

- OS: Windows 11 `Windows-11-10.0.26200-SP0`
- Python: 3.12.14
- pytest: 9.1.1
- Z3: 5.1.0
- branch: `claude/eloquent-feynman-c0o8rf`
- HEAD: `46715e8b3f46eca6ae28b8a3da2c7dd2caf92568`
- commit / push / PR更新 / mainマージ: すべて未実施

作業開始時に存在し、保持した変更:

- `README.md`
- `arbitration/consistency_solver.py`
- `benchmarks/run_consistency_eval.py`
- `docs/stage_a_report.md`
- `docs/stage_b_report.md`
- `arbitration/axis_quality_firewall.py`
- `benchmarks/run_grounding_dino_eval.py`
- `docs/axis_quality_firewall_report.md`
- `docs/axis_quality_firewall_test_log.md`
- `tests/test_axis_quality_firewall.py`
- `tests/test_grounding_dino_eval.py`

## 実装前後のテスト

- 実装前: **80 passed in 8.96s**
- 新規統合試験: **54 passed in 1.21s**
- 実装後全件: **134 passed in 9.84s**
- 既存80件: 80 / 80維持
- ランダム統合: 1000 / 1000、seed=`20260922`

## 実在した入口・存在しなかった入口

実装前に実在した推論用入口は、テストとベンチマークから低レベルAPIを直接呼ぶ合成入力だけだった。Webアプリ `app.py` とモバイル画面は今回の自動積算推論へ接続されていない。

今回実装した入口:

- Python API: `InferenceOrchestrator.process`
- JSON文字列/bytes: `InferenceOrchestrator.process_json`
- テスト用合成入力: Python APIと同じ経路

存在しない、または今回実装しなかった入口:

- Web HTTP API
- 実運用CLI
- CSV入力
- バッチ入力
- 推論用ファイル読込
- 業務画面との接続
- 将来用外部アダプター

性能測定CLIはベンチマーク専用であり、実運用入口ではない。

## 単一オーケストレーター

`arbitration/inference_orchestrator.py` を追加した。責務は以下。

- request / JSONの安全な解析
- element / trace / target / source / axis / method / unit / rangeの検証
- 単位別名の正規化と補正記録
- source registryによるsource IDと元データ指紋の照合
- method policyによる校正状態・最大strengthの強制
- 証拠の重複排除
- `AxisEvidence`への変換
- `AxisQualityFirewall`の呼出し
- Z3結果、3段階判定、advisory、abstainedの保持
- 必要なイベント生成とルーター受渡し
- trace ID＋入力fingerprintによるプロセス内冪等性
- 区間別処理時間の記録

## 直接投入の防止方式

採用方式は「安全な単一公開入口＋低レベル内部API＋静的禁止試験」。

- オーケストレーターは `ConsistencySolver` やz3を直接参照しない。
- Z3生成は `consistency_solver.py`、呼出しは `axis_quality_firewall.py` に限定。
- 旧APIは回帰互換のため削除せず、内部・低レベルAPIであることを明記。
- AST静的試験で、テスト・ベンチマークを除く他モジュールからの直接ソルバー生成を禁止。
- 未登録methodは既定でweak・未校正へ降格。
- 入力の `calibrated=true` だけでは昇格せず、信頼済みmethod policyとの一致が必要。
- source_idだけで独立性を数えず、信頼済みsource registryの元データ指紋で集約。

## 発見・修正した迂回経路

発見した問題:

1. 旧低レベルソルバーはテスト・ベンチマークから直接生成可能だった。
2. 自己申告のsource_idだけでは名称変更による水増しを防げない。
3. 自己申告の `calibrated=true` / `strong` だけでは品質偽装を防げない。
4. 外部relationを安全に名前解決・循環検査する層は存在しなかった。

修正:

1. 実運用入口をオーケストレーターに限定し、静的試験で新しい直接呼出しを禁止。
2. source registryと元データ指紋を追加。同一指紋の別source_idは独立数を増やさない。
3. 信頼済みmethod policyを追加。未登録手法と未校正Grounding DINOはハード不可。
4. 外部relationは黙って採用せず `invalid_input` として安全側に拒否。ファイアウォール内部relationのみ許可。

残存する危険な迂回経路: **現在のリポジトリ内の実運用コードでは0件**。

ただし、リポジトリ外の利用者がPythonの旧APIを直接importすることを実行時権限制御で禁止する仕組みはない。現時点では実運用接続自体がなく、今後の接続時にはオーケストレーターだけを公開する必要がある。

## 固定15ケース

| No. | 結果 | 確認内容 |
|---:|:---:|---|
| 1 | PASS | 独立・校正済strong 2件一致→SAT、階層1、イベントなし |
| 2 | PASS | strong 1＋独立weak 2→階層2、仮採用・監査理由 |
| 3 | PASS | 同一source 3手法→独立1件、階層1禁止 |
| 4 | PASS | 未校正Grounding DINO高confidence→Z3不参加、uncalibrated_source |
| 5 | PASS | abstained→Z3変数・独立数に不参加 |
| 6 | PASS | weak誤答はstrong解をUNSATにしない |
| 7 | PASS | 独立strong矛盾→UNSAT、暫定値なし、axis_contradiction |
| 8 | PASS | 同一画像の整合誤答→correlated_source_risk |
| 9 | PASS | 全weak→階層3、insufficient_independent_evidence |
| 10 | PASS | 全abstained→階層3、候補範囲なし |
| 11 | PASS | source_id欠落→invalid_input、偽ID生成なし |
| 12 | PASS | axis_id欠落→invalid_input |
| 13 | PASS | method_id欠落→invalid_input（拒否仕様を採用） |
| 14 | PASS | 同一証拠再送→重複排除、独立数不増 |
| 15 | PASS | 同一trace・同一入力→同一結果・イベントを再利用 |

## 異常入力試験

以下を安全側に拒否し、自動確定しないことを確認した。

- 空文字ID、null
- NaN、Infinity
- 負の個数、逆転レンジ、100万超の個数
- 不明status、不明axis、不明unit
- 数値文字列
- 同一source/method/axisの矛盾値
- relation入力、重複relation相当、循環relation相当
- 空unsat_coreのaxis_contradictionイベント
- 壊れたJSON、配列root、null root、不正bytes
- confidence status欠落、calibration欠落
- trace ID再利用時の異なるpayload
- 未登録source、source fingerprint不一致
- 未登録methodによるstrong・校正済み自己申告

外部relationは今回の範囲では未実装であり、補完・推測せず `invalid_input` に送る。

## ランダム統合試験

- 試行数: 1000
- seed: `20260922`
- 失敗数: 0

不変条件違反件数:

| 不変条件 | 違反 |
|---|---:|
| ファイアウォール未通過のZ3投入 | 0 |
| weak / advisory / abstained漏洩 | 0 |
| 未校正証拠のstrong昇格 | 0 |
| 同一source fingerprintの重複加算 | 0 |
| 同一証拠の再送加算 | 0 |
| strong独立2未満で階層1 | 0 |
| UNSAT時自動確定 | 0 |
| invalid_inputから自動確定 | 0 |
| 必要イベントの空failure_type | 0 |
| 同一trace再実行の不整合 | 0 |

## failure_type別ルーティング

| failure_type | resource | 結果 |
|---|---|:---:|
| image_degraded | floorplan_dataset_lookup | PASS |
| unknown_symbol | symbol_dataset_lookup | PASS |
| conflicting_candidates | constraint_exhaustive_search | PASS |
| axis_contradiction | r1_standard_checker | PASS |
| statistically_unusual | mlit_statistics_checker | PASS |
| trade_specific_unclear | trade_rule_lookup | PASS |
| insufficient_independent_evidence | human_confirmation | PASS |
| uncalibrated_source | calibration_required | PASS |
| correlated_source_risk | independent_source_required | PASS |
| invalid_input | input_correction_required | PASS |

すべて `status=not_executed` とし、深掘り処理が完了したようには見せない。

## 性能

1000回（SAT 500、UNSAT＋イベント500）の結果:

| 区間 | 中央値 | p95 | p99 |
|---|---:|---:|---:|
| 全体 | 2.888ms | 3.973ms | 4.602ms |
| ファイアウォール（Z3込み） | 2.826ms | 3.918ms | 4.559ms |
| Z3単体 | 2.646ms | 3.700ms | 4.410ms |
| イベント生成＋ルーティング | 0.031ms | 0.042ms | 0.052ms |

前回Z3参考値（中央値4.491ms、p95 7.019ms）より今回値は小さい。入力形状・環境・計測範囲が異なるため直接の速度改善とは断定しないが、著しい劣化は見られない。

## v8設計との一致点と差

一致点:

- 元データ源単位で独立性を評価
- 校正済みstrongだけをハード制約へ投入
- weak / advisory / abstainedをZ3から分離
- SATだけを正しさと見なさず、独立strong 2件を階層1条件にする
- UNSAT時に暫定採用せず、衝突制約とunsat coreを渡す
- 同一データ源の相関誤りを理由として残す

差・未実装:

- 業務画面・HTTP API・バッチ・ファイル入口との接続
- source registry / method policyの永続管理、署名、版管理
- イベント永続化とプロセス・端末をまたぐ冪等性
- 深掘りリソース本体
- 外部relationの安全な宣言・重複排除・循環解析

## 変更ファイル

今回追加・更新:

- `arbitration/inference_orchestrator.py`
- `arbitration/escalation_router.py`
- `arbitration/axis_quality_firewall.py`
- `arbitration/consistency_solver.py`
- `tests/test_inference_orchestrator.py`
- `benchmarks/run_inference_orchestrator_eval.py`
- `docs/inference_orchestrator_integration_report.md`
- `docs/inference_orchestrator_test_log.md`

## 次に優先すべき検証（1つ）

**source registry・method policy・trace冪等性を永続ストアへ移し、プロセス再起動と並行要求を含む真正性・冪等性試験を行う。**

