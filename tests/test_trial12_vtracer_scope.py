"""トライアル12(VTracer の適用範囲を数量の種類ごとに分ける)の回帰テスト。

`docs/design_v8.md` 12章の訂正は「コードの変更ではなく、呼び出し側が
どの手法をどの強度で登録するかの方針」である。その方針が意味を持つのは、
ファイアウォールが次の3つの性質を保っている間だけなので、ここで縛る。

1. 同一図面に複数手法を並べても、それだけでは階層1(自動確定)にならない
   (相関した誤りを独立軸として重複加算しない)
2. 手法同士が食い違えば、そのデータ源ごとハード制約から降格する
3. **ただし誤りが正解レンジと"重なる"場合、2の安全弁は働かない。**
   v8 10章19項の未解決問題が、床面積という具体的な数量で現れる経路。
   **この経路はコードでは止まらない**ので、12-2節(2)の「VTracer を床面積の
   強い軸として登録しない」という登録側の規律が必要になる。
   3が「守られている」ことではなく「破れている」ことを固定するテストである。

1か2が壊れると、12-2節(1)の「複数手法を並行して走らせる」がそのまま
自動確定の水増しになる。3が(修理されて)変われば、12-2節(2)の根拠が変わる。
"""

from __future__ import annotations

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall

#: 同じ1枚の図面。3手法はすべてこの画素を入力にしているので独立ではない。
_DRAWING = dict(
    source_id="drawing-trial12", axis_id="image", source_fingerprint="sha256:drawing"
)


def _pipe(method: str, rng: tuple[int, int]) -> AxisEvidence:
    return AxisEvidence(target="pipe_total", count_range=rng, method_id=method,
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


def test_overlapping_vtracer_floor_area_error_auto_confirms_unnoticed() -> None:
    """性質3【未解決】: 重なる誤りは止まらない。v8 10章19項・12-4節 実測3。

    文章軸(別データ源)が正解 995000〜1005000 cm² を出しているところへ、
    62.49% 上振れした VTracer の床面積を強い軸として並べる。積集合は空に
    ならないので矛盾として検出されず、**確定値が正解レンジの上端へ
    引きずられたまま階層1で自動確定する。**

    これは望ましい挙動ではない。ここが直った(階層3になる等)ときに
    このテストが落ちることで、12-2節(2)の規律の根拠が変わったと気づける。
    """
    decision = AxisQualityFirewall().assess([
        AxisEvidence(target="floor_area", count_range=(1_000_000, 1_625_000),
                     source_id="drawing-trial12", axis_id="image",
                     method_id="vtracer_floor_area", unit="cm2",
                     source_fingerprint="sha256:drawing"),
        AxisEvidence(target="floor_area", count_range=(995_000, 1_005_000),
                     source_id="spec-sheet-trial12", axis_id="text",
                     method_id="spec_area", unit="cm2",
                     source_fingerprint="sha256:spec"),
    ])

    assert decision.tier == 1
    assert decision.action == "auto_confirm"
    # 正解の中心(1000000)ではなく、上端に寄った値で確定してしまう。
    assert decision.confirmed_range == (1_000_000, 1_005_000)


def test_vtracer_floor_area_as_advisory_does_not_auto_confirm() -> None:
    """12-2節(2)を守った場合の挙動。

    VTracer の床面積を参考情報(strength="weak")で登録すれば、上振れした
    レンジが確定値を動かすことはなく、文章軸1つでは階層1にも届かない。
    """
    decision = AxisQualityFirewall().assess([
        AxisEvidence(target="floor_area", count_range=(1_000_000, 1_625_000),
                     source_id="drawing-trial12", axis_id="image",
                     method_id="vtracer_floor_area", unit="cm2",
                     source_fingerprint="sha256:drawing", strength="weak"),
        AxisEvidence(target="floor_area", count_range=(995_000, 1_005_000),
                     source_id="spec-sheet-trial12", axis_id="text",
                     method_id="spec_area", unit="cm2",
                     source_fingerprint="sha256:spec"),
    ])

    assert decision.tier != 1
    assert decision.confirmed_range == (995_000, 1_005_000)
