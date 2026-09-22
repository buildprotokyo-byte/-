# 段階B追加検証3：拮抗候補の制約全探索・深掘り実効性試験

## 総合判定

**条件付き合格**。

`constraint_exhaustive_search` はZ3モデル列挙を使う実機能となり、一意解、複数解、無解、タイムアウト、組合せ上限、不正入力、未対応relationを区別できる。全探索は証拠を生成せず、独立性・confidenceを上げない。既存134件、新規固定・難問・E2E、2,000試行はすべて成功した。

条件付きの理由は、永続的冪等性と業務入口接続が未実装、人へのキラークエスチョンは変数名ベースの候補提示まで、対応relationが整数線形式に限定、大規模問題には安全上限が必須だからである。

## 環境・Git状態

- OS: Windows 11 `Windows-11-10.0.26200-SP0`
- Python: 3.12.14
- pytest: 9.1.1
- Z3: 5.1.0
- branch: `claude/eloquent-feynman-c0o8rf`
- HEAD: `46715e8b3f46eca6ae28b8a3da2c7dd2caf92568`
- mainマージ、commit、push、PR更新: 未実施

開始時の未コミット変更は次のとおりで、削除・リセットせず保持した。

- `README.md`
- `arbitration/consistency_solver.py`
- `benchmarks/run_consistency_eval.py`
- `docs/stage_a_report.md`
- `docs/stage_b_report.md`
- `arbitration/axis_quality_firewall.py`
- `arbitration/escalation_router.py`
- `arbitration/inference_orchestrator.py`
- `benchmarks/run_grounding_dino_eval.py`
- `benchmarks/run_inference_orchestrator_eval.py`
- `docs/axis_quality_firewall_report.md`
- `docs/axis_quality_firewall_test_log.md`
- `docs/inference_orchestrator_integration_report.md`
- `docs/inference_orchestrator_test_log.md`
- `tests/test_axis_quality_firewall.py`
- `tests/test_grounding_dino_eval.py`
- `tests/test_inference_orchestrator.py`

## 実装前調査

- `constraint_exhaustive_search` は `ROUTE_TABLE` の文字列だけで、実装は存在しなかった。
- `conflicting_candidates` を生成する実運用判定は存在しなかった。
- 候補値、候補レンジ、外部relationの共通構造は存在しなかった。
- 深掘り結果をオーケストレーターへ返す契約・再判定経路は存在しなかった。
- したがって全探索結果を独立証拠へ誤登録する経路もなかったが、深掘りの実効性もなかった。

## 実装内容

### 構造化モデル

`ExhaustiveSearchRequest` に以下を実装した。

- trace / event ID
- element IDs
- 有限候補値または有限整数レンジ
- relation / hard constraint / advisory information
- source / axis / method IDs
- 元confidence tier / strength / calibration状態
- abstained element IDs
- max solutions / max combinations / timeout

`ExhaustiveSearchResult` は指定されたstatus・解・削減候補・除外理由・unsat core・安全フラグ・経過時間を返す。

以下はコード上の固定値である。

- `independence_added = false`
- `confidence_upgrade_allowed = false`
- `human_confirmation_required = true`

通常オーケストレーターが元から階層1の場合だけ、E2E最終判定は通常ルールによる自動確定を維持する。探索を第3軸には数えない。

### 全探索

- Z3のモデル列挙とblocking clauseで重複解を防止
- 候補domainとhard relationを `assert_and_track` して無解時のunsat coreを取得
- advisory relationはZ3へ渡さない
- candidate値は重複排除・昇順正規化し、入力順序に依存しない
- max solutions到達後に追加解の有無を確認し、不完全なら `is_complete=false`
- 理論組合せ数を候補展開前に計算し、max combinations超過なら即時棄権
- timeout時は途中解を返し得るが、候補削減・一意判定・自動確定には使用しない
- 無解時は近似値・暫定値を生成しない

## 対応relation

整数の有限domainに対する線形式。

- `==`
- `!=`
- `<=`
- `>=`
- `<`
- `>`
- 任意個の整数係数項と整数定数
- 合計＝内訳、差分、大小関係、整合・矛盾する循環

未対応:

- 変数同士の乗算・除算など非線形式
- 浮動小数、連続無限レンジ
- OR / implication / all-different等の高水準演算
- R1、SHASE-S206、内線規程など規格由来制約
- 外部関数・ユーザーコード

未対応operatorは `unsupported_relation` とし、黙って無視しない。

## 固定20ケース

| No. | 結果 | 内容 |
|---:|:---:|---|
| 1 | PASS | 2変数を制約で一意化 |
| 2 | PASS | 複数解を維持、代表値なし |
| 3 | PASS | 無解、暫定値なし |
| 4 | PASS | 全候補有効をcompleted_without_reductionで報告 |
| 5 | PASS | 合計＝内訳＋追加制約で一意化・削減記録 |
| 6 | PASS | 内訳入替えを複数解と判定 |
| 7 | PASS | 同一source metadataから独立性を追加しない |
| 8 | PASS | 全weakでもstrong昇格なし |
| 9 | PASS | 未校正でも校正済み昇格なし |
| 10 | PASS | abstainedの数値候補復活を拒否 |
| 11 | PASS | advisory衝突でhard解を除外しない |
| 12 | PASS | hard矛盾でno_solution＋unsat core |
| 13 | PASS | 候補重複を解として重複計数しない |
| 14 | PASS | 空候補はinvalid_input仕様 |
| 15 | PASS | min > maxはinvalid_input |
| 16 | PASS | 未対応operatorはunsupported_relation |
| 17 | PASS | max solutions到達時は不完全・非一意 |
| 18 | PASS | max combinations超過はlimit_exceeded |
| 19 | PASS | 1ms安全予算でtimeout・非一意 |
| 20 | PASS | 同一trace/event・同一入力は同一結果を再利用 |

