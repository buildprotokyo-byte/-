"""段階0.5(方式A2)と、軸に値の由来を申告させる経路の回帰テスト。

守りたい性質は4つ。

1. **資料を物理的に分けない。** 方式A2 は全ページを1回で渡し、考える順序
   だけを指示する形である。分けた方式Bはトライアル15で全条件負けた
   (`docs/trial15_two_stage_reading_report.md`)。
2. **3要素を先に確定させる指示が入っている。** これが段階0.5の中身である。
3. **数量ごとに由来を申告させ、申告が無い回答は採用しない。** 既定値で
   埋めると、この仕組み全体が無意味になる。
4. **一般則で埋めた値は、計算を1回通しても読んだ事実に化けない。**

3と4は受け取る側(`arbitration/axis_quality_firewall.py`)に既に規則が
あるので、ここでは**軸の出力側**が同じ規則を守ることと、
入口(`InferenceOrchestrator`)を通して階層1に届かないことまでを固定する。
"""

from __future__ import annotations

import json

import pytest

from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy
from axes.reading import (
    DERIVATION_LABELS,
    FOUNDATION_ELEMENTS,
    QuantityRequest,
    ReadingRequest,
    ReadingResponseError,
    build_reading_prompt,
    parse_reading_response,
    to_orchestrator_evidence,
)
from axes.reading.protocol import answer_schema, describe_sequence, foundation_summary

PAGES = (
    "【1ページ】工事概要。基準グリッドは 910mm。対象はリビングと廊下のみ。",
    "【2ページ】現況。既存は縁甲板張りで、撤去のうえ新規フローリングとする。",
    "【3ページ】計上ルール。巾木は出入口1箇所につき0.90m控除する。",
    "【4ページ】詳細。リビング 4x4 グリッド、廊下 1x4 グリッド。出入口2箇所。",
)

QUANTITIES = (
    QuantityRequest(target="floor_area", text="新規フローリングの床面積", unit="m2"),
    QuantityRequest(target="baseboard_length", text="巾木の延長", unit="m"),
    QuantityRequest(target="light_count", text="照明器具の個数", unit="個"),
)


def _request() -> ReadingRequest:
    return ReadingRequest(documents=PAGES, quantities=QUANTITIES)


