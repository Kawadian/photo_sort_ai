import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photo_sort_ai import openai_api
from photo_sort_ai.cli import main
from photo_sort_ai.config import Config
from photo_sort_ai.engine import Engine
from photo_sort_ai.scoring import Evaluation
from photo_sort_ai.state import Store

REPLY = {"choices": [{"finish_reason": "stop", "message": {"content": '{"rating":4,"reason":"よい"}'}}]}


@contextmanager
def slow_server(delay=0.0, statuses=(), retry_after=None):
    """Chat server that records peak concurrency; returns `statuses` in order before succeeding."""
    state = {"active": 0, "peak": 0, "requests": 0}
    lock = threading.Lock()
    queue = list(statuses)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            with lock:
                state["requests"] += 1
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                status = queue.pop(0) if queue else 200
            time.sleep(delay)
            with lock:
                state["active"] -= 1
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status != 200 and retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.end_headers()
            self.wfile.write(json.dumps(REPLY if status == 200 else {"error": {"message": "slow down"}}).encode())

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def photos(self, folder, count):
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            Image.new("RGB", (160 + i, 100), (20 * i % 255, 120, 90)).save(folder / f"p{i:02d}.png")
        return folder

    def api_cfg(self, base, folder, state, **kwargs):
        cfg = Config(openai_base_url=base, openai_model="test-vision", openai_api_key_env="",
                     folders=[str(folder)], state_dir=str(state), output="report",
                     settle_seconds=0, low_priority=False, poll_seconds=0.01, **kwargs)
        cfg.use_preset("openai")
        return cfg

    def test_parallel_requests_match_sequential_results_and_are_faster(self):
        folder = self.photos(self.root / "photos", 8)
        timings, rows = {}, {}
        for workers in (1, 4):
            with slow_server(delay=0.25) as (base, state):
                events = []
                cfg = self.api_cfg(base, folder, self.root / f"state{workers}", api_concurrency=workers)
                start = time.perf_counter()
                with Engine(cfg, events.append) as engine:
                    summary = engine.scan()
                    self.assertEqual(engine.scan()["processed"], 0)  # cached on the second pass
                timings[workers] = time.perf_counter() - start
                self.assertEqual(summary, {"found": 8, "processed": 8, "errors": 0, "remaining": 0, "stopped": False})
                self.assertEqual(state["requests"], 8)
                self.assertEqual(state["peak"], 1 if workers == 1 else 4)
                self.assertEqual(sum(e["kind"] == "result" for e in events), 8)
                store = Store(Path(cfg.state_dir))
                try:
                    rows[workers] = {Path(r["path"]).name: (json.loads(r["result"]), r["status"]) for r in store.rows()}
                    self.assertEqual(store.failures(), [])
                finally:
                    store.close()
        self.assertEqual(rows[1].keys(), rows[4].keys())
        for name, (sequential, status) in rows[1].items():
            parallel, parallel_status = rows[4][name]
            self.assertEqual(status, parallel_status)
            self.assertEqual(sequential.keys(), parallel.keys())
            for result in (sequential, parallel):
                result.pop("seconds")
                result["details"].pop("endpoint")  # each run uses its own test server port
            self.assertEqual(sequential, parallel)
        self.assertLess(timings[4], timings[1] * 0.6)

    def test_limit_is_respected_with_parallel_workers(self):
        folder = self.photos(self.root / "photos", 6)
        with slow_server(delay=0.05) as (base, state):
            cfg = self.api_cfg(base, folder, self.root / "state", api_concurrency=4)
            with Engine(cfg) as engine:
                self.assertEqual(engine.scan(limit=3)["processed"], 3)
            self.assertEqual(state["requests"], 3)

    def test_rate_limit_waits_for_retry_after_and_then_succeeds(self):
        folder = self.photos(self.root / "photos", 1)
        with slow_server(statuses=[429, 503], retry_after="0") as (base, state):
            cfg = self.api_cfg(base, folder, self.root / "state", api_concurrency=2)
            with Engine(cfg) as engine:
                self.assertEqual(engine.scan()["processed"], 1)
            self.assertEqual(state["requests"], 3)

    def test_rate_limit_retries_are_bounded(self):
        folder = self.photos(self.root / "photos", 1)
        with slow_server(statuses=[429, 429, 429], retry_after="0") as (base, state):
            events = []
            cfg = self.api_cfg(base, folder, self.root / "state", api_rate_limit_retries=1)
            with Engine(cfg, events.append) as engine:
                self.assertEqual(engine.scan()["errors"], 1)
            self.assertEqual(state["requests"], 2)
            self.assertIn("API HTTP 429", next(e["error"] for e in events if e["kind"] == "error"))

    def test_other_http_errors_are_not_retried(self):
        folder = self.photos(self.root / "photos", 1)
        with slow_server(statuses=[400]) as (base, state):
            cfg = self.api_cfg(base, folder, self.root / "state")
            with Engine(cfg) as engine:
                self.assertEqual(engine.scan()["errors"], 1)
            self.assertEqual(state["requests"], 1)

    def test_rate_limit_cooldown_is_shared_by_all_workers(self):
        cfg = self.api_cfg("http://127.0.0.1:9", self.root, self.root / "state")
        scorer = openai_api.OpenAICompatibleScorer(cfg)
        scorer.cooldown_until = time.monotonic() + 0.3
        start = time.perf_counter()
        threads = [threading.Thread(target=scorer.wait_for_cooldown) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertGreaterEqual(time.perf_counter() - start, 0.25)

    def test_retry_after_parsing(self):
        self.assertEqual(openai_api.retry_after_seconds("5", 0), 5)
        self.assertEqual(openai_api.retry_after_seconds("9999", 0), openai_api.RATE_LIMIT_MAX_SECONDS)
        self.assertEqual(openai_api.retry_after_seconds(None, 0), openai_api.RATE_LIMIT_BASE_SECONDS)
        self.assertEqual(openai_api.retry_after_seconds("garbage", 2), openai_api.RATE_LIMIT_BASE_SECONDS * 4)
        self.assertLessEqual(openai_api.retry_after_seconds(formatdate(time.time() + 3, usegmt=True), 0), 3.5)

    def test_worker_failure_is_recorded_without_stopping_other_photos(self):
        folder = self.photos(self.root / "photos", 5)
        cfg = Config(folders=[str(folder)], state_dir=str(self.root / "state"), output="report",
                     settle_seconds=0, low_priority=False, poll_seconds=0.01, api_concurrency=3)
        cfg.use_preset("ollama")

        class Scorer:
            def evaluate(self, image):
                time.sleep(0.02)
                if image.size[0] == 162:
                    raise RuntimeError("broken photo")
                return Evaluation(75.0, 4, "ok", {"backend": "ollama"})

        events = []
        with patch("photo_sort_ai.engine.create_scorer", return_value=Scorer()), Engine(cfg, events.append) as engine:
            summary = engine.scan()
            self.assertEqual((summary["processed"], summary["errors"]), (4, 1))
            failures = engine.store.failures()
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0]["path"].endswith("p02.png"))
        self.assertEqual(sum(e["kind"] == "processing" for e in events), 5)

    def test_stop_during_parallel_run_skips_writes(self):
        folder = self.photos(self.root / "photos", 6)
        stop = threading.Event()
        cfg = Config(folders=[str(folder)], state_dir=str(self.root / "state"), output="report",
                     settle_seconds=0, low_priority=False, poll_seconds=0.01, api_concurrency=2)
        cfg.use_preset("ollama")

        class Scorer:
            def evaluate(self, image):
                stop.set()
                return Evaluation(75.0, 4, "ok", {"backend": "ollama"})

        with patch("photo_sort_ai.engine.create_scorer", return_value=Scorer()), Engine(cfg, stop=stop) as engine:
            summary = engine.scan()
            self.assertTrue(summary["stopped"])
            self.assertEqual(summary["processed"], 0)
            self.assertEqual(engine.store.rows(), [])

    def test_validation(self):
        for value in [0, 17, "4", 2.0, True]:
            with self.assertRaises(ValueError):
                Config(api_concurrency=value).validate(require_folders=False)
        for value in [-1, 11, "2"]:
            with self.assertRaises(ValueError):
                Config(api_rate_limit_retries=value).validate(require_folders=False)
        Config(api_concurrency=16, api_rate_limit_retries=0).validate(require_folders=False)

    def test_concurrency_does_not_change_analysis_signature(self):
        self.assertEqual(Config(api_concurrency=1).analysis_signature(), Config(api_concurrency=8).analysis_signature())

    def test_cli_option_only_for_http_backends(self):
        folder = self.photos(self.root / "photos", 1)
        config = self.root / "config.json"
        config.write_text(json.dumps({"folders": [str(folder)], "preset": "light",
                                      "state_dir": str(self.root / "state"), "output": "report"}), encoding="utf-8")
        with patch("photo_sort_ai.cli.output") as out:
            self.assertEqual(main(["--config", str(config), "scan", "--api-concurrency", "4"]), 1)
        self.assertIn("--api-concurrency", out.call_args[0][0]["error"])


if __name__ == "__main__":
    unittest.main()
