"""難易度4: 方式Aの天井を壊すための資料(2026-09-21、v8 10章23項)。

トライアル15の最大の欠陥は、方式Aが 98〜100% に張り付いて
**「先に3要素を確定する読み方が精度を上げる」方向を検出できなかった**ことだった
(`docs/trial15_two_stage_reading_report.md` 5節1項)。

難易度2(分量だけ24ページに増やす)では天井が壊れなかった。読み手の作業記録を見ると、
おとりを退けた理由はほぼ例外なく**おとりのページ自身に書かれた但し書き**だった
(「参考として添付する」「過去の工事の記録であり、本工事の数量ではない」
「詳細は別紙参照」)。つまり難易度1〜3のおとりは**ラベルで無効化されており**、
読み手は中身を照合していない。

そこで難易度4は、次の3つだけを変える。**正解値は変えない**
(`two_stage_reading_key.json` は凍結したまま)。

1. **但し書きの無い集計表。** おとりの数字を、注記のない普通の集計表として置く。
   「参考」とも「別紙参照」とも書かない。工事対象範囲を持っていなければ、
   この表が対象範囲全体の合計なのか、対象外を含む建物全体の合計なのか判断できない。
2. **旧版の工事概要書。** 対象範囲が**広い**旧版(第一版)を、現行版の前に置く。
   どちらが有効かは版番号と日付でしか分からない。現行版には
   「本書が最新版である」と明記してある(版を見れば決着はつく。理不尽な罠ではない)。
3. **旧版の数量計上ルール。** 旧版には、**現行版と食い違う数え方の決まり**が載っている。
   その中身は、トライアル15で方式Bが実際に落ちたときに埋めた一般則そのもの
   (開口は全部控除する、見切り材は全部の境界に回す、等)。

この3つはどちらの方式にも等しくかかる。片方だけを難しくしていない。
日付と版番号は漢数字で書き、数字の照合テストを汚さないようにしてある。
"""

from __future__ import annotations

import re

from benchmarks.two_stage_reading_fixtures import ALL_SETS, CaseSet
from benchmarks.two_stage_reading_padding import _common_filler, _stale_number_filler

TARGET_PAGE_COUNT = 40

#: 旧版(第一版)が定めていた、広い工事対象範囲。
SUPERSEDED_SCOPE: dict[str, str] = {
    "S1": "住戸の全室(リビング・廊下・洋室1・洋室2・和室・水回り)の床仕上げ張り替え。",
    "S2": "基準階の全ゾーン(A・B・C・D)の天井改修。",
    "S3": "外壁4面(北面・東面・南面・西面)の塗装改修。",
    "S4": "店舗全体(売場・バックヤード・事務室)の床張り替え。",
    "S5": "共用廊下 1階から5階までの防水改修。",
    "S6": "校舎 1階から3階までの建具更新。",
}

#: 旧版(第一版)が定めていた数量計上ルール。**現行版と食い違う。**
#: 中身は、トライアル15で方式Bが決まりを落としたときに実際に当てはめた一般則
#: (報告書3節の表)。ここで釣れるなら、それは同じ失敗である。
SUPERSEDED_RULE: dict[str, str] = {
    "S1": "巾木は対象室の室内周長で計上する。出入口による控除は行わない。",
    "S2": "照明器具は天井改修面積 15 m2 につき1台とし、端数は切り捨てる。",
    "S3": "開口部は、1箇所あたりの面積にかかわらず、すべて塗装面積から控除する。",
    "S4": "見切り材は、外部への出入口を含め、張り替え範囲のすべての境界に計上する。",
    "S5": "立上りは手すり側・住戸側の両面に、高さ 0.10 m で計上する。",
    "S6": "カバー工法の場合も、有効開口は符号表の寸法をそのまま用いる。",
}

#: 但し書きの無い集計表に載せる見出し(セットごとの言い回しを合わせる)。
_TOTAL_TABLE_TITLE: dict[str, str] = {
    "S1": "【集計表 床面積】",
    "S2": "【集計表 天井面積】",
    "S3": "【集計表 外壁面積】",
    "S4": "【集計表 床面積】",
    "S5": "【集計表 防水面積】",
    "S6": "【集計表 建具】",
}