def _answer(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "基準寸法": "基準グリッド 910mm",
        "工事対象範囲": "リビングと廊下のみ",
        "現況": "既存は縁甲板張り。撤去のうえ新規フローリング",
        "数量": {
            "floor_area": {
                "値": 16.5620, "下限": 16.5620, "上限": 16.5620,
                "由来": "読んだ値から計算した",
                "根拠": ["資料に書いてあった", "資料に書いてあった"],
                "根拠となった記述": "910mm グリッド x 20 グリッド",
            },
            "baseboard_length": {
                "値": 16.76, "下限": 16.76, "上限": 16.76,
                "由来": "読んだ値から計算した",
                "根拠": ["資料に書いてあった"],
                "根拠となった記述": "周長から出入口2箇所ぶん 0.90m を控除",
            },
            "light_count": {
                "値": 2, "下限": 2, "上限": 2,
                "由来": "一般則で補った",
                "根拠": [],
                "根拠となった記述": "資料に台数の記載が無いため 10 ㎡/台 で算出",
            },
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------
# 1. 資料を物理的に分けない(方式A2 の形)
# ---------------------------------------------------------------------


def test_全ページが1つの資料の塊として渡る() -> None:
    """方式Bのようにページを分けないこと。分けると情報が落ちる。"""
    prompt = build_reading_prompt(_request())
    for page in PAGES:
        assert page in prompt, f"渡していないページがある: {page[:12]}"
    assert prompt.count("===== 資料ここから =====") == 1, (
        "資料の塊が2つ以上あると、工程を分けた形になってしまう"
    )
    assert prompt.count("===== 資料ここまで =====") == 1


def test_概要と詳細を選り分ける指示が入っていない() -> None:
    """「概要ページだけ」「詳細ページだけ」の類は方式Bの特徴である。"""
    prompt = build_reading_prompt(_request())
    for banned in ("概要ページだけ", "詳細ページだけ", "渡されていません", "別の担当者"):
        assert banned not in prompt, f"方式Bの言い回しが混じっている: {banned}"


def test_ページは資料の並び順のまま渡る() -> None:
    prompt = build_reading_prompt(_request())
    positions = [prompt.index(page) for page in PAGES]
    assert positions == sorted(positions), "ページの並びが入れ替わっている"


# ---------------------------------------------------------------------
# 2. 3要素を先に確定させる(段階0.5 の中身)
# ---------------------------------------------------------------------


def test_3要素を先に書き出す指示が数量の設問より前にある() -> None:
    prompt = build_reading_prompt(_request())
    for name in FOUNDATION_ELEMENTS:
        assert name in prompt
    assert prompt.index("まず") < prompt.index("設問:"), (
        "順序の指示が設問より後にあると、先に確定させる意味が無い"
    )
    assert "前提として" in prompt


def test_根本要素はちょうど3つである() -> None:
    """受け皿を増やすとトライアル15の前提が変わる。増やすなら測り直す。"""
    assert FOUNDATION_ELEMENTS == ("基準寸法", "工事対象範囲", "現況")


def test_3要素を答えない回答は採用しない() -> None:
    """3点の確定が段階0.5の中身なので、欠けた回答は指示が守られていない。"""
    answer = _answer()
    del answer["現況"]
    with pytest.raises(ReadingResponseError) as caught:
        parse_reading_response(answer, _request())
    assert caught.value.code == "foundation_elements_missing"
    assert "現況" in caught.value.detail


# ---------------------------------------------------------------------
# 3. 由来の申告(申告が無ければ採用しない)
# ---------------------------------------------------------------------


def test_由来のラベルが全部プロンプトに書かれている() -> None:
    prompt = build_reading_prompt(_request())
    for label in DERIVATION_LABELS:
        assert label in prompt, f"読み手が選べない由来がある: {label}"
    assert "資料に書かれていない情報を" in prompt


def test_由来を書かない数量は既定値で埋めず落とす() -> None:
    """**この1件がこの仕組みの要である。**

    「たぶん読んだ値だろう」と補った瞬間に、由来の申告は意味を失う。
    """
    answer = _answer()
    del answer["数量"]["light_count"]["由来"]  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert [t for t, _ in parsed.rejected] == ["light_count"]
    assert "derivation_not_declared" in dict(parsed.rejected)["light_count"]
    assert all(f.target != "light_count" for f in parsed.findings), (
        "由来の無い数量が採用されている"
    )


def test_落とした数量は理由付きで残る() -> None:
    """黙って消すと、読み手が答えたのに無くなったことに誰も気づけない。"""
    answer = _answer()
    del answer["数量"]["baseboard_length"]  # type: ignore[union-attr]
    parsed = parse_reading_response(answer, _request())
    assert dict(parsed.rejected)["baseboard_length"] == "回答が無い"


def test_知らない由来のラベルは拒否される() -> None:
    answer = _answer()
    answer["数量"]["light_count"]["由来"] = "たぶんこう"  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert "derivation_not_declared" in dict(parsed.rejected)["light_count"]


def test_根拠を書かない計算値は拒否される() -> None:
    """根拠なしの「計算した」を許すと、一般則を計算値と名乗って通せる。"""
    answer = _answer()
    answer["数量"]["floor_area"]["根拠"] = []  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert "derived_requires_basis" in dict(parsed.rejected)["floor_area"]


def test_計算値以外に根拠は書けない() -> None:
    answer = _answer()
    answer["数量"]["light_count"]["根拠"] = ["資料に書いてあった"]  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert "basis_not_allowed" in dict(parsed.rejected)["light_count"]


def test_設問に無い対象が返ってきたら落とす() -> None:
    answer = _answer()
    answer["数量"]["勝手な対象"] = {"値": 1, "由来": "資料に書いてあった"}  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert dict(parsed.rejected)["勝手な対象"] == "設問に無い対象が返ってきた"


def test_文字列のJSONでも受け取れる() -> None:
    parsed = parse_reading_response(
        json.dumps(_answer(), ensure_ascii=False), _request()
    )
    assert len(parsed.findings) == 3


def test_JSONでない回答は明確に拒否する() -> None:
    with pytest.raises(ReadingResponseError) as caught:
        parse_reading_response("回答: よくわかりません", _request())
    assert caught.value.code == "response_not_json"


# ---------------------------------------------------------------------
# 単位の正規化(㎡ と 平米 が別物にならないこと)
# ---------------------------------------------------------------------


def test_単位は正規形の整数に直る() -> None:
    parsed = parse_reading_response(_answer(), _request())
    by_target = {f.target: f for f in parsed.findings}
    # 16.5620 ㎡ = 165,620 cm²
    assert by_target["floor_area"].unit == "cm2"
    assert by_target["floor_area"].count_range == (165_620, 165_620)
    # 16.76 m = 16,760 mm
    assert by_target["baseboard_length"].unit == "mm"
    assert by_target["baseboard_length"].count_range == (16_760, 16_760)
    assert by_target["light_count"].unit == "count"


def test_刻みより細かい値は黙って丸めない() -> None:
    answer = _answer()
    answer["数量"]["baseboard_length"]["下限"] = 16.7601  # type: ignore[index]
    answer["数量"]["baseboard_length"]["上限"] = 16.7601  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    assert "value_finer_than_unit_step" in dict(parsed.rejected)["baseboard_length"]


def test_読み手が単位を選べない() -> None:
    """単位は設問側が決める。読み手に選ばせると ㎡ と 平米 が別物になる。"""
    prompt = build_reading_prompt(_request())
    assert "(単位: m2)" in prompt
    with pytest.raises(Exception):
        QuantityRequest(target="x", text="y", unit="ヤード")


# ---------------------------------------------------------------------
# 4. 一般則は計算を通しても読んだ事実に化けない
# ---------------------------------------------------------------------


def test_一般則を根拠にした計算値は一般則のままになる() -> None:
    answer = _answer()
    answer["数量"]["floor_area"]["根拠"] = [  # type: ignore[index]
        "資料に書いてあった", "一般則で補った",
    ]
    parsed = parse_reading_response(answer, _request())
    by_target = {f.target: f for f in parsed.findings}
    assert by_target["floor_area"].derivation == "derived"
    assert by_target["floor_area"].effective_derivation == "assumed", (
        "等式を1回通すだけで読んだ事実に化けている"
    )


def test_一般則で埋めた対象を一覧できる() -> None:
    """人が確認する対象がその場で分かること。"""
    parsed = parse_reading_response(_answer(), _request())
    assert parsed.assumed_targets == ("light_count",)


# ---------------------------------------------------------------------
# 入口を通して階層1に届かないこと(受け取る側までの通し)
# ---------------------------------------------------------------------


def _orchestrator() -> InferenceOrchestrator:
    return InferenceOrchestrator(
        {"reading_a2": MethodPolicy(calibrated=True, max_strength="strong")},
        {"drawing-A": "fp-A", "spec-A": "fp-B"},
    )


def _two_source_request(parsed, target: str) -> dict[str, object]:
    """同じ読み取りを2つの独立したデータ源から出した形の入力を組み立てる。

    独立した強いデータ源が2つ揃う形にして、**階層1に届く条件を満たした
    うえで**由来だけが効いているかを見る。
    """
    evidence: list[dict[str, object]] = []
    for source_id, fingerprint, axis_id in (
        ("drawing-A", "fp-A", "image"), ("spec-A", "fp-B", "text"),
    ):
        evidence.extend(
            e
            for e in to_orchestrator_evidence(
                parsed, source_id=source_id, source_fingerprint=fingerprint,
                axis_id=axis_id, method_id="reading_a2",
            )
            if e["target"] == target
        )
    return {
        "trace_id": f"trace-{target}-{len(evidence)}",
        "element_id": target,
        "evidence": evidence,
        "relations": [],
    }


def test_申告した由来が入口を通ってファイアウォールまで届く() -> None:
    parsed = parse_reading_response(_answer(), _request())
    result = _orchestrator().process(_two_source_request(parsed, "light_count"))
    assert not result.is_invalid, [e.reason_codes for e in result.events]
    assert result.decision is not None
    assert result.decision.independent_strong_source_count == 0, (
        "一般則で埋めた値が強いデータ源として数えられている"
    )


def test_一般則で埋めた値は2つ揃っても階層1に入らない() -> None:
    """2つの軸が一般則で同じ値を出しても自動確定させない。

    トライアル15で実際に起きたのは、決まりを落とした読み取りが棄権せず
    もっともらしい一般則で埋めて**自信を持って違う値を出した**ことだった。
    """
    parsed = parse_reading_response(_answer(), _request())
    result = _orchestrator().process(_two_source_request(parsed, "light_count"))
    assert result.decision is not None
    assert result.decision.tier == 3
    assert result.decision.action == "requires_review"


def test_負の対照_読んだ事実なら同じ形で階層1に届く() -> None:
    """規則が効いているのか、そもそも階層1に届かない条件なのかを分ける。"""
    answer = _answer()
    answer["数量"]["light_count"]["由来"] = "資料に書いてあった"  # type: ignore[index]
    answer["数量"]["light_count"]["根拠"] = []  # type: ignore[index]
    parsed = parse_reading_response(answer, _request())
    result = _orchestrator().process(_two_source_request(parsed, "light_count"))
    assert result.decision is not None
    assert result.decision.tier == 1
    assert result.decision.action == "auto_confirm"


def test_一般則を根拠にした計算値も階層1に届かない() -> None:
    """等式を1回通して「計算した」と名乗っても通らないことを、入口まで通して固定する。"""
    answer = _answer()
    answer["数量"]["light_count"]["由来"] = "読んだ値から計算した"  # type: ignore[index]
    answer["数量"]["light_count"]["根拠"] = [  # type: ignore[index]
        "資料に書いてあった", "一般則で補った",
    ]
    parsed = parse_reading_response(answer, _request())
    result = _orchestrator().process(_two_source_request(parsed, "light_count"))
    assert result.decision is not None
    assert result.decision.tier == 3


# ---------------------------------------------------------------------
# 組み立ての検査
# ---------------------------------------------------------------------


def test_回答の形に3要素と全対象が入っている() -> None:
    schema = answer_schema(_request())
    for name in FOUNDATION_ELEMENTS:
        assert f'"{name}"' in schema
    for quantity in QUANTITIES:
        assert f'"{quantity.target}"' in schema


def test_同じ対象を2回聞くことはできない() -> None:
    with pytest.raises(ValueError):
        ReadingRequest(
            documents=PAGES,
            quantities=(QUANTITIES[0], QUANTITIES[0]),
        )


def test_資料が空なら組み立てを拒否する() -> None:
    with pytest.raises(ValueError):
        ReadingRequest(documents=(), quantities=QUANTITIES)


def test_人が読む要約が出せる() -> None:
    parsed = parse_reading_response(_answer(), _request())
    summary = foundation_summary(parsed)
    assert "基準グリッド 910mm" in summary
    assert describe_sequence(QUANTITIES).startswith("段階0.5(方式A2)")


def test_ベンチマークの方式A2と同じ形をしている() -> None:
    """検証に使った形と本番の形がずれていないこと。

    ベンチマークのプロンプト文そのものは凍結済みの実測
    (`docs/trial15_two_stage_reading_runs.json`)と対応しているので
    変えられない。ここでは**形が同じであること**だけを固定する。
    """
    from benchmarks.run_two_stage_reading_eval import prompt_arm_a2
    from benchmarks.two_stage_reading_fixtures import ALL_SETS

    benchmark = prompt_arm_a2(ALL_SETS[0], 1)
    production = build_reading_prompt(_request())
    for prompt in (benchmark, production):
        assert prompt.count("===== 資料ここから =====") == 1
        for name in FOUNDATION_ELEMENTS:
            assert name in prompt
        assert prompt.index("まず") < prompt.index("設問:")
