# 【最優先課題】単位・粒度の取り違えを止める仕組みが無い

作成日: 2026年9月21日
優先度: **最上位**(おーちゃんの指示、2026-09-21)
状態: **未修理。** この文書は点検結果と修理案であり、コードはまだ直していない
関連: `docs/design_v8.md` 3-2節・8章・10章、`docs/proposal_industry_statistics_repositioning.md`

---

## 0. なぜ最優先なのか

業界一般統計軸の位置づけ修正(`docs/proposal_industry_statistics_repositioning.md`)の
過程で、「単位の違う数値が、数値として重なったために支持と判定される」経路が
見つかった。**これは業界統計軸に固有の問題ではなく、どの軸でも単位を間違えれば
同じことが起きる設計全体の欠陥である。** 統計軸を軸カタログから外しても、この
欠陥は残る。

v8 8章の「穴2:尤もらしい誤り(複数の解釈がルールに違反せず、フィルターを
すり抜ける)」の実例そのものであり、かつ**集計表を見ても気づけない**種類の
誤りである(段階Aで Grounding DINO について確認した「個数は4/4で正解なのに
F1 0.00」と同じ性質)。

---

## 1. 点検の範囲と方法

段階B以降の統合結果すべてを対象に、「単位・粒度の違う数値が混ざる箇所」を
探した。**すべての指摘は、主張ではなく実行して確認した結果である。**
確認スクリプトの内容は本文に併記する。

| 対象 | ファイル |
|---|---|
| 軸品質ファイアウォール | `arbitration/axis_quality_firewall.py` |
| 整合性ソルバー | `arbitration/consistency_solver.py` |
| 実運用の入口 | `arbitration/inference_orchestrator.py` |
| 網羅探索 | `arbitration/constraint_exhaustive_search.py` |
| キラークエスチョン | `killer_question/engine.py`、`dependency_graph.py`、`firewall_bridge.py` |

---

## 2. 実運用の入口は、実は単位を検査していた(前回の報告の訂正)

**まず、前回の報告を訂正する。** 「単位の検査がどこにも無い」と報告したが、
これは不正確だった。`arbitration/inference_orchestrator.py` は**入力の段階で
単位を検査している。**

```
点検6: 実運用の入口(InferenceOrchestrator)は単位を検査するか
  count(対照)        invalid=True  ['evidence[0]:unregistered_source']   ← 単位エラー無し
  万円/件            invalid=True  ['evidence[0]:unknown_unit', ...]     ← 弾かれる
  単位フィールドなし     invalid=True  ['evidence[0]:missing_unit', ...]     ← 弾かれる
```

つまり `万円/件` の証拠を**実運用の経路から入れることはできない。**
`unit` は必須フィールドであり、未知の単位は `unknown_unit` で拒否される。
さらに複数の単位が混ざった場合の `unit_mismatch` チェックも書かれている
(`inference_orchestrator.py` 274行目)。

**したがって、階層2への誤昇格が起きるのは「入口を通さず、
`AxisQualityFirewall.assess()` や `ConsistencySolver` を直接呼んだ場合」に
限られる。** 前回報告した実測は、まさにこの直接呼び出しの経路だった。そして
削除した `axes/industry_statistics_axis/reading.py` が、その直接呼び出しを
する公開APIを提供していた。

**危険度の評価は下がる。しかし課題が消えるわけではない。** 理由は3節。

---

## 3. それでも修理が必要な4つの理由(すべて実測済み)

### 3-1. `AxisEvidence` 自体が単位を持たないので、入口の検査を迂回できる

`AxisEvidence` のフィールドは11個で、単位はその中に無い。

```
fields: ['target', 'count_range', 'source_id', 'axis_id', 'method_id',
         'source_fingerprint', 'strength', 'status', 'calibrated',
         'model_confidence', 'evidence']
```

入口が検査した単位は `evidence={"unit": unit}` という自由形式の辞書に入るだけで、
**`AxisQualityFirewall` も `ConsistencySolver` も、それを読まない。** そのため
`AxisEvidence` を直接組み立てれば、単位の検査を完全に迂回できる。
`consistency_solver.py` の冒頭は「実運用は必ず `InferenceOrchestrator` を
入口にせよ」と書いているが、**それはコメントであって、強制する仕組みは無い。**

実測(業界統計軸の削除前、実データを使用):

```
強い軸(画像軸)     : door_count = (4, 4)       ← 個数
過去実績軸(弱い)    : door_count = (3, 5)       ← 個数
業界一般統計軸(弱い) : 住宅 計   = (3, 246)     ← 万円/件
→ tier = 2 / provisional_audit / independent_advisory_source_count = 2
```

`AxisQualityFirewall._has_two_agreeing_advisory_sources()` は
**レンジが数値として重なるかだけを見る。**

### 3-2. 整合性ソルバーも、単位を見ずに「整合」と報告する(新規発見)

