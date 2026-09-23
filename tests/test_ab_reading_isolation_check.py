"""読み方の比較実験の漏れ口検査のテスト。

**否定対照を必ず置く。** 「漏れを見つけた」テストだけだと、常に何か
見つけるだけの壊れた検査でも通ってしまう。4 経路それぞれについて、
漏れのある置き方と、漏れの無い置き方の両方を測る。
"""

from __future__ import annotations

import pathlib

import pytest

from benchmarks.ab_reading_isolation_check import (
    ANSWER_LOCATION_TERMS,
    EXPERIMENT_TERMS,
    VERIFIED_VALUE_TERMS,
    check_memory_unchanged,
    format_report,
    memory_fingerprint,
    run_all,
    scan_match_claims,
    scan_reading_method_verdicts,
    scan_memory,
    scan_package,
    scan_probe_transcript,
    scan_runs_dir,
    scan_shared_root,
)

PACKAGE_NAME = "ab_reading_input_P011"
RUNS_NAME = "ab_reading_runs"


def _clean_package(root: pathlib.Path) -> pathlib.Path:
    package = root / PACKAGE_NAME
    package.mkdir(parents=True)
    (package / "README.md").write_text(
        "設計図面 34 ページの文字・数字・表が入っています。\n", encoding="utf-8"
    )
    (package / "page_01.md").write_text("# 図面リスト\n- 文字数: 812\n", encoding="utf-8")
    (package / "出力の形.md").write_text("JSON を 1 つ作ります。\n", encoding="utf-8")
    return package


