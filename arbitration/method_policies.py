"""手法ごとの校正状態と最大強度の、このリポジトリにおける正式な登録簿。

なぜ登録簿が要るのか
--------------------
`InferenceOrchestrator` の `MethodPolicy` は **`method_id` だけ**をキーにする。
つまり「どの手法をどこまで信じてよいか」は、呼び出し側が渡す辞書に書かれた
とおりにしかならない。v8 で決まった採否(11-3の汎用ゼロショット検出モデルの
除外、12-2節の VTracer の数量種別ごとの扱い)は、これまで**設計書と docstring
にしか無く、コードのどこにも登録されていなかった。**

このモジュールはその決定を1か所に集める。

2つの使い方
-----------
1. **既定の方針として渡す**::

       InferenceOrchestrator(DEFAULT_METHOD_POLICIES)
       InferenceOrchestrator(with_defaults({"my_detector": MethodPolicy(True, "strong")}))

2. **上限(天井)として自動的に効く。** `InferenceOrchestrator` は、
   呼び出し側が渡した方針に対して、この登録簿を**緩める方向には使わず、
   きつくする方向にだけ**適用する(`clamp_to_defaults`)。呼び出し側が
   うっかり ``vtracer_floor_area`` を ``calibrated=True, max_strength="strong"``
   で渡しても、**ここに登録された上限まで引き下げられる。**

   登録簿に無い手法には何もしない(未登録手法はもともと
   `InferenceOrchestrator` 側で weak / 非校正に落ちる)。

**この登録簿は緩める方向に働かない。** ここに ``strong`` と書いてあっても、
呼び出し側が渡さなければ強い軸にはならない。「既定で信用が増える」経路を
作らないための約束である。
"""

from __future__ import annotations

from typing import Mapping

from axes.image_axis.pdf_dimensions import (
    METHOD_DIMENSION_SCALE,
    METHOD_DIMENSION_TEXT,
)
from axes.image_axis.pdf_vector_symbols import (
    METHOD_DOOR_ARC,
    METHOD_TEXT_AREA,
    METHOD_TEXT_SCALE,
)
from axes.image_axis.schedule_tables import (
    METHOD_DOOR_SCHEDULE,
    METHOD_FINISH_SCHEDULE,
)
from axes.image_axis.vtracer_vectorizer import METHOD_FLOOR_AREA, METHOD_WALL_LINEWORK

from arbitration.inference_orchestrator import MethodPolicy

#: 人が入れた基準点の手法ID。実体は `intake/start_kit.py` にあるが、
#: `intake` は `arbitration` を import するので、ここに文字列として置く
#: (逆向きに import すると循環する)。
METHOD_HUMAN_REFERENCE_POINT = "human_reference_point"