`ConsistencySolver._build_advisories()` にも同じ判定がある。実測:

```
agrees  = True
message = statistical の参考情報: 'door_count' は (3, 246) で、強い軸の解 (4, 4) と整合
```

ハードな解は変えないが、**人が読む文面に「整合」と出す。** レビューする人を
誤らせるので、ハード制約への影響が無いことは弁護にならない。

### 3-3. キラークエスチョンの橋渡しで、単位違いと棄権がハード制約に入る(新規発見・最も重い)

`killer_question/firewall_bridge.add_target_to_joint_solver()` は、
`decision.confirmed_range` が `None` のとき(= 強い軸が1つも無いとき)、
**すべての証拠のレンジの和集合**を取る。

```python
lower = min(e.count_range[0] for e in evidences)
upper = max(e.count_range[1] for e in evidences)
...
solver.add_variable(target, lower, upper, axis=axis)   # strength の既定値は "strong"
```

実測(過去実績軸(3,5) + 統計軸(3,246)万円 + 棄権した軸(0,0)):

```
firewall: tier = 3  confirmed_range = None
joint solver: lower=0 upper=246 axis=history strength=strong
→ 万円のレンジがハード変数の上限を 246 まで広げたか: True
→ 棄権した軸の (0,0) が下限を 0 まで広げたか: True
```

**2つの欠陥が同時に出ている。**

1. **単位違いのレンジが、弱い軸なのに `strength="strong"` のハード変数になる。**
   ファイアウォールでは「参考情報」に留まっていたものが、ここでハードに昇格する
2. **棄権した軸の番兵値 `(0, 0)` が、下限を 0 まで引き下げる。** 「レンジを
   主張せず棄権する」と宣言した軸が、実際には範囲を書き換えている。これは
   単位とは別の、独立した欠陥である

### 3-4. 階層3(要確認)の要素が、確定済みとして扱われる(新規発見)

同じ関数の分岐条件が `decision.action` ではなく
`decision.confirmed_range is None` になっているため、**自分のdocstringと
一致していない。** docstringは「階層3は生の読み取りレンジ」と書いているが、
階層3でも `confirmed_range` が非Noneなら階層1・2と同じ経路を通る。

実測(強い軸1つだけ → 階層3 `requires_review`、`confirmed_range=(4,4)`):

```
firewall: tier = 3  action = requires_review  confirmed_range = (4, 4)
joint solver: lower=4 upper=4 axis=firewall_provisional
→ 階層3なのに軸名が firewall_provisional(階層2の名前)か: True
→ レンジ幅0で、キラークエスチョンの質問対象から外れるか: True
confirmed_amount = 40000.0 / estimated_total = 40000.0
→ 人の確認が必要な要素の金額が『確定済み』に計上されたか: True
```

**人の確認が必要な要素が、質問対象から外れ、かつ「確定済み金額」に
計上される。** v8 3-3節の階層3(人に確認を仰ぐ)が骨抜きになる経路で、
これは単位の問題ではなく階層の取り違えだが、同じ「集計表を見ても気づけない
誤り」の family に属するため、ここに記録する。

---

## 4. その他に見つかったこと

### 4-1. `unit_mismatch` チェックは発火しえない(死んだ検査)

```
UNIT_ALIASES = {'count': 'count', '件': 'count', '個': 'count', '数量': 'count'}
→ 正規化後の単位の種類: {'count'}
```

正規化後の単位は1種類しか無いので、`len(units) > 1` は永遠に成立しない。
**「単位の不一致を検査している」という見かけだけがあって、実際には
機能していない。** これが前回「検査が無い」と誤って報告した一因でもある。

### 4-2. v8 9.5節が要求する連続量を、入口が表現できない(設計と実装の乖離)

v8 9.5節は、トライアル10の結果として「連続的な長さ・面積にも有効」と
結論し、「連続量における階層1の定義を許容誤差内での自動採用として
再定義する」と決めている。しかし入口は次のとおり。

```
点検8: 連続量(長さ・面積)を入口で表現できるか
  unit=m           unknown_unit
  unit=メートル      unknown_unit
  unit=m2          unknown_unit
  unit=㎡           unknown_unit
  unit=平方メートル    unknown_unit
  unit=円           unknown_unit

点検9: 小数の数量(配管延長 12.5m 等)を表現できるか
  count_range=[12.5, 13.0]  count_must_be_integer
```

**長さも面積も金額も入力できず、小数も扱えない。** つまり v8 9.5節で
「有効性を確認した」とされている連続量は、現在の実運用の入口では
**そもそも1件も処理できない。** これは単位安全性の修理と同じ場所を触るので、
一緒に設計するのが合理的である。

### 4-3. 単価の単位が宣言されていない(潜在)

