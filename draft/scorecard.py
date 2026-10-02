"""K-67 4 節: **採点表。**段階ごとの合格ラインと、いまの値と、到達度。

**合格ラインはおーちゃんの K-67 4 節の数字をそのまま使う。**こちらで決めた値は 1 つも無い。
**正解が要る行と、人が要る行は `未取得` と出す。0 にしない。**

満足の段階(おーちゃんの言葉):

- 段階 1「台帳」 … 満足度の約 50%
- 段階 2「概略書」 … 約 75%
- 段階 3「下書き」 … 100% 近い

到達度 = いまの値 ÷ 合格ライン(上限 100%)。**下限のある行(0 が合格)は、合えば 100%、外れれば 0%。**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

UNKNOWN = "未取得"
NEEDS_GOLDEN = "未取得(正解が要る。パソコン側)"
NEEDS_HUMAN = "未取得(人が要る)"

STAGES = {1: "台帳", 2: "概略書", 3: "下書き"}
SHARE = {1: 0.50, 2: 0.75, 3: 1.00}


@dataclass
class Row:
    段階: int | str
    名前: str
    測るもの: str
    合格ライン: Any
    いまの値: Any = UNKNOWN
    向き: str = "以上"
    """`以上`(大きいほうがよい)/ `以下`(小さいほうがよい)/ `ちょうど`(0 など)"""
    時間: float | None = None
    備考: str = ""

    @property
    def 到達度(self) -> float | None:
        value, line = self.いまの値, self.合格ライン
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if not isinstance(line, (int, float)):
            return None
        if self.向き == "以上":
            return 1.0 if line == 0 else min(value / line, 1.0)
        if self.向き == "以下":
            if value <= line:
                return 1.0
            return round(line / value, 4) if value else 1.0
        return 1.0 if value == line else 0.0

    @property
    def 合否(self) -> str:
        reached = self.到達度
        if reached is None:
            return UNKNOWN
        return "合格" if reached >= 1.0 else "未達"

    def as_dict(self) -> dict[str, Any]:
        return {
            "段階": self.段階, "名前": self.名前, "測るもの": self.測るもの,
            "合格ライン": self.合格ライン, "向き": self.向き, "いまの値": self.いまの値,
            "到達度": None if self.到達度 is None else round(self.到達度, 4),
            "合否": self.合否, "時間(秒)": self.時間, "備考": self.備考,
        }


def declared_found_in_drawing(
    finish: Mapping[str, Any] | None, items: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """K-67 4 節 段階 2「仕様書の宣言を図面で見つけた割合」。

    **K-63 2 節 5 と同じ数え方**(正解を使わない自己整合): 仕上表の原本の行(室 × 部位)のうち、
    **仕上表以外のページ**に同じ室(揃えた室名)・同じ部位の項目があった割合。

    原本の行の `照らし合わせ`(ひな型との比較)とは別のものである。あちらは「ひな型が原本と
    合っているか」で、こちらは「仕様書が宣言したものを図面の中で見つけたか」。
    """
    from sameness.normalize import room_key
    from sameness.terms import default_terms

    rows = (finish or {}).get("原本の行") or []
    if not rows:
        return {"分母": 0, "見つけた": 0, "割合": None, "理由": "仕上表の原本が無い"}
    schedule_pages = set((finish or {}).get("原本のページ") or ())
    terms = default_terms()

    seen: set[tuple[str, str | None]] = set()
    for item in items:
        if item.get("ページ") in schedule_pages:
            continue
        room = room_key(item.get("場所") or item.get("室"))
        part = terms.find("部位", item.get("部位")) or terms.find("部位", item.get("工事"))
        if room:
            seen.add((room, part))

    found = 0
    for row in rows:
        room = room_key(row.get("室"))
        part = terms.find("部位", row.get("部位")) or terms.find("部位", row.get("部位(書かれたまま)"))
        if not room:
            continue
        # **部位が取れている行は、室と部位の両方が合ったときだけ数える。**
        # 室だけで数えると、その室に何か 1 つあれば全部の部位が見つかったことになる
        # (1 回それで 1.0 になった。K-63 は同じ数え方で 0.78〜0.93 だった)。
        if part is not None:
            if (room, part) in seen:
                found += 1
        elif any(r == room for r, _ in seen):
            found += 1
    return {
        "分母": len(rows), "見つけた": found,
        "割合": round(found / len(rows), 4) if rows else None,
        "仕上表のページ": sorted(schedule_pages),
    }


def _rate(bucket: Mapping[str, Any] | None, key: str = "読了率") -> Any:
    if not bucket:
        return UNKNOWN
    value = bucket.get(key)
    return UNKNOWN if value is None else value


def build(
    *,
    readthrough: Mapping[str, Any] | None = None,
    search: Mapping[str, Any] | None = None,
    checklist: Mapping[str, Any] | None = None,
    finish: Mapping[str, Any] | None = None,
    items: Sequence[Mapping[str, Any]] = (),
    timings: Mapping[str, Any] | None = None,
    fabricated: Any = UNKNOWN,
) -> dict[str, Any]:
    """採点表を組む。渡されなかったものは `未取得` のまま残る。"""
    kinds = (readthrough or {}).get("種類ごと(重なりなし)") or {}
    slices = (readthrough or {}).get("別の切り口(重なる)") or {}
    ink = (readthrough or {}).get("墨の量で見た読了率") or {}
    rows: list[Row] = []

    # --- 段階 1 台帳 ---
    for name, bucket in (("文字", kinds.get("文字")), ("記号", kinds.get("記号")),
                         ("数字だけの語(寸法の見込み)", slices.get("数字だけの語(寸法の見込み)")),
                         ("表", slices.get("表"))):
        rows.append(Row(1, f"読了率 {name}", "台帳に位置付きで載った図形 ÷ 数える図形", 0.98, _rate(bucket)))
    for name in ("線(長さ)", "点・小さい図形(面積)"):
        rows.append(Row(1, f"読了率 {name}", "墨の量で見た読了率", 0.90, _rate(ink.get(name))))
    rows.append(Row(1, "未読の所在", "未読の図形のうち、ページと位置で指せる割合", 1.0,
                    _rate(readthrough, "未読の所在が指せた割合")))
    rows.append(Row(1, "「どこに書いてあるか」検索", "文字の層から自動で 100 問", 0.90,
                    _rate(search, "正答率")))
    rows.append(Row(1, "作った数字", "図面に無い数字を台帳に書いた件数", 0, fabricated, 向き="ちょうど",
                    備考="画像で 1 件ずつ確かめる測定が要る(K-50 の型)。この周では測っていない"
                    if fabricated == UNKNOWN else ""))
    rows.append(Row(1, "読み取りの時間", "読む段にかかった秒", 600,
                    (timings or {}).get("読む", UNKNOWN), 向き="以下"))

    # --- 段階 2 概略書 ---
    frames = (checklist or {}).get("枠") or []
    with_state = sum(1 for f in frames if f.get("状態"))
    rows.append(Row(2, "16 枠に状態が付く", "状態が付いた枠 ÷ 16", 1.0,
                    round(with_state / 16, 4) if frames else UNKNOWN))
    located = [i for i in items if i.get("ページ") and i.get("位置")]
    rows.append(Row(2, "出典の実在", "項目のうち、ページと位置の両方を持つ割合", 0.95,
                    round(len(located) / len(items), 4) if items else UNKNOWN))
    if frames:
        legal = all(
            set(f["分からないこと"]["理由"]) <= set(__import__("draft.work_checklist", fromlist=["REASONS"]).REASONS)
            for f in frames
        )
        wrongly = (checklist or {}).get("語が見つかるのに記載が見当たらないとした枠") or []
        rows.append(Row(2, "「分からない」の理由の妥当性", "理由が決めた 5 つの中にあり、語が見つかるのに「記載が見当たらない」と言った枠が 0",
                        0, len(wrongly) if legal else UNKNOWN, 向き="ちょうど"))
    else:
        rows.append(Row(2, "「分からない」の理由の妥当性", "理由の種類ごとに機械で検算", 0, UNKNOWN, 向き="ちょうど"))
    rows.append(Row(2, "候補ページの関連性", "枠の語彙と頁の文字の一致で近似(線は置かない)", UNKNOWN,
                    (checklist or {}).get("候補ページの関連性", UNKNOWN),
                    備考="人の確認はカード(おーちゃんが 10 枠)"))
    declared = declared_found_in_drawing(finish, items)
    rows.append(Row(2, "仕様書の宣言を図面で見つけた割合",
                    "仕上表の原本の行(室×部位)のうち、仕上表以外のページで同じ室・部位が見つかった割合(K-63 と同じ数え方)",
                    0.90, UNKNOWN if declared["割合"] is None else declared["割合"],
                    備考=f"分母 {declared['分母']} 行"))
    unfounded = [i for i in items if not i.get("根拠の種類") and not i.get("位置")]
    rows.append(Row(2, "根拠のない主張", "根拠の種類も位置も無い項目の件数", 0,
                    len(unfounded) if items else UNKNOWN, 向き="ちょうど"))

    # --- 段階 3 下書き(正解が要る) ---
    rows.append(Row(3, "科目(厳密)", "出した科目が正解の科目と同じ割合", 0.95, NEEDS_GOLDEN))
    rows.append(Row(3, "中科目(厳密)", "同じ", 0.90, NEEDS_GOLDEN))
    rows.append(Row(3, "細目(同じ意味。K-66)", "K-66 の部品で当たりとした細目 ÷ 正解の細目", 0.70, NEEDS_GOLDEN))
    for mode, line in (("概算", 0.50), ("通常", 0.70), ("精密", 0.90)):
        rows.append(Row(3, f"数量が合った細目の金額の割合({mode})", "数量が許容差内の細目の金額 ÷ 総額", line, NEEDS_GOLDEN))

    # --- 共通 ---
    rows.append(Row("共通", "外れた項目に印が出ていた割合", "外れた項目のうち「未確定・低・要確認」と出ていた割合", 0.80, NEEDS_GOLDEN))
    rows.append(Row("共通", "確度「高」の的中率", "確度が高の項目のうち当たっていた割合", 0.95, NEEDS_GOLDEN))
    rows.append(Row("共通", "全項目が図面の位置へ辿れる", "項目のうちページと位置を持つ割合", 1.0,
                    round(len(located) / len(items), 4) if items else UNKNOWN))
    rows.append(Row("共通", "手直し時間 ÷ 最初から作る時間", "人が直した時間と、人が最初から作る時間の比", 1 / 3, NEEDS_HUMAN, 向き="以下"))
    rows.append(Row("共通", "「どこで間違ったか分からない」事例", "外れた項目のうち、根拠の位置が辿れないものの件数", 0,
                    len([i for i in items if not i.get("位置")]) if items else UNKNOWN, 向き="ちょうど"))

    per_stage: dict[str, Any] = {}
    for stage in (1, 2, 3, "共通"):
        group = [r for r in rows if r.段階 == stage]
        measured = [r for r in group if r.到達度 is not None]
        name = STAGES.get(stage, "共通") if isinstance(stage, int) else "共通"
        if not measured:
            per_stage[f"段階{stage} {name}" if isinstance(stage, int) else "共通"] = {
                "判定": "未測定", "行": len(group), "測れた行": 0,
            }
            continue
        reached = sum(r.到達度 for r in measured) / len(measured)
        unmeasured = len(group) - len(measured)
        # **測れなかった行があるときに「合格」と出してはいけない。**
        # 資料を隠した版で、測れなくなった行が落ちたせいで段階 2 が「合格」に見えた
        # (2026-10-02、hidden2 の回で実際に起きた)。**測定が消えることが点になる**のは欠陥である。
        if not all(r.合否 == "合格" for r in measured):
            verdict = f"未達 {reached:.0%}"
        elif unmeasured:
            verdict = f"測れた {len(measured)} 行はすべて合格だが、未取得が {unmeasured} 行あるので合格とは言わない"
        else:
            verdict = "合格"
        per_stage[f"段階{stage} {name}" if isinstance(stage, int) else "共通"] = {
            "判定": verdict,
            "行": len(group), "測れた行": len(measured), "未取得の行": unmeasured,
            "到達度の平均": round(reached, 4),
            "満足度の目安": SHARE.get(stage) if isinstance(stage, int) else None,
        }
    return {
        "但し書き": "下書き(人が直す前提)。合格ラインはおーちゃんの K-67 4 節のまま。未取得は 0 にしない",
        "段階ごと": per_stage,
        "行": [r.as_dict() for r in rows],
    }


def headline(card: Mapping[str, Any]) -> str:
    """画面の上に出す 1 行(K-67 5 節)。"""
    parts = []
    for name, value in (card.get("段階ごと") or {}).items():
        if name == "共通":
            continue
        verdict = value["判定"]
        if verdict.startswith("測れた"):
            verdict = f"未取得 {value['未取得の行']} 行あり(測れた行は合格)"
        parts.append(f"{name}:{verdict}")
    return " / ".join(parts)
