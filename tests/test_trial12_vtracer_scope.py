"""トライアル12(VTracer の適用範囲を数量の種類ごとに分ける)の回帰テスト。

`docs/design_v8.md` 12章の訂正は「コードの変更ではなく、呼び出し側が
どの手法をどの強度で登録するかの方針」である。その方針が意味を持つのは、
ファイアウォールが次の3つの性質を保っている間だけなので、ここで縛る。

1. 同一図面に複数手法を並べても、それだけでは階層1(自動確定)にならない
   (相関した誤りを独立軸として重複加算しない)
2. 手法同士が食い違えば、そのデータ源ごとハード制約から降格する
3. 誤りが正解レンジと"重なる"場合、2の安全弁(積集合が空になること)は
   働かない。**この経路は当初コードでは止まらなかったが、2026-09-21 に
   中心値の一致を確定条件に加えて塞いだ**(v8 10章19項)。
   12-2節(2)の「VTracer を床面積の強い軸として登録しない」という登録側の
   規律は、二重の守りとして引き続き有効である。

1か2が壊れると、12-2節(1)の「複数手法を並行して走らせる」がそのまま
自動確定の水増しになる。3は「破れている」ことを固定するテストだったが、
修理されたので「塞がれている」ことを固定するテストに書き換えた。
"""

from __future__ import annotations

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall

#: 同じ1枚の図面。3手法はすべてこの画素を入力にしているので独立ではない。
_DRAWING = dict(
    source_id="drawing-trial12", axis_id="image", source_fingerprint="sha256:drawing"
)


def _pipe(method: str, rng: tuple[int, int]) -> AxisEvidence:
    return AxisEvidence(derivation="read", target="pipe_total", count_range=rng, method_id=method,
                        unit="mm", **_DRAWING)


def test_same_drawing_three_methods_never_auto_confirm() -> None:
    """性質1: 同一図面の3手法が一致しても階層1にはならない。

    全件 strength="strong" / calibrated=True で渡している。それでも
    独立データ源は1つなので、自動確定の条件(2つ以上)を満たさない。
    """
    decision = AxisQualityFirewall().assess([
        _pipe("vtracer", (12_000, 12_600)),
        _pipe("spline", (12_200, 12_900)),
        _pipe("classical_robust", (12_100, 12_700)),
    ])

    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.independent_strong_source_count == 1
    assert any("重複加算しない" in r for r in decision.reasons)
    assert any("自動確定しない" in r for r in decision.reasons)


def test_same_drawing_three_methods_still_narrow_the_range() -> None:
    """性質1の裏: 積集合は計算される。

    複数手法を並行して走らせる目的は確信度の上積みではなく、レンジの絞り込み
    (v8 12-2節(1))。絞り込みまで失われていないことを縛る。
    """
    decision = AxisQualityFirewall().assess([
        _pipe("vtracer", (12_000, 12_600)),
        _pipe("spline", (12_200, 12_900)),
        _pipe("classical_robust", (12_100, 12_700)),
    ])

    # max(下限) と min(上限)。どの単独手法のレンジよりも狭い。
    assert decision.confirmed_range == (12_200, 12_600)


def test_one_broken_method_demotes_the_whole_source() -> None:
    """性質2: 手法同士が食い違えば、そのデータ源ごとハードから外れる。"""
    decision = AxisQualityFirewall().assess([
        _pipe("vtracer", (20_000, 21_000)),  # 1つだけ大きく外れて重ならない
        _pipe("spline", (12_200, 12_900)),
        _pipe("classical_robust", (12_100, 12_700)),
    ])

    assert decision.tier == 3
    assert decision.confirmed_range is None
    assert decision.independent_strong_source_count == 0
    assert any("降格" in r for r in decision.reasons)


def test_overlapping_vtracer_floor_area_error_now_escalates() -> None:
    """性質3【2026-09-21 解消】: 重なる誤りが階層1で素通りしなくなった。

    **このテストは当初、直っていないことを固定するために書かれていた。**
    元の名前は `..._auto_confirms_unnoticed` で、「ここが直った(階層3に
    なる等)ときにこのテストが落ちることで、12-2節(2)の規律の根拠が
    変わったと気づける」という趣旨だった。**実際にそのとおりになったので
    書き換えた。**

    文章軸(別データ源)が正解 995000〜1005000 cm² を出しているところへ、
    62.49% 上振れした VTracer の床面積を強い軸として並べる。積集合は空に
    ならないので矛盾としては検出されないが、**中心値が 100.0 ㎡ 対
    131.25 ㎡ と離れている**ので、階層1の確定条件(v8 10章19項の対応)で
    止まるようになった。

    **12-2節(2)「VTracer 由来の床面積を階層1に使わない」という呼び出し側の
    規律は、引き続き有効である。** ただしその根拠は変わった。以前は
    ファイアウォールがこの誤りを止められないことが理由の一部だったが、
    今はファイアウォール側でも止まる。規律は二重の守りとして残す。
    """
    decision = AxisQualityFirewall().assess([
        AxisEvidence(derivation="read", target="floor_area", count_range=(1_000_000, 1_625_000),
                     source_id="drawing-trial12", axis_id="image",
                     method_id="vtracer_floor_area", unit="cm2",
                     source_fingerprint="sha256:drawing"),
        AxisEvidence(derivation="read", target="floor_area", count_range=(995_000, 1_005_000),
                     source_id="spec-sheet-trial12", axis_id="text",
                     method_id="spec_area", unit="cm2",
                     source_fingerprint="sha256:spec"),
    ])

    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "center_disagreement"
    assert any("中心値" in r for r in decision.reasons)


def test_vtracer_floor_area_as_advisory_does_not_auto_confirm() -> None:
    """12-2節(2)を守った場合の挙動。

    VTracer の床面積を参考情報(strength="weak")で登録すれば、上振れした
    レンジが確定値を動かすことはなく、文章軸1つでは階層1にも届かない。
    """
    decision = AxisQualityFirewall().assess([
        AxisEvidence(derivation="read", target="floor_area", count_range=(1_000_000, 1_625_000),
                     source_id="drawing-trial12", axis_id="image",
                     method_id="vtracer_floor_area", unit="cm2",
                     source_fingerprint="sha256:drawing", strength="weak"),
        AxisEvidence(derivation="read", target="floor_area", count_range=(995_000, 1_005_000),
                     source_id="spec-sheet-trial12", axis_id="text",
                     method_id="spec_area", unit="cm2",
                     source_fingerprint="sha256:spec"),
    ])

    assert decision.tier != 1
    assert decision.confirmed_range == (995_000, 1_005_000)
