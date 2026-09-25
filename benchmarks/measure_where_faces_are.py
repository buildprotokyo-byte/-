"""周14(空間): **取れている面が、間取りのどこなのか**を数える。

**この道具は数えるだけで、本番の経路には繋がっていない。**
`find_room_outlines` の既定値は 1 つも動かしていない(K-29)。

**なぜこれを測るのか**

周11(閉じ方)・周12(面積の窓)・周13(幅の下限)で、調整つまみは 3 つとも
原因ではなかった。いま通っている面は 67 個あり、**うち幅 600mm 以上が 43 個**
ある。600mm は実装自身が「室や廊下はここから」と書いている値である。
**室としてありうる幅の面は取れている。なのに天井高の行き先にならない。**
そこで数えるのは「面がいくつあるか」ではなく「**その面が間取りのどこか**」。

**見立て(測る前に書いた。外れてもそのまま報告する)**

天井高が書かれている場所(居間・食堂のような**居室**)と、面が閉じている
場所(浴室・便所・収納・廊下のような**非居室**)が、別なのではないか。

**区分の語はこちらが用意した知識である**

図面が刷っている語がこの表に無ければ「その他」に落ちる。だから
**その他の件数を必ず出す**。その他が居室・非居室の合計より多ければ、
表が図面に合っていないので**線3・線4 の結論は出さない**。

**図面が刷っている語そのものは既定では返さない**(``--with-text`` を
付けたときだけ返し、その出力は共有フォルダにしか置かない)。

基準は `docs/loop_round14_where_are_faces_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    _inside,
    build_plan_graph,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from benchmarks.measure_height_destination import (  # noqa: E402
    FALLBACK_DENOMINATOR,
    ceiling_notes,
    land,
    scatter,
)

#: 室としてありうる幅(ミリメートル)。**実装自身の説明文から取った値**で、
#: こちらが選んだ値ではない(`pdf_room_outlines.py` 冒頭:
#: 「壁の中身は 150mm 程度、室や廊下は 600mm 以上」)。
ROOM_WIDTH_MM = 600.0

#: 居室(人が居る部屋)の語。**測る前に決めた。測ってから足さない。**
LIVING_WORDS = (
    "居間", "リビング", "LDK", "LD", "DK", "食堂", "ダイニング",
    "台所", "キッチン", "洋室", "和室", "寝室", "主寝室", "書斎",
    "子供室", "子供部屋", "客間", "応接", "事務室", "執務室",
    "会議室", "ホール", "店舗", "教室",
)

#: 非居室(水回り・収納・通路)の語。**測る前に決めた。測ってから足さない。**
SERVICE_WORDS = (
    "浴室", "ユニットバス", "UB", "洗面", "脱衣", "便所", "トイレ", "WC",
    "収納", "押入", "物入", "納戸", "クローゼット", "CL", "WIC",
    "玄関", "廊下", "階段", "土間", "勝手口", "PS", "パイプスペース",
    "DS", "倉庫", "給湯室", "湯沸", "バルコニー", "ベランダ",
    "テラス", "物干",
)

LIVING = "居室"
SERVICE = "非居室"
OTHER = "その他"


def classify(text: str) -> str:
    """文字が区分の語を**含む**かで分ける。当たらなければ「その他」。

    **「ホール」は居室側と「ホール階段」の両方に出るので、
    先に非居室を見る**(階段・廊下のほうが狭い意味だから)。
    """
    for word in SERVICE_WORDS:
        if word in text:
            return SERVICE
    for word in LIVING_WORDS:
        if word in text:
            return LIVING
    return OTHER


def room_name_spans(
    spans: list[tuple[str, tuple[float, float]]],
) -> list[tuple[str, tuple[float, float], str]]:
    """区分の語に当たった文字だけを返す。**その他は落とす。**"""
    out = []
    for text, center in spans:
        kind = classify(text)
        if kind != OTHER:
            out.append((text, center, kind))
    return out


def faces_with_any(
    points: list[tuple[float, float]],
    polygons: list[tuple[tuple[float, float], ...]],
) -> int:
    """点が 1 つ以上入った**面の数**(点の数ではない)。"""
    _, per_face = land(points, polygons)
    return len(per_face)


def measure_page(pdf_path: Path, page_index: int, seed: int, with_text: bool) -> dict:
    """1 ページ分。**天井高の値は返さない。室名は ``with_text`` のときだけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        notes = ceiling_notes(page)
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()
    if not notes:
        return {"ページ": page_index + 1, "注記": 0}

    scale = extract_scale(pdf_path, page_index)
    if scale is None:
        scale = DrawingScale(
            denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
        )
    faces = find_room_outlines(pdf_path, page_index, scale)
    graph = build_plan_graph(pdf_path, page_index, scale)
    spans = graph.spans if graph is not None else []

    wide = [face for face in faces if face.min_width_mm >= ROOM_WIDTH_MM]
    wide_polygons = [face.polygon_pt for face in wide]

    # 線1: 天井高の注記が入った面の数(幅 600mm 以上のなかで)
    real_faces = faces_with_any(notes, wide_polygons)
    decoy_points = scatter(notes, width, height, seed + page_index)
    decoy_faces = faces_with_any(decoy_points, wide_polygons)

    # 線2: 室名の語が入った面の数。**ちょうど 1 つ入った面だけ数える**
    # (2 つ以上は「名前が決まらない」。基準の落とし穴 2)。
    named = room_name_spans(spans)
    name_points = [center for _, center, _ in named]
    _, real_per_face = land(name_points, wide_polygons)
    real_one = [index for index, count in real_per_face.items() if count == 1]
    real_many = [index for index, count in real_per_face.items() if count >= 2]

    decoy_names = scatter(name_points, width, height, seed + 1000 + page_index)
    _, decoy_per_face = land(decoy_names, wide_polygons)
    decoy_one = sum(1 for count in decoy_per_face.values() if count == 1)

    # 線3: 面の側の区分と、紙の側の区分(クラスの事前分布)
    kind_of_face: dict[int, str] = {}
    text_of_face: dict[int, str] = {}
    for text, center, kind in named:
        for index in real_one:
            if _inside(center, list(wide_polygons[index])):
                kind_of_face[index] = kind
                text_of_face[index] = text
                break
    face_living = sum(1 for kind in kind_of_face.values() if kind == LIVING)
    face_service = sum(1 for kind in kind_of_face.values() if kind == SERVICE)
    paper_living = sum(1 for _, _, kind in named if kind == LIVING)
    paper_service = sum(1 for _, _, kind in named if kind == SERVICE)
    paper_other = sum(1 for text, _ in spans if classify(text) == OTHER)

    # 線4: 天井高が入った面(幅で絞らず全部)が、どちらの区分の名前を持つか。
    # **数えるだけ。判定には使わない。**
    all_polygons = [face.polygon_pt for face in faces]
    _, note_per_face = land(notes, all_polygons)
    order = sorted(
        range(len(faces)), key=lambda index: faces[index].area_sqm, reverse=True
    )
    rank = {face: position + 1 for position, face in enumerate(order)}
    receivers = []
    for index in sorted(note_per_face):
        inside_names = [
            kind
            for text, center, kind in named
            if _inside(center, list(all_polygons[index]))
        ]
        receivers.append(
            {
                "面積の順位": rank[index],
                "面積㎡": round(faces[index].area_sqm, 2),
                "幅mm": round(faces[index].min_width_mm, 1),
                "入った注記": note_per_face[index],
                "中にあった室名の区分": inside_names or ["名前なし"],
            }
        )

    row = {
        "ページ": page_index + 1,
        "注記": len(notes),
        "閉じた面": len(faces),
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        "線1_天井高が入った面": real_faces,
        "線1_囮": decoy_faces,
        "線2_室名が1つ入った面": len(real_one),
        "線2_囮": decoy_one,
        "参考_室名が2つ以上入った面": len(real_many),
        "線3_面の側_居室": face_living,
        "線3_面の側_非居室": face_service,
        "線3_紙の側_居室": paper_living,
        "線3_紙の側_非居室": paper_service,
        "参考_区分に当たらなかった文字": paper_other,
        "線4_天井高を受けた面": receivers,
    }
    if with_text:
        row["室名の文字(共有フォルダにのみ置く)"] = sorted(text_of_face.values())
    return row