#: 手法IDごとの、このリポジトリで認められた上限。
#:
#: 出どころ(いずれも実測に基づく決定):
#:
#: - ``vtracer_wall_linework`` … 段階A実測。重度劣化で偽の図形 2811個→18個、
#:   壁の総延長誤差 +117.7%→-4.0%(大津の二値化を先にかけた場合)。
#:   v8 12-3節で「条件付き採用」。
#: - ``vtracer_floor_area`` … トライアル12。劣化が重なる現実的な条件
#:   (heavy・realistic)で誤差 62.49%。v8 12-2節(2)で
#:   **「単独でハードな確定に使わない」**と決定。
#:   `max_strength="weak"` にしてあるので、ハード制約(階層1の根拠)には
#:   絶対に昇格しない。参考情報としては使える。
#: - ``pdf_text_scale`` … 表題欄に印字された縮尺から長さを計算する
#:   (`axes/image_axis/pdf_vector_symbols.extract_scale`)。未校正。
#:   印字は**用紙の拡大縮小を保証しない**(A3 の図面を A0 で出しても印字は
#:   1/50 のまま)ので、人が入れた基準点と突き合わせる相手として使う。
#: - ``human_reference_point`` … 人が図面上の2点と実際の長さを入れたもの
#:   (`intake/start_kit.py`)。手法の実体は intake 側にあるが、**人の入力も
#:   それだけを根拠に自動確定させない**というおーちゃんの指示(2026-09-22)を
#:   コードで担保するため、ここで未校正として登録する。`calibrated=False` の
#:   あいだは `is_hard_eligible` が False になり、ハード制約に入らない。
#: - ``pdf_text_area`` … 図面に**文字として書かれている**面積の記載をそのまま
#:   読む(`axes/image_axis/pdf_vector_symbols.find_area_labels`)。
#:   上限は ``strong`` にしてあるが **``calibrated=False``** なので、
#:   `AxisEvidence.is_hard_eligible` は False のままで、階層1の根拠にはならない。
#:   P011 で読めた2値(専有延床 95.54㎡ / 施工床 90.61㎡)はどちらも正解と
#:   一致したが、**案件1件・値2つは校正ではない。** 誤り率を独立データで
#:   測ってから `calibrated=True` にすること。上限だけ ``strong`` なのは、
#:   「印字された数値をそのまま読む」手法が原理的にはハード制約になりうる
#:   ためで、校正が済めばこの1行だけで昇格できるようにしてある。
#: - ``pdf_vector_door_arc`` … CAD 由来 PDF のベジェ曲線から円弧の幾何で
#:   開き戸を拾う(`find_door_arcs`)。**独立データでの校正が無いので
#:   強い軸として扱わない**(2026-09-22 のおーちゃんの指示5)。
#:   加えて、引戸・折戸は原理的に拾えず、スキャンページでは常に0件になる。
#:   つまり「0件」は「建具が無い」ではない。``max_strength="weak"`` は
#:   この見落としが階層1へ伝わらないための歯止めでもある。
#: - ``pdf_table_door_schedule`` … 建具表を罫線の升目として読み、
#:   建具番号ごとの数量を印字されたまま取る
#:   (`axes/image_axis/schedule_tables.read_door_schedules`)。
#:   上限を ``strong`` にしてあるのは ``pdf_text_area`` と同じ理由で、
#:   「印字された数値をそのまま読む」手法は原理的にはハード制約になりうる
#:   ため。**``calibrated=False`` なので今は階層1の根拠にならない。**
#:   校正には少なくとも 2 つ要る: ①表の升目を取り違えていないか
#:   (罫線が途切れている表・セル内改行・続き表)②建具表に載っていない
#:   建具がどれだけあるか(表は「工事対象の建具」だけを載せることがある)。
#:   **合成の表でしか確かめていないので、実図面での誤り率は未知である。**
#: - ``pdf_dimension_text`` … 図面に**記入された寸法の数字**を、それが指す2点と
#:   一緒に読む(`axes/image_axis/pdf_dimensions.read_dimensions`)。上限を
#:   ``strong`` にしてあるのは ``pdf_text_area`` と同じ理由で、「印字された数値を
#:   そのまま読む」手法は原理的にはハード制約になりうるため。
#:   **``calibrated=False`` なので今は階層1の根拠にならない。**
#:   校正には少なくとも3つ要る: ①数字と寸法線の対応を取り違えていないか
#:   (寸法線を横切る線があると測る区間が短く取られる)②単位が書かれていない
#:   数字を mm と m で取り違えていないか(**1000 倍ずれる**)③寸法線に平行に
#:   文字が入っていない図面でどれだけ落ちるか。
#:   **合成 PDF でしか確かめていないので、実図面での誤り率は未知である。**
#: - ``pdf_dimension_scale`` … 記入された寸法どうしの一致から出したページの縮尺
#:   (`page_scale_from_dimensions`)。**表題欄の印字(`pdf_text_scale`)とは別の
#:   手法**にしてあるのは、原則3-1「図面に書かれた縮尺の表記は当てにしない」を
#:   守るために突き合わせる相手が要るから。上限は ``pdf_text_scale`` と同じ
#:   ``strong`` / **``calibrated=False``**。ただし**同じ PDF から読んでいるので、
#:   印字の縮尺とは独立なデータ源ではない**(`source_fingerprint` が同じ)。
#:   独立なのは人が入れた基準点(`human_reference_point`)との間だけである。
#: - ``pdf_table_finish_schedule`` … 内装仕上表から「室名・部位・仕上」の
#:   対応を読む。**この対応そのものは数量ではない**(室の輪郭を取る実装が
#:   無いので面積が出せない)。数量を出す経路ができるまでは証拠として
#:   渡されないが、将来つないだときに強い軸へ昇格しないよう
#:   ``max_strength="weak"`` で登録しておく。
DEFAULT_METHOD_POLICIES: Mapping[str, MethodPolicy] = {
    METHOD_WALL_LINEWORK: MethodPolicy(calibrated=True, max_strength="strong"),
    METHOD_FLOOR_AREA: MethodPolicy(calibrated=False, max_strength="weak"),
    METHOD_TEXT_AREA: MethodPolicy(calibrated=False, max_strength="strong"),
    METHOD_DOOR_ARC: MethodPolicy(calibrated=False, max_strength="weak"),
    METHOD_TEXT_SCALE: MethodPolicy(calibrated=False, max_strength="strong"),
    METHOD_HUMAN_REFERENCE_POINT: MethodPolicy(calibrated=False, max_strength="strong"),
    METHOD_DOOR_SCHEDULE: MethodPolicy(calibrated=False, max_strength="strong"),
    METHOD_FINISH_SCHEDULE: MethodPolicy(calibrated=False, max_strength="weak"),
    METHOD_DIMENSION_TEXT: MethodPolicy(calibrated=False, max_strength="strong"),
    METHOD_DIMENSION_SCALE: MethodPolicy(calibrated=False, max_strength="strong"),
}


def with_defaults(policies: Mapping[str, MethodPolicy]) -> dict[str, MethodPolicy]:
    """呼び出し側の方針を、登録簿の上限まで引き下げた辞書として返す。

    登録簿に無い手法はそのまま通す(`InferenceOrchestrator` 側で
    未登録手法として weak / 非校正に落ちる)。
    """
    merged = dict(DEFAULT_METHOD_POLICIES)
    for method_id, policy in policies.items():
        merged[method_id] = clamp_to_defaults(method_id, policy)
    return merged


def clamp_to_defaults(method_id: str, policy: MethodPolicy) -> MethodPolicy:
    """1件の方針を、登録簿の上限まで引き下げる。

    **引き上げは絶対にしない。** 登録簿に無い手法はそのまま返す。
    """
    ceiling = DEFAULT_METHOD_POLICIES.get(method_id)
    if ceiling is None:
        return policy
    return MethodPolicy(
        calibrated=policy.calibrated and ceiling.calibrated,
        max_strength=(
            "strong"
            if policy.max_strength == "strong" and ceiling.max_strength == "strong"
            else "weak"
        ),
        # 由来もきつくする方向にだけ効かせる。登録簿が「この手法は常に
        # 一般則による補完」と言っているなら、呼び出し側が何と名乗っても
        # その上限が勝つ。
        always_assumed=policy.always_assumed or ceiling.always_assumed,
    )
