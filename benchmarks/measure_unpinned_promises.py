"""46周目: コードが自分で宣言した前提のうち、守られていないのは何件か。

基準は `docs/c_unpinned_promises_criteria.md`(測る前にコミット済み)。

やることは単純である。**前提を 1 つずつ壊し、全件テストが気づくかを数える。**

実図面は使わない。**この測定は全件テストだけで完結する。**

使い方::

    .venv/bin/python benchmarks/measure_unpinned_promises.py

**作業ツリーを直接書き換えて、必ず戻す。** 始める前に `git diff` が空であることを
確かめ、終わったあとにも空であることを確かめる。途中で落ちても `finally` で戻す。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"


@dataclass(frozen=True)
class Promise:
    """壊す前提 1 つ。"""

    key: str
    path: str
    promise: str
    old: str
    new: str
    #: 壊れが出力を変えることを示す小さな探り。空なら全件テストの結果だけで判断する。
    probe: str = ""


PROMISES: tuple[Promise, ...] = (
    Promise(
        key="C1",
        path="axes/image_axis/pdf_vector_symbols.py",
        promise="呼び出し順に依存しないよう位置で並べる",
        old="    out.sort(key=lambda a: (round(a.rect_pt[1], 1), round(a.rect_pt[0], 1)))\n",
        new="",
        probe="probe_arc_order",
    ),
    Promise(
        key="C2",
        path="arbitration/consistency_solver.py",
        promise="単位が違うレンジは比較しない",
        old="            if advisory.unit and match_unit and advisory.unit != match_unit:",
        new="            if False:",
        probe="probe_unit_mismatch",
    ),
    Promise(
        key="C3",
        path="arbitration/method_policies.py",
        promise="vtracer の床面積はハード制約に絶対に昇格しない",
        old='    METHOD_FLOOR_AREA: MethodPolicy(calibrated=False, max_strength="weak"),',
        new='    METHOD_FLOOR_AREA: MethodPolicy(calibrated=False, max_strength="strong"),',
        probe="probe_policy_floor_area",
    ),
    Promise(
        key="C4",
        path="arbitration/method_policies.py",
        promise="人の入力もそれだけを根拠に自動確定させない",
        old='    METHOD_HUMAN_REFERENCE_POINT: MethodPolicy(calibrated=False, max_strength="strong"),',
        new='    METHOD_HUMAN_REFERENCE_POINT: MethodPolicy(calibrated=True, max_strength="strong"),',
        probe="probe_policy_human",
    ),
    Promise(
        key="C5",
        path="axes/image_axis/schedule_tables.py",
        promise="建具番号が空の行はそもそも行にしない",
        old="        if not mark:",
        new="        if False:",
        probe="probe_empty_mark",
    ),
    Promise(
        key="C6",
        path="arbitration/consistency_solver.py",
        promise="弱い軸は z3 の変数にならず、ハードな解に影響しない",
        old='        if strength == "weak" or reading.status != "confident":',
        new="        if False:",
        probe="probe_weak_axis",
    ),
    Promise(
        key="C7",
        path="intake/drawing_intake.py",
        promise="建具表の数量と開き戸の円弧は別の対象にしてある",
        old='TARGET_DOOR_QUANTITY_PREFIX = "建具数量::"',
        new='TARGET_DOOR_QUANTITY_PREFIX = "開き戸::"',
        probe="probe_door_target_prefix",
    ),
    Promise(
        key="C8",
        path="arbitration/method_policies.py",
        promise="図面に印字された面積は階層1の根拠にならない",
        old='    METHOD_TEXT_AREA: MethodPolicy(calibrated=False, max_strength="strong"),',
        new='    METHOD_TEXT_AREA: MethodPolicy(calibrated=True, max_strength="strong"),',
        probe="probe_policy_text_area",
    ),
)

#: 対照1。**取り決めで禁じられている形そのもの。**全件テストが落ちなければ測り方が壊れている。
CONTROL = Promise(
    key="対照1",
    path="intake/drawing_intake.py",
    promise="回答が無いときに片方を既定として採らない",
    old="    answer = stored\n    if answer is None and from_kit is not None:",
    new="    answer = stored\n    if answer is None:\n        from_kit = from_kit or available[0]\n    if answer is None and from_kit is not None:",
)


# ---------------------------------------------------------------------------
# 壊れが出力を変えることを示す探り
# ---------------------------------------------------------------------------

PROBES: dict[str, str] = {
    "probe_policy_floor_area": (
        "from arbitration.method_policies import DEFAULT_METHOD_POLICIES, METHOD_FLOOR_AREA as K\n"
        "p = DEFAULT_METHOD_POLICIES[K]\n"
        "print((p.calibrated, p.max_strength))\n"
    ),
    "probe_policy_human": (
        "from arbitration.method_policies import DEFAULT_METHOD_POLICIES, METHOD_HUMAN_REFERENCE_POINT as K\n"
        "p = DEFAULT_METHOD_POLICIES[K]\n"
        "print((p.calibrated, p.max_strength))\n"
    ),
    "probe_policy_text_area": (
        "from arbitration.method_policies import DEFAULT_METHOD_POLICIES, METHOD_TEXT_AREA as K\n"
        "p = DEFAULT_METHOD_POLICIES[K]\n"
        "print((p.calibrated, p.max_strength))\n"
    ),
    "probe_door_target_prefix": (
        "from intake.drawing_intake import TARGET_DOOR_QUANTITY_PREFIX\n"
        "print(TARGET_DOOR_QUANTITY_PREFIX)\n"
    ),
    "probe_arc_order": (
        "import pymupdf, tempfile, pathlib\n"
        "from axes.image_axis.pdf_vector_symbols import find_door_arcs, DrawingScale\n"
        "PT = (1/50)/25.4*72\n"
        "doc = pymupdf.open(); page = doc.new_page(width=1190, height=842)\n"
        "# **下の円弧を先に描く。**並べ替えが無ければ描いた順のまま返る。\n"
        "for cx, cy in ((600.0, 600.0), (200.0, 200.0)):\n"
        "    s = page.new_shape()\n"
        "    s.draw_sector(pymupdf.Point(cx, cy), pymupdf.Point(cx + 800.0*PT, cy), 90)\n"
        "    s.finish(color=(0,0,0), width=0.3); s.commit()\n"
        "path = pathlib.Path(tempfile.mkdtemp())/'arc.pdf'\n"
        "doc.save(path); doc.close()\n"
        "arcs = find_door_arcs(path, 0, DrawingScale(denominator=50.0, source_text='1/50'))\n"
        "print([(round(a.rect_pt[1]), round(a.rect_pt[0])) for a in arcs])\n"
    ),
    "probe_empty_mark": (
        "import pymupdf, tempfile, pathlib, sys\n"
        "sys.path.insert(0, '.')\n"
        "from tests.test_pdf_tables import draw_table\n"
        "from axes.image_axis.schedule_tables import read_door_schedules\n"
        "doc = pymupdf.open(); page = doc.new_page(width=1190, height=842)\n"
        "draw_table(page, origin=(80.0,120.0), col_widths=(110.0,90.0,80.0,80.0,70.0),\n"
        "           row_height=24.0, caption='建具表',\n"
        "           rows=(('建具番号','種別','幅','高さ','数量'),\n"
        "                 ('WD-01','引戸','1650','2000','2'),\n"
        "                 ('','','','',''),))\n"
        "path = pathlib.Path(tempfile.mkdtemp())/'t.pdf'\n"
        "doc.save(path); doc.close()\n"
        "res = read_door_schedules(path, 0)\n"
        "print(sum(len(r.rows) for r in res))\n"
    ),
    "probe_unit_mismatch": (
        "from arbitration.consistency_solver import ConsistencySolver\n"
        "from axes.image_axis.grounding_dino_adapter import SymbolCountReading\n"
        "r0 = SymbolCountReading(category='x', prompt='x', count_range=(10,12), status='confident')\n"
        "s = ConsistencySolver()\n"
        "s.add_variable('T', 10, 12, strength='strong', axis='strong_axis', unit='m2')\n"
        "s.add_advisory_reading('T', r0, axis='weak_axis', unit='mm')\n"
        "print([(n.axis, n.agrees) for n in s.solve().advisories])\n"
    ),
    "probe_weak_axis": (
        "from arbitration.consistency_solver import ConsistencySolver\n"
        "from axes.image_axis.grounding_dino_adapter import SymbolCountReading\n"
        "r0 = SymbolCountReading(category='x', prompt='x', count_range=(3,3), status='confident')\n"
        "s = ConsistencySolver()\n"
        "s.add_variable_from_reading('T', r0, axis='weak_axis', strength='weak')\n"
        "print(sorted(s.solve().variables))\n"
    ),
}


# ---------------------------------------------------------------------------


def run(cmd: list[str], *, timeout: int = 1800) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin", "HOME": "/root",
             "PYTHONPATH": str(ROOT)},
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def clear_pycache() -> None:
    for cache in ROOT.rglob("__pycache__"):
        if ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)


def full_suite() -> tuple[bool, str]:
    """全件テストを流す。**最初の失敗で止める**(欲しいのは落ちたかどうかだけ)。"""
    clear_pycache()
    code, out = run([str(PYTHON), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"])
    tail = [line for line in out.splitlines() if line.strip()][-1:]
    failed = [line for line in out.splitlines() if line.startswith("FAILED")]
    return code != 0, " / ".join(failed[:1] + tail)


def probe(name: str) -> str:
    if not name:
        return ""
    clear_pycache()
    code, out = run([str(PYTHON), "-c", PROBES[name]], timeout=300)
    return out.strip() if code == 0 else f"<探りが動かなかった: {out.strip()[-200:]}>"


def apply_break(promise: Promise) -> str:
    path = ROOT / promise.path
    original = path.read_text(encoding="utf-8")
    assert original.count(promise.old) == 1, f"{promise.key}: 置き換え先が 1 つではない"
    path.write_text(original.replace(promise.old, promise.new), encoding="utf-8")
    return original


@dataclass
class Row:
    key: str
    promise: str
    suite_failed: bool = False
    detail: str = ""
    probe_before: str = ""
    probe_after: str = ""
    observable: bool | None = None
    bucket: str = ""


def measure(promise: Promise, *, with_probe: bool) -> Row:
    row = Row(key=promise.key, promise=promise.promise)
    if with_probe and promise.probe:
        row.probe_before = probe(promise.probe)
    original = apply_break(promise)
    try:
        row.suite_failed, row.detail = full_suite()
        if with_probe and promise.probe:
            row.probe_after = probe(promise.probe)
    finally:
        (ROOT / promise.path).write_text(original, encoding="utf-8")
        clear_pycache()
    if row.probe_before or row.probe_after:
        row.observable = row.probe_before != row.probe_after
    return row


def main() -> int:
    code, out = run(["/usr/bin/git", "diff", "--quiet"])
    if code != 0:
        print("作業ツリーが汚れている。始めない。")
        return 1

    print("=== 対照2 素の全件テスト ===", flush=True)
    failed, detail = full_suite()
    print(f"   落ちたか: {'はい' if failed else 'いいえ'} / {detail}", flush=True)
    baseline_clean = not failed

    print("\n=== 対照1 効くことの確認(禁じられている形を入れる) ===", flush=True)
    control = measure(CONTROL, with_probe=False)
    print(f"   落ちたか: {'はい' if control.suite_failed else 'いいえ'} / {control.detail}",
          flush=True)

    rows: list[Row] = []
    for promise in PROMISES:
        print(f"\n=== {promise.key} {promise.promise} ===", flush=True)
        row = measure(promise, with_probe=True)
        print(f"   全件テストは落ちたか: {'はい' if row.suite_failed else 'いいえ'}",
              flush=True)
        print(f"   {row.detail}", flush=True)
        if row.suite_failed:
            row.bucket = "X2 気づいた"
        elif row.observable:
            row.bucket = "X4 気づかなかった(壊れは出ている)"
            print(f"   探り: 元 {row.probe_before} → 壊し {row.probe_after}", flush=True)
        else:
            row.bucket = "X5 壊せなかった"
            print(f"   探り: 元 {row.probe_before} → 壊し {row.probe_after} (同じ)",
                  flush=True)
        rows.append(row)

    x2 = sum(1 for r in rows if r.bucket.startswith("X2"))
    x4 = sum(1 for r in rows if r.bucket.startswith("X4"))
    x5 = sum(1 for r in rows if r.bucket.startswith("X5"))

    code, diff_out = run(["/usr/bin/git", "diff", "--stat"])
    restored = not diff_out.strip()

    print("\n\n=== まとめ ===", flush=True)
    print(f"{'記号':<5}{'全件テスト':<12}{'箱'}")
    for row in rows:
        print(f"{row.key:<5}{'落ちた' if row.suite_failed else '通った':<12}{row.bucket}")
    print(f"\nX1 試した数: {len(rows)}")
    print(f"X2 気づいた: {x2}")
    print(f"X3 気づかなかった: {len(rows) - x2}")
    print(f"X4 そのうち壊れが出ていた(= 守られていない前提): {x4}")
    print(f"X5 壊せなかった: {x5}")

    print("\n=== 対照 ===")
    print(f"対照1 禁じられている形で全件テストが落ちたか: "
          f"{'はい' if control.suite_failed else 'いいえ'}")
    print(f"対照2 素の全件テストが通ったか: {'はい' if baseline_clean else 'いいえ'}")
    print(f"対照3 8件ぜんぶ戻せたか(git diff が空): {'はい' if restored else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not control.suite_failed:
        print("   **対照1 が落ちなかった。測り方のほうが壊れているので結論を出さない。**")
    elif x4 >= 3:
        print(f"   X4 = {x4} ≧ 3 → **穴は1件ではなく形である。**"
              "前提を固定するテストを足す周へ(テストだけ)。")
    elif x4 >= 1:
        print(f"   X4 = {x4} → **少数。**見つかった分のテストだけ足して、"
              "トライアルCの本体(段取り待ち)へ戻る。")
    else:
        print("   X4 = 0 → **45周目の1件が例外だった。**"
              "何も足さずトライアルCの本体へ戻る。")

    payload = {
        "round": 46,
        "X1": len(rows), "X2": x2, "X3": len(rows) - x2, "X4": x4, "X5": x5,
        "rows": [
            {"key": r.key, "promise": r.promise, "suite_failed": r.suite_failed,
             "detail": r.detail, "probe_before": r.probe_before,
             "probe_after": r.probe_after, "observable": r.observable,
             "bucket": r.bucket}
            for r in rows
        ],
        "controls": {
            "1_forbidden_shape_fails": control.suite_failed,
            "1_detail": control.detail,
            "2_baseline_clean": baseline_clean,
            "3_restored": restored,
        },
    }
    out_path = ROOT / "docs" / "c_unpinned_promises_result.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"\n書き出し: {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
