"""画像軸: Grounding DINO(ゼロショット記号検出)のアダプタ。

`6軸_実装詳細設計書.md` の「`axes/image_axis/grounding_dino_adapter.py`:閾値調整可能な
ラッパー」に対応します。設計書が指摘しているとおり、**`box_threshold` と
`text_threshold` の 2 つの閾値こそが `confident / low_confidence / abstained` の
確信度ステータスに直結するパラメータ**なので、このモジュールの中心は

1. モデルの生スコアを取り出すバックエンドの抽象化
2. その生スコアを v8 設計の確信度ステータスと「候補値レンジ」に落とし込む変換規則

の 2 点です。1 と 2 を分けてあるため、モデルが手元で動かせない環境でも 2 の規則は
そのままテスト・検証できます。

------------------------------------------------------------------------------
ステータス割り当ての規則(本モジュールの提案)
------------------------------------------------------------------------------
記号カテゴリごとに、受理しきい値 ``box_threshold`` / ``text_threshold``(実例値は
0.30 / 0.25)と、余裕幅 ``confident_margin``(既定 0.15)を持たせます。

============================ ================== ==============================
検出 1 件ごとの条件           個別の扱い          軸の出力への寄与
============================ ================== ==============================
両スコアが 受理しきい値+余裕幅 confident          レンジの下限・上限の両方に数える
以上
両スコアが 受理しきい値 以上   low_confidence     レンジの上限にだけ数える
(ただし余裕幅未満)
どちらかが 受理しきい値 未満   rejected           数えない
============================ ================== ==============================

軸全体のステータスは次のように決めます。

- 受理された検出が 1 件も無い → ``abstained``(v8 の「分布外の軸はレンジを主張せず
  棄権する」に対応。誤って 0 個と断定しない)
- 受理はされたが confident が 1 件も無い → ``low_confidence``
- confident が 1 件以上あり、かつ low_confidence の件数が confident の件数を
  超えない → ``confident``
- confident はあるが low_confidence の方が多い → ``low_confidence``
  (候補が割れている状態を confident と呼ばない)

出力は単一値ではなく ``count_range = (confident 件数, confident+low 件数)`` という
**レンジ + 根拠**です。これは v8 の「各軸は単一値ではなく『あり得る範囲 + 根拠』を
出す」(3-2 多軸レンジ収束)にそのまま乗ります。

------------------------------------------------------------------------------
バックエンドについて
------------------------------------------------------------------------------
``HuggingFaceGroundingDinoBackend`` が本番用です。ただし **重みの取得元である
huggingface.co が遮断されている環境では動きません**(段階A検証時の実測: 403)。
その場合でもアダプタとしきい値規則の検証ができるよう、``StaticBackend`` を
同梱しています。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence

import numpy as np

ConfidenceStatus = Literal["confident", "low_confidence", "abstained"]
"""v8 設計の 3 段階確信度ステータス。

