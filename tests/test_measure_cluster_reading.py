"""周1「まとまりで読む」の測定の道具の試験。

**合成データだけを使う。**実図面は読まない(取り決め④)。
基準は `docs/loop_round1_cluster_reading_criteria.md`。
"""

from __future__ import annotations

import pytest

from benchmarks.measure_cluster_reading import (
    MAX_EXTENT_HEIGHTS,
    MIN_TABLE_MATCH_LENGTH,
    NEIGHBOUR_HEIGHTS,
    Cluster,
    Word,
    _box_gap,
    _segment_length,
    build_clusters,
    meaning_of_word,
    normalize,
)


def word(x0: float, y0: float, text: str, *, height: float = 10.0, width: float = 20.0) -> Word:
    return Word((x0, y0, x0 + width, y0 + height), text)


# ---------------------------------------------------------------------------
# そろえ方
# ---------------------------------------------------------------------------


def test_そろえ方は全角と記号を吸収する() -> None:
    assert normalize("ＷＤ－５") == "wd5"
    assert normalize("(あ い)") == "あい"


def test_そろえ方は空を空のまま返す() -> None:
    assert normalize("   ") == ""


# ---------------------------------------------------------------------------
# 箱の距離
# ---------------------------------------------------------------------------


def test_重なっている箱の距離は0() -> None:
    assert _box_gap((0, 0, 10, 10), (5, 5, 15, 15)) == 0.0


def test_離れている箱の距離は縦横の隙間から出る() -> None:
    assert _box_gap((0, 0, 10, 10), (13, 0, 20, 10)) == pytest.approx(3.0)


def test_線の長さ() -> None:
    assert _segment_length(((0.0, 0.0), (3.0, 4.0))) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# まとまりの作り方
# ---------------------------------------------------------------------------


def test_近い文字は同じまとまりになる() -> None:
    words = [word(0, 0, "あ"), word(25, 0, "い")]
    clusters = build_clusters(words, [], [], heights=NEIGHBOUR_HEIGHTS)
    assert len(clusters) == 1


def test_遠い文字は別のまとまりになる() -> None:
    words = [word(0, 0, "あ"), word(200, 0, "い")]
    clusters = build_clusters(words, [], [], heights=NEIGHBOUR_HEIGHTS)
    assert len(clusters) == 2


def test_しきいは文字の高さの倍数なので大きい文字ほど遠くまで繋がる() -> None:
    """**紙の上の長さで持たないので、縮尺が変わっても同じしきいで動く。**"""
    small = [word(0, 0, "あ", height=5, width=5), word(16, 0, "い", height=5, width=5)]
    large = [word(0, 0, "あ", height=50, width=5), word(16, 0, "い", height=50, width=5)]
    assert len(build_clusters(small, [], [], heights=2.0)) == 2
    assert len(build_clusters(large, [], [], heights=2.0)) == 1


def test_近さは連鎖する() -> None:
    """**これが1回目の測定で出た落ちどころそのものである。**"""
    words = [word(i * 25, 0, "あ") for i in range(6)]
    clusters = build_clusters(words, [], [], heights=NEIGHBOUR_HEIGHTS)
    assert len(clusters) == 1
    assert len(clusters[0].words) == 6


def test_上限を置くと連鎖が止まる() -> None:
    words = [word(i * 25, 0, "あ") for i in range(6)]
    clusters = build_clusters(words, [], [], heights=NEIGHBOUR_HEIGHTS, max_extent=3.0)
    assert len(clusters) > 1
    assert max(len(c.words) for c in clusters) < 6


def test_上限を置いても近い2つは繋がる() -> None:
    words = [word(0, 0, "あ"), word(25, 0, "い")]
    clusters = build_clusters(words, [], [], heights=NEIGHBOUR_HEIGHTS, max_extent=MAX_EXTENT_HEIGHTS)
    assert len(clusters) == 1


def test_線は近いまとまりに入る() -> None:
    words = [word(0, 0, "あ")]
    near = ((0.0, 15.0), (50.0, 15.0))
    far = ((0.0, 500.0), (50.0, 500.0))
    clusters = build_clusters(words, [near, far], [], heights=NEIGHBOUR_HEIGHTS)
    assert clusters[0].segments == [near]


# ---------------------------------------------------------------------------
# 意味の当て方(**裏が取れたものだけ**)
# ---------------------------------------------------------------------------


def _meaning(w: Word, cluster: Cluster | None, **kw):
    kw.setdefault("mm_per_point", 1.0)
    kw.setdefault("door_cells", set())
    kw.setdefault("finish_cells", set())
    kw.setdefault("segments", [])
    kw.setdefault("min_table_length", MIN_TABLE_MATCH_LENGTH)
    return meaning_of_word(w, cluster, **kw)


def test_寸法は近くの線の長さと合ったときだけ当たる() -> None:
    w = word(0, 0, "100")
    cluster = Cluster(words=[w], segments=[((0.0, 20.0), (100.0, 20.0))])
    assert _meaning(w, cluster) == {"寸法"}


def test_寸法は線の長さが合わなければ当たらない() -> None:
    w = word(0, 0, "100")
    cluster = Cluster(words=[w], segments=[((0.0, 20.0), (50.0, 20.0))])
    assert _meaning(w, cluster) == set()


def test_寸法は許容差5パーセントの内と外で分かれる() -> None:
    w = word(0, 0, "100")
    inside = Cluster(words=[w], segments=[((0.0, 0.0), (104.0, 0.0))])
    outside = Cluster(words=[w], segments=[((0.0, 0.0), (106.0, 0.0))])
    assert _meaning(w, inside) == {"寸法"}
    assert _meaning(w, outside) == set()


def test_単独では寸法は当たらない() -> None:
    """**まとまりが無ければ、数字だけでは何の寸法か決まらない。**"""
    w = word(0, 0, "100")
    assert _meaning(w, None) == set()


def test_表の升目に当たれば単独でも当たる() -> None:
    w = word(0, 0, "WD-5")
    assert _meaning(w, None, door_cells={"wd5"}) == {"建具の記号"}


def test_2文字以下は表の升目に当たっても数えない() -> None:
    """**追記4。1〜2 文字は表のどこかに必ず出てくるので証拠にならない。**"""
    w = word(0, 0, "W1")
    assert _meaning(w, None, door_cells={"w1"}) == set()
    assert _meaning(w, None, door_cells={"w1"}, min_table_length=1) == {"建具の記号"}


def test_通り芯は円の内側で線の端に付いているときだけ当たる() -> None:
    w = word(10, 10, "X", height=10, width=10)
    circle = (5.0, 5.0, 25.0, 25.0)
    cluster = Cluster(words=[w], circles=[circle])
    on_line = [((15.0, 15.0), (15.0, 300.0))]
    assert _meaning(w, cluster, segments=on_line) == {"通り芯"}
    assert _meaning(w, cluster, segments=[((500.0, 500.0), (600.0, 600.0))]) == set()


def test_円の外にある文字は通り芯にならない() -> None:
    w = word(100, 100, "X", height=10, width=10)
    cluster = Cluster(words=[w], circles=[(5.0, 5.0, 25.0, 25.0)])
    assert _meaning(w, cluster, segments=[((15.0, 15.0), (15.0, 300.0))]) == set()


def test_1つの文字に2つの意味が当たることがある() -> None:
    """**線3 が数えているのはこれである。**"""
    w = word(0, 0, "100")
    cluster = Cluster(words=[w], segments=[((0.0, 20.0), (100.0, 20.0))])
    assert _meaning(w, cluster, door_cells={"100"}) == {"寸法", "建具の記号"}
