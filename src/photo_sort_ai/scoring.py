from __future__ import annotations

import base64
import io
import json
import math
import urllib.request
from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image

from .config import Config


@dataclass
class Evaluation:
    score: float
    rating: int
    reason: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalized(value: float, low: float, high: float, lower_better: bool = False) -> float:
    if not math.isfinite(value):
        raise ValueError("モデルがNaNまたは無限大を返しました。")
    score = max(0.0, min(1.0, (value - low) / (high - low)))
    return (1 - score if lower_better else score) * 100


def star_rating(score: float, thresholds: list[float]) -> int:
    if not math.isfinite(score):
        raise ValueError("評価値が有限ではありません。")
    return 1 + sum(score >= t for t in thresholds)


class HeuristicScorer:
    """Cheap image statistics. This is deliberately not described as an AI model."""

    def __init__(self, config: Config):
        self.config = config

    def evaluate(self, image: Image.Image) -> Evaluation:
        small = image.copy()
        small.thumbnail((512, 512), Image.Resampling.LANCZOS)
        rgb = np.asarray(small.convert("RGB"), dtype=np.float32) / 255
        gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        lap = (4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1]
               - gray[1:-1, :-2] - gray[1:-1, 2:])
        sharpness = float(np.var(lap))
        clipped = float(np.mean((gray < 0.015) | (gray > 0.985)))
        contrast = float(np.std(gray))
        # Starting coefficients for triage; grain/textures can inflate sharpness.
        sharp_score = float(np.clip(np.log1p(sharpness * 1000) / np.log(16), 0, 1))
        exposure_score = 1 - min(1, clipped / 0.65)
        contrast_score = min(1, contrast / 0.20)
        score = 100 * (0.55 * sharp_score + 0.30 * exposure_score + 0.15 * contrast_score)
        return Evaluation(score, star_rating(score, self.config.thresholds),
                          "輪郭・明暗・コントラストの統計評価（構図や表情は評価しません）",
                          {"sharpness": sharpness, "clipped_fraction": clipped, "contrast": contrast,
                           "backend": "heuristic", "device": "cpu"})


class PyiqaScorer:
    def __init__(self, config: Config):
        try:
            import pyiqa
            import torch
        except ImportError as error:
            raise RuntimeError("専用AIが未導入です。install.ps1を通常実行するか pip install '.[ai]' を実行してください。") from error
        self.config, self.torch = config, torch
        torch.set_num_threads(config.cpu_threads)
        self.device = "cuda" if config.device == "auto" and torch.cuda.is_available() else config.device
        if self.device == "auto":
            self.device = "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDAが利用できません。CPUを選択するかCUDA版PyTorchとGPUドライバを確認してください。")
        self.models = []
        for spec in config.metrics:
            model = pyiqa.create_metric(spec.name, device=self.device)
            if model.metric_mode != "NR":
                raise ValueError(f"{spec.name}は参照画像が必要です。NR（参照なし）モデルを選択してください。")
            self.models.append((spec, model))

    def evaluate(self, image: Image.Image) -> Evaluation:
        array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
        tensor = self.torch.from_numpy(array.transpose(2, 0, 1).copy()).unsqueeze(0)
        details, total, weights = {}, 0.0, 0.0
        with self.torch.inference_mode():
            for spec, model in self.models:
                # Models may internally resize or crop according to their training.
                output = model(tensor)
                if isinstance(output, tuple) or output.numel() != 1:
                    raise ValueError(f"{spec.name}の出力が単一の評価値ではありません。")
                raw = float(output.item())
                lower = model.lower_better if spec.lower_better is None else spec.lower_better
                score = normalized(raw, spec.minimum, spec.maximum, lower)
                details[spec.name] = {"raw": raw, "normalized": score, "weight": spec.weight}
                total += score * spec.weight
                weights += spec.weight
        score = total / weights
        details.update({"backend": "pyiqa", "device": self.device})
        return Evaluation(score, star_rating(score, self.config.thresholds),
                          " / ".join(f"{name}: {item['raw']:.3f}" for name, item in details.items() if isinstance(item, dict)), details)


