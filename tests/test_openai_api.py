import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photo_sort_ai.cli import main
from photo_sort_ai.config import Config, load_config, save_config
from photo_sort_ai.engine import Engine
from photo_sort_ai.metadata import read_sidecar_rating, sidecar_path
from photo_sort_ai.scoring import create_scorer


def chat_reply(content='{"rating":4,"reason":"構図がよい"}', **message_fields):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content, **message_fields}}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 20}}


@contextmanager
def server(reply=None, status=200, headers=None):
    records = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            records.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(length))})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps(reply if reply is not None else chat_reply()).encode())
        def log_message(self, *_):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", records
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


class OpenAIAPITests(unittest.TestCase):
    def cfg(self, base, **kwargs):
        cfg = Config(openai_base_url=base, openai_model="test-vision", openai_api_key_env="", **kwargs)
        cfg.use_preset("openai")
        return cfg

    def image(self):
        return Image.new("RGB", (160, 100), (90, 150, 110))

    def test_chat_real_http_path_auth_image_and_usage(self):
        with server() as (base, records), patch.dict(os.environ, {"PHOTO_SORT_TEST_KEY": "test-token"}):
            cfg = self.cfg(base + "/proxy/v1", openai_image_detail="low")
            cfg.openai_api_key_env = "PHOTO_SORT_TEST_KEY"
            result = create_scorer(cfg).evaluate(self.image())
            request = records[0]
            self.assertEqual(request["path"], "/proxy/v1/chat/completions")
            self.assertEqual(request["headers"]["Authorization"], "Bearer test-token")
            body = request["body"]
            self.assertEqual(body["max_completion_tokens"], 2048)
            self.assertFalse(body["store"])
            self.assertEqual(body["response_format"], {"type": "json_object"})
            part = body["messages"][0]["content"][1]["image_url"]
            self.assertEqual(part["detail"], "low")
            self.assertTrue(part["url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(result.rating, 4)
            self.assertEqual(result.details["usage"]["prompt_tokens"], 123)

    def test_full_endpoint_and_schema(self):
        with server() as (base, records):
            cfg = self.cfg(base + "/v1/chat/completions/", openai_response_format="json_schema")
            create_scorer(cfg).evaluate(self.image())
            self.assertEqual(records[0]["path"], "/v1/chat/completions")
            schema = records[0]["body"]["response_format"]["json_schema"]
            self.assertTrue(schema["strict"])
            self.assertEqual(schema["schema"]["required"], ["rating", "reason"])

    def test_responses_real_http_request_and_output_parts(self):
        reply = {"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": '{"rating":5,"reason":"よい写真"}'}]}],
            "usage": {"input_tokens": 100, "output_tokens": 12}}
        with server(reply) as (base, records):
            cfg = self.cfg(base + "/responses", openai_api_mode="responses", openai_response_format="json_schema")
            result = create_scorer(cfg).evaluate(self.image())
            body = records[0]["body"]
            self.assertEqual(records[0]["path"], "/responses")
            self.assertEqual(body["input"][0]["content"][1]["type"], "input_image")
            self.assertEqual(body["text"]["format"]["type"], "json_schema")
            self.assertEqual(body["max_output_tokens"], 2048)
            self.assertEqual(result.rating, 5)

    def test_compatible_omits_unsupported_fields_and_accepts_fenced_json(self):
        with server(chat_reply('```json\n{"rating":3,"reason":"普通"}\n```')) as (base, records):
            cfg = self.cfg(base, openai_response_format="none", openai_image_detail="omit",
                           openai_max_output_tokens=0, openai_store=None,
                           openai_extra_body={"max_tokens": 512, "temperature": 0})
            self.assertEqual(create_scorer(cfg).evaluate(self.image()).rating, 3)
            body = records[0]["body"]
            self.assertNotIn("Authorization", records[0]["headers"])
            self.assertNotIn("response_format", body)
            self.assertNotIn("store", body)
            self.assertNotIn("max_completion_tokens", body)
            self.assertNotIn("detail", body["messages"][0]["content"][1]["image_url"])
            self.assertEqual(body["max_tokens"], 512)

    def test_bad_ratings_refusal_and_truncation_are_errors(self):
        replies = [chat_reply(json.dumps({"rating": rating, "reason": "invalid"})) for rating in [True, 0, 6, 3.5, "4"]]
        replies += [chat_reply('{"rating":4}'), chat_reply("[]"), chat_reply("not JSON"),
                    chat_reply(None, refusal="cannot rate"),
                    {"choices": [{"finish_reason": "length", "message": {"content": '{"rating":4,"reason":"partial"}'}}]}]
        for reply in replies:
            with self.subTest(reply=reply), server(reply) as (base, _):
                with self.assertRaises(ValueError):
                    create_scorer(self.cfg(base)).evaluate(self.image())

    def test_responses_refusal_incomplete_and_json_object(self):
        replies = [{"status": "incomplete", "output_text": '{"rating":4,"reason":"partial"}'},
                   {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}]
        for reply in replies:
            with self.subTest(reply=reply), server(reply) as (base, _):
                with self.assertRaises(ValueError):
                    create_scorer(self.cfg(base, openai_api_mode="responses")).evaluate(self.image())
        with server({"output_text": '{"rating":2,"reason":"難点"}'}) as (base, records):
            result = create_scorer(self.cfg(base, openai_api_mode="responses")).evaluate(self.image())
            self.assertEqual(result.rating, 2)
            self.assertEqual(records[0]["body"]["text"]["format"], {"type": "json_object"})

    def test_http_error_does_not_echo_server_secrets(self):
        with server({"error": {"message": "secret-test-token"}}, status=429) as (base, _):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429") as caught:
                create_scorer(self.cfg(base)).evaluate(self.image())
            self.assertNotIn("secret-test-token", str(caught.exception))

    def test_redirect_does_not_forward_photo_or_key(self):
        with server() as (other, foreign_records):
            with server(status=302, headers={"Location": other + "/leak"}) as (base, records):
                with self.assertRaises(RuntimeError):
                    create_scorer(self.cfg(base)).evaluate(self.image())
                self.assertEqual(len(records), 1)
                self.assertEqual(foreign_records, [])

    def test_missing_key_is_reported_before_network(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = self.cfg("https://api.openai.com/v1")
            cfg.openai_api_key_env = "PHOTO_SORT_MISSING_KEY"
            with self.assertRaisesRegex(RuntimeError, "PHOTO_SORT_MISSING_KEY"):
                create_scorer(cfg)

    def test_configuration_validation_and_signature(self):
        cfg = self.cfg("https://api.openai.com/v1")
        signature = cfg.analysis_signature()
        cfg.openai_api_key_env = "OTHER_KEY"
        self.assertEqual(signature, cfg.analysis_signature())
        cfg.openai_model = "other-model"
        self.assertNotEqual(signature, cfg.analysis_signature())
        for url in ["file:///tmp/model", "https://secret:password@host/v1", "https://host/v1?key=secret", "https://host:bad/v1", "http://host/responses"]:
            with self.subTest(url=url):
                cfg.openai_base_url = url
                with self.assertRaises(ValueError):
                    cfg.validate(require_folders=False)
        cfg.openai_base_url = "https://api.openai.com/v1"
        cfg.openai_extra_body = {"messages": []}
        with self.assertRaises(ValueError):
            cfg.validate(require_folders=False)
        local = Config()
        signature = local.analysis_signature()
        local.openai_model = "unused"
        self.assertEqual(signature, local.analysis_signature())

    def test_engine_api_cache_sidecar_and_config_has_no_key(self):
        with tempfile.TemporaryDirectory() as directory, server() as (base, records):
            root = Path(directory).resolve()
            image = root / "写真.png"
            self.image().save(image)
            cfg = self.cfg(base, folders=[str(root)], state_dir=str(root / "state"),
                           output="sidecar", settle_seconds=0, low_priority=False)
            with patch.dict(os.environ, {"PHOTO_SORT_TEST_KEY": "secret-test-token"}):
                cfg.openai_api_key_env = "PHOTO_SORT_TEST_KEY"
                save_config(cfg, root / "config.json")
                self.assertNotIn("secret-test-token", (root / "config.json").read_text(encoding="utf-8"))
                with Engine(load_config(root / "config.json")) as engine:
                    self.assertEqual(engine.scan()["processed"], 1)
                    self.assertEqual(engine.scan()["processed"], 0)
            self.assertEqual(len(records), 1)
            self.assertEqual(read_sidecar_rating(sidecar_path(image)), 4)

    def test_cli_api_overrides_without_gui_or_persistent_config_change(self):
        with tempfile.TemporaryDirectory() as directory, server() as (base, records):
            root = Path(directory).resolve()
            self.image().save(root / "写真.png")
            config_path = root / "config.json"
            cfg = Config(folders=[str(root)], state_dir=str(root / "state"), output="report",
                         settle_seconds=0, low_priority=False)
            save_config(cfg, config_path)
            with redirect_stdout(io.StringIO()):
                code = main(["--config", str(config_path), "scan", "--preset", "openai", "--api-url", base,
                             "--api-model", "test-vision", "--api-mode", "chat_completions",
                             "--api-key-env", "", "--api-json-mode", "none"])
            self.assertEqual(code, 0)
            self.assertEqual(len(records), 1)
            self.assertEqual(load_config(config_path).preset, "light")


if __name__ == "__main__":
    unittest.main()
