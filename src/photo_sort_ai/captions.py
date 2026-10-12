"""Human-readable evaluation provenance for Lightroom's XMP caption."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config


def xml_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\t\n" or 0x20 <= ord(c) <= 0xD7FF
                   or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)


def evaluation_caption(config: Config, result: dict) -> str:
    details = dict(result.get("details", {}))
    backend = details.get("backend", config.backend)
    model = details.get("model")
    if not model:
        model = {"heuristic": "軽量統計（AIモデルなし）", "pyiqa": " + ".join(m.name for m in config.metrics),
                 "onnx": Path(config.onnx_path).name, "ollama": config.ollama_model,
                 "openai": config.openai_model}.get(backend, backend)
    lines = ["[Photo Sort AI]", f"評価方式: {backend} / {config.preset}", f"使用モデル: {model}",
             f"算出した評価: ★{result['rating']} ({result['rating']}/5)"]
    suffix = "（星からの換算値）" if details.get("score_is_star_conversion") else ""
    lines.append(f"100点換算スコア: {result['score']:.2f}{suffix}")
    mode = details.get("api_mode", "")
    if mode:
        lines.append(f"API方式: {mode}")
    reason = result.get("reason", "")
    if mode not in {"decisions", "openrouter_decisions"} and isinstance(reason, str) and reason.strip():
        lines += ["コメント・評価理由:", reason.strip()]
    # Connection URLs and token usage are not evaluation details.
    for key in ["endpoint", "usage", "backend", "model", "api_mode", "score_is_star_conversion"]:
        details.pop(key, None)
    if details:
        lines += ["評価の詳細:", json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False)]
    return xml_text("\n".join(lines))
