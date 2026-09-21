"""トライアル15(2段階読み)のベンチマークが、測りたいものを測っているかの回帰テスト。

このトライアルで守らなければならない性質は4つある。
  1. 正解値は読み取り前に凍結してあり、後から動いていない。
  2. 読み手に渡る文章のどこにも正解値が書かれていない(ブラインド)。
  3. 方式Bの段階2には、概要ページが本当に渡っていない(情報制限が実在する)。
  4. 難易度3は「書き方」だけを変えており、数字の中身は難易度1〜2と同じ。
報告書に載せた数値そのものも、記録した生回答から採点し直して再現できることを確かめる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from benchmarks.run_two_stage_reading_eval import (
    KEY_PATH,
    _pages,
    is_correct,
    load_key,
    prompt_arm_a,
    prompt_arm_a2,
    prompt_arm_b_stage1,
    prompt_arm_b_stage2,
    relative_error,
    score,
)
from benchmarks.two_stage_reading_fixtures import (
    ALL_SETS,
    get_set,
    ground_truth,
    question_traps,
)
from benchmarks.two_stage_reading_hard import (
    SUPERSEDED_RULE,
    SUPERSEDED_SCOPE,
    hard_detail_pages,
    hard_overview_pages,
    hard_pages,
    superseded_overview_page,
    unlabelled_total_page,
)
from benchmarks.two_stage_reading_padding import (
    TARGET_PAGE_COUNT,
    padded_detail_pages,
    padded_pages,
)
from benchmarks.two_stage_reading_prose import (
    prose_detail_pages,
    prose_overview_pages,
    prose_pages,
)

DOCS = Path(__file__).resolve().parents[1] / "docs"
LEVELS = (1, 2, 3, 4)


def _numbers(text: str) -> set[str]:
    """文章に現れる数を、桁区切りのカンマを外して集める。

    行頭の箇条書き番号(「4. 工事対象範囲」の 4)は中身の数字ではないので落とす。
    散文版には番号付きの項目立てが無く、ここを数えると必ず差が出てしまう。
    """
    body = re.sub(r"(?m)^\s*\d+\.\s", "", text)
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", body)}


def _contains_number(text: str, value: float) -> bool:
    """value がその数として(他の数の一部としてではなく)text に現れるか。

    「18」が「180.00」や「18,000」の一部として当たるのを防ぐため、
    前後に数字・小数点・桁区切りのカンマが続かないことまで見る。
    """
    for form in {f"{value:g}", f"{value:.1f}", f"{value:.2f}", f"{value:.3f}"}:
        pattern = rf"(?<![0-9.])(?<!\d,){re.escape(form)}(?![0-9.])(?!,\d)"
        if re.search(pattern, text):
            return True
    return False


# --- 1. 正解値の凍結 ---------------------------------------------------------


def test_凍結した正解値がコード側の計算と一致する() -> None:
    frozen = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    computed = ground_truth()
    assert frozen.keys() == computed.keys()
    for qid, expected in computed.items():
        assert frozen[qid] == pytest.approx(expected), qid


def test_正解値は6セット3問の18個ある() -> None:
    key = load_key()
    assert len(key) == 18
    for case in ALL_SETS:
        for q in case.questions:
            assert q.qid in key


def test_設問の狙いは3種類で各6問ずつ() -> None:
    traps = question_traps()
    counts: dict[str, int] = {}
    for trap in traps.values():
        counts[trap] = counts.get(trap, 0) + 1
    assert counts == {"scope": 6, "condition": 6, "extra_rule": 6}


def test_基準寸法だけを問う設問は存在しない() -> None:
    """報告書5節7項で「基準寸法は単独で測っていない」と書いた根拠。

    ここが将来変わったら報告書の制約も直す必要があるので、固定しておく。
    """
    assert "datum" not in set(question_traps().values())


# --- 2. ブラインド性 ---------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_読み手に渡る文章に正解値が書かれていない(level: int) -> None:
    key = load_key()
    for case in ALL_SETS:
        all_pages, detail_pages = _pages(case, level)
        haystack = "\n".join(all_pages)
        for q in case.questions:
            expected = key[q.qid]
            if expected == 0:
                # 「撤去しない」の 0 は現況の記述から導く答えで、
                # 0 という数字自体は資料のあちこちに出てよい。
                continue
            assert not _contains_number(haystack, expected), (
                f"L{level} {q.qid} の正解 {expected} が資料に書かれている"
            )
        assert set(detail_pages) <= set(all_pages)


@pytest.mark.parametrize("level", LEVELS)
def test_設問文の生成は正解値に触れない(level: int, monkeypatch) -> None:
    """prompts 経路が ground_truth()/load_key() を呼ばないことを実際に確かめる。"""

    def 呼んではいけない(*_args, **_kwargs):  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("設問文の生成が正解値に触れた")

    import benchmarks.run_two_stage_reading_eval as ev

    monkeypatch.setattr(ev, "load_key", 呼んではいけない)
    monkeypatch.setattr(ev, "ground_truth", 呼んではいけない)
    for case in ALL_SETS:
        ev.prompt_arm_a(case, level=level)
        ev.prompt_arm_a2(case, level=level)
        ev.prompt_arm_b_stage1(case, level=level)
        ev.prompt_arm_b_stage2(case, {"基準寸法": "x"}, level=level)


def test_おとりの数字は資料に実際に書かれている() -> None:
    """おとりが資料に無ければ、罠として機能していない。"""
    for case in ALL_SETS:
        haystack = "\n".join(case.overview_pages + case.detail_pages)
        for name, value in case.decoys.items():
            assert _contains_number(haystack, value), f"{case.set_id} {name}"


def test_詰め物ページに正解値が紛れ込んでいない() -> None:
    key = load_key()
    for case in ALL_SETS:
        本編 = set(case.overview_pages + case.detail_pages)
        詰め物 = "\n".join(p for p in padded_pages(case) if p not in 本編)
        for q in case.questions:
            expected = key[q.qid]
            if expected == 0:
                continue
            assert not _contains_number(詰め物, expected), f"{q.qid}"


# --- 3. 方式Bの情報制限が実在すること ---------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_段階2には概要ページが渡らない(level: int) -> None:
    for case in ALL_SETS:
        all_pages, detail_pages = _pages(case, level)
        overview = [p for p in all_pages if p not in detail_pages]
        assert overview, f"L{level} {case.set_id}: 概要ページが無い"
        prompt = prompt_arm_b_stage2(
            case, {"基準寸法": "a", "工事対象範囲": "b", "現況": "c"}, level=level
        )
        for page in overview:
            assert page not in prompt


@pytest.mark.parametrize("level", LEVELS)
def test_段階1には詳細ページが渡らない(level: int) -> None:
    for case in ALL_SETS:
        all_pages, detail_pages = _pages(case, level)
        prompt = prompt_arm_b_stage1(case, level=level)
        for page in detail_pages:
            assert page not in prompt


@pytest.mark.parametrize("level", LEVELS)
def test_方式Aと方式A2は同じページを見る(level: int) -> None:
    """A2 との差は「3要素を先に書き出すかどうか」だけであってほしい。"""
    for case in ALL_SETS:
        all_pages, _ = _pages(case, level)
        a = prompt_arm_a(case, level=level)
        a2 = prompt_arm_a2(case, level=level)
        for page in all_pages:
            assert page in a and page in a2
        assert "3点" in a2 or "3つ" in a2
        assert "基準寸法" in a2


def test_知らない難易度は拒否される() -> None:
    with pytest.raises(ValueError):
        _pages(ALL_SETS[0], 9)


# --- 4. 難易度のかさ増しと散文化 ---------------------------------------------


def test_難易度2は24ページで重複が無く概要が先頭にある() -> None:
    for case in ALL_SETS:
        pages = padded_pages(case)
        assert len(pages) == TARGET_PAGE_COUNT
        assert len(set(pages)) == len(pages), f"{case.set_id}: 同じページが2回出る"
        assert pages[: len(case.overview_pages)] == case.overview_pages
        for page in case.detail_pages:
            assert page in pages
        assert set(padded_detail_pages(case)) == set(pages) - set(case.overview_pages)


def test_難易度3は概要の書き方だけを変え数字の中身は同じ() -> None:
    """報告書5節3項の「両方式に等しくかかる変更」を担保する。"""
    for case in ALL_SETS:
        箇条書き = _numbers("\n".join(case.overview_pages))
        散文 = _numbers("\n".join(prose_overview_pages(case)))
        assert 箇条書き == 散文, (
            f"{case.set_id}: 概要の数字が散文版でずれている "
            f"(箇条書きのみ {sorted(箇条書き - 散文)} / 散文のみ {sorted(散文 - 箇条書き)})"
        )


def test_散文の概要には3要素の見出し語が無い() -> None:
    """難易度3が「見出しの無い概要」になっていることを固定する。"""
    for case in ALL_SETS:
        text = "\n".join(prose_overview_pages(case))
        for 見出し in ("基準寸法", "工事対象範囲", "現況"):
            assert 見出し not in text, f"{case.set_id} に見出し {見出し} が残っている"


def test_難易度3の詳細ページは難易度2と同じ() -> None:
    """概要1ページを散文2ページに置き換えるので、全体は25ページになる。"""
    for case in ALL_SETS:
        pages = prose_pages(case)
        assert len(pages) == TARGET_PAGE_COUNT + 1
        assert len(set(pages)) == len(pages)
        assert set(prose_detail_pages(case)) == set(pages) - set(
            prose_overview_pages(case)
        )
        # 概要以外は難易度2と共通。
        assert set(prose_detail_pages(case)) == set(padded_detail_pages(case))


# --- 5. 採点の挙動 -----------------------------------------------------------


def test_個数の単位は完全一致を求める() -> None:
    assert is_correct("S2Q3", 28, 28.0, "台")
    assert not is_correct("S2Q3", 27, 28.0, "台")
    assert not is_correct("S6Q2", 1, 0.0, "箇所")


def test_面積と長さは相対1パーセントか絶対0_05まで許す() -> None:
    assert is_correct("S1Q1", 16.562, 16.562, "m2")
    assert is_correct("S1Q1", 16.60, 16.562, "m2")
    assert not is_correct("S1Q1", 17.5, 16.562, "m2")
    # 正解が 0 のときは絶対 0.05 が効く。
    assert is_correct("S2Q2", 0.02, 0.0, "m2")
    assert not is_correct("S2Q2", 12.0, 0.0, "m2")


def test_棄権は不正解として数え相対誤差は出さない() -> None:
    assert not is_correct("S1Q1", None, 16.562, "m2")
    assert relative_error(None, 16.562) is None


def test_採点は棄権と誤答を別々に数える() -> None:
    runs = [
        {"arm": "A", "level": 1, "set_id": "S1", "rep": 1,
         "answers": {"S1Q1": 16.562, "S1Q2": None, "S1Q3": 99.0}},
    ]
    summary = score(runs)["summary"]["by_arm"]["L1-A"]
    assert summary == {
        "n": 3, "correct": 1, "accuracy": 0.3333, "abstained": 1,
        "median_rel_error": summary["median_rel_error"],
    }


# --- 6. 報告書に載せた数値が生回答から再現すること ---------------------------


def test_記録した生回答を採点し直すと結果ファイルが再現する() -> None:
    """報告書2節の表は、この生回答から機械的に出たものでなければならない。"""
    runs = json.loads((DOCS / "trial15_two_stage_reading_runs.json").read_text("utf-8"))
    recorded = json.loads(
        (DOCS / "trial15_two_stage_reading_result.json").read_text("utf-8")
    )
    assert score(runs)["summary"] == recorded["summary"]


def test_生回答は6条件あり各54問ぶんある() -> None:
    runs = json.loads((DOCS / "trial15_two_stage_reading_runs.json").read_text("utf-8"))
    counts: dict[tuple[int, str], int] = {}
    for run in runs:
        key = (run["level"], run["arm"])
        counts[key] = counts.get(key, 0) + len(run["answers"])
    assert counts == {
        (1, "A"): 54, (1, "A2"): 54, (1, "B"): 54,
        (3, "A"): 54, (3, "A2"): 54, (3, "B"): 54,
    }


def test_段階1は全36本で確定できなかった要素を空と申告した() -> None:
    """報告書4節の出発点。ここが変わったら4節の記述も変わる。"""
    stage1 = json.loads(
        (DOCS / "trial15_two_stage_reading_stage1.json").read_text("utf-8")
    )
    assert len(stage1) == 36
    for row in stage1:
        assert not row["output"].get("確定できなかった要素"), row["set_id"]
        for 要素 in ("基準寸法", "工事対象範囲", "現況"):
            assert row["output"].get(要素), f"{row['set_id']} の {要素} が空"


def test_方式Bの負けは数え方の決まりの設問に集中している() -> None:
    """報告書2節・6節の結論の根拠。狙い別の内訳が変わったら落ちる。"""
    runs = json.loads((DOCS / "trial15_two_stage_reading_runs.json").read_text("utf-8"))
    items = score(runs)["items"]

    def 正答(level: int, arm: str, trap: str) -> tuple[int, int]:
        sel = [
            i for i in items
            if i["level"] == level and i["arm"] == arm and i["trap"] == trap
        ]
        return sum(1 for i in sel if i["correct"]), len(sel)

    for level in (1, 3):
        for trap in ("scope", "condition"):
            # 3要素そのものは方式Bも取りこぼしていない。
            assert 正答(level, "B", trap) == (18, 18), (level, trap)
    # 落ちたのは extra_rule だけで、難易度1では方式Aに大きく負ける。
    assert 正答(1, "A", "extra_rule") == (18, 18)
    assert 正答(1, "B", "extra_rule")[0] < 10
    # 方式A2(1段階のまま3要素を書き出す)は両難易度とも満点。
    assert 正答(1, "A2", "extra_rule") == (18, 18)
    assert 正答(3, "A2", "extra_rule") == (18, 18)


def test_方式Bは方式Aを上回っていない() -> None:
    """「効果があった」と誤って書き換えられないように、向きを固定する。"""
    runs = json.loads((DOCS / "trial15_two_stage_reading_runs.json").read_text("utf-8"))
    by_arm = score(runs)["summary"]["by_arm"]
    for level in (1, 3):
        assert by_arm[f"L{level}-B"]["accuracy"] <= by_arm[f"L{level}-A"]["accuracy"]


def test_方式Aは天井に張り付いており上振れの余地がほとんど無い() -> None:
    """報告書5節1項・6節2項の制約。これが崩れたら結論の書き方を変える必要がある。"""
    runs = json.loads((DOCS / "trial15_two_stage_reading_runs.json").read_text("utf-8"))
    by_arm = score(runs)["summary"]["by_arm"]
    for level in (1, 3):
        assert by_arm[f"L{level}-A"]["accuracy"] >= 0.98


# --- 7. 難易度4(方式Aの天井を壊すための条件。v8 10章23項) --------------------


def test_難易度4は40ページで重複が無い() -> None:
    for case in ALL_SETS:
        pages = hard_pages(case)
        assert len(pages) == 40
        assert len(set(pages)) == len(pages), f"{case.set_id}: 同じページが2回出る"
        for page in case.detail_pages:
            assert page in pages


def test_難易度4の概要は旧版と現行版の両方を含む() -> None:
    """旧版を概要から外すと、方式Bだけが版の判断を免除されて不公平になる。"""
    for case in ALL_SETS:
        overview = hard_overview_pages(case)
        assert len(overview) == 2
        assert overview[0] == superseded_overview_page(case)
        assert "第一版" in overview[0]
        assert "第二版" in overview[1]
        assert set(hard_detail_pages(case)) == set(hard_pages(case)) - set(overview)


def test_旧版は現行版と食い違っている() -> None:
    """食い違っていなければ、版を見分ける必要が無く難易度が上がらない。"""
    for case in ALL_SETS:
        old = superseded_overview_page(case)
        assert SUPERSEDED_SCOPE[case.set_id] in old
        assert SUPERSEDED_RULE[case.set_id] in old
        current = "\n".join(case.overview_pages)
        assert SUPERSEDED_SCOPE[case.set_id] not in current
        assert SUPERSEDED_RULE[case.set_id] not in current


def test_現行版が最新であることは資料から判定できる() -> None:
    """理不尽な罠にしない。版を読めば決着がつくこと。"""
    for case in ALL_SETS:
        assert "本書が最新版であり" in hard_overview_pages(case)[1]


def test_難易度4のおとりには但し書きが付いていない() -> None:
    """難易度1〜3のおとりはラベルで無効化されていた(報告書。10章23項)。"""
    for case in ALL_SETS:
        page = unlabelled_total_page(case)
        for label in ("参考", "別紙", "本工事の数量ではない", "過去"):
            assert label not in page, f"{case.set_id} のおとりに但し書き {label} が残っている"
        for value in case.decoys.values():
            assert _contains_number(page, value)


def test_難易度4でも正解値は変わっていない() -> None:
    """難しくしたのは資料の書き方だけで、答えは凍結したまま。"""
    frozen = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    assert frozen == {k: pytest.approx(v) for k, v in ground_truth().items()}


def test_難易度4の補足資料の番号は数字を使わない() -> None:
    """数字にすると、たまたま正解と同じ値になって資料に正解が現れる。

    実際に「補足資料 28」が S2Q3 の正解 28 と衝突した。
    """
    for case in ALL_SETS:
        for page in hard_pages(case):
            if page.startswith("【補足資料"):
                head = page.split("】")[0]
                assert not any(ch.isdigit() for ch in head), head


# ---------------------------------------------------------------------
# 難易度4の実測結果の固定(10章23項)
#
# 数値そのものを守りたいわけではない。守りたいのは
# **「報告書とv8に書いた数字が、採点をやり直しても同じになる」**ことである。
# 過去に、走らせていない件数を報告書に書いてしまった事故があった。
# ---------------------------------------------------------------------

LEVEL4_RUNS = Path(__file__).resolve().parents[1] / "docs" / "trial15_level4_runs.json"
LEVEL4_RESULT = Path(__file__).resolve().parents[1] / "docs" / "trial15_level4_result.json"


def _level4_summary() -> dict:
    """コミット済みの回答を採点し直した要約を返す。"""
    runs = json.loads(LEVEL4_RUNS.read_text(encoding="utf-8"))
    return score(runs)["summary"]


def test_難易度4を採点し直すと同じ要約になる() -> None:
    committed = json.loads(LEVEL4_RESULT.read_text(encoding="utf-8"))["summary"]
    assert _level4_summary() == committed, (
        "コミット済みの採点結果が、いまの採点器で再現しない。"
        "報告書・v8に書いた数字が実測と食い違っている疑いがある"
    )


def test_難易度4でも方式Aの天井は壊れていない() -> None:
    """**これは失敗の記録である。** 難易度4は方式Aを落とすために作ったが、
    落ちなかった。この事実を固定しておかないと、あとから
    「天井は壊した」と読み違えられる。"""
    assert _level4_summary()["by_arm"]["L4-A"]["accuracy"] == 1.0


def test_難易度4で方式A2が方式Aを下回った() -> None:
    """13章で段階0.5を方式A2の形で採用した根拠が弱くなったことの固定。

    難易度1・3では A2 = 54/54 で「害が無い」ことが採用根拠だった。
    難易度4では下回る。10章24項の判断待ち。
    """
    summary = _level4_summary()["by_arm"]
    assert summary["L4-A2"]["correct"] < summary["L4-A"]["correct"]


def test_難易度4でも方式Bは方式Aを上回っていない() -> None:
    summary = _level4_summary()["by_arm"]
    assert summary["L4-B"]["correct"] < summary["L4-A"]["correct"]


def test_方式Bの損失は数え方の決まりに集中している() -> None:
    """損失の在り処が「順序」ではなく「受け渡し様式」であることの固定。

    範囲・現況の設問はほぼ通るのに、3要素のどれでもない
    「数え方の決まり」だけが落ちる。難易度1と同じ形。
    """
    by_trap = _level4_summary()["by_arm_trap"]["L4-B"]
    assert by_trap["extra_rule"]["correct"] < by_trap["scope"]["correct"]
    assert by_trap["extra_rule"]["correct"] < by_trap["condition"]["correct"]


def test_方式Aは3つの罠すべてを取りこぼしていない() -> None:
    """対照。同じ資料・同じ設問なので、罠そのものが解けないわけではない。"""
    for trap, agg in _level4_summary()["by_arm_trap"]["L4-A"].items():
        assert agg["correct"] == agg["n"], trap


def test_段階1は損失を自己申告できていない() -> None:
    """**検査が働いていないことの固定。** 段階1は18本すべてが
    「確定できなかった要素は無い」と申告しているのに、段階2は落としている。
    自己申告を信頼の根拠に使えないことを、ここで明示しておく。
    """
    stage1 = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "trial15_level4_stage1.json")
        .read_text(encoding="utf-8")
    )
    assert len(stage1) == 18
    assert all(v.get("確定できなかった要素") == [] for v in stage1.values())
    assert _level4_summary()["by_arm"]["L4-B"]["accuracy"] < 1.0


def test_難易度4の回答に正解値が資料から漏れていない() -> None:
    """難易度4の資料に正解値が文字列として現れないこと(補足資料の通し番号を
    算用数字にしていたとき、S2Q3 の正解 28 が「補足資料 28」として漏れていた)。"""
    key = load_key()
    for case in ALL_SETS:
        material = "\n".join(hard_pages(case))
        for q in case.questions:
            assert not _contains_number(material, key[q.qid]), (
                f"{q.qid} の正解が難易度4の資料に現れている"
            )
