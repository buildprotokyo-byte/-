# 自動積算AI(BUILD PRO)

建設図面(線・数字・記号・手書き)を自動で読み取り、自動積算に活用できる精度のAIを内製で構築するプロジェクトです。目標は精度100%ではなく、「業務で使える許容範囲に、限りなく安全に収束させる」ことです。

設計の背景・トライアル1〜10の結果・引き継ぎ内容は `docs/` 配下にまとめています。

- [`docs/design_v8.md`](docs/design_v8.md) — 統合設計書 v8(層構造・軸カタログ・統合ロジック・キラークエスチョンエンジン)
- [`docs/handoff_2026-09-20.md`](docs/handoff_2026-09-20.md) — 開発引き継ぎ資料(トライアル結果サマリー・未解決課題・次の優先順位)
- [`docs/claude_handoff_prompt.md`](docs/claude_handoff_prompt.md) — 実装フェーズの依頼内容(ステップ1〜5)
- [`docs/stage_a_report.md`](docs/stage_a_report.md) — 段階A実装検証報告書(Grounding DINO / VTracer / IfcOpenShell)

> 補足:リポジトリ内の `mobile/` は BUILD PRO 現場管理モバイルページ(GitHub Pages 配信)で、本プロジェクトとは別系統の既存資産です。ルートの `app.py` / `render.yaml` / `requirements.txt` は元々空のプレースホルダーで、今回このAIプロジェクトの土台として使用します。

## ディレクトリ構成(ステップ1の提案)

引き継ぎプロンプトのステップ1で依頼された「ディレクトリ構成の案」です。設計書の層構造(Layer 0〜9)のうち、まずコード化対象となる Layer 1・4・6・7 に対応させています。

```
.
├── axes/              # Layer 1/4: 各軸(画像・文章・絶対ルール・整合性・過去実績・空間認識 等)
│   ├── image_axis/     #   画像軸: grounding_dino_adapter.py(記号検出)、vtracer_vectorizer.py(ベクター化)
│   └── absolute_rule_axis/  # 絶対ルール軸: ifc_containment.py(IFC 空間階層と矛盾検出)
│                      #   共通インターフェース(base.py)を持ち、各軸を独立したモジュールとして実装する(ステップ2)
├── arbitration/        # Layer 6: 統合・アービトレーション層
│                      #   軸内統合 → 軸間の立体照合 → 多軸レンジ収束 → 3段階確信度階層(ステップ3)
├── killer_question/    # Layer 7: キラークエスチョンエンジン
│                      #   依存関係グラフの表現、影響度スコアの算出、質問選定ループ(ステップ4)
├── benchmarks/         # 効果測定のハーネス(本番の推論パスではない)
│                      #   synthetic_plans.py(劣化レベル付きの合成図面)、run_vtracer_eval.py、run_ifc_eval.py
├── tests/               # トライアル7・8・9等の再現テスト(ステップ5)。設計変更が過去の検証結果を
│                      #   壊していないかを自動確認する回帰テストとして機能させる
├── docs/                # 設計書・引き継ぎ資料(このリポジトリでの参照用コピー)
├── app.py               # (将来のAPI/実行エントリポイント。ステップ2以降で実装)
├── requirements.txt     # 依存パッケージ(numpy, pillow, opencv-python-headless, pytest)
├── pyproject.toml       # パッケージ定義・pytest設定
└── render.yaml          # デプロイ設定(未実装)
```

補足:
- `arbitration/`・`killer_question/` は現時点では空パッケージ(`__init__.py` のみ)です。中身はステップ2以降で実装します
- `axes/` には段階A(オープンリソースの機能化)で作った画像軸・絶対ルール軸のモジュールが入っています。軸の共通インターフェース(`axes/base.py`)は未実装です
- 過去実績軸は設計上「恒久的に補助専用」(他の軸の判定を排除する権限を持たない)ため、`arbitration/` 側でその制約を明示的に扱う想定です
- 依存関係グラフ(`killer_question/`)は、まず辞書ベースの単純な表現(要素ID → 影響を与える下流要素IDのリスト、および値ごとの制約関数)から始め、必要に応じて拡張する方針です

## セットアップ

```bash
pip install -r requirements.txt
pytest
```

段階Aの効果測定は次のコマンドで再現できます。

```bash
python -m benchmarks.run_vtracer_eval   # VTracer のパラメータ探索と読み取り精度
python -m benchmarks.run_ifc_eval       # 読み取り → IFC 空間階層 → 矛盾検出
```

`axes/image_axis/grounding_dino_adapter.py` の実モデルを動かすには `torch` と `transformers` に加えて、
huggingface.co への到達が必要です(検証時の環境では遮断されていました。詳細は段階A報告書を参照)。
