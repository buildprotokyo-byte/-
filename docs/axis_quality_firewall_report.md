# 段階B追加検証：軸品質ファイアウォール試験

## 総合判定

**条件付き合格**。

指定された固定10ケース、シード固定の500試行、既存回帰68件はすべて成功した。試験対象の共通判定層では、誤自動確定、非ハード情報の漏洩、同一データ源の重複加算、UNSAT時の暫定採用、未校正Grounding DINOの強い軸採用はいずれも0件だった。

条件付きとした理由は、今回追加した判定層を実運用の全入力経路へ接続するオーケストレーターがリポジトリに存在せず、深掘りルーター全体への実送信も今回の対象外だからである。判定結果としての `axis_contradiction` と衝突制約は生成できるが、本番経路全体での強制は未検証である。

## 対象と実行環境

- リポジトリ: `buildprotokyo-byte/-`
- ブランチ: `claude/eloquent-feynman-c0o8rf`
- HEAD: `46715e8b3f46eca6ae28b8a3da2c7dd2caf92568`
- OS: Windows 11 (`Windows-11-10.0.26200-SP0`)
- Python: 3.12.14, 64 bit
- pytest: 9.1.1
- Z3: 5.1.0
- mainへのマージ: 未実施
- commit / push: 未実施

開始時点には既存の未コミット変更があり、削除・リセット・上書きを行わず保持した。

## テスト結果

### 変更前

専用の書込み可能な一時領域とオフライン設定を使った有効な基準実行は **68 passed in 8.50s**。

初回の通常実行は外部モデル取得待ちで停止したため中断した。次のオフライン実行は既定一時領域の権限不足で 66 passed / 2 errors だった。これはコード不良ではなく実行環境要因であり、隔離した `--basetemp` と `HF_HOME` により68件成功を確認した。

### 変更後

**80 passed in 10.50s**。

- 既存回帰: 68 / 68
- 固定ケース: 10 / 10
- 乱数耐性: 500 / 500（seed=`20260921`）
- 旧API漏洩防止回帰: 1 / 1

詳細ログは `docs/axis_quality_firewall_test_log.md` を参照。

## 固定10ケースの結果

| ケース | 結果 | 確認内容 |
|---|---:|---|
| 1 | PASS | 校正済みの独立strong 2 source一致で階層1・自動確定 |
| 2 | PASS | 同じsourceの2軸を独立2軸に数えない |
| 3 | PASS | Grounding DINO / VTracer / 古典処理を画像source内の3レンズとして扱う |
| 4 | PASS | strong 1件＋同一画像weak 3手法は階層3、理由を出力 |
| 5 | PASS | 誤ったweak値がZ3解・レンジ・unsat coreを変更しない |
| 6 | PASS | 内部confidence 0.99でも未校正ならadvisory |
| 7 | PASS | abstainedはZ3変数・制約・証拠数を増やさない |
| 8 | PASS | 独立strong間の矛盾をUNSAT、`axis_contradiction` として引渡し |
| 9 | PASS | 全件weak / abstainedなら階層3・要確認 |
| 10 | PASS | 同一画像の尤もらしい誤答一致を階層1にせず、相関誤り理由を保持 |

## 乱数耐性試験

- 試行数: 500
- seed: `20260921`
- 失敗数: 0
- 誤った階層1自動確定: 0
- 非ハード情報のハード制約漏洩: 0
- 同一sourceの重複加算: 0
- UNSAT時の暫定値採用: 0
- abstainedによる候補範囲縮小: 0
- 高い内部confidenceによる未校正上書き: 0

各assertionの失敗メッセージにはseedとtrial番号を含め、固定入力10件と合わせて再現可能にした。

## 実装されていた機能

- Z3による整数レンジ制約、関係制約、レンジ収束
- `assert_and_track` / `unsat_core` による衝突制約の特定
- `SymbolCountReading.status == "abstained"` の変数生成スキップ
- `add_advisory_reading` によるZ3変数を生成しない参考情報登録
- strong変数だけをZ3へ渡すフィルタ
- Grounding DINOの3段階status判定。ただし従来はモデル出力スコア中心

## 未実装だった機能

- source / axis / methodを別概念として保持する共通データ構造
- 元データ源単位の独立性集計
- 実測校正をハード参加条件にする判定
- 独立strong数を使う3段階階層判定
- 相関誤りの抑止理由
- `axis_contradiction` のエスカレーション用データ生成
- 判定層を全実運用経路へ強制するオーケストレーター
- 深掘りルーターへの実際の送信処理（今回の対象外）

