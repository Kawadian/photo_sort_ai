"""Real CLI/Waitress HTTP smoke, on Windows and Linux without a display."""
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from photo_sort_ai.config import Config, save_config

with tempfile.TemporaryDirectory() as directory:
    config_path = Path(directory) / "config.json"
    save_config(Config(state_dir=str(Path(directory) / "state")), config_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.pop("PHOTO_SORT_WEB_PASSWORD", None)
    log_path = Path(directory) / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "photo_sort_ai", "--config", str(config_path),
                                    "web", "--no-browser", "--port", str(port)], stdout=log, stderr=log, env=env)
        try:
            base = f"http://127.0.0.1:{port}"
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            deadline = time.monotonic() + 15
            while True:
                try:
                    with opener.open(base + "/api/session", timeout=2) as response:
                        info = json.load(response)
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(log_path.read_text())
                    time.sleep(0.1)
            assert info["authenticated"] and not info["public"]
            for path, expected in [("/", b"Photo Sort AI"), ("/assets/app.js", b"initialize"),
                                   ("/assets/style.css", b"sidebar")]:
                with opener.open(base + path, timeout=2) as response:
                    assert expected in response.read()
            with opener.open(base + "/api/config", timeout=2) as response:
                data = json.load(response)["config"]
            data["api_concurrency"] = 3
            req = urllib.request.Request(base + "/api/config", data=json.dumps({"config": data}).encode(),
                                         headers={"Content-Type": "application/json", "X-CSRF-Token": info["csrf"]})
            with opener.open(req, timeout=2) as response:
                assert json.load(response)["config"]["api_concurrency"] == 3
            print("WebUI real HTTP startup, assets, session and config save: OK")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