def ratio(service: int, living: int) -> float | None:
    """非居室の割合。**どちらも 0 なら割合は無い(None)。**"""
    total = service + living
    return None if total == 0 else service / total


def measure(pdf_path: Path, seed: int, with_text: bool) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed, with_text) for index in range(pages)]
    with_notes = [row for row in rows if row["注記"]]

    def total(key: str) -> int:
        return sum(row[key] for row in with_notes)

    line1_real = total("線1_天井高が入った面")
    line1_decoy = total("線1_囮")
    line2_real = total("線2_室名が1つ入った面")
    line2_decoy = total("線2_囮")
    face_service = total("線3_面の側_非居室")
    face_living = total("線3_面の側_居室")
    paper_service = total("線3_紙の側_非居室")
    paper_living = total("線3_紙の側_居室")
    other = total("参考_区分に当たらなかった文字")

    face_ratio = ratio(face_service, face_living)
    paper_ratio = ratio(paper_service, paper_living)
    #: 語の表が図面に合っているか。**合っていなければ線3・線4 の結論は出さない。**
    table_fits = (paper_service + paper_living) >= other

    if face_ratio is None or paper_ratio is None or not table_fits:
        line3 = {"判定できない": True}
    else:
        line3 = {
            "面の側の非居室の割合": round(face_ratio, 3),
            "紙の側の非居室の割合": round(paper_ratio, 3),
            "差(ポイント)": round((face_ratio - paper_ratio) * 100, 1),
            "見立てどおり": (face_ratio - paper_ratio) * 100 >= 10.0,
        }

    return {
        "ページ": rows,
        "線1_天井高が入った面": {
            "本物": line1_real,
            "囮": line1_decoy,
            "通過": line1_real > line1_decoy,
        },
        "線2_室名が1つ入った面": {
            "本物": line2_real,
            "囮": line2_decoy,
            "通過": line2_real > line2_decoy,
        },
        "線3_見立て": line3,
        "参考_語の表が図面に合っているか": {
            "区分に当たった文字": paper_service + paper_living,
            "当たらなかった文字": other,
            "合っている": table_fits,
        },
        "合計": {
            "注記": total("注記"),
            "閉じた面": total("閉じた面"),
            f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": total(f"幅{ROOM_WIDTH_MM:.0f}mm以上の面"),
            "室名が2つ以上入った面": total("参考_室名が2つ以上入った面"),
        },
    }


