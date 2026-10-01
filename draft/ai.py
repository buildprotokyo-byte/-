"""AI を呼ぶ口(K-61)。**鍵があればその場で呼び、無ければ指示を書き出して答えを待つ。どちらでも止めない。**

鍵の置き場所
------------
- 環境変数 ``ANTHROPIC_API_KEY``(または ``ANTHROPIC_AUTH_TOKEN``)。部品は ``pip install anthropic``。
- モデルは環境変数 ``DRAFT_AI_MODEL``(既定 `DEFAULT_MODEL`)。
- 同時に呼ぶ数の上限は ``--parallel``(既定 `DEFAULT_PARALLEL`)。

鍵が無いとき(このクラウドがそう)
--------------------------------
呼ぶはずだった指示を 1 件ずつ ``<答えのフォルダ>/待っている問い/<段>_<鍵>_<指紋>/指示.md`` に書き出す。
指示には、見てよい画像・添えたデータのパスと、答えを書く先(``<答えのフォルダ>/答え/<指紋>.json``)が
書いてある。誰か(別の AI の作業役、または人)がそこに答えを置けば、次に走らせたときにそれを使う。
答えが無い段は「未取得」として進む。

指紋
----
指示の本文・画像のバイト列・添えたデータから作る。**同じ指示・同じ画像・同じデータなら同じ答えを使い回す。**
画像やデータが 1 バイトでも違えば別の問いになる(古い答えを黙って当てない)。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_MODEL = "claude-opus-5-5"
MODEL_ENV = "DRAFT_AI_MODEL"
DEFAULT_PARALLEL = 8

#: 費用の概算に使う単価(ドル / 100 万トークン)。モデルの一覧の値(2026-09)。
PRICE_PER_MTOK = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
}

#: 画像のトークンの目安: 画素数 / 750(長い辺は 1,568 画素に縮められる前提)。**概算であって請求額ではない。**
IMAGE_LONG_EDGE_FOR_TOKENS = 1568
#: 文字のトークンの目安: 日本語は 1 文字 ≒ 1 トークンとして数える(多めに見積もる)。
CHARS_PER_TOKEN = 1.0

SOURCE_API = "その場で呼んだ"
SOURCE_FILE = "置かれた答え"
SOURCE_MISSING = "未取得"


@dataclass
class AIRequest:
    """1 回の呼び出し。``images`` は PNG/JPEG のパス、``data`` は添える JSON(名前 → 中身)。"""

    stage: str
    key: str
    instructions: str
    images: Sequence[Path] = ()
    data: dict[str, Any] = field(default_factory=dict)
    #: 答えの形の説明(指示.md に書く)。
    answer_shape: str = ""

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.stage.encode())
        h.update(self.instructions.encode())
        for path in self.images:
            h.update(hashlib.sha256(Path(path).read_bytes()).digest())
        h.update(json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode())
        return h.hexdigest()[:24]


@dataclass
class AIAnswer:
    payload: Any
    source: str
    seconds: float | None = None
    note: str = ""


@dataclass
class CallRecord:
    stage: str
    key: str
    fingerprint: str
    source: str
    images: int
    input_tokens_est: int
    output_tokens_est: int
    seconds: float | None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "段": self.stage,
            "鍵": self.key,
            "指紋": self.fingerprint,
            "答えの出どころ": self.source,
            "画像の数": self.images,
            "入力トークンの目安": self.input_tokens_est,
            "出力トークンの目安": self.output_tokens_est,
            "秒": self.seconds,
            **({"メモ": self.note} if self.note else {}),
        }


def image_tokens(path: Path) -> int:
    """画像 1 枚のトークンの目安。"""
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    scale = min(1.0, IMAGE_LONG_EDGE_FOR_TOKENS / max(w, h))
    return int((w * scale) * (h * scale) / 750)


def estimate_input_tokens(req: AIRequest) -> int:
    text = len(req.instructions) + len(json.dumps(req.data, ensure_ascii=False))
    return int(text / CHARS_PER_TOKEN) + sum(image_tokens(Path(p)) for p in req.images)


def _json_object(text: str) -> Any:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("答えに JSON のオブジェクトがありません")
    return json.loads(match.group(0))


class AICaller:
    """呼び出しの記録を持つ土台。``call`` は**例外を投げない**(失敗は未取得の答えにする)。"""

    def __init__(self, answers_dir: Path) -> None:
        self.answers_dir = Path(answers_dir)
        self.records: list[CallRecord] = []
        self.pending_written: list[Path] = []
        self._lock = threading.Lock()

    # 答えのファイル -------------------------------------------------------
    def answer_path(self, fp: str) -> Path:
        return self.answers_dir / "答え" / f"{fp}.json"

    def pending_dir(self, req: AIRequest, fp: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", f"{req.stage}_{req.key}")
        return self.answers_dir / "待っている問い" / f"{safe}_{fp[:8]}"

    def _stored(self, fp: str) -> Any | None:
        path = self.answer_path(fp)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def write_pending(self, req: AIRequest, fp: str) -> Path:
        """指示を、別の作業役がそのまま読める形で書き出す。"""
        folder = self.pending_dir(req, fp)
        folder.mkdir(parents=True, exist_ok=True)
        data_paths = []
        for name, value in req.data.items():
            p = folder / f"{name}.json"
            p.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
            data_paths.append(p)
        answer = self.answer_path(fp)
        lines = [
            f"# {req.stage}({req.key})",
            "",
            req.instructions.strip(),
            "",
            "## 見てよいもの",
            *(f"- 画像: {Path(p).resolve()}" for p in req.images),
            *(f"- データ: {p.resolve()}" for p in data_paths),
            "- 細部は Python の PIL で切り抜いて拡大してよい(切り抜きは "
            f"{(folder / 'work').resolve()} に保存)。",
            "- ほかのファイル(見積・正解・採点の資料、リポジトリ)は見ない。",
            "",
            "## 答えを書く先",
            f"{answer.resolve()}(JSON のオブジェクト 1 つ。量が多ければ Python で組み立てて書いてよい。"
            "ただし中身は自分で見て決めたものにする)",
        ]
        if req.answer_shape:
            lines += ["", "## 答えの形", req.answer_shape.strip()]
        lines += ["", "最後に、書いたファイルのパスと、作業の開始と終了の時刻だけ返してください。"]
        (folder / "指示.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self._lock:
            self.pending_written.append(folder)
        return folder

    def retire_stale(self) -> list[Path]:
        """この通しで出さなかった古い指示を「古い指示.md」にする(前の通しの見立てで出た問いを残さない)。"""
        root = self.answers_dir / "待っている問い"
        stale = []
        if root.exists():
            keep = {p.resolve() for p in self.pending_written}
            for md in root.glob("*/指示.md"):
                if md.parent.resolve() not in keep:
                    md.rename(md.with_name("古い指示.md"))
                    stale.append(md.parent)
        return stale

    # 呼ぶ -----------------------------------------------------------------
    def call(self, req: AIRequest) -> AIAnswer:
        fp = req.fingerprint()
        stored = self._stored(fp)
        if stored is not None:
            answer = AIAnswer(stored, SOURCE_FILE, note="")
            done = self.pending_dir(req, fp)
            if (done / "指示.md").exists():
                (done / "指示.md").rename(done / "答えが置かれた指示.md")
            meta = self.answers_dir / "答え" / f"{fp}.meta.json"
            if meta.exists():
                try:
                    answer.seconds = json.loads(meta.read_text(encoding="utf-8")).get("秒")
                except json.JSONDecodeError:
                    pass
        else:
            answer = self._call_fresh(req, fp)
            if answer.payload is not None and answer.source == SOURCE_API:
                path = self.answer_path(fp)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(answer.payload, ensure_ascii=False), encoding="utf-8")
            if answer.payload is None:
                self.write_pending(req, fp)
        out_tokens = len(json.dumps(answer.payload, ensure_ascii=False)) if answer.payload is not None else 0
        record = CallRecord(
            req.stage, req.key, fp, answer.source, len(req.images),
            estimate_input_tokens(req), int(out_tokens / CHARS_PER_TOKEN), answer.seconds, answer.note,
        )
        with self._lock:
            self.records.append(record)
        return answer

    def _call_fresh(self, req: AIRequest, fp: str) -> AIAnswer:
        return AIAnswer(None, SOURCE_MISSING, note="鍵が無いので呼んでいない。指示を書き出した")

    def map(self, reqs: Sequence[AIRequest], parallel: int, fn: Callable[[AIRequest], AIAnswer] | None = None) -> list[AIAnswer]:
        """並べて呼ぶ。順番は渡した順のまま返す。"""
        fn = fn or self.call
        if parallel <= 1 or len(reqs) <= 1:
            return [fn(r) for r in reqs]
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            return list(pool.map(fn, reqs))

    def summary(self, model: str) -> dict[str, Any]:
        price_in, price_out = PRICE_PER_MTOK.get(model, PRICE_PER_MTOK[DEFAULT_MODEL])
        by_stage: dict[str, dict[str, Any]] = {}
        for r in self.records:
            s = by_stage.setdefault(r.stage, {"回数": 0, "入力トークンの目安": 0, "出力トークンの目安": 0, "未取得": 0})
            s["回数"] += 1
            s["入力トークンの目安"] += r.input_tokens_est
            s["出力トークンの目安"] += r.output_tokens_est
            s["未取得"] += r.source == SOURCE_MISSING
        tin = sum(r.input_tokens_est for r in self.records)
        tout = sum(r.output_tokens_est for r in self.records)
        return {
            "呼んだ回数": len(self.records),
            "答えの出どころ": {
                src: sum(1 for r in self.records if r.source == src)
                for src in (SOURCE_API, SOURCE_FILE, SOURCE_MISSING)
            },
            "段ごと": by_stage,
            "費用の概算(ドル)": {
                "モデル": model,
                "入力": round(tin / 1e6 * price_in, 2),
                "出力(考える分を除く)": round(tout / 1e6 * price_out, 2),
                "注": "画像は画素数/750、文字は1字1トークンの目安。考える分(thinking)は数えていないので、"
                "実際はこれより高くなる。",
            },
        }


class FolderCaller(AICaller):
    """鍵が無いときの口。置かれた答えだけを使い、無ければ指示を書き出す。"""


class ApiCaller(AICaller):
    """鍵があるときの口。置かれた答えがあればそれを使い、無ければその場で呼ぶ。"""

    def __init__(self, answers_dir: Path, client: Any, model: str) -> None:
        super().__init__(answers_dir)
        self.client = client
        self.model = model

    def content(self, req: AIRequest) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for path in req.images:
            p = Path(path)
            media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            blocks.append({"type": "text", "text": f"画像: {p.name}"})
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media,
                           "data": base64.standard_b64encode(p.read_bytes()).decode("ascii")},
            })
        for name, value in req.data.items():
            blocks.append({"type": "text", "text": f"データ {name}:\n" + json.dumps(value, ensure_ascii=False)})
        tail = "指示の形の JSON のオブジェクトだけを答えてください。"
        if req.answer_shape:
            tail = f"答えの形:\n{req.answer_shape}\n" + tail
        blocks.append({"type": "text", "text": tail})
        return blocks

    def _call_fresh(self, req: AIRequest, fp: str) -> AIAnswer:
        started = time.perf_counter()
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=64000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=req.instructions,
                messages=[{"role": "user", "content": self.content(req)}],
            ) as stream:
                response = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001  呼び出しの失敗も止めずに理由を残す
            return AIAnswer(None, SOURCE_MISSING, round(time.perf_counter() - started, 1),
                            f"呼び出しが失敗した: {type(exc).__name__}: {exc}")
        seconds = round(time.perf_counter() - started, 1)
        if getattr(response, "stop_reason", None) == "refusal":
            return AIAnswer(None, SOURCE_MISSING, seconds, "AI が答えを断った")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = _json_object(text)
        except (ValueError, json.JSONDecodeError) as exc:
            return AIAnswer(None, SOURCE_MISSING, seconds, f"答えの形が違う: {exc}")
        note = "長さの上限で切れた" if getattr(response, "stop_reason", None) == "max_tokens" else ""
        return AIAnswer(payload, SOURCE_API, seconds, note)


def make_caller(answers_dir: Path, client: Any = None, model: str | None = None) -> AICaller:
    """鍵があれば `ApiCaller`、無ければ `FolderCaller`。``client`` を渡せば鍵を見ない(テスト用)。"""
    model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    if client is None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return FolderCaller(answers_dir)
        try:
            import anthropic  # noqa: PLC0415  入れていなくてもリポジトリは動く
        except ImportError:
            return FolderCaller(answers_dir)
        client = anthropic.Anthropic()
    return ApiCaller(answers_dir, client, model)
