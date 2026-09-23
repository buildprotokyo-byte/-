"""57周目: **人が読む要約は「探せなかった」と「探したが 0 件」を区別しているか。**

基準は `docs/d_summary_distinguishes_criteria.md`(測る前にコミット済み `c5c7f69`)。

**合成図面で線を引く(取り決め④)。実図面は記録にしか使わない。**
**測る周なので製品コードは 1 行も変えない。**

使い方::

    .venv/bin/python benchmarks/measure_summary_distinguishes.py [実図面v2のPDF]

**出すのは件数と真偽だけ。**
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from intake.drawing_intake import IntakeConfig, IntakeResult, read_drawing  # noqa: E402

#: 実寸 1mm が 1/50 の図面で何ポイントか(テストと同じ値)。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72

#: **3 つとも同じ案件名で回す。**案件名は要約の 1 行目に出るので、
#: 違えると理由とは無関係に「違う行」が 1 本立ってしまう。
CASE_ID = "synthetic"


def _draw_quarter_arc(page: pymupdf.Page, x: float, y: float, radius_pt: float) -> None:
    start = pymupdf.Point(x + radius_pt, y)
    shape = page.new_shape()
    shape.draw_sector(pymupdf.Point(x, y), start, 90)
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _draw_sliding_door(page: pymupdf.Page, x: float, y: float, width_pt: float) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + width_pt, y))
    shape.draw_line(
        pymupdf.Point(x + width_pt / 2, y - 2), pymupdf.Point(x + width_pt * 1.5, y - 2)
    )
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def build(path: Path, *, scale: bool, door_widths: tuple[float, ...]) -> Path:
    """合成の平面図を 1 ページ作る。**違うのは縮尺の印字と開き戸の有無だけ。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)  # A3 横
    if scale:
        page.insert_text(
            pymupdf.Point(850, 780), "縮尺 1/50", fontname="japan", fontsize=11
        )
    else:
        # **同じだけ文字を置く。**文字の有無そのものが差にならないようにする。
        page.insert_text(
            pymupdf.Point(850, 780), "図面名 平面図", fontname="japan", fontsize=11
        )
    x = 100.0
    for width_mm in door_widths:
        _draw_quarter_arc(page, x, 300.0, width_mm * PT_PER_MM_AT_50)
        x += 200.0
    for index in range(2):
        _draw_sliding_door(page, 150.0 + index * 180.0, 600.0, 1600.0 * PT_PER_MM_AT_50)
    doc.save(path)
    doc.close()
    return path


def summary_lines(result: IntakeResult) -> list[str]:
    """**図面の指紋の行だけ落とす**(中身が違えば必ず違うので、比較の邪魔になる)。"""
    return [
        line for line in result.summary().splitlines()
        if not line.startswith("図面の指紋")
    ]


def run(path: Path, case_id: str) -> IntakeResult:
    return read_drawing(IntakeConfig(pdf_path=path, case_id=case_id))


