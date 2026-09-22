"""案件ごとに**1回だけ人に聞く**質問と、その回答の保管。

なぜこれが要るのか
------------------
P011 の図面には床面積が 2 つ書かれている(専有延床面積 95.54㎡ / 施工床面積
90.61㎡)。見積の正解は専有延床のほうだが、**どちらを使うかは図面のどこにも
書かれていない。** 図面を何回読み直しても決まらないので、これは読み取りの
精度の問題ではなく、人に聞く以外に決めようがない項目である
(`docs/real_drawing_eval_report.md` の判断待ち)。

同じ質問を養生・墨出し・清掃と項目ごとに繰り返すのは人の手間でしかないので、
**案件ごとに1回聞いて、同じ案件の他の項目に使い回す。**

回答が無い間は確定しない
------------------------
回答が無いときに「たぶん専有延床だろう」と埋めるのは、トライアル15 で
実際に起きた失敗(情報が欠けている場所をもっともらしい一般則で埋め、
自信を持って違う値を出す)と同じ形になる。だから未回答の質問は
`PendingQuestion` として残し、**その質問に依存する数量は一切出さない。**

保管場所はリポジトリの外
------------------------
回答は案件の情報なので、リポジトリには置かない。`AnswerStore` は設定で
渡された JSON ファイルだけを読み書きし、パスを既定値として持たない。
パスを渡さなければ、その実行の中だけで有効な(保存しない)保管になる。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

#: 質問ID。案件ごとに1回だけ聞く。
QUESTION_AREA_BASIS = "area_basis"

#: 面積の選択肢。図面にこの名前で書かれている2つの記載に対応する。
AREA_BASIS_OPTIONS: tuple[str, ...] = ("専有延床面積", "施工床面積")

#: この回答を使い回す見積項目(人に質問を見せるときの説明に使う)。
#: **ここに並んでいる項目の数量を、このモジュールが計算することはない。**
#: 同じ床面積を基準に拾う項目がどれかを、人が判断できるようにするためだけの
#: 一覧である。
AREA_BASIS_DEPENDENT_ITEMS: tuple[str, ...] = ("養生", "墨出し", "清掃")


class AnswerError(Exception):
    """回答の保存が受け付けられなかった。"""


@dataclass(frozen=True)
class Answer:
    """人が1回答えた内容と、誰がいつ答えたか。"""

    question_id: str
    answer: str
    answered_by: str
    answered_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "answered_by": self.answered_by,
            "answered_at": self.answered_at,
        }


@dataclass(frozen=True)
class PendingQuestion:
    """まだ回答が無い質問。**これがある限り、依存する数量は確定しない。**"""

    question_id: str
    case_id: str
    question: str
    options: tuple[str, ...]
    #: 図面から実際に読めた値。人が選ぶときの材料。
    observed: tuple[tuple[str, float], ...]
    #: この回答が無いために確定しない対象。
    blocks: tuple[str, ...]


class AnswerStore:
    """案件ごとの回答を保持する。`path` を渡すと JSON ファイルに保存する。

    `path` が None のときは、そのプロセスの中だけで有効な保管になる
    (テストと、保存先をまだ決めていない実行のため)。
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._data: dict[str, dict[str, dict[str, str]]] = {}
        if self._path is not None and self._path.exists():
            self._data = self._load(self._path)

    @staticmethod
    def _load(path: Path) -> dict[str, dict[str, dict[str, str]]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            # 壊れたファイルを空として扱うと、保存済みの回答が黙って消えて
            # もう一度人に聞くことになる。気づけるように落とす。
            raise AnswerError(f"回答ファイルを読めません: {path} ({error})") from None
        if not isinstance(raw, Mapping):
            raise AnswerError(f"回答ファイルの形式が不正です: {path}")
        out: dict[str, dict[str, dict[str, str]]] = {}
        for case_id, answers in raw.items():
            if not isinstance(case_id, str) or not isinstance(answers, Mapping):
                raise AnswerError(f"回答ファイルの形式が不正です: {path}")
            out[case_id] = {
                str(question_id): dict(value)
                for question_id, value in answers.items()
                if isinstance(value, Mapping)
            }
        return out

    def get(self, case_id: str, question_id: str) -> Answer | None:
        """回答を1件取り出す。まだ答えられていなければ None。"""
        entry = self._data.get(case_id, {}).get(question_id)
        if entry is None:
            return None
        return Answer(
            question_id=question_id,
            answer=str(entry.get("answer", "")),
            answered_by=str(entry.get("answered_by", "")),
            answered_at=str(entry.get("answered_at", "")),
        )

    def record(
        self,
        case_id: str,
        question_id: str,
        answer: str,
        *,
        answered_by: str,
        options: tuple[str, ...] | None = None,
        answered_at: str | None = None,
    ) -> Answer:
        """回答を保存する。

        `options` を渡すと、その中の値かどうかを確かめる。選択肢の外の値を
        黙って受けると、回答の取り違えが下流の全項目に伝わる。

        **既に回答がある質問には上書きしない。** 同じ案件で答えが2回変わると、
        どちらの答えで拾った数量なのかが分からなくなる。答えを変えるときは
        `override=True` を明示する経路を別に作ること(今は無い)。
        """
        if not case_id or not question_id:
            raise AnswerError("case_id と question_id は空にできません")
        if options is not None and answer not in options:
            raise AnswerError(
                f"回答 {answer!r} は選択肢 {list(options)} にありません"
            )
        if not answered_by:
            raise AnswerError("誰が答えたかを空にはできません")
        existing = self.get(case_id, question_id)
        if existing is not None:
            raise AnswerError(
                f"案件 {case_id} の {question_id} には既に回答があります"
                f"({existing.answer})。案件ごとに1回だけ聞く約束のため上書きしません"
            )
        record = Answer(
            question_id=question_id,
            answer=answer,
            answered_by=answered_by,
            answered_at=answered_at or datetime.now(timezone.utc).isoformat(),
        )
        self._data.setdefault(case_id, {})[question_id] = record.as_dict()
        self._flush()
        return record

    def _flush(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = self._data
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
