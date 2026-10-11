import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photo_sort_ai.cli import parser
from photo_sort_ai.config import Config, load_config
from photo_sort_ai.metadata import read_sidecar_rating, sidecar_path
from photo_sort_ai.state import RunLock
from photo_sort_ai.web import WebController, create_app, run_web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cfg = Config(folders=[str(self.root)], state_dir=str(self.root / "state"),
                          settle_seconds=0, low_priority=False, output="sidecar", backup=False)
        self.controller = WebController(self.cfg, self.root / "config.json")
        self.app = create_app(self.controller)
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").get_json()["csrf"]

    def tearDown(self):
        self.controller.close()
        self.temporary.cleanup()

    def post(self, path, body=None):
        return self.client.post(path, json=body if body is not None else {}, headers={"X-CSRF-Token": self.csrf})

    def wait_finished(self):
        self.controller.worker.join(timeout=5)
        self.assertFalse(self.controller.active())
        self.assertIsNone(self.controller.error)

    def photo(self):
        path = self.root / "写真.png"
        Image.new("RGB", (160, 100), (150, 180, 200)).save(path)
        return path

    def test_web_cli_options_and_removed_gui(self):
        args = parser().parse_args(["web", "--public", "--host", "127.0.0.1",
                                    "--public-url", "https://photos.example.com", "--port", "9000", "--no-browser"])
        self.assertTrue(args.public)
        self.assertEqual(args.port, 9000)
        self.assertNotIn("gui", parser()._subparsers._group_actions[0].choices)

    def test_local_host_origin_csrf_and_static_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Photo Sort AI", response.data)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        response.close()
        for path in ["/assets/app.js", "/assets/style.css"]:
            with self.client.get(path) as static_response:
                self.assertEqual(static_response.status_code, 200)
        self.assertEqual(self.client.get("/api/config", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/stop", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/stop", json={}, headers={"X-CSRF-Token": self.csrf,
                                                                         "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/results", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.post("/api/stop").status_code, 200)

    def test_external_authentication_protects_all_private_routes(self):
        app = create_app(self.controller, password="very-long-password", public=True,
                         public_url="https://photos.example.com")
        client = app.test_client()
        base = "https://photos.example.com"
        info = client.get("/api/session", base_url=base).get_json()
        self.assertFalse(info["authenticated"])
        for route in ["config", "status", "results", "export.csv", "duplicates.json", "preview/unknown"]:
            self.assertEqual(client.get("/api/" + route, base_url=base).status_code, 401)
        login = client.post("/api/login", base_url=base, json={"password": "very-long-password"},
                            headers={"X-CSRF-Token": info["csrf"], "Origin": base})
        self.assertEqual(login.status_code, 200)
        self.assertIn("Secure", login.headers["Set-Cookie"])
        self.assertIn("HttpOnly", login.headers["Set-Cookie"])
        self.assertEqual(client.get("/api/config", base_url=base).status_code, 200)
        self.assertEqual(client.get("/api/config", base_url="https://different.example").status_code, 403)
        self.assertEqual(client.post("/api/logout", base_url=base, json={},
                                    headers={"X-CSRF-Token": login.get_json()["csrf"]}).status_code, 200)
        self.assertEqual(client.get("/api/config", base_url=base).status_code, 401)

    def test_public_requires_password_url_and_local_binding_cannot_be_bypassed(self):
        for password, url in [("", "https://photos.example.com"), ("short", "https://photos.example.com"),
                              ("very-long-password", None), ("very-long-password", "https://host/path"),
                              ("very-long-password", "https://user:pass@host")]:
            with self.assertRaises(ValueError):
                create_app(self.controller, public=True, password=password, public_url=url)
        with self.assertRaises(ValueError):
            run_web(self.cfg, self.root / "config.json", host="0.0.0.0")
        with self.assertRaises(ValueError):
            run_web(self.cfg, self.root / "config.json", port=0)

    def test_login_rate_limit_and_wrong_password(self):
        app = create_app(self.controller, password="very-long-password")
        client = app.test_client()
        csrf = client.get("/api/session").get_json()["csrf"]
        for _ in range(10):
            self.assertEqual(client.post("/api/login", json={"password": "wrong"},
                                        headers={"X-CSRF-Token": csrf}).status_code, 401)
        self.assertEqual(client.post("/api/login", json={"password": "very-long-password"},
                                    headers={"X-CSRF-Token": csrf}).status_code, 429)

    def test_config_keeps_advanced_options_and_key_is_not_returned_or_saved(self):
        data = asdict(self.cfg)
        data.update(preset="openai", openai_model="openai/gpt-6-luna-decisions",
                    openai_api_mode="openrouter_decisions", openai_base_url="https://openrouter.ai/api/alpha/decisions",
                    api_concurrency=4, onnx_size=320, openai_api_key_env="PHOTO_SORT_TEST_API_KEY")
        with patch.dict(os.environ, {}):
            response = self.post("/api/config", {"config": data, "api_key": "session-only-secret"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(os.environ["PHOTO_SORT_TEST_API_KEY"], "session-only-secret")
            self.assertNotIn(b"session-only-secret", response.data)
            self.assertNotIn(b"session-only-secret", self.client.get("/api/config").data)
            loaded = load_config(self.root / "config.json")
            self.assertEqual(loaded.backend, "openai")
            self.assertEqual(loaded.api_concurrency, 4)
            self.assertEqual(loaded.onnx_size, 320)
            self.assertNotIn("session-only-secret", (self.root / "config.json").read_text())
            self.assertEqual(self.post("/api/config", {"config": data, "api_key": ""}).status_code, 200)
            self.assertEqual(os.environ["PHOTO_SORT_TEST_API_KEY"], "")

    def test_invalid_config_and_bodies_never_save(self):
        for key, value in [("unknown", "value"), ("cpu_threads", "2"), ("recursive", "false"),
                           ("thresholds", [25, 25, 65, 85]), ("api_concurrency", 0)]:
            with self.subTest(key=key):
                data = asdict(self.cfg)
                data[key] = value
                self.assertEqual(self.post("/api/config", {"config": data}).status_code, 400)
                self.assertFalse((self.root / "config.json").exists())
        self.assertEqual(self.post("/api/config", []).status_code, 400)
        self.assertEqual(self.post("/api/jobs", {"mode": "invalid"}).status_code, 400)

    def test_real_scan_history_preview_export_and_unchanged_photo_pixels(self):
        path = self.photo()
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.post("/api/jobs", {"mode": "scan"}).status_code, 202)
        self.wait_finished()
        history = self.client.get("/api/results").get_json()
        self.assertEqual(len(history["results"]), 1)
        result = history["results"][0]
        self.assertEqual(read_sidecar_rating(sidecar_path(path)), result["rating"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
        preview = self.client.get("/api/preview/" + result["preview_id"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(preview.data)).format, "JPEG")
        self.assertEqual(self.client.get("/api/preview/../config.json").status_code, 404)
        self.assertEqual(self.client.get("/api/export.csv").status_code, 200)
        self.assertIn(b"AI_rating", self.client.get("/api/export.csv").data)
        self.assertEqual(json.loads(self.client.get("/api/duplicates.json").data), [])
        self.controller.config.folders = [str(self.root / "other")]
        self.assertEqual(self.client.get("/api/preview/" + result["preview_id"]).status_code, 404)

    def test_watch_stop_running_conflict_and_browser_reconnect(self):
        self.photo()
        self.assertEqual(self.post("/api/jobs", {"mode": "watch"}).status_code, 202)
        self.assertEqual(self.post("/api/jobs", {"mode": "scan"}).status_code, 409)
        self.assertEqual(self.post("/api/config", {"config": asdict(self.cfg)}).status_code, 409)
        self.assertEqual(self.post("/api/retry").status_code, 409)
        second = self.app.test_client()
        self.assertTrue(second.get("/api/status").get_json()["running"])
        self.assertEqual(self.post("/api/stop").status_code, 200)
        self.wait_finished()
        self.assertFalse(self.client.get("/api/status").get_json()["running"])
        self.assertEqual(self.post("/api/retry").status_code, 200)

    def test_benchmark_and_prepare_dont_write_stars(self):
        path = self.photo()
        self.assertEqual(self.post("/api/jobs", {"mode": "prepare"}).status_code, 202)
        self.wait_finished()
        self.assertEqual(self.post("/api/jobs", {"mode": "benchmark", "limit": 1}).status_code, 202)
        self.wait_finished()
        result = self.client.get("/api/status").get_json()["benchmark"]
        self.assertEqual(result["measured"], 1)
        self.assertFalse(sidecar_path(path).exists())
        self.assertEqual(self.client.get("/api/results").get_json()["results"], [])

    def test_existing_cli_lock_is_respected(self):
        self.photo()
        lock = RunLock(Path(self.cfg.state_dir))
        try:
            self.post("/api/jobs", {"mode": "scan"})
            self.controller.worker.join(timeout=5)
            self.assertIn("別の処理", self.controller.error)
        finally:
            lock.close()

    def test_events_are_bounded_and_cursor_filters(self):
        for index in range(600):
            self.controller.emit({"kind": "log", "message": str(index)})
        status = self.client.get("/api/status?after=598").get_json()
        self.assertEqual(len(status["events"]), 2)
        self.assertEqual(len(self.controller.events), 500)