def main(argv: list[str]) -> int:
    real_pdf = Path(argv[1]) if len(argv) > 1 else None

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        s0 = build(tmpdir / "s0.pdf", scale=True, door_widths=(800.0, 750.0))
        s1 = build(tmpdir / "s1.pdf", scale=True, door_widths=())
        s2 = build(tmpdir / "s2.pdf", scale=False, door_widths=())

        print("=== C0 3 つとも通るか ===", flush=True)
        # **案件名は 3 つとも同じにする。**違えると要約の 1 行目が必ず違ってしまい、
        # 「違う行がある」が理由と無関係に立ってしまう(**最初の実装の穴**)。
        r0, r1, r2 = run(s0, CASE_ID), run(s1, CASE_ID), run(s2, CASE_ID)
        print("   例外なし。S0/S1/S2 とも 1 ページ")

        print("\n=== 対照 ===", flush=True)
        control1 = len(r0.findings) >= 1
        print(f"対照1 S0(開き戸あり)の数量: {len(r0.findings)} 件 → "
              f"{'はい' if control1 else 'いいえ'}")
        control2 = len(r1.findings) == 0 and len(r2.findings) == 0
        print(f"対照2 S1/S2 の数量がどちらも 0 件か: "
              f"{len(r1.findings)} / {len(r2.findings)} → "
              f"{'はい' if control2 else 'いいえ'}")

        print("\n=== C1 要約の違う行の数 ===", flush=True)
        lines1, lines2 = summary_lines(r1), summary_lines(r2)
        differing = [
            (a, b) for a, b in zip(lines1, lines2) if a != b
        ] + [("", b) for b in lines2[len(lines1):]] + [(a, "") for a in lines1[len(lines2):]]
        c1 = len(differing)
        print(f"   S1 の要約: {len(lines1)} 行 / S2 の要約: {len(lines2)} 行")
        print(f"   C1 違う行の数: {c1}")
        for a, b in differing:
            print(f"     違った行(S1 側): {a[:40]}")
            print(f"     違った行(S2 側): {b[:40]}")

        c2 = any(
            ("未対応" in a or "未対応" in b or "縮尺" in a or "縮尺" in b)
            for a, b in differing
        )
        print(f"   C2 違う行が「未対応」か「縮尺」に触れているか: "
              f"{'はい' if c2 else 'いいえ'}")

        print("\n=== C3 ページごとの記録では区別が付くか(56周目の再確認)===", flush=True)
        notes1 = {note for page in r1.pages for note in page.notes}
        notes2 = {note for page in r2.pages for note in page.notes}
        c3 = notes1 != notes2
        print(f"   記録の集合が違うか: {'はい' if c3 else 'いいえ'}")
        print(f"   S1 の記録 {len(notes1)} 種 / S2 の記録 {len(notes2)} 種")

        repeats = []
        for _ in range(2):
            a, b = summary_lines(run(s1, CASE_ID)), summary_lines(run(s2, CASE_ID))
            repeats.append(sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b)))
        repeats.append(c1)
        control3 = len(set(repeats)) == 1
        print(f"\n対照3 反復 3 回(C1): {repeats} → {'はい' if control3 else 'いいえ'}")

    c4 = None
    if real_pdf is not None and real_pdf.exists():
        print("\n=== C4 実図面 v2 の要約(記録だけ。線には使わない)===", flush=True)
        real = run(real_pdf, "p011-v2")
        c4 = "縮尺" in real.summary()
        print(f"   要約に「縮尺」の語が現れるか: {'はい' if c4 else 'いいえ'}")
        print(f"   未対応ページ: {len(real.unsupported_pages)} / 全 {len(real.pages)}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        verdict = "control_failed"
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif c1 == 0:
        verdict = "summary_does_not_distinguish"
        print("   **C1 = 0。人が読む要約は区別していない。**")
        print("   ページごとの記録には理由が残っているのに、**人に渡す形で消えている。**")
        print("   → **再現する失敗テストを先に書いてから、"
              "要約に『手法を試せなかったページ数』を足す。**")
    elif c2:
        verdict = "summary_distinguishes"
        print(f"   **C1 = {c1} かつ C2 = 真。区別している。**どの行かを名指しする。")
    else:
        verdict = "differs_but_no_reason"
        print(f"   **C1 = {c1} だが C2 = 偽。違いはあるが理由を表していない。**")
        print("   → **「区別していない」と同じ扱いにする。**")

    payload = {
        "round": 57,
        "criteria_commit": "c5c7f69",
        "criteria_file": "docs/d_summary_distinguishes_criteria.md",
        "benchmark": "benchmarks/measure_summary_distinguishes.py",
        "C1_differing_lines": c1,
        "C2_mentions_reason": c2,
        "C3_page_notes_differ": c3,
        "C4_real_summary_mentions_scale": c4,
        "controls": {
            "1_s0_has_findings": control1,
            "2_s1_s2_both_zero": control2,
            "3_repeat": repeats,
        },
        "verdict": verdict,
        "implementation_changed": False,
    }
    out = ROOT / "docs" / "d_summary_distinguishes_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