`killer_question/engine.py` の `_unit_prices: dict[str, float]` は、
数量に掛ける単価を持つが、**単位の情報を持たない。**
`estimated_total_amount()` は `price × quantity` を合計するだけで、
「数量がメートルなのに単価が円/㎡」を検出できない。出口検査
(`arbitration/total_amount_sanity_check.py`)は合計金額を万円で受け取るため、
**この合計をどの単位として渡すかを宣言する必要がある**(現在は呼び出し側の
責任になっている)。

### 4-4. 呼び出し側が宣言する関係式・候補レンジ(潜在)

`ConsistencySolver.add_relation()` と
`ConstraintExhaustiveSearch` の `candidate_ranges` は、呼び出し側が宣言した
変数同士を単位を見ずに結びつける。ここは「設計者が明示的に書いた式」なので
3-1〜3-4 より危険度は低いが、単位を持たせれば同時に守れる。

---

## 5. 修理案

### 5-1. `AxisEvidence` に単位と粒度を第一級のフィールドとして持たせる

```python
@dataclass(frozen=True)
class AxisEvidence:
    target: str
    count_range: IntRange
    unit: str                     # 追加(必須)。"count" / "m" / "m2" / "yen" 等
    granularity: str = "element"  # 追加。"element"(対象要素1つ)/ "job"(工事1件)
    ...
```

`unit` を**必須**にするのが要点である。既定値を与えると、既存の呼び出しが
黙って通ってしまい、迂回経路が残る。既存の呼び出し箇所はすべて修正が必要になる
(その修正作業自体が、単位を意識せずに書かれた箇所の洗い出しになる)。

### 5-2. 照合する側で単位の一致を強制する

以下の3箇所で、単位・粒度が揃っていない証拠を**支持として数えない**。

1. `AxisQualityFirewall.assess()` — 冒頭で `targets` の一意性を検査しているのと
   同じ位置に、単位・粒度の一意性検査を足す。揃っていなければ
   `EscalationRequest(failure_type="unit_mismatch")` でエスカレーションする
   (黙って落とさない)
2. `AxisQualityFirewall._has_two_agreeing_advisory_sources()` — 単位が
   一致する証拠だけを数える
3. `ConsistencySolver._build_advisories()` — 単位が違う場合は
   `agrees=None` とし、「単位が違うため比較不可」と書く。**「整合」とは書かない**

### 5-3. `firewall_bridge` の2つの欠陥を直す(3-3・3-4)

1. 和集合を取る対象から、**`status == "abstained"` の証拠を除外する。**
   棄権した軸が範囲を広げてはならない
2. 和集合に入れる証拠を、**単位が揃っているものに限る**
3. 和集合で作った変数は `strength="weak"` で登録する(弱い軸の和集合が
   ハードな強い変数になってはならない)
4. 分岐条件を `decision.confirmed_range is None` から
   **`decision.action != "requires_review"`** に変える(docstringが元々
   意図していた挙動)。あわせて軸名も階層に合わせる

### 5-4. `UNIT_ALIASES` を実際に複数単位へ広げる(4-2 と同時に)

`count` / `m` / `m2` / `yen` を正規形とし、日本語表記(個・件・メートル・
平方メートル・円・㎡)を別名として受ける。同時に、連続量のために
`count_range` の整数制約を見直す必要がある(v8 9.5節の「許容誤差内での
自動採用」の実装と同じ作業になる)。

### 5-5. 迂回できないようにする

`consistency_solver.py` 冒頭の「実運用は必ず `InferenceOrchestrator` を
入口にせよ」というコメントを、**仕組みに変える。** 案としては、
`AxisEvidence` の生成を検証済みファクトリ経由に限る(直接コンストラクタを
呼んだ証拠には検証済みフラグが立たず、`assess()` が受け付けない)。

---

## 6. この課題の扱い

- **3-3 と 3-4 は、まだ直していない。** 挙動が変わる修正であり、既存テスト
  (`test_killer_question_integration.py`、`test_killer_question_precision_modes.py`)
  の期待値の見直しを伴うため、おーちゃんの判断を仰ぐ
- 5-1〜5-5 は1つの作業としてまとめて行うのが合理的である。単位フィールドを
  足す作業と、それを使って検査する作業と、連続量を通せるようにする作業は、
  同じ場所を触る
- 作業量の目安: `AxisEvidence` を生成している箇所は**リポジトリ全体で20箇所、
  6ファイル**(実測)。`unit` を必須にすると全箇所の修正が必要になる。

  | ファイル |
  |---|
  | `arbitration/inference_orchestrator.py`(実運用の入口。ここだけが既に単位を検査している) |
  | `benchmarks/run_killer_question_eval.py` |
  | `tests/test_axis_quality_firewall.py` |
  | `tests/test_industry_statistics_integration.py` |
  | `tests/test_killer_question_integration.py` |
  | `tests/test_killer_question_precision_modes.py` |

  生成箇所が実運用コードでは入口1箇所に集まっており、残りはテストと
  ベンチマークである。**つまり修理の影響範囲は思ったより狭い。**
  既存277件のテストの一部が一度落ちる状態を経由する
