# 実運用入口→ファイアウォール→Z3→エスカレーション統合試験ログ

実行日: 2026-09-21 (Asia/Tokyo)

## 環境

- Windows 11 `Windows-11-10.0.26200-SP0`
- Python 3.12.14
- pytest 9.1.1
- Z3 5.1.0
- branch: `claude/eloquent-feynman-c0o8rf`
- HEAD: `46715e8b3f46eca6ae28b8a3da2c7dd2caf92568`

## 実装前

```text
80 passed in 8.96s
```

`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、書込み可能な専用 `HF_HOME` と `--basetemp` を使用。

## 新規統合試験

```text
......................................................                   [100%]
54 passed in 1.21s
```

54件には、固定15ケース、異常入力、Python/JSON入口、全10 failure_type、冪等性、1000試行、静的迂回検査を含む。

## 実装後全件

```text
........................................................................ [ 53%]
..............................................................           [100%]
134 passed in 9.84s
```

## ランダム統合試験

- 試行数: 1000
- seed: `20260922`
- 失敗: 0
- 10個の必須不変条件の違反: すべて0
- 各失敗assertionはseedとtrial番号を表示するため再現可能

## 性能（1000回）

SAT 500回、UNSAT＋イベント生成 500回を交互に実行。

| 区間 | 中央値 | p95 | p99 |
|---|---:|---:|---:|
| オーケストレーター全体 | 2.888 ms | 3.973 ms | 4.602 ms |
| ファイアウォール（Z3込み） | 2.826 ms | 3.918 ms | 4.559 ms |
| Z3単体 | 2.646 ms | 3.700 ms | 4.410 ms |
| イベント生成＋ルーティング | 0.031 ms | 0.042 ms | 0.052 ms |

実行コマンド:

```text
python -m benchmarks.run_inference_orchestrator_eval --iterations 1000
```

イベント生成数は500。深掘りリソースは実行せず、ルート決定のみ。

## 静的迂回検査

- 実運用モジュールでの直接 `z3` import: 0（低レベル内部実装を除く）
- ファイアウォール外での `ConsistencySolver()` 生成: 0（テスト・ベンチマークを除く）
- 実運用コードからの `add_variable_from_reading` 直接呼出し: 0
- 実運用コードからの `add_advisory_reading` 直接呼出し: 0
- 実運用コードからの `add_relation` 直接呼出し: 0

許可した内部実装は `arbitration/consistency_solver.py` と `arbitration/axis_quality_firewall.py` のみ。ASTベースの回帰試験で監視する。