def project_code(case: CaseSet) -> str:
    """現行版の概要ページに書かれている物件記号。

    **旧版にも必ずこれと同じ記号を書く。** 違う記号を書くと、読み手は
    「別物件の資料が紛れている」と判断して、版の新旧ではなく記号の不一致で
    切り分けてしまう(実測で起きた。3回中2回が null を返し、理由も
    「物件記号が違うので別案件」だった)。それでは版の判断を測れない。
    """
    match = re.search(r"物件記号\s+(\S+)", case.overview_pages[0])
    if match is None:  # pragma: no cover - 定義上起きない
        raise AssertionError(f"{case.set_id}: 概要ページに物件記号が無い")
    return match.group(1)


def superseded_overview_page(case: CaseSet) -> str:
    """旧版(第一版)の工事概要書。現行版と対象範囲・計上ルールが食い違う。"""
    return (
        f"【工事概要書 第一版】 物件記号 {project_code(case)} / {case.title}\n"
        "  版数  第一版(四月十日)\n"
        "\n"
        f"  工事対象範囲   {SUPERSEDED_SCOPE[case.set_id]}\n"
        f"  数量計上ルール {SUPERSEDED_RULE[case.set_id]}\n"
    )


def current_overview_pages(case: CaseSet) -> tuple[str, ...]:
    """現行版の工事概要書。版数の行だけを足してある(中身は変えない)。"""
    header = "  版数  第二版(六月一日)。本書が最新版であり、第一版に優先する。\n\n"
    return tuple(header + page for page in case.overview_pages)


def unlabelled_total_page(case: CaseSet) -> str:
    """但し書きの無い集計表。おとりの数字を、注記なしでそのまま置く。"""
    rows = "\n".join(
        f"   {name:<24}{value:,.2f}" for name, value in case.decoys.items()
    )
    return f"{_TOTAL_TABLE_TITLE[case.set_id]} {case.title}\n\n{rows}\n"


def _hard_filler(case: CaseSet) -> list[str]:
    """難易度2の詰め物から、但し書きを外したもの。

    「参考」「本工事の数量ではない」を消すと、読み手は中身を見るしかなくなる。
    数字は変えていないので、正解が資料に現れることは無い。
    """
    stripped: list[str] = []
    for page in _stale_number_filler(case):
        lines = [
            line
            for line in page.split("\n")
            if "参考" not in line and "本工事の数量ではない" not in line
        ]
        stripped.append("\n".join(lines))
    return stripped


def hard_overview_pages(case: CaseSet) -> tuple[str, ...]:
    """方式Bの段階1に渡す概要ページ。旧版と現行版の両方を含む。

    **旧版も概要ページに含める。** 除いてしまうと、方式Bだけが
    「どちらの版が有効か」という問題を免除され、不公平になる。
    """
    return (superseded_overview_page(case),) + current_overview_pages(case)


def hard_pages(case: CaseSet) -> tuple[str, ...]:
    """難易度4の資料一式(40ページ)。"""
    overview = list(hard_overview_pages(case))
    details = list(case.detail_pages)
    filler = (
        [unlabelled_total_page(case)]
        + _hard_filler(case)
        + _common_filler(case.set_id, case.title)
    )
    need = TARGET_PAGE_COUNT - len(overview) - len(details)
    while len(filler) < need:
        # 共通の詰め物を記号だけ変えて増やす。**番号に数字を使わない。**
        # 数字にすると、たまたま正解と同じ値になって資料に正解が現れる
        # (実際に「補足資料 28」が S2Q3 の正解 28 と衝突した)。
        index = len(filler)
        mark = chr(ord("A") + index % 26) * (1 + index // 26)
        filler.append(
            f"【補足資料 {mark}】 {case.title}\n"
            "  本紙は施工計画の補足であり、数量の根拠とはならない。\n"
        )
    filler = filler[:need]

    tail_len = need + len(details)
    step = tail_len / (len(details) + 1)
    slots = {int(round(step * (i + 1))) - 1 for i in range(len(details))}
    while len(slots) < len(details):  # pragma: no cover - 丸めの保険
        slots.add(max(slots) + 1)
    slots = sorted(slots)[: len(details)]

    tail: list[str] = []
    fi = di = 0
    for pos in range(tail_len):
        if pos in slots and di < len(details):
            tail.append(details[di])
            di += 1
        else:
            tail.append(filler[fi])
            fi += 1
    tail.extend(details[di:])
    return tuple(overview + tail)


def hard_detail_pages(case: CaseSet) -> tuple[str, ...]:
    """方式Bの段階2に渡すページ。概要(旧版・現行版とも)を抜いた残り。"""
    overview = set(hard_overview_pages(case))
    return tuple(page for page in hard_pages(case) if page not in overview)


def all_hard() -> dict[str, tuple[str, ...]]:
    return {case.set_id: hard_pages(case) for case in ALL_SETS}
