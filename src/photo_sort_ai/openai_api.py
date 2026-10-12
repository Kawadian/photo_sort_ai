"""OpenAI-compatible HTTP image rating, without an additional SDK dependency."""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from PIL import Image

from .config import Config
from .scoring import Evaluation

RATING_SCHEMA = {
    "type": "object",
    "properties": {"rating": {"type": "integer", "minimum": 1, "maximum": 5},
                   "reason": {"type": "string"}},
    "required": ["rating", "reason"], "additionalProperties": False,
}

API_ROUTES = {"chat_completions": "chat/completions", "responses": "responses",
              "decisions": "decisions", "openrouter_decisions": "decisions"}
STAR_CHOICES = {
    "1": "大きな撮影失敗があり、選別で外す候補。",
    "2": "目立つ欠点があり、残す優先度が低い。",
    "3": "通常の記録写真として残せる。",
    "4": "画質・構図・被写体の捉え方がよく、積極的に残したい。",
    "5": "画質・構図・表現に優れ、代表作として残したい。",
}


RETRY_STATUSES = {429, 503}
RATE_LIMIT_BASE_SECONDS = 2.0
RATE_LIMIT_MAX_SECONDS = 120.0


def retry_after_seconds(value: str | None, attempt: int) -> float:
    """Seconds to wait before retrying: the server's Retry-After, else exponential backoff."""
    if value:
        try:
            return min(RATE_LIMIT_MAX_SECONDS, max(0.0, float(value.strip())))
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:
                return min(RATE_LIMIT_MAX_SECONDS, max(0.0, parsedate_to_datetime(value).timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(RATE_LIMIT_MAX_SECONDS, RATE_LIMIT_BASE_SECONDS * (2 ** attempt))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("APIのリダイレクトを拒否しました。転送先をAPI URLに直接設定してください。")


class OpenAICompatibleScorer:
    def __init__(self, config: Config):
        config.validate_openai()
        self.config = config
        variable = config.openai_api_key_env
        self.api_key = os.environ.get(variable, "").strip() if variable else ""
        if variable and not self.api_key:
            raise RuntimeError(f"APIキーを環境変数 {variable} またはWebUIの起動中のみの入力欄に設定してください。")
        if any(ord(c) < 32 or ord(c) > 126 for c in self.api_key):
            raise ValueError("APIキーに使用できない文字が含まれています。")
        route = API_ROUTES[config.openai_api_mode]
        base = config.openai_base_url.rstrip("/")
        self.endpoint = base if base.endswith("/" + route) else base + "/" + route
        # One scorer is shared by all workers: a 429 pauses every request, not just one.
        self.cooldown_lock = threading.Lock()
        self.cooldown_until = 0.0
        handlers = [NoRedirect()]
        if urlparse(base).hostname in {"127.0.0.1", "localhost", "::1"}:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def payload(self, image: Image.Image) -> dict:
        cfg = self.config
        jpeg = io.BytesIO()
        image.convert("RGB").save(jpeg, format="JPEG", quality=90, exif=b"")
        image_url = "data:image/jpeg;base64," + base64.b64encode(jpeg.getvalue()).decode("ascii")
        if cfg.openai_api_mode in {"decisions", "openrouter_decisions"}:
            # Decisions returns a typed answer, not generated rating/reason text.
            instructions = cfg.openai_prompt + "\n採点はratingの選択肢で回答してください。文章やJSONの生成指示は適用しません。"
            question = {"type": "choice", "instructions": instructions}
            if cfg.openai_api_mode == "openrouter_decisions":
                part = {"url": image_url}
                if cfg.openai_image_detail != "omit":
                    part["detail"] = cfg.openai_image_detail
                payload = {"model": cfg.openai_model,
                           "state": [instructions, {"type": "image_url", "image_url": part}],
                           "questions": {"rating": {**question, "criteria": STAR_CHOICES}}}
            else:
                payload = {"model": cfg.openai_model,
                           "input": [{"role": "user", "content": [
                               {"type": "input_text", "text": instructions},
                               {"type": "input_image", "image_url": image_url}]}],
                           "questions": [{**question, "name": "rating", "choices": [
                               {"value": value, "description": description}
                               for value, description in STAR_CHOICES.items()]}]}
            payload.update(cfg.openai_extra_body)
            return payload
        prompt = cfg.openai_prompt + '\nJSONオブジェクトのみを返してください。例: {"rating":3,"reason":"短い評価理由"}'
        payload = {"model": cfg.openai_model, "stream": False}
        if cfg.openai_store is not None:
            payload["store"] = cfg.openai_store
        image_part = {"image_url": image_url}
        if cfg.openai_image_detail != "omit":
            image_part["detail"] = cfg.openai_image_detail
        if cfg.openai_api_mode == "chat_completions":
            chat_image = {"url": image_url}
            if cfg.openai_image_detail != "omit":
                chat_image["detail"] = cfg.openai_image_detail
            payload["messages"] = [{"role": "user", "content": [
                {"type": "text", "text": prompt}, {"type": "image_url", "image_url": chat_image}]}]
            if cfg.openai_max_output_tokens:
                payload["max_completion_tokens"] = cfg.openai_max_output_tokens
            if cfg.openai_response_format == "json_schema":
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "photo_rating", "strict": True, "schema": RATING_SCHEMA}}
            elif cfg.openai_response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
        else:
            payload["input"] = [{"role": "user", "content": [
                {"type": "input_text", "text": prompt}, {"type": "input_image", **image_part}]}]
            if cfg.openai_max_output_tokens:
                payload["max_output_tokens"] = cfg.openai_max_output_tokens
            if cfg.openai_response_format == "json_schema":
                payload["text"] = {"format": {"type": "json_schema", "name": "photo_rating",
                                               "strict": True, "schema": RATING_SCHEMA}}
            elif cfg.openai_response_format == "json_object":
                payload["text"] = {"format": {"type": "json_object"}}
        payload.update(cfg.openai_extra_body)
        return payload

    def response_text(self, data: dict) -> str:
        if data.get("error"):
            raise ValueError("APIがエラーを返しました。モデルとリクエスト設定を確認してください。")
        if self.config.openai_api_mode == "chat_completions":
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("APIの返答にchoicesがありません。")
            choice = choices[0]
            if choice.get("finish_reason") not in {None, "stop"}:
                raise ValueError("APIの返答が完了していません。出力上限やモデル設定を確認してください。")
            message = choice.get("message", {})
            if message.get("refusal"):
                raise ValueError("APIが写真評価を拒否しました。星は保存しません。")
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if part.get("type") == "text")
        else:
            if data.get("status") not in {None, "completed"}:
                raise ValueError("APIの返答が未完了です。出力上限やモデル設定を確認してください。")
            parts = []
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "refusal":
                            raise ValueError("APIが写真評価を拒否しました。星は保存しません。")
                        if part.get("type") == "output_text":
                            parts.append(part.get("text", ""))
            content = "".join(parts) or data.get("output_text")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("APIの返答に評価テキストがありません。")
        # Some compatible servers wrap JSON in a single Markdown code fence.
        text = content.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"}:
                text = "\n".join(lines[1:-1]).strip()
        return text

    def evaluate(self, image: Image.Image) -> Evaluation:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        body = json.dumps(self.payload(image), allow_nan=False).encode("utf-8")
        raw = self.post(body, headers)
        if len(raw) > 2_000_000:
            raise ValueError("APIの返答が大きすぎます。")
        return self.parse(raw)

    def wait_for_cooldown(self):
        while True:
            with self.cooldown_lock:
                remaining = self.cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    def post(self, body: bytes, headers: dict) -> bytes:
        attempt = 0
        while True:
            self.wait_for_cooldown()
            req = urllib.request.Request(self.endpoint, body, headers)
            try:
                with self.opener.open(req, timeout=self.config.openai_timeout) as response:
                    return response.read(2_000_001)
            except urllib.error.HTTPError as error:
                code = error.code
                retry_after = error.headers.get("Retry-After") if error.headers else None
                error.close()
                if code in RETRY_STATUSES and attempt < self.config.api_rate_limit_retries:
                    delay = retry_after_seconds(retry_after, attempt)
                    with self.cooldown_lock:
                        self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay)
                    attempt += 1
                    continue
                self.http_error(code)
            except (OSError, http.client.HTTPException):
                raise RuntimeError("APIへの接続に失敗しました。API URL・通信設定・待機時間を確認してください。") from None

    @staticmethod
    def http_error(code: int):
        hint = {401: "APIキーを確認してください。", 403: "利用権限を確認してください。",
                400: "モデル・JSON方式・追加パラメータを確認してください。",
                404: "API URLとモデル名を確認してください。", 429: "待機後に再試行してください。"}.get(code, "APIサービスの状態を確認してください。")
        # Never put a provider's raw error body (possibly echoing secrets) in logs.
        raise RuntimeError(f"API HTTP {code}: {hint}") from None

    def parse(self, raw: bytes) -> Evaluation:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("APIの返答がJSONオブジェクトではありません。")
        if self.config.openai_api_mode in {"decisions", "openrouter_decisions"}:
            return self.decision_evaluation(data)
        answer = json.loads(self.response_text(data))
        if not isinstance(answer, dict):
            raise ValueError("APIの評価がJSONオブジェクトではありません。")
        rating = answer.get("rating")
        if type(rating) is not int or not 1 <= rating <= 5 or not isinstance(answer.get("reason"), str):
            raise ValueError("APIが有効なrating(整数1〜5)とreasonを返しませんでした。")
        return Evaluation((rating - 1) * 25.0, rating, answer["reason"][:2000],
                          {"backend": "openai", "model": self.config.openai_model,
                           "api_mode": self.config.openai_api_mode, "endpoint": self.endpoint,
                           "usage": data.get("usage"), "score_is_star_conversion": True})

    def decision_evaluation(self, data: dict) -> Evaluation:
        if data.get("error") or data.get("status") not in {None, "completed"}:
            raise ValueError("Decisions APIがエラーまたは未完了の返答を返しました。")
        answers = data.get("answers")
        if self.config.openai_api_mode == "openrouter_decisions":
            answer = answers.get("rating") if isinstance(answers, dict) else None
        else:
            matches = [item for item in answers if isinstance(item, dict) and item.get("name") == "rating"] if isinstance(answers, list) else []
            answer = matches[0] if len(matches) == 1 else None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ValueError("Decisions APIに有効なratingの選択結果がありません（拒否を含む）。")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in STAR_CHOICES:
            raise ValueError("Decisions APIの選択結果が星1〜5ではありません。")
        # Reject non-finite numbers before persisting provider results to SQLite/CSV.
        json.dumps(answer, allow_nan=False)
        rating = int(choice)
        return Evaluation((rating - 1) * 25.0, rating,
                          f"Decisionsの選択結果: 星{rating}（文章の評価理由は生成しません）",
                          {"backend": "openai", "model": self.config.openai_model,
                           "api_mode": self.config.openai_api_mode, "endpoint": self.endpoint,
                           "usage": data.get("usage"), "decision": answer,
                           "score_is_star_conversion": True})