def _clean_shared(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    shared = tmp_path / "shared"
    shared.mkdir()
    package = _clean_package(shared)
    runs = shared / RUNS_NAME
    runs.mkdir()
    (runs / "README.md").write_text("答案はここに書く。\n", encoding="utf-8")
    return shared, package, runs


def _clean_memory(tmp_path: pathlib.Path) -> pathlib.Path:
    memory = tmp_path / "memory" / "team" / "silo"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(
        "日本語プロジェクト。図面から数量を拾う積算の設計と実装。\n", encoding="utf-8"
    )
    (memory / "jidou-sekisan-project.md").write_text(
        "リポジトリと環境の基礎。\n", encoding="utf-8"
    )
    return memory


# ---------------------------------------------------------------------------
# 経路 1: 渡すフォルダ
# ---------------------------------------------------------------------------
def test_clean_package_has_no_findings(tmp_path: pathlib.Path) -> None:
    package = _clean_package(tmp_path)
    assert scan_package(package) == []


def test_package_pointing_outside_is_caught(tmp_path: pathlib.Path) -> None:
    """1 回目の実行で実際に起きた漏れ方。作り方のスクリプトを同梱していた。"""
    package = _clean_package(tmp_path)
    (package / "作り方_build_package.py").write_text(
        "SRC = '/mnt/project-files/uploads/hearth/5d6591db'\n", encoding="utf-8"
    )
    findings = scan_package(package)
    assert findings, "外を指すパスを見落とした"
    assert any("uploads" in f.detail for f in findings)


def test_package_naming_the_answer_file_is_caught(tmp_path: pathlib.Path) -> None:
    package = _clean_package(tmp_path)
    (package / "notes.md").write_text(
        "正解は renovation-golden-001.json\n", encoding="utf-8"
    )
    findings = scan_package(package)
    assert any("renovation-golden-001" in f.detail for f in findings)


def test_package_may_contain_values_printed_on_the_drawing(
    tmp_path: pathlib.Path,
) -> None:
    """図面に印字されている面積は入力そのもの。漏れ口として挙げてはいけない。

    危ないのは値ではなく「この値は正解と一致した」という情報の方で、
    それは記憶と捨てスレッドの写しの側で検査する。
    """
    package = _clean_package(tmp_path)
    (package / "page_02.md").write_text(
        "専有延床面積 95.54 ㎡ / 施工床面積 90.61 ㎡\n", encoding="utf-8"
    )
    assert scan_package(package) == []


def test_missing_package_is_reported(tmp_path: pathlib.Path) -> None:
    findings = scan_package(tmp_path / "ない")
    assert len(findings) == 1
    assert "フォルダが無い" in findings[0].detail


# ---------------------------------------------------------------------------
# 経路 2: 前の回の答案
# ---------------------------------------------------------------------------
def test_empty_runs_dir_has_no_findings(tmp_path: pathlib.Path) -> None:
    _, _, runs = _clean_shared(tmp_path)
    assert scan_runs_dir(runs) == []


def test_leftover_run_output_is_caught(tmp_path: pathlib.Path) -> None:
    _, _, runs = _clean_shared(tmp_path)
    (runs / "P-1.json").write_text("{}", encoding="utf-8")
    findings = scan_runs_dir(runs)
    assert len(findings) == 1
    assert "P-1.json" in findings[0].detail


def test_absent_runs_dir_is_not_a_finding(tmp_path: pathlib.Path) -> None:
    """まだ作っていない答案置き場は漏れ口ではない。"""
    assert scan_runs_dir(tmp_path / "まだ無い") == []


# ---------------------------------------------------------------------------
# 経路 3: ファイル名の一覧
# ---------------------------------------------------------------------------
def test_clean_shared_root_has_no_findings(tmp_path: pathlib.Path) -> None:
    shared, _, _ = _clean_shared(tmp_path)
    assert scan_shared_root(shared, [PACKAGE_NAME, RUNS_NAME]) == []


def test_experiment_named_folder_is_caught(tmp_path: pathlib.Path) -> None:
    """中身を隠しても名前は見える。名前だけでも手がかりになる。"""
    shared, _, _ = _clean_shared(tmp_path)
    (shared / "ab_reading_answers").mkdir()
    findings = scan_shared_root(shared, [PACKAGE_NAME, RUNS_NAME])
    assert len(findings) == 1
    assert findings[0].where == "ab_reading_answers"


def test_allowed_names_are_not_flagged(tmp_path: pathlib.Path) -> None:
    """渡すフォルダ自身の名前は許可リストに入っているので挙がらない。"""
    shared, _, _ = _clean_shared(tmp_path)
    findings = scan_shared_root(shared, [PACKAGE_NAME, RUNS_NAME])
    assert [f.where for f in findings] == []


def test_unrelated_file_is_not_flagged(tmp_path: pathlib.Path) -> None:
    shared, _, _ = _clean_shared(tmp_path)
    (shared / "段階A実装検証報告書.md").write_text("報告\n", encoding="utf-8")
    assert scan_shared_root(shared, [PACKAGE_NAME, RUNS_NAME]) == []


# ---------------------------------------------------------------------------
# 経路 4: 記憶
# ---------------------------------------------------------------------------
def test_clean_memory_has_no_findings(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    assert scan_memory(memory) == []


def test_memory_with_verified_answer_value_is_caught(tmp_path: pathlib.Path) -> None:
    """1 回目の実行で実際に渡っていた漏れ方。照合済みの実数が書いてあった。"""
    memory = _clean_memory(tmp_path)
    (memory / "jidou-sekisan-eval.md").write_text(
        "独立に検証できたのは面積2値(専有延床95.54㎡・施工床90.61㎡、正解と一致)\n",
        encoding="utf-8",
    )
    findings = scan_memory(memory)
    details = " ".join(f.detail for f in findings)
    assert "95.54" in details and "90.61" in details


def test_memory_naming_the_experiment_is_caught(tmp_path: pathlib.Path) -> None:
    """中身を書かなくても、実験を名指しすれば手がかりになる。"""
    memory = _clean_memory(tmp_path)
    (memory / "jidou-sekisan-x.md").write_text(
        "読み方の比較実験の中身は記憶に書かない。\n", encoding="utf-8"
    )
    findings = scan_memory(memory)
    assert any("実験の手がかり" in f.detail for f in findings)


def test_memory_with_answer_file_name_is_caught(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    (memory / "jidou-sekisan-y.md").write_text(
        "正解は renovation-golden-001.json（99項目）。\n", encoding="utf-8"
    )
    findings = scan_memory(memory)
    assert any("renovation-golden-001" in f.detail for f in findings)


# ---------------------------------------------------------------------------
# 経路 4 の確かめ: 捨てスレッドの写し
# ---------------------------------------------------------------------------
def test_probe_transcript_without_hints_is_clean() -> None:
    assert scan_probe_transcript("記憶は入っていません。\n") == []


def test_probe_transcript_carrying_the_experiment_is_caught() -> None:
    """記憶が本当に届くかは、受け取る側に写させるしか確かめようがない。"""
    transcript = "私の文脈に入っている原文の写し: 読み方の比較実験の中身は…\n"
    findings = scan_probe_transcript(transcript)
    assert len(findings) == 1
    assert findings[0].channel == "捨てスレッドの写し"


def test_probe_transcript_carrying_the_answer_is_caught() -> None:
    findings = scan_probe_transcript("面積は 95.54 で正解と一致\n")
    assert any("95.54" in f.detail for f in findings)


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------
def test_run_all_is_clean_when_everything_is_clean(tmp_path: pathlib.Path) -> None:
    shared, package, runs = _clean_shared(tmp_path)
    memory = _clean_memory(tmp_path)
    findings = run_all(
        package_dir=package,
        runs_dir=runs,
        shared_root=shared,
        memory_dir=memory,
        allowed_shared_names=[PACKAGE_NAME, RUNS_NAME],
        probe_transcript="記憶は入っていません。",
    )
    assert findings == []
    assert "0 件" in format_report(findings)


def test_run_all_collects_every_channel(tmp_path: pathlib.Path) -> None:
    """4 経路すべてに漏れを置くと、4 経路すべてが挙がる。"""
    shared, package, runs = _clean_shared(tmp_path)
    memory = _clean_memory(tmp_path)
    (package / "作り方.py").write_text(
        "SRC='/mnt/project-files/uploads/hearth/x'\n", encoding="utf-8"
    )
    (runs / "Q-1.json").write_text("{}", encoding="utf-8")
    (shared / "ab_reading_answers").mkdir()
    (memory / "jidou-sekisan-z.md").write_text("正解 95.54\n", encoding="utf-8")

    findings = run_all(
        package_dir=package,
        runs_dir=runs,
        shared_root=shared,
        memory_dir=memory,
        allowed_shared_names=[PACKAGE_NAME, RUNS_NAME],
        probe_transcript="読み方の比較実験について聞きました",
    )
    channels = {f.channel for f in findings}
    assert channels == {
        "渡すフォルダ",
        "前の回の答案",
        "ファイル名の一覧",
        "記憶",
        "捨てスレッドの写し",
    }
    assert "5 件" in format_report(findings) or len(findings) >= 5


def test_probe_transcript_is_optional(tmp_path: pathlib.Path) -> None:
    """写しがまだ無い段階でも、残り 4 経路は検査できる。"""
    shared, package, runs = _clean_shared(tmp_path)
    memory = _clean_memory(tmp_path)
    assert (
        run_all(
            package_dir=package,
            runs_dir=runs,
            shared_root=shared,
            memory_dir=memory,
            allowed_shared_names=[PACKAGE_NAME, RUNS_NAME],
        )
        == []
    )


@pytest.mark.parametrize("term", ANSWER_LOCATION_TERMS + VERIFIED_VALUE_TERMS)
def test_every_answer_term_is_actually_checked(term: str, tmp_path: pathlib.Path) -> None:
    """禁止語の一覧に足しただけで検査されないことが無いように。"""
    memory = _clean_memory(tmp_path)
    (memory / "jidou-sekisan-probe.md").write_text(f"…{term}…\n", encoding="utf-8")
    assert any(term in f.detail for f in scan_memory(memory))


@pytest.mark.parametrize("term", EXPERIMENT_TERMS)
def test_every_experiment_term_is_actually_checked(
    term: str, tmp_path: pathlib.Path
) -> None:
    memory = _clean_memory(tmp_path)
    (memory / "jidou-sekisan-probe.md").write_text(f"…{term}…\n", encoding="utf-8")
    assert any(term in f.detail for f in scan_memory(memory))


# ---------------------------------------------------------------------------
# 記憶が写しを取ったときから変わっていないか
# ---------------------------------------------------------------------------
def test_fingerprint_is_stable_for_the_same_content(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    assert memory_fingerprint(memory) == memory_fingerprint(memory)


def test_fingerprint_changes_when_a_memory_changes(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    before = memory_fingerprint(memory)
    (memory / "MEMORY.md").write_text("書き換えた索引。\n", encoding="utf-8")
    assert memory_fingerprint(memory) != before


def test_fingerprint_changes_when_a_memory_is_added(tmp_path: pathlib.Path) -> None:
    """他のスレッドが記憶を1つ足しただけでも、前の写しは根拠にならない。"""
    memory = _clean_memory(tmp_path)
    before = memory_fingerprint(memory)
    (memory / "jidou-sekisan-new.md").write_text("新しい記憶。\n", encoding="utf-8")
    assert memory_fingerprint(memory) != before


def test_unchanged_memory_is_not_a_finding(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    assert check_memory_unchanged(memory, memory_fingerprint(memory)) == []


def test_changed_memory_asks_for_a_new_probe(tmp_path: pathlib.Path) -> None:
    memory = _clean_memory(tmp_path)
    stale = memory_fingerprint(memory)
    (memory / "jidou-sekisan-new.md").write_text("新しい記憶。\n", encoding="utf-8")
    findings = check_memory_unchanged(memory, stale)
    assert len(findings) == 1
    assert "写しを取り直すこと" in findings[0].detail


def test_run_all_flags_stale_probe(tmp_path: pathlib.Path) -> None:
    shared, package, runs = _clean_shared(tmp_path)
    memory = _clean_memory(tmp_path)
    stale = memory_fingerprint(memory)
    (memory / "jidou-sekisan-new.md").write_text("新しい記憶。\n", encoding="utf-8")
    findings = run_all(
        package_dir=package,
        runs_dir=runs,
        shared_root=shared,
        memory_dir=memory,
        allowed_shared_names=[PACKAGE_NAME, RUNS_NAME],
        probe_transcript="記憶は入っていません。",
        expected_memory_fingerprint=stale,
    )
    assert [f.channel for f in findings] == ["記憶"]


def test_run_all_without_fingerprint_does_not_check_it(tmp_path: pathlib.Path) -> None:
    shared, package, runs = _clean_shared(tmp_path)
    memory = _clean_memory(tmp_path)
    assert (
        run_all(
            package_dir=package,
            runs_dir=runs,
            shared_root=shared,
            memory_dir=memory,
            allowed_shared_names=[PACKAGE_NAME, RUNS_NAME],
        )
        == []
    )


# ---------------------------------------------------------------------------
# 「どの数量が正解と一致したか」— 数値を伏せても残っていた手がかり
#
# 2026-09-22、数値を消したあとに捨てスレッドへ聞いたところ、数値は知らないと
# 答えた一方で、照合の結果そのものは知っていた。そこを検査に足した。
# **見つけるテストだけでは足りない**ので、無害な「一致」も並べて置く。
# ---------------------------------------------------------------------------
REAL_MATCH_CLAIMS = [
    # 記憶に実際に書かれていた 1 行（数値は伏せてある）
    "専有延床面積 / 施工床面積 の2値も取得でき、**ゴールデンの input_profile と完全一致**",
    "図面から読んだ面積は正解と一致していた",
    "見積の行のうち3件が正解に一致した",
    "この案件の建具は正解に対して全部的中した",
]


def test_known_blind_spot_match_claim_without_golden_word() -> None:
    """**見つけられないものを、見つけられないと書いておく。**

    検査は「正解を指す語」と「一致したと述べる語」の同居で探すので、
    正解を指す語が無い言い方（「全部的中した」「3/3 と 0/3 に分かれた」）は
    素通りする。語を減らすと、無害な「一致」を毎回拾う壊れた検査になるため、
    ここは**広げずに、決まりの側（記憶に書かない）で防ぐ**ことにした。
    この行が落ちたら、検査を広げたということなので、
    否定対照が全部通っているかを必ず見直すこと。
    """
    assert scan_match_claims("この案件の建具は全部的中した") == []
    assert scan_match_claims("上の3項目は 3/3 と 0/3 に分かれた") == []


@pytest.mark.parametrize("line", REAL_MATCH_CLAIMS)
def test_match_claim_is_found(line: str) -> None:
    assert scan_match_claims(line), f"照合結果を見逃した: {line}"


# 否定対照。**無害な「一致」を漏れ口と呼ばないこと。**
# ここが落ちると、検査は「常に何か見つける壊れた検査」になる。
HARMLESS_LINES = [
    # Z3 の再現性（正解とは無関係）
    "同じ入力を解いても核が毎回違った。新しい Context を作って同一入力20回で完全一致。",
    # OCR の 2 エンジン照合（正解とは無関係）
    "2モデルが一致した語だけを通すと、通った語の誤りは0件。",
    # 正解が無いときの扱いを述べているだけ
    "正解の無い対象を「一致」と数えると的中率が常に100%になる。",
    # リポジトリを指すだけの行（これが漏れ口になっては困る）
    "（突き合わせの結果はリポジトリの `docs/real_drawing_eval_report.md` 11節。記憶には書かない）",
    # 図面に何が印字されているかの話（読む側は図面を持っている）
    "図面には床面積が2つ書いてある（専有延床面積と施工床面積）。",
]


@pytest.mark.parametrize("line", HARMLESS_LINES)
def test_harmless_line_is_not_flagged(line: str) -> None:
    assert not scan_match_claims(line), f"無害な行を漏れ口と呼んだ: {line}"


def test_match_claim_reaches_memory_scan(tmp_path: pathlib.Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "note.md").write_text(
        "床面積はゴールデンの input_profile と完全一致した。\n", encoding="utf-8"
    )
    findings = scan_memory(memory)
    assert findings and any("照合結果" in f.detail for f in findings)


def test_match_claim_reaches_probe_scan() -> None:
    findings = scan_probe_transcript("読み取った面積は正解と一致していた。")
    assert findings and any("届いている" in f.detail for f in findings)


def test_clean_memory_has_no_match_claim(tmp_path: pathlib.Path) -> None:
    """否定対照: 掃除ずみの書き方なら 0 件。"""
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "note.md").write_text(
        "床面積の数値も文字として取得できた。"
        "（突き合わせの結果はリポジトリの `docs/real_drawing_eval_report.md` 11節。"
        "記憶には書かない）\n",
        encoding="utf-8",
    )
    assert scan_memory(memory) == []


# ---------------------------------------------------------------------------
# 「どちらの読み方が良いか」— 索引にも書いてあった
#
# 2026-09-22、正解の数値も照合結果も消したあとに `memory_recall` の記録を
# 1 回ごとに突き合わせて、やっと気づいた漏れ口。片側の回にだけ一方のやり方の
# 成否が渡っており、さらに**索引の 1 行**にどちらを既定にしたかが書いてあった。
# ---------------------------------------------------------------------------
REAL_VERDICT_LINES = [
    # 索引に実際に書かれていた 1 行
    "段階0.5(方式A2)→ `axes/reading/protocol.py`。既定は方式A。A2は害が無いことは示せたが利点は示せていない",
    "2段階読みは精度を上げず、下げた。",
    "方式B は 1 段階読みを一度も上回っていない",
    "どちらの読み方が良いかは測って決めた",
]


@pytest.mark.parametrize("line", REAL_VERDICT_LINES)
def test_reading_method_verdict_is_found(line: str) -> None:
    assert scan_reading_method_verdicts(line), f"優劣の記述を見逃した: {line}"


# 否定対照。**手順や原則は外さない。**
# 手順の知識は片方の条件をもう片方に近づける＝差を小さくする向きに効くので、
# それでも差が出たなら本物である。ここを拾い始めたら検査は使えなくなる。
HARMLESS_READING_LINES = [
    # 手順そのもの（残す側）
    "要素ごとの推論3段階（区分・目的との照合・波及）の原則",
    "人の入力は「読み方の軸」を足すものであり、「読む範囲」を狭めるものではない",
    "人が宣言したページの種類を「読み方を縛るもの」から「弱い手がかり」に変えた",
    # 掃除ずみの指し方（これを拾っては困る）
    "採否と、測定の結果はリポジトリの `docs/trial15_two_stage_reading_report.md`(記憶には書かない)",
    "測定の結果は、ひとつ残らずリポジトリの報告書にある。記憶には書かない",
    # 読み方と関係ない「良かった」
    "ベクター経由の記号検出は結果が良かった",
    # 読み方と関係ない「既定は」
    "`drawing_mm_per_pixel()` は既定で None",
]


@pytest.mark.parametrize("line", HARMLESS_READING_LINES)
def test_harmless_reading_line_is_not_flagged(line: str) -> None:
    assert not scan_reading_method_verdicts(line), f"無害な行を漏れ口と呼んだ: {line}"


def test_index_file_is_scanned(tmp_path: pathlib.Path) -> None:
    """**索引そのものが検査に掛かること。**

    2026-09-22 に見落としたのはここだった。個別の記憶ファイルだけを見ていて、
    索引（`MEMORY.md`）に 1 行書いてあるのに気づかなかった。
    索引は全文が毎回どのセッションにも渡るので、**一番危ない 1 ファイル**である。
    """
    memory = tmp_path / "memory" / "team" / "silo"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(
        "2. 段階0.5(方式A2)。既定は方式A。\n", encoding="utf-8"
    )
    findings = scan_memory(tmp_path / "memory")
    assert findings, "索引が検査されていない"
    assert any(f.where == "MEMORY.md" for f in findings)


def test_reading_method_verdict_reaches_probe_scan() -> None:
    findings = scan_probe_transcript("2段階読みは精度を下げた。")
    assert findings and any("届いている" in f.detail for f in findings)


def test_clean_index_line_is_not_flagged(tmp_path: pathlib.Path) -> None:
    """否定対照: 掃除ずみの索引なら 0 件。"""
    memory = tmp_path / "memory" / "team" / "silo"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(
        "2. 段階0.5(方式A2)→ `axes/reading/protocol.py`。"
        "採否と、測定の結果はリポジトリの`docs/trial15_two_stage_reading_report.md`"
        "(記憶には書かない)\n",
        encoding="utf-8",
    )
    assert scan_memory(tmp_path / "memory") == []