## 追加難問

- 論理上一意だが誤った制約: `unique_solution` は返すが、制約正当性の確認理由を残しconfidenceを上げない。PASS。
- 制約不足: 複数解を維持し、変数名ベースの確認質問候補を返す。PASS。
- weakだけが真値を含む: weakをhard化せず、strong評価・制約再確認が必要な危険を保持。PASS。
- 循環依存: 整合循環を解き、矛盾循環をno_solutionとし、無限ループなし。PASS。
- 候補順序: 完全列挙時の解集合が同一。PASS。

## E2E結果

| ケース | 結果 | 最終挙動 |
|---|:---:|---|
| E2E-1 | PASS | 探索で一意でもstrong独立1件のため階層3、人確認 |
| E2E-2 | PASS | 元からstrong独立2件の通常ルールで階層1。探索は独立性を増やさない |
| E2E-3 | PASS | 複数解を人確認・質問候補へ返す |
| E2E-4 | PASS | 無解を制約／strong再確認へ返し、暫定値なし |
| E2E-5 | PASS | timeoutで棄権し、途中解を確定しない |

実際の経路:

`Python入力 → InferenceOrchestrator → AxisQualityFirewall → Z3 → conflicting_candidatesイベント → EscalationRouter → ConstraintExhaustiveSearch → DeepenedOrchestrationResult`

深掘り後も元の `FirewallDecision` を保持し、探索結果から `AxisEvidence` は作らない。

## ランダム・性質ベース試験

- seed: `20260923`
- 2,000試行 / 2,000成功
- 不変条件違反: 0
- 除外確定候補値: 合計391

| status | 件数 |
|---|---:|
| unique_solution | 191 |
| multiple_solutions | 233 |
| completed_without_reduction | 162 |
| no_solution | 679 |
| timeout | 129 |
| limit_exceeded | 541 |
| invalid_input | 65 |

不正解を有効解として返した件数、weak/advisory漏洩、abstained復活、独立性加算、confidence昇格、不完全探索の一意判定、代表値無断採用、暫定値生成、timeout/上限時の自動確定はすべて0件。

## 性能・組合せ爆発

各規模30回。

| 規模 | 理論組合せ | 列挙解中央値 | 中央値 | p95 | p99 | 安全動作 |
|---|---:|---:|---:|---:|---:|---|
| 3変数×3候補 | 27 | 7 | 6.189ms | 8.434ms | 11.644ms | 完全列挙 |
| 6変数×5候補 | 15,625 | 200 | 128.364ms | 151.572ms | 157.600ms | max_solutionsで不完全打切り |
| 10変数×10候補 | 10,000,000,000 | 0 | 0.035ms | 0.050ms | 0.132ms | 30/30 limit_exceeded |
| 12変数×10候補 | 1,000,000,000,000 | 0 | 0.039ms | 0.043ms | 0.051ms | 30/30 limit_exceeded |

メモリ最大値は未取得。Z3ネイティブ領域を含む信頼できる計測器を追加していないため、推測値は記載しない。

## v8設計との一致と差

一致:

- 弱い情報をハード制約にしない
- 全探索を新規証拠・独立軸にしない
- 論理的一意性と現実の正しさを分離
- 無解・複数解・不完全探索から値を捏造しない
- 解決不能時に人確認へ戻す
- 制約自体の誤り可能性を理由へ残す

差・未実装:

- source / method policyと探索冪等性の永続化
- 実業務入口との接続
- 自然言語に最適化されたキラークエスチョン生成
- 非線形・論理複合relation
- 深掘り結果の永続監査ログ
- 他9種類の深掘りリソース（スタブのまま）

## テスト結果

- 実装前: 134 / 134成功
- 新規: 32 / 32成功
- 実装後: **166 / 166成功**（最終実行18.58秒）

## 変更ファイル

- `arbitration/constraint_exhaustive_search.py`（新規）
- `arbitration/inference_orchestrator.py`
- `arbitration/escalation_router.py`
- `arbitration/consistency_solver.py`（内部API説明のみ）
- `tests/test_constraint_exhaustive_search.py`（新規）
- `tests/test_inference_orchestrator.py`（許可された深掘りZ3実装を静的検査へ追加）
- `benchmarks/run_constraint_exhaustive_search_eval.py`（新規）
- `docs/constraint_exhaustive_search_report.md`（新規）
- `docs/constraint_exhaustive_search_test_log.md`（新規）

## 次に優先すべき検証（1つ）

**実案件由来の候補・制約を匿名化した小規模データセットで、制約自体の誤り率と「論理上一意だが現実には誤り」の発生率を測定する。**
