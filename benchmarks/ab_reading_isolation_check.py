"""読み方の比較実験 — 実行の前後に通す漏れ口の検査。

**なぜこれがあるか**

2026-09-22 の 1 回目の実行で、読む側のスレッドに渡ってはいけないものが
2 つの経路で渡っていた。

1. 渡すフォルダの中に置いたままだったスクリプトに、正解のあるフォルダの
   パスが書いてあった。
2. **記憶（memory）の本文が、ハーネスによって自動で読む側の文脈に入っていた。**
   その中に実験の狙い、正解ファイルの名前と置き場所、**正解と一致することが
   確認済みの面積 2 値**が書かれていた。

どちらも「指示で禁止する」では防げない。**毎回、機械で確かめる。**
決まりと手順は `docs/experiments/ab_reading/isolation_checklist.md`。

**使い方**

    python3 -m benchmarks.ab_reading_isolation_check \
        --package /mnt/project-files/ab_reading_input_P011 \
        --runs-dir /mnt/project-files/ab_reading_runs \
        --shared-root /mnt/project-files \
        --memory-dir /tmp/claude/memory

終了コードは、漏れ口が 1 つも無ければ 0、あれば 1。

**この検査が見ないもの**

- 読む側が実際に何を開いたかは、実行の**後**にセッションの記録
  （`list_events` の `tool_use` の入力）と突き合わせる。ここでは見られない。
- 本気で探しに行くスレッドから隠し切ることはできない。
  この検査は「うっかり渡ってしまう」ものを止めるためのもの。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import pathlib
import re
import sys
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# 禁止語
# ---------------------------------------------------------------------------
# **この一覧そのものはリポジトリに置いてよい。** 読む側はリポジトリを取り込まない。
# 記憶（どのスレッドに渡るか選べない場所）に置いてはいけない語を並べる。

# 正解の在り処を指す語。**どの経路でも書いてはいけない。**
ANSWER_LOCATION_TERMS: tuple[str, ...] = (
    "renovation-golden-001",
    "uploads/hearth",
)

# 正解と照合済みであることが分かっている実数。
#
# **渡すフォルダは検査しない。**この 2 値は図面そのものに印字されている
# （物件概要のページ）ので、入力に出てくるのは当たり前である。
# 危ないのは値そのものではなく、**「この値は正解と一致した」という情報**の方で、
# それが書かれ得るのは記憶と、捨てスレッドの写しである。
# （この切り分けは、検査を実地で 1 回通して気づいた。渡すフォルダまで
# 検査していたときは、図面の印字を漏れ口として挙げてしまっていた。）
VERIFIED_VALUE_TERMS: tuple[str, ...] = (
    "95.54",
    "90.61",
)

# この実験の存在・狙い・条件を明かす語。
EXPERIMENT_TERMS: tuple[str, ...] = (
    "ab_reading",
    "ab-reading",
    "読み方の比較実験",
    "全ページ平読み",
    "2 つの読み方",
    "2つの読み方",
)

# 渡すフォルダの中のファイルが指してはいけない場所。
OUTSIDE_PATH_PATTERNS: tuple[str, ...] = (
    r"/mnt/project-files/uploads",
    r"/mnt/project-files/\.sealed",
    r"/home/user/",
    r"docs/experiments/ab_reading",
)

TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".csv", ".yaml", ".yml"}


@dataclasses.dataclass(frozen=True)
class Finding:
    """見つかった漏れ口 1 件。"""

    channel: str  # どの経路か
    where: str  # どのファイル・どの場所か
    detail: str  # 何が見つかったか

    def __str__(self) -> str:  # pragma: no cover - 表示のため
        return f"[{self.channel}] {self.where}: {self.detail}"


def _iter_text_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - 読めないファイルは事実として出す
        return f"<<読めなかった: {exc}>>"


# ---------------------------------------------------------------------------
# 経路 1: 渡すフォルダの中身
# ---------------------------------------------------------------------------
def scan_package(package_dir: pathlib.Path) -> list[Finding]:
    """渡すフォルダの中に、外を指すパスや正解の手がかりが無いか。"""
    findings: list[Finding] = []
    if not package_dir.exists():
        return [Finding("渡すフォルダ", str(package_dir), "フォルダが無い")]

    for path in _iter_text_files(package_dir):
        text = _read(path)
        rel = path.relative_to(package_dir)
        for pattern in OUTSIDE_PATH_PATTERNS:
            for match in re.finditer(pattern, text):
                findings.append(
                    Finding(
                        "渡すフォルダ",
                        str(rel),
                        f"外を指すパスが書かれている: {match.group(0)}",
                    )
                )
        for term in ANSWER_LOCATION_TERMS:
            if term in text:
                findings.append(
                    Finding("渡すフォルダ", str(rel), f"正解の在り処: {term}")
                )
    return findings


# ---------------------------------------------------------------------------
# 経路 2: 前の回の答案
# ---------------------------------------------------------------------------
def scan_runs_dir(runs_dir: pathlib.Path) -> list[Finding]:
    """答案の置き場に、前の回の答案が残っていないか。

    次のスレッドを立てる前に必ず空になっていること。
    共有フォルダのファイル**名**の一覧は自動で文脈に入るので、
    名前が残っているだけでも手がかりになる。
    """
    if not runs_dir.exists():
        return []
    leftovers = sorted(p.name for p in runs_dir.glob("*.json"))
    return [
        Finding("前の回の答案", str(runs_dir), f"引き上げ忘れ: {name}")
        for name in leftovers
    ]


# ---------------------------------------------------------------------------
# 経路 3: 共有フォルダのファイル名の一覧
# ---------------------------------------------------------------------------
def scan_shared_root(
    shared_root: pathlib.Path, allowed_names: Sequence[str]
) -> list[Finding]:
    """共有フォルダの直下に、渡すもの以外の実験関連の名前が無いか。

    ファイル名の一覧はハーネスが自動で文脈に入れるので、**名前だけで手がかりになる。**
    """
    if not shared_root.exists():
        return [Finding("ファイル名の一覧", str(shared_root), "フォルダが無い")]

    allowed = set(allowed_names)
    findings: list[Finding] = []
    for entry in sorted(shared_root.iterdir()):
        if entry.name in allowed:
            continue
        lowered = entry.name.lower()
        for term in EXPERIMENT_TERMS + ANSWER_LOCATION_TERMS:
            if term.lower() in lowered:
                findings.append(
                    Finding(
                        "ファイル名の一覧",
                        entry.name,
                        f"名前が手がかりになる: {term}",
                    )
                )
                break
    return findings


# ---------------------------------------------------------------------------
# 経路 4: 記憶の本文
# ---------------------------------------------------------------------------
def scan_memory(memory_dir: pathlib.Path) -> list[Finding]:
    """記憶に、正解そのものと実験の手がかりが書かれていないか。

    **記憶は、どのスレッドに渡るかをこちらで選べない置き場所である。**
    ハーネスが仕事の中身に意味が近いものを自動で選んで渡す。本文も渡る
    （索引は全文が渡る）ので、書いてあるものは読む側に届くと考える。
    """
    findings: list[Finding] = []
    if not memory_dir.exists():
        return [Finding("記憶", str(memory_dir), "フォルダが無い")]

    for path in _iter_text_files(memory_dir):
        text = _read(path)
        for term in ANSWER_LOCATION_TERMS + VERIFIED_VALUE_TERMS:
            if term in text:
                findings.append(
                    Finding("記憶", path.name, f"正解そのもの、または在り処: {term}")
                )
        for term in EXPERIMENT_TERMS:
            if term in text:
                findings.append(
                    Finding("記憶", path.name, f"実験の手がかり: {term}")
                )
    return findings


# ---------------------------------------------------------------------------
# 経路 4 の確かめ: 捨てスレッドが写し出した文脈
# ---------------------------------------------------------------------------
def scan_probe_transcript(transcript: str) -> list[Finding]:
    """捨てスレッドに「いま自分の中に入っているもの」を写させた文章を検査する。

    記憶の本文が本当に渡るかどうかは、記憶の側のファイルを見ても分からない
    （`memory_recall` の記録にはファイル名しか残らない）。**受け取る側に
    写させるのが唯一の確かめ方。** その写しをここに通す。
    """
    findings: list[Finding] = []
    for term in ANSWER_LOCATION_TERMS + VERIFIED_VALUE_TERMS:
        if term in transcript:
            findings.append(
                Finding("捨てスレッドの写し", "文脈", f"正解の手がかりが届いている: {term}")
            )
    for term in EXPERIMENT_TERMS:
        if term in transcript:
            findings.append(
                Finding("捨てスレッドの写し", "文脈", f"実験の手がかりが届いている: {term}")
            )
    return findings


# ---------------------------------------------------------------------------
# 記憶が写しを取ったときから変わっていないか
# ---------------------------------------------------------------------------
def memory_fingerprint(memory_dir: pathlib.Path) -> str:
    """記憶のフォルダ全体の指紋。

    捨てスレッドで写しを取るのは 1 本ぶんの手間がかかる。毎回立てるのは重いが、
    **記憶が変わったのに前の写しを根拠にする**のは危ない。このプロジェクトでは
    他のスレッドがいつでも記憶を書き換える。

    そこで、写しを取ったときの指紋を残しておき、実行のたびに突き合わせる。
    **変わっていなければ前の写しがそのまま根拠になる。変わっていたら取り直す。**
    指紋の計算に手間はかからないので、毎回できる。
    """
    digest = hashlib.sha256()
    for path in _iter_text_files(memory_dir):
        digest.update(str(path.relative_to(memory_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def check_memory_unchanged(
    memory_dir: pathlib.Path, expected_fingerprint: str
) -> list[Finding]:
    """記憶が、写しを取ったときから変わっていないか。"""
    actual = memory_fingerprint(memory_dir)
    if actual == expected_fingerprint:
        return []
    return [
        Finding(
            "記憶",
            str(memory_dir),
            "写しを取ったときから記憶が変わっている。捨てスレッドで写しを取り直すこと "
            f"(写しのとき {expected_fingerprint[:12]} / いま {actual[:12]})",
        )
    ]


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------
def run_all(
    package_dir: pathlib.Path,
    runs_dir: pathlib.Path,
    shared_root: pathlib.Path,
    memory_dir: pathlib.Path,
    allowed_shared_names: Sequence[str],
    probe_transcript: str | None = None,
    expected_memory_fingerprint: str | None = None,
) -> list[Finding]:
    """4 つの経路をまとめて検査する。捨てスレッドの写しと指紋があればそれも。"""
    findings: list[Finding] = []
    findings += scan_package(package_dir)
    findings += scan_runs_dir(runs_dir)
    findings += scan_shared_root(shared_root, allowed_shared_names)
    findings += scan_memory(memory_dir)
    if probe_transcript is not None:
        findings += scan_probe_transcript(probe_transcript)
    if expected_memory_fingerprint is not None:
        findings += check_memory_unchanged(memory_dir, expected_memory_fingerprint)
    return findings


def format_report(findings: Sequence[Finding]) -> str:
    if not findings:
        return "漏れ口は見つかりませんでした（4 経路とも 0 件）。"
    lines = [f"漏れ口 {len(findings)} 件:"]
    lines += [f"  {finding}" for finding in findings]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - 入口
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=pathlib.Path)
    parser.add_argument("--runs-dir", required=True, type=pathlib.Path)
    parser.add_argument("--shared-root", required=True, type=pathlib.Path)
    parser.add_argument("--memory-dir", required=True, type=pathlib.Path)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="共有フォルダの直下で、あってよい名前（渡すフォルダと答案の置き場）",
    )
    parser.add_argument(
        "--probe-transcript",
        type=pathlib.Path,
        default=None,
        help="捨てスレッドが写し出した文脈のファイル",
    )
    parser.add_argument(
        "--expect-memory-fingerprint",
        default=None,
        help="写しを取ったときの記憶の指紋。変わっていたら写しを取り直す",
    )
    parser.add_argument(
        "--print-memory-fingerprint",
        action="store_true",
        help="いまの記憶の指紋を出して終わる（写しを取った直後に使う）",
    )
    args = parser.parse_args(argv)

    if args.print_memory_fingerprint:
        print(memory_fingerprint(args.memory_dir))
        return 0

    transcript = None
    if args.probe_transcript is not None:
        transcript = _read(args.probe_transcript)

    findings = run_all(
        package_dir=args.package,
        runs_dir=args.runs_dir,
        shared_root=args.shared_root,
        memory_dir=args.memory_dir,
        allowed_shared_names=args.allow,
        probe_transcript=transcript,
        expected_memory_fingerprint=args.expect_memory_fingerprint,
    )
    print(format_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