## 今回追加・修正した機能

- `AxisEvidence`: `source_id` / `axis_id` / `method_id`、strength、status、calibratedを分離
- `AxisQualityFirewall`: 元データ源ごとの集約、ハード参加判定、階層判定、理由生成
- 同一source内の複数手法は1 sourceとして集約
- 同一source内でハード候補同士が矛盾した場合、そのsourceをハードから降格
- 未校正、weak、low-confidence、abstainedのZ3ハード制約からの排除
- 独立strong間UNSAT時の `axis_contradiction` とunsat core引渡し
- 旧 `add_variable_from_reading` へweak / low-confidenceを渡した場合もadvisoryへ自動退避

## 設計上の難問への回答

1. **軸と手法を区別できるか**: 変更前は不可。追加した `AxisEvidence` では区別できる。
2. **source_id / axis_id / method_idは存在するか**: 変更前は共通識別子なし。今回すべて追加した。
3. **異なるライブラリだけで独立と誤判定しないか**: 新判定層では `source_id` が同じ限り独立数は増えない。
4. **3段階statusが内部スコアだけで決まらないか**: Grounding DINOアダプター単体では内部スコア中心。ファイアウォールでは `calibrated` を別条件にし、未校正はハード不可。
5. **実測校正を強弱へ反映できるか**: 今回追加した `calibrated` で可能。ただし校正値を永続化・自動供給する仕組みは未実装。
6. **add_advisory_readingがZ3変数・制約を生成しないか**: 既存実装とテストの双方で確認。`_advisories` への登録のみ。
7. **弱い軸がunsat coreへ混入する経路はないか**: 新判定層および修正後の旧APIではない。500試行と回帰試験で漏洩0件。
8. **同一sourceの相関誤りを検出・抑止できるか**: 新判定層では独立数を増やさず、理由を残して階層1を抑止できる。
9. **「矛盾なし」と「正しい」を混同していないか**: SATだけでは階層1にしない。独立・校正済みstrong 2 source以上を追加条件にした。
10. **なお防げない尤もらしい誤りは何か**: 異なる `source_id` が実際には同じ上流資料のコピーである場合、複数sourceに共通する系統誤差、古い校正結果、誤ったsource_id付与、独立2 sourceが同じ誤答を返す場合は防げない。

## v8基本設計との差

変更前は、Z3の整合性計算とadvisory分離は実装済みだったが、v8の核心である「元データ源単位の独立性」「実測校正」「階層判定」はデータ構造として未接続だった。今回のファイアウォールでこの差を局所的に埋めた。一方、校正レジストリ、source provenanceの自動生成・検証、実運用オーケストレーターへの組込みは残る。

## 不合格分類に照らした残課題

- **E（部分残存）**: Grounding DINOアダプター自身のstatusは内部スコア中心。ファイアウォールで校正を分離したが、校正結果の自動供給はない。
- **G**: 深掘りルーター全体への接続は未実装（今回の明示的な対象外）。
- **H**: source provenanceの真正性検証と全入力経路への強制が未実装。

A〜D、Fはファイアウォール層内では解消または試験済み。

## 変更ファイル

今回の変更:

- `arbitration/axis_quality_firewall.py`（新規）
- `arbitration/consistency_solver.py`（weak / low-confidenceの漏洩防止。開始時点から別変更が存在するためファイル全体は累積差分）
- `tests/test_axis_quality_firewall.py`（新規）
- `docs/axis_quality_firewall_report.md`（新規）
- `docs/axis_quality_firewall_test_log.md`（新規）

開始前から存在し、そのまま保持した未コミット変更:

- `README.md`
- `arbitration/consistency_solver.py`
- `benchmarks/run_consistency_eval.py`
- `docs/stage_a_report.md`
- `docs/stage_b_report.md`
- `benchmarks/run_grounding_dino_eval.py`
- `tests/test_grounding_dino_eval.py`

## 設計上の未解決事項

- source_idを誰が発行し、同一上流データのコピーをどう検出するか
- 校正結果の版、対象分布、期限をどこで管理するか
- ファイアウォールを迂回できない実運用の単一入口
- エスカレーション深掘りルーターとの実接続
- 同一source内で複数手法が矛盾した場合の再観測・代表値選択方針

## 次に優先すべき検証（1つ）

**実運用入口からZ3までの統合試験**。全読み取りが必ず `AxisQualityFirewall` を通り、source provenanceと校正状態が欠落した証拠をハード化できないことを、実際のオーケストレーター経路で確認する。