class OllamaScorer:
    def __init__(self, config: Config):
        self.config = config
        # Disable proxies and redirects: image data must stay on loopback.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise RuntimeError("Ollamaのリダイレクトを拒否しました。ローカルURLを確認してください。")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def evaluate(self, image: Image.Image) -> Evaluation:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=90)
        payload = {
            "model": self.config.ollama_model,
            "messages": [{"role": "user", "content": self.config.ollama_prompt,
                          "images": [base64.b64encode(buf.getvalue()).decode("ascii")]}],
            "stream": False,
            "format": {"type": "object", "properties": {
                "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["rating", "reason"]},
            "options": {"temperature": 0, "num_thread": self.config.cpu_threads,
                        "num_gpu": 0 if self.config.device == "cpu" else -1, "num_predict": 256},
        }
        req = urllib.request.Request(self.config.ollama_url.rstrip("/") + "/api/chat",
                                     json.dumps(payload).encode(), {"Content-Type": "application/json"})
        with self.opener.open(req, timeout=self.config.ollama_timeout) as response:
            data = json.loads(response.read(2_000_000))
        if data.get("error"):
            raise RuntimeError(data["error"])
        answer = json.loads(data["message"]["content"])
        rating = answer.get("rating")
        if type(rating) is not int or not 1 <= rating <= 5 or not isinstance(answer.get("reason"), str):
            raise ValueError("Ollamaが有効なrating(1〜5)とreasonを返しませんでした。")
        return Evaluation((rating - 1) * 25.0, rating, answer["reason"][:2000],
                          {"backend": "ollama", "model": self.config.ollama_model,
                           "device_request": self.config.device, "score_is_star_conversion": True})


class OnnxScorer:
    def __init__(self, config: Config):
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise RuntimeError("ONNX用の追加依存を導入してください（.[onnx] またはWindowsの .[directml]）。") from error
        if config.onnx_provider not in ort.get_available_providers():
            raise ValueError(f"ONNXプロバイダが利用できません: {config.onnx_provider}")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = config.cpu_threads
        self.session = ort.InferenceSession(config.onnx_path, sess_options=opts,
                                           providers=[config.onnx_provider])
        self.config = config
        if len(self.session.get_inputs()) != 1:
            raise ValueError("ONNX方式は単一のfloat32画像入力モデルに対応しています。")
        if self.session.get_inputs()[0].type != "tensor(float)":
            raise ValueError("ONNX入力はfloat32である必要があります。")
        available = {x.name for x in self.session.get_outputs()}
        if config.onnx_output and config.onnx_output not in available:
            raise ValueError("指定したONNX出力名がありません。")

    def evaluate(self, image: Image.Image) -> Evaluation:
        cfg = self.config
        resized = image.resize((cfg.onnx_size, cfg.onnx_size), Image.Resampling.LANCZOS)
        x = np.asarray(resized.convert("RGB"), dtype=np.float32) / 255
        x = (x - np.array(cfg.onnx_mean, dtype=np.float32)) / np.array(cfg.onnx_std, dtype=np.float32)
        if cfg.onnx_layout == "NCHW":
            x = x.transpose(2, 0, 1)
        names = [cfg.onnx_output] if cfg.onnx_output else [self.session.get_outputs()[0].name]
        output = self.session.run(names, {self.session.get_inputs()[0].name: x[None].copy()})[0]
        if output.size != 1:
            raise ValueError("ONNX出力は単一スコアが必要です。分類確率・分布モデルは変換アダプタが必要です。")
        raw = float(output.reshape(-1)[0])
        score = normalized(raw, cfg.onnx_minimum, cfg.onnx_maximum, cfg.onnx_lower_better)
        return Evaluation(score, star_rating(score, cfg.thresholds), f"ONNX: {raw:.3f}",
                          {"backend": "onnx", "raw": raw, "providers": self.session.get_providers()})


def create_scorer(config: Config):
    config.validate(require_folders=False)
    return {"heuristic": HeuristicScorer, "pyiqa": PyiqaScorer,
            "ollama": OllamaScorer, "onnx": OnnxScorer}[config.backend](config)