NOTE: 軸の共通インターフェース(`axes/base.py`)は別途ステップ2で実装される予定です。
それが入った時点で、このモジュールのローカル定義はそちらへ寄せてください。
"""

DEFAULT_BOX_THRESHOLD = 0.30
"""検出の確からしさの下限。`6軸_実装詳細設計書.md` に載っている実例値。"""

DEFAULT_TEXT_THRESHOLD = 0.25
"""テキストとの一致度の下限。同上。"""

DEFAULT_CONFIDENT_MARGIN = 0.15
"""受理しきい値に上乗せする余裕幅。これを超えたものだけを confident と呼ぶ。"""


@dataclass(frozen=True)
class RawDetection:
    """バックエンドが返す、しきい値を掛ける前の生の検出結果。

    box は (x1, y1, x2, y2) の画素座標。
    """

    box: tuple[float, float, float, float]
    box_score: float
    text_score: float
    label: str = ""


@dataclass(frozen=True)
class ScoredDetection:
    """しきい値を適用した後の検出結果。"""

    detection: RawDetection
    verdict: Literal["confident", "low_confidence", "rejected"]

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.detection.box

    @property
    def box_score(self) -> float:
        return self.detection.box_score

    @property
    def text_score(self) -> float:
        return self.detection.text_score


@dataclass(frozen=True)
class SymbolCountReading:
    """1 カテゴリ分の記号検出の読み取り結果(レンジ + 根拠 + 確信度)。"""

    category: str
    prompt: str
    count_range: tuple[int, int]
    status: ConfidenceStatus
    detections: tuple[ScoredDetection, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def confident_detections(self) -> tuple[ScoredDetection, ...]:
        return tuple(d for d in self.detections if d.verdict == "confident")

    @property
    def low_confidence_detections(self) -> tuple[ScoredDetection, ...]:
        return tuple(d for d in self.detections if d.verdict == "low_confidence")

    @property
    def accepted_detections(self) -> tuple[ScoredDetection, ...]:
        return tuple(d for d in self.detections if d.verdict != "rejected")


# ---------------------------------------------------------------------------
# バックエンド
# ---------------------------------------------------------------------------


class DetectionBackend(Protocol):
    """画像 + テキストプロンプト → 生の検出結果。

    **しきい値は適用しない**こと。しきい値と確信度の割り当てはアダプタ側の責務で、
    ここで先に切ってしまうと low_confidence 帯の情報が失われます。
    """

    def detect(self, image: np.ndarray, prompt: str) -> Sequence[RawDetection]: ...


class StaticBackend:
    """あらかじめ与えた検出結果をそのまま返すバックエンド。

    しきい値規則・ステータス割り当ての単体テスト用、および実モデルが使えない環境で
    アダプタの配線を確認するために使います。実際の検出は一切しません。
    """

    def __init__(self, responses: dict[str, Sequence[RawDetection]] | None = None) -> None:
        self._responses = dict(responses or {})
        self.calls: list[tuple[tuple[int, ...], str]] = []

    def set_response(self, prompt: str, detections: Sequence[RawDetection]) -> None:
        self._responses[prompt] = list(detections)

    def detect(self, image: np.ndarray, prompt: str) -> Sequence[RawDetection]:
        self.calls.append((image.shape, prompt))
        return list(self._responses.get(prompt, []))


class HuggingFaceGroundingDinoBackend:
    """transformers 経由の本番バックエンド(IDEA-Research/grounding-dino-*)。

    重みの取得に huggingface.co への到達が必要です。到達できない環境では
    ``RuntimeError`` を送出します(黙って 0 件を返して「検出できなかった」ように
    見せることはしません)。

    ``box_threshold`` / ``text_threshold`` はここでは掛けず、プロセッサ側の
    しきい値を十分低く設定して生スコアを取り出し、判定はアダプタに委ねます。
    """

    def __init__(
        self,
        model_id: str = "IDEA-Research/grounding-dino-tiny",
        device: str = "cpu",
        raw_score_floor: float = 0.05,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.raw_score_floor = raw_score_floor
        self._processor = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise RuntimeError(
                "Grounding DINO を使うには torch と transformers が必要です: "
                f"{exc}"
            ) from exc
        try:
            self._processor = AutoProcessor.from_pretrained(self.model_id)
            self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
                self.model_id
            ).to(self.device)
        except Exception as exc:  # pragma: no cover - 環境依存
            raise RuntimeError(
                f"Grounding DINO の重み({self.model_id})を取得できませんでした。"
                "huggingface.co に到達できない環境ではこのバックエンドは使えません: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def detect(self, image: np.ndarray, prompt: str) -> Sequence[RawDetection]:  # pragma: no cover
        self._ensure_loaded()
        import torch
        from PIL import Image

        if image.ndim == 2:
            pil = Image.fromarray(image).convert("RGB")
        else:
            pil = Image.fromarray(image[:, :, ::-1])

        inputs = self._processor(images=pil, text=prompt, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(**inputs)

        results = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=self.raw_score_floor,
            text_threshold=self.raw_score_floor,
            target_sizes=[pil.size[::-1]],
        )[0]

        detections: list[RawDetection] = []
        for box, score, label in zip(
            results["boxes"], results["scores"], results.get("text_labels", results.get("labels"))
        ):
            x1, y1, x2, y2 = (float(v) for v in box.tolist())
            # transformers の post_process は box と text のスコアを 1 本に統合して
            # 返すため、同じ値を両方に入れる。分離したい場合は outputs.logits から
            # 直接取り出す実装に差し替えること。
            s = float(score)
            detections.append(
                RawDetection((x1, y1, x2, y2), box_score=s, text_score=s, label=str(label))
            )
        return detections


# ---------------------------------------------------------------------------
# アダプタ本体
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryConfig:
    """記号カテゴリ 1 つ分のプロンプトとしきい値。"""

    category: str
    prompt: str
    box_threshold: float = DEFAULT_BOX_THRESHOLD
    text_threshold: float = DEFAULT_TEXT_THRESHOLD
    confident_margin: float = DEFAULT_CONFIDENT_MARGIN

    def __post_init__(self) -> None:
        for name in ("box_threshold", "text_threshold"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} は 0.0〜1.0 の範囲で指定してください: {value}")
        if self.confident_margin < 0.0:
            raise ValueError(
                f"confident_margin は 0 以上で指定してください: {self.confident_margin}"
            )


#: 平面図でよく使う記号カテゴリの既定プロンプト。
#: 閾値はカテゴリごとにチューニングする前提の初期値です。
DEFAULT_CATEGORIES: tuple[CategoryConfig, ...] = (
    CategoryConfig("door", "a door symbol in a floor plan"),
    CategoryConfig("window", "a window symbol in a floor plan"),
    CategoryConfig("outlet", "an electrical outlet symbol in a floor plan"),
)


class GroundingDinoAdapter:
    """テキストプロンプトで記号を検出し、v8 の確信度ステータスに変換する。"""

    def __init__(
        self,
        backend: DetectionBackend,
        categories: Sequence[CategoryConfig] = DEFAULT_CATEGORIES,
    ) -> None:
        self.backend = backend
        self.categories = {c.category: c for c in categories}
        if not self.categories:
            raise ValueError("categories が空です")

    # -- しきい値の適用 ---------------------------------------------------
    @staticmethod
    def classify_detection(
        detection: RawDetection, config: CategoryConfig
    ) -> Literal["confident", "low_confidence", "rejected"]:
        """検出 1 件を confident / low_confidence / rejected に振り分ける。"""
        if (
            detection.box_score < config.box_threshold
            or detection.text_score < config.text_threshold
        ):
            return "rejected"
        if (
            detection.box_score >= config.box_threshold + config.confident_margin
            and detection.text_score >= config.text_threshold + config.confident_margin
        ):
            return "confident"
        return "low_confidence"

    @staticmethod
    def _axis_status(n_confident: int, n_low: int) -> ConfidenceStatus:
        """軸全体のステータスを決める。"""
        if n_confident == 0 and n_low == 0:
            # 受理できる検出が 1 件も無い。0 個と断定せず棄権する。
            return "abstained"
        if n_confident == 0:
            return "low_confidence"
        if n_low > n_confident:
            # 候補が割れている状態を confident とは呼ばない。
            return "low_confidence"
        return "confident"

    # -- 公開 API ---------------------------------------------------------
    def detect_category(
        self,
        image: np.ndarray,
        category: str,
        *,
        box_threshold: float | None = None,
        text_threshold: float | None = None,
        confident_margin: float | None = None,
    ) -> SymbolCountReading:
        """1 カテゴリを検出し、レンジ + 根拠 + 確信度として返す。

        ``box_threshold`` 等を渡すと、そのカテゴリの既定値を一時的に上書きします
        (チューニングの実験用)。
        """
        if category not in self.categories:
            raise KeyError(f"未登録のカテゴリです: {category}")
        base = self.categories[category]
        config = CategoryConfig(
            category=base.category,
            prompt=base.prompt,
            box_threshold=base.box_threshold if box_threshold is None else box_threshold,
            text_threshold=base.text_threshold if text_threshold is None else text_threshold,
            confident_margin=(
                base.confident_margin if confident_margin is None else confident_margin
            ),
        )

        raw = list(self.backend.detect(image, config.prompt))
        scored = tuple(
            ScoredDetection(d, self.classify_detection(d, config)) for d in raw
        )
        n_confident = sum(1 for d in scored if d.verdict == "confident")
        n_low = sum(1 for d in scored if d.verdict == "low_confidence")

        return SymbolCountReading(
            category=config.category,
            prompt=config.prompt,
            count_range=(n_confident, n_confident + n_low),
            status=self._axis_status(n_confident, n_low),
            detections=scored,
            evidence={
                "box_threshold": config.box_threshold,
                "text_threshold": config.text_threshold,
                "confident_margin": config.confident_margin,
                "raw_detection_count": len(raw),
                "rejected_count": len(raw) - n_confident - n_low,
                "backend": type(self.backend).__name__,
            },
        )

    def detect_all(self, image: np.ndarray) -> dict[str, SymbolCountReading]:
        """登録済みの全カテゴリを検出する。"""
        return {name: self.detect_category(image, name) for name in self.categories}
