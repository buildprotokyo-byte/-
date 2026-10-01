"""AI を呼ぶ口(K-61)。**鍵があればその場で呼び、無ければ指示を書き出して答えを待つ。どちらでも止めない。**

鍵の置き場所
------------
- 環境変数 ``ANTHROPIC_API_KEY``(または ``ANTHROPIC_AUTH_TOKEN``)。部品は ``pip install anthropic``。
- モデルは環境変数 ``DRAFT_AI_MODEL``(既定 `DEFAULT_MODEL`)。段ごとに変えるときは ``DRAFT_AI_MODEL_<段>``
  (段は ORGANIZE 整理・PASS1 通読・REREAD 読み直し・UNDERSTAND 理解・ORIGINAL 仕上表の原本。
  例 ``DRAFT_AI_MODEL_PASS1=claude-sonnet-5-5``。K-62 の手段 c)。既定のモデル以外で読んだ答えは、指紋を分けて
  既定のモデルの答えと混ぜない。
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
#: 1 回の出力の上限(モデルの一覧の値)。考える分もここに入る。
MAX_OUTPUT_TOKENS = {"claude-opus-5-5": 128000, "claude-opus-5": 128000, "claude-sonnet-5-5": 128000,
                     "claude-haiku-4-5": 64000}

#: 費用の概算に使う単価(ドル / 100 万トークン)。モデルの一覧の値(2026-09)。
PRICE_PER_MTOK = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
#: キャッシュから読んだ分の単価(ドル / 100 万トークン)。モデルの一覧の値。Haiku 4.5 は一覧に無いので入力の 1 割と置いた(要確認)。
CACHE_READ_PER_MTOK = {"claude-opus-5-5": 0.20, "claude-sonnet-5-5": 0.20, "claude-haiku-4-5": 0.10}
#: キャッシュに書いた分は入力の 1.25 倍(5 分の保持)。
CACHE_WRITE_FACTOR = 1.25
#: まとめて処理(即時でない処理方式、Message Batches)の値引き。すべての使用量が半額、結果は最長 24 時間後。
BATCH_FACTOR = 0.5
BATCH_KEY = "まとめて処理(半額)"


def price_of(model: str) -> tuple[float, float, float]:
    """(入力, 出力, キャッシュ読み出し) の単価。日付の付いた名前は付かない名前に寄せる。"""
    base = re.sub(r"-\d{8}$", "", model or DEFAULT_MODEL)
    pin, pout = PRICE_PER_MTOK.get(base, PRICE_PER_MTOK[DEFAULT_MODEL])
    return pin, pout, CACHE_READ_PER_MTOK.get(base, pin * 0.1)


def usage_cost(model: str, usage: dict[str, int]) -> float:
    """API が返した使用量から費用(ドル)。出力には考える分(thinking)も入っている。"""
    pin, pout, pread = price_of(model)
    cost = (usage.get("入力", 0) * pin + usage.get("出力", 0) * pout
            + usage.get("キャッシュに書いた", 0) * pin * CACHE_WRITE_FACTOR
            + usage.get("キャッシュから読んだ", 0) * pread) / 1e6
    # まとめて処理(Message Batches)はすべての使用量が 50% 引き。
    return cost * (BATCH_FACTOR if usage.get(BATCH_KEY) else 1.0)


#: 段ごとのモデルを決める環境変数の名前(シェルによっては日本語の変数名が使えないので英字にする)。
STAGE_ENV = {"整理": "ORGANIZE", "通読": "PASS1", "読み直し": "REREAD", "理解": "UNDERSTAND", "仕上表の原本": "ORIGINAL"}


def model_for_stage(stage: str, default: str | None = None) -> str:
    name = STAGE_ENV.get(stage, stage)
    return os.environ.get(f"{MODEL_ENV}_{name}") or default or os.environ.get(MODEL_ENV) or DEFAULT_MODEL

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
    model: str = ""
    #: API が返した使用量(入力・出力・キャッシュに書いた・キャッシュから読んだ)。置かれた答えでは前の記録から読む。
    usage: dict[str, int] | None = None


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
    model: str = ""
    usage: dict[str, int] | None = None
    #: この通しで API を呼んで払ったか(置かれた答えなら False)。
    paid_now: bool = False

    @property
    def cost(self) -> float | None:
        return usage_cost(self.model, self.usage) if self.usage else None

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
            "モデル": self.model,
            "使用量": self.usage,
            "費用(ドル)": self.cost,
            "この通しで払った": self.paid_now,
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

    def __init__(self, answers_dir: Path, model: str | None = None) -> None:
        self.answers_dir = Path(answers_dir)
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.records: list[CallRecord] = []
        self.pending_written: list[Path] = []
        self._lock = threading.Lock()

    def model_for(self, stage: str) -> str:
        return model_for_stage(stage, self.model)

    def fingerprint(self, req: AIRequest) -> str:
        """既定のモデルなら指示の指紋のまま(K-61 の答えをそのまま使える)。ほかのモデルはモデル名も混ぜて分ける。"""
        fp = req.fingerprint()
        model = self.model_for(req.stage)
        if model == DEFAULT_MODEL:
            return fp
        return hashlib.sha256(f"{fp}:{model}".encode()).hexdigest()[:24]

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
        model = self.model_for(req.stage)
        lines = [
            f"# {req.stage}({req.key})",
            *([f"(この段のモデル: {model})", ""] if model != DEFAULT_MODEL else []),
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
        fp = self.fingerprint(req)
        stored = self._stored(fp)
        if stored is not None:
            answer = AIAnswer(stored, SOURCE_FILE, note="", model=self.model_for(req.stage))
            done = self.pending_dir(req, fp)
            if (done / "指示.md").exists():
                (done / "指示.md").rename(done / "答えが置かれた指示.md")
            meta = self.answers_dir / "答え" / f"{fp}.meta.json"
            if meta.exists():
                try:
                    m = json.loads(meta.read_text(encoding="utf-8"))
                    answer.seconds = m.get("秒")
                    answer.usage = m.get("使用量")
                    answer.model = m.get("モデル") or answer.model
                except json.JSONDecodeError:
                    pass
        else:
            answer = self._call_fresh(req, fp)
            if answer.payload is not None and answer.source == SOURCE_API:
                path = self.answer_path(fp)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(answer.payload, ensure_ascii=False), encoding="utf-8")
                (path.parent / f"{fp}.meta.json").write_text(json.dumps(
                    {"秒": answer.seconds, "モデル": answer.model, "使用量": answer.usage}, ensure_ascii=False),
                    encoding="utf-8")
            if answer.payload is None:
                self.write_pending(req, fp)
        out_tokens = len(json.dumps(answer.payload, ensure_ascii=False)) if answer.payload is not None else 0
        record = CallRecord(
            req.stage, req.key, fp, answer.source, len(req.images),
            estimate_input_tokens(req), int(out_tokens / CHARS_PER_TOKEN), answer.seconds, answer.note,
            answer.model or self.model_for(req.stage), answer.usage, answer.source == SOURCE_API and answer.usage is not None,
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
        measured = [r for r in self.records if r.usage]
        real_by_stage: dict[str, dict[str, Any]] = {}
        for r in measured:
            s = real_by_stage.setdefault(r.stage, {"回数": 0, "入力": 0, "出力": 0, "キャッシュに書いた": 0,
                                                   "キャッシュから読んだ": 0, "費用": 0.0, "モデル": set()})
            s["回数"] += 1
            for k in ("入力", "出力", "キャッシュに書いた", "キャッシュから読んだ"):
                s[k] += int(r.usage.get(k, 0))
            s["費用"] += r.cost or 0.0
            s["モデル"].add(r.model)
        total_real = sum(r.cost or 0.0 for r in measured)
        for s in real_by_stage.values():
            s["モデル"] = sorted(s["モデル"])
            s["割合"] = round(s["費用"] / total_real, 3) if total_real else None
            s["費用"] = round(s["費用"], 6)
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
            "使用量で数えた費用(ドル)": {
                "呼び出しの数": len(measured),
                "使用量の無い呼び出し": len(self.records) - len(measured),
                "合計": round(total_real, 6),
                "この通しで払った分": round(sum(r.cost or 0.0 for r in measured if r.paid_now), 6),
                "段ごと": real_by_stage,
                "注": "API が返した使用量(出力には考える分を含む)× モデルの単価。キャッシュの書き込みは入力の 1.25 倍。"
                "使用量の無い呼び出し(鍵が無く人や作業役が答えを置いた分)は入っていない。",
            },
        }


class FolderCaller(AICaller):
    """鍵が無いときの口。置かれた答えだけを使い、無ければ指示を書き出す。"""


class ApiCaller(AICaller):
    """鍵があるときの口。置かれた答えがあればそれを使い、無ければその場で呼ぶ。"""

    def __init__(self, answers_dir: Path, client: Any, model: str, batch: bool = False,
                 poll_seconds: float = 30.0) -> None:
        super().__init__(answers_dir, model)
        self.client = client
        #: 評価用の回だけ: 段ごとの呼び出しをまとめて送る(手段 f)。待つあいだ一本道は止まる。
        self.batch = batch
        self.poll_seconds = poll_seconds
        self._prefetched: dict[str, AIAnswer] = {}

    def params(self, req: AIRequest) -> dict[str, Any]:
        model = self.model_for(req.stage)
        return {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS.get(model, 64000),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
            "system": req.instructions,
            "messages": [{"role": "user", "content": self.content(req)}],
        }

    def map(self, reqs: Sequence[AIRequest], parallel: int, fn: Callable[[AIRequest], AIAnswer] | None = None) -> list[AIAnswer]:
        if not self.batch or fn is not None:
            return super().map(reqs, parallel, fn)
        todo: dict[str, AIRequest] = {}
        for r in reqs:
            fp = self.fingerprint(r)
            if self._stored(fp) is None and fp not in self._prefetched:
                todo[fp] = r
        if todo:
            self._run_batch(todo)
        return [self.call(r) for r in reqs]

    def _run_batch(self, todo: dict[str, AIRequest]) -> None:
        started = time.perf_counter()
        ids = {f"r{i}": fp for i, fp in enumerate(todo)}
        try:
            batch = self.client.messages.batches.create(requests=[
                {"custom_id": cid, "params": self.params(todo[fp])} for cid, fp in ids.items()])
            while getattr(batch, "processing_status", "ended") != "ended":
                time.sleep(self.poll_seconds)
                batch = self.client.messages.batches.retrieve(batch.id)
            results = list(self.client.messages.batches.results(batch.id))
        except Exception as exc:  # noqa: BLE001  失敗しても止めず、1 件ずつの未取得にする
            note = f"まとめて送るのに失敗した: {type(exc).__name__}: {exc}"
            for fp in ids.values():
                self._prefetched[fp] = AIAnswer(None, SOURCE_MISSING, None, note)
            return
        seconds = round(time.perf_counter() - started, 1)
        for res in results:
            fp = ids.get(getattr(res, "custom_id", ""))
            if fp is None:
                continue
            body = getattr(res, "result", None)
            if getattr(body, "type", "") != "succeeded":
                self._prefetched[fp] = AIAnswer(None, SOURCE_MISSING, seconds,
                                                f"まとめて送った 1 件が {getattr(body, 'type', '不明')}")
                continue
            ans = self._parse(body.message, self.model_for(todo[fp].stage), seconds)
            if ans.usage is not None:
                ans.usage[BATCH_KEY] = 1
            self._prefetched[fp] = ans
        for fp in ids.values():
            self._prefetched.setdefault(fp, AIAnswer(None, SOURCE_MISSING, seconds, "まとめて送った結果に無かった"))

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
        if self.batch and fp not in self._prefetched:
            self._run_batch({fp: req})  # 1 件だけの段(整理)も同じ処理方式で送る
        if fp in self._prefetched:
            return self._prefetched.pop(fp)
        started = time.perf_counter()
        model = self.model_for(req.stage)
        try:
            with self.client.messages.stream(**self.params(req)) as stream:
                response = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001  呼び出しの失敗も止めずに理由を残す
            return AIAnswer(None, SOURCE_MISSING, round(time.perf_counter() - started, 1),
                            f"呼び出しが失敗した: {type(exc).__name__}: {exc}")
        return self._parse(response, model, round(time.perf_counter() - started, 1))

    def _parse(self, response: Any, model: str, seconds: float | None) -> AIAnswer:
        if getattr(response, "stop_reason", None) == "refusal":
            return AIAnswer(None, SOURCE_MISSING, seconds, "AI が答えを断った")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = _json_object(text)
        except (ValueError, json.JSONDecodeError) as exc:
            return AIAnswer(None, SOURCE_MISSING, seconds, f"答えの形が違う: {exc}")
        note = "長さの上限で切れた" if getattr(response, "stop_reason", None) == "max_tokens" else ""
        u = getattr(response, "usage", None)
        usage = None
        if u is not None:
            usage = {
                "入力": int(getattr(u, "input_tokens", 0) or 0),
                "出力": int(getattr(u, "output_tokens", 0) or 0),
                "キャッシュに書いた": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                "キャッシュから読んだ": int(getattr(u, "cache_read_input_tokens", 0) or 0),
            }
        return AIAnswer(payload, SOURCE_API, seconds, note, str(getattr(response, "model", "") or model), usage)


def make_caller(answers_dir: Path, client: Any = None, model: str | None = None, batch: bool = False,
                poll_seconds: float = 30.0) -> AICaller:
    """鍵があれば `ApiCaller`、無ければ `FolderCaller`。``client`` を渡せば鍵を見ない(テスト用)。"""
    model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    if client is None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return FolderCaller(answers_dir, model)
        try:
            import anthropic  # noqa: PLC0415  入れていなくてもリポジトリは動く
        except ImportError:
            return FolderCaller(answers_dir, model)
        client = anthropic.Anthropic()
    return ApiCaller(answers_dir, client, model, batch=batch, poll_seconds=poll_seconds)