def build_check_sheet(path: Path) -> None:
    """**合成の紙**: 室に名前を刷り、**居室のほうにだけ天井高を書く。**

    実図面で見立てが当たっていたら、こう見えるはずという形そのもの。
    **数え方が正しいかを先に確かめるための紙で、実図面の代わりではない**
    (周12 の教訓: 合成で効いても実図面で効くとは限らない)。

    **升目を格子にしない。**格子にして全部の升目に文字を入れると、
    `find_room_outlines` が**平面図を罫線の表と取り違えて 0 件を返す**
    (この実装が自分の説明文に書いている落とし穴そのもの)。
    だから左側は仕切りを増やして**文字の無い升目を作ってある。**
    """
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    shape.draw_line(pymupdf.Point(300, 50), pymupdf.Point(300, 350))
    shape.draw_line(pymupdf.Point(300, 200), pymupdf.Point(550, 200))
    for y in (120, 190, 270):
        shape.draw_line(pymupdf.Point(50, y), pymupdf.Point(300, y))
    shape.finish(width=1)
    shape.commit()
    # **日本語の内蔵書体を指名する。**既定の書体では字が "··" に化けて、
    # 区分の語に 1 つも当たらなくなる(この確かめ自体が嘘になる)。
    page.insert_text((100, 160), "洋室", fontsize=9, fontname="japan")
    page.insert_text((150, 160), "CH=2400", fontsize=9)
    page.insert_text((360, 130), "便所", fontsize=9, fontname="japan")
    page.insert_text((360, 280), "収納", fontsize=9, fontname="japan")
    document.save(path)
    document.close()


def check_definition(tmp_dir: Path) -> dict:
    """**数え方そのものを合成の紙で確かめる**(周11 の教訓)。

    3 つの室に名前を刷り、居室にだけ天井高を書いた紙で、
    **区分がそのとおりに分かれるか**を見る。分かれなければ、
    実図面に当てても意味を取り違える。
    """
    path = tmp_dir / "check.pdf"
    build_check_sheet(path)
    row = measure_page(path, 0, 20260925, with_text=False)
    return {
        "面の側_居室": row["線3_面の側_居室"],
        "面の側_非居室": row["線3_面の側_非居室"],
        "天井高が入った面": row["線1_天井高が入った面"],
        "室名が1つ入った面": row["線2_室名が1つ入った面"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf", type=Path, nargs="?", help="図面 PDF の場所(設定で渡す)"
    )
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument(
        "--check",
        type=Path,
        default=None,
        help="合成の紙で数え方だけを確かめて終わる(作業用の置き場所を渡す)",
    )
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="室名の文字も返す。**出力は共有フォルダにしか置かないこと。**",
    )
    args = parser.parse_args()
    if args.check is not None:
        print(json.dumps(check_definition(args.check), ensure_ascii=False, indent=2))
        return 0
    result = measure(args.pdf, args.seed, args.with_text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
