"""周16(空間): **出典を変えて、仕上表から「どの室があるか」を取る。**

**この道具は数えるだけで、本番の経路には繋いでいない。**
`read_finish_schedules` にも `find_room_outlines` にも手を入れていない(K-29)。

**前の周と何が違うか**

周11〜周15 は技術を 5 通り変えたが、**出典は 5 通りとも同じ**——
この PDF の**線の図形データ 1 つ**だった。**今回は出典ひとつだけを変える。**
線ではなく**内装仕上表の文字**から室名を取る。
紙も種も区分の語の表も、周14・周15 と同じ。

**囮はこの周だけ作りが違う**

位置をばらすのではなく、**列を取り違えたときに同じだけ取れるか**を見る。

- **囮A** … 同じ行の、**室名ではない最初の文字**を取る(行ごとに 1 つなので
  **本物と同じ数**になる)。表から室名が取れているのか、どの列でも
  同じだけ取れるのかを分ける。
- **囮B'** … その囮A の文字のうち、**平面図の文字に現れるものの数**。
  最初に書いた囮B(平面図からでたらめに選ぶ)は**当たりようのない囮**
  だったので、**測る前に差し替えた**(基準の追記)。

**0 件はありうる**

見出しの語が揃わない表は仕上表と名乗らない作りになっている。
**0 件は「仕上表が無い」ではなく「この見出しの表では当たらなかった」。**
**0 件だったときに、しきいや見出しの語をその場で足さない。**

基準は `docs/loop_round16_schedule_source_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.schedule_tables import read_finish_schedules  # noqa: E402
from benchmarks.measure_height_destination import ceiling_notes  # noqa: E402
from benchmarks.measure_where_faces_are import OTHER, classify  # noqa: E402

#: 囮との差が**これ以上**なら通過。**周15 と同じ幅。測る前に決めた。**
MARGIN = 5

#: 落とした行の室名が読めた行の室名のこの割合以上なら、線1 に但し書きを付ける。
SKIPPED_SHARE = 0.2


def plan_pages(pdf_path: Path) -> list[int]:
    """**平面図とみなすページ**= 天井高の注記があるページ。

    周5・周14・周15 と同じ 5 ページ。**ここを変えると周をまたいで
    比べられなくなる。**
    """
    document = pymupdf.open(pdf_path)
    try:
        return [
            index
            for index in range(document.page_count)
            if ceiling_notes(document[index])
        ]
    finally:
        document.close()


def page_texts(pdf_path: Path, page_indexes: list[int]) -> list[str]:
    """そのページに印字されている文字を、升目に分けずそのまま集める。"""
    out: list[str] = []
    document = pymupdf.open(pdf_path)
    try:
        for index in page_indexes:
            for block in document[index].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        if text:
                            out.append(text)
    finally:
        document.close()
    return out


def appears_in(name: str, texts: list[str]) -> bool:
    """**一致**= 平面図の文字のどれかが、その語をそのまま含むこと。"""
    return any(name in text for text in texts)


def collect(pdf_path: Path) -> dict:
    """34 ページ全部に仕上表の読み取りを当て、室名と囮を集める。"""
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()

    rooms: list[str] = []
    decoys: list[str] = []
    skipped: list[str] = []
    per_page: list[dict] = []

    for index in range(pages):
        schedules = read_finish_schedules(pdf_path, index)
        if not schedules:
            continue
        page_rooms: list[str] = []
        page_decoys: list[str] = []
        page_skipped: list[str] = []
        for schedule in schedules:
            for row in schedule.rows:
                if row.room:
                    page_rooms.append(row.room)
                    # 囮A: **同じ行の、室名ではない最初の文字。**
                    # 行ごとに 1 つなので、本物と同じ数になる。
                    other = next(
                        (text for text in row.row_texts if text != row.room), None
                    )
                    if other is not None:
                        page_decoys.append(other)
            for row in schedule.skipped_rows:
                page_skipped.extend(row.texts)
        rooms.extend(page_rooms)
        decoys.extend(page_decoys)
        skipped.extend(page_skipped)
        per_page.append(
            {
                "ページ": index + 1,
                "仕上表": len(schedules),
                "室名の行": len(page_rooms),
                "落とした行の文字": len(page_skipped),
            }
        )

    return {
        "ページごと": per_page,
        "室名": rooms,
        "囮A": decoys,
        "落とした行の文字": skipped,
    }


def distinct_room_words(texts: list[str]) -> list[str]:
    """**区分の語に当たった文字**を、重複を除いて返す。"""
    seen: list[str] = []
    for text in texts:
        if classify(text) != OTHER and text not in seen:
            seen.append(text)
    return seen


def measure(pdf_path: Path, with_text: bool) -> dict:
    started = time.time()
    found = collect(pdf_path)
    pages = plan_pages(pdf_path)
    texts = page_texts(pdf_path, pages)

    real = distinct_room_words(found["室名"])
    decoy_a = distinct_room_words(found["囮A"])
    skipped = distinct_room_words(found["落とした行の文字"])

    real_in_plan = [name for name in real if appears_in(name, texts)]
    decoy_in_plan = [name for name in decoy_a if appears_in(name, texts)]

    share = len(skipped) / len(real) if real else None
    result = {
        "ページごと": found["ページごと"],
        "合計": {
            "仕上表のあるページ": len(found["ページごと"]),
            "室名の行": len(found["室名"]),
            "区分の語に当たった室名(重複なし)": len(real),
            "落とした行の文字": len(found["落とした行の文字"]),
        },
        "線1_仕上表から室名が取れるか": {
            "本物": len(real),
            "囮A": len(decoy_a),
            "差": len(real) - len(decoy_a),
            "通過": len(real) - len(decoy_a) >= MARGIN,
        },
        "線2_平面図にも印字されているか": {
            "本物": len(real_in_plan),
            "囮B'": len(decoy_in_plan),
            "差": len(real_in_plan) - len(decoy_in_plan),
            "通過": len(real_in_plan) - len(decoy_in_plan) >= MARGIN,
        },
        "線3_落とした行に室名が隠れていないか": {
            "落とした行の室名": len(skipped),
            "読めた行の室名": len(real),
            "割合": None if share is None else round(share, 3),
            "但し書きが要る": share is not None and share >= SKIPPED_SHARE,
        },
        "かかった秒": round(time.time() - started, 1),
    }
    if with_text:
        result["文字(共有フォルダにのみ置く)"] = {
            "室名": real,
            "囮A": decoy_a,
            "平面図にもあった室名": real_in_plan,
            "落とした行の室名": skipped,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF の場所(設定で渡す)")
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="文字も返す。**出力は共有フォルダにしか置かないこと。**",
    )
    args = parser.parse_args()
    print(json.dumps(measure(args.pdf, args.with_text), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
