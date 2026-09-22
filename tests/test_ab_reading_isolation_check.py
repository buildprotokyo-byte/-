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
    format_report,
    run_all,
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
