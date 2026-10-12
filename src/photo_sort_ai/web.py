"""Single-user WebUI. One worker owns the existing CLI engine and metadata lock."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import secrets
import signal
import tempfile
import threading
import time
import webbrowser
from collections import OrderedDict, deque
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file, session
from werkzeug.exceptions import HTTPException

from .config import PRESETS, Config, config_from_dict, save_config
from .engine import Engine
from .images import ImagePreparer
from .scoring import create_scorer
from .state import Store, duplicate_groups, export_csv


def origin_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(ord(c) <= 32 for c in value)):
        raise ValueError("公開URLはパス・認証情報を含まないHTTP(S)のURLにしてください。")
    try:
        parsed.port
    except ValueError:
        raise ValueError("公開URLのポート番号が不正です。") from None
    return f"{parsed.scheme}://{parsed.netloc.lower()}".rstrip("/")


class WebController:
    def __init__(self, config: Config, config_path: Path):
        self.config = copy.deepcopy(config)
        self.config_path = config_path.resolve()
        self.lock = threading.RLock()
        self.worker = None
        self.stop = threading.Event()
        self.mode = None
        self.events = deque(maxlen=500)
        self.sequence = 0
        self.current_path = ""
        self.summary = None
        self.benchmark = None
        self.error = None
        self.started = None
        self.finished = None
        self.previews = OrderedDict()

    def active(self):
        return self.worker is not None and self.worker.is_alive()

    def idle(self):
        if self.active():
            abort(409, description="処理を停止してから設定や履歴を変更してください。")

    def update_config(self, data, api_key=None):
        with self.lock:
            self.idle()
            defaults = asdict(Config())
            if not isinstance(data, dict) or set(data) - defaults.keys():
                raise ValueError("不明な設定項目、または設定JSONの形式が不正です。")
            for name, value in data.items():
                default = defaults[name]
                expected = type(default)
                if expected is float or name.endswith("_seconds") or name.endswith("_timeout"):
                    valid = type(value) in {int, float}
                elif name == "openai_store":
                    valid = value is None or type(value) is bool
                else:
                    valid = type(value) is expected
                if not valid:
                    raise ValueError(f"設定の型が不正です: {name}")
            cfg = config_from_dict(data, self.config_path)
            cfg.validate(require_folders=False)
            if api_key is not None:
                if (not isinstance(api_key, str) or len(api_key) > 4096
                        or any(ord(c) < 32 or ord(c) > 126 for c in api_key)):
                    raise ValueError("APIキーに使用できない文字が含まれています。")
                if not cfg.openai_api_key_env:
                    raise ValueError("APIキーを入力する場合は環境変数名も指定してください。")
                # Restrict browser-written environment variables to API key names.
                if not cfg.openai_api_key_env.endswith("_API_KEY"):
                    raise ValueError("WebUIで入力するキーの変数名は_API_KEYで終わる名前にしてください。")
            save_config(cfg, self.config_path)
            if api_key is not None:
                os.environ[cfg.openai_api_key_env] = api_key.strip()
            self.config = cfg
            self.previews.clear()
            return asdict(cfg)

    def emit(self, event):
        with self.lock:
            self.sequence += 1
            self.events.append({**event, "id": self.sequence, "time": time.time()})
            if event["kind"] == "processing":
                self.current_path = event["path"]
            elif event["kind"] == "summary":
                self.summary = event
            elif event["kind"] == "benchmark":
                self.benchmark = event

    def start(self, mode, limit=20, force=False):
        if mode not in {"scan", "watch", "benchmark", "prepare"}:
            raise ValueError("処理方法が不正です。")
        if type(limit) is not int or not 1 <= limit <= 10000 or type(force) is not bool:
            raise ValueError("計測枚数は1〜10000、forceは真偽値にしてください。")
        with self.lock:
            self.idle()
            cfg = copy.deepcopy(self.config)
            cfg.validate(require_folders=mode != "prepare")
            self.stop = threading.Event()
            self.mode, self.error, self.summary = mode, None, None
            self.current_path, self.benchmark = "", None
            self.started, self.finished = time.time(), None

            def work():
                try:
                    directory = Path(cfg.state_dir)
                    directory.mkdir(parents=True, exist_ok=True)
                    if mode == "prepare":
                        create_scorer(cfg)
                        self.emit({"kind": "log", "message": "API設定・キー取得を確認しました（通信は未実施）。" if cfg.backend == "openai" else "モデルの準備が完了しました。"})
                    else:
                        with Engine(cfg, self.emit, self.stop) as engine:
                            if mode == "scan":
                                engine.scan(force=force)
                            elif mode == "watch":
                                engine.watch()
                            else:
                                result = engine.benchmark(limit)
                                (directory / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as error:
                    with self.lock:
                        self.error = str(error)
                    self.emit({"kind": "error", "error": str(error)})
                finally:
                    with self.lock:
                        self.current_path = ""
                        self.finished = time.time()
                    self.emit({"kind": "finished", "stopped": self.stop.is_set()})

            self.worker = threading.Thread(target=work, name="photo-sort-worker", daemon=False)
            self.worker.start()

    def snapshot(self, after=0):
        with self.lock:
            return {"running": self.active(), "stopping": self.active() and self.stop.is_set(),
                    "mode": self.mode, "current_path": self.current_path, "summary": self.summary,
                    "benchmark": self.benchmark, "error": self.error, "started": self.started,
                    "finished": self.finished, "events": [e for e in self.events if e["id"] > after]}

    def history(self):
        with self.lock:
            directory = Path(self.config.state_dir)
        store = Store(directory)
        try:
            rows = [{"path": row["path"], "status": row["status"], "updated": row["updated"],
                     "preview_id": hashlib.sha256(row["path"].encode()).hexdigest(),
                     **json.loads(row["result"])} for row in store.rows(limit=1000)]
            return {"results": rows, "failures": store.failures()[:1000]}
        finally:
            store.close()

    def preview(self, identifier):
        with self.lock:
            cfg = copy.deepcopy(self.config)
        row = next((r for r in self.history()["results"] if r["preview_id"] == identifier), None)
        if not row:
            abort(404)
        path = Path(row["path"]).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in cfg.folders) or not path.is_file():
            abort(404)
        stamp = (str(path), path.stat().st_mtime_ns, cfg.image_source)
        with self.lock:
            if stamp in self.previews:
                self.previews.move_to_end(stamp)
                return self.previews[stamp]
        cfg.max_side = 640
        image = ImagePreparer(cfg).prepare(path).image
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=80, exif=b"")
        result = buffer.getvalue()
        with self.lock:
            self.previews[stamp] = result
            while len(self.previews) > 32:
                self.previews.popitem(last=False)
        return result

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join()


def create_app(controller: WebController, *, password="", public_url=None, public=False):
    if public and (len(password) < 12 or not public_url):
        raise ValueError("外部公開は12文字以上のパスワードと--public-urlが必要です。")
    origin = origin_url(public_url) if public_url else None
    if origin and not public:
        raise ValueError("--public-urlを使う場合は--publicを指定してください。")
    app = Flask(__name__, static_folder="web_assets", static_url_path="/assets")
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=1_000_000,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=bool(origin and origin.startswith("https://")),
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    attempts = deque(maxlen=100)
    auth_lock = threading.Lock()

    def body_object():
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("JSONオブジェクトを指定してください。")
        return body

    @app.before_request
    def protect():
        allowed = {urlsplit(origin).netloc.lower()} if origin else {
            "127.0.0.1", "localhost", "[::1]",
            f"127.0.0.1:{request.environ.get('SERVER_PORT')}",
            f"localhost:{request.environ.get('SERVER_PORT')}", f"[::1]:{request.environ.get('SERVER_PORT')}"}
        if request.host.lower() not in allowed:
            abort(403, description="許可されていないHostです。公開URLの設定を確認してください。")
        if request.path.startswith("/api/") and request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403, description="別サイトからのアクセスは許可されていません。")
        csrf = session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected_origin = origin or request.host_url.rstrip("/")
            if request.headers.get("Origin") not in {None, expected_origin}:
                abort(403, description="別サイトからの操作は許可されていません。")
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
                abort(403, description="操作用トークンが無効です。画面を再読み込みしてください。")
        if request.path.startswith("/api/") and request.path not in {"/api/session", "/api/login"}:
            if password and not session.get("authenticated"):
                abort(401, description="ログインしてください。")

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(Exception)
    def error_response(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        if isinstance(error, (ValueError, TypeError, KeyError, OSError)):
            return jsonify(error=str(error)), 400
        logging.exception("WebUIの操作に失敗しました")
        return jsonify(error="操作に失敗しました。サーバーのログを確認してください。"), 500

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/session")
    def session_info():
        return jsonify(authenticated=not password or bool(session.get("authenticated")),
                       auth_required=bool(password), csrf=session["csrf"], public=public, public_url=origin)

    @app.post("/api/login")
    def login():
        with auth_lock:
            now = time.monotonic()
            while attempts and now - attempts[0] >= 60:
                attempts.popleft()
            if len(attempts) >= 10:
                abort(429, description="ログインを繰り返したため、1分待ってください。")
            value = body_object().get("password", "")
            if not isinstance(value, str) or not secrets.compare_digest(value.encode(), password.encode()):
                attempts.append(now)
                abort(401, description="パスワードが違います。")
        session.clear()
        session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return jsonify(csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/config")
    def config_get():
        with controller.lock:
            return jsonify(config=asdict(controller.config), presets=list(PRESETS) + ["custom"])

    @app.post("/api/config")
    def config_set():
        body = body_object()
        return jsonify(config=controller.update_config(body["config"], body.get("api_key")))

    @app.get("/api/status")
    def status():
        return jsonify(controller.snapshot(int(request.args.get("after", "0"))))

    @app.post("/api/jobs")
    def start_job():
        body = body_object()
        controller.start(body["mode"], body.get("limit", 20), body.get("force", False))
        return jsonify(ok=True), 202

    @app.post("/api/stop")
    def stop_job():
        controller.stop.set()
        return jsonify(ok=True)

    @app.get("/api/results")
    def results():
        return jsonify(controller.history())

    @app.post("/api/retry")
    def retry():
        with controller.lock:
            controller.idle()
            store = Store(Path(controller.config.state_dir))
            try:
                store.clear_failures()
            finally:
                store.close()
        return jsonify(ok=True)

    @app.get("/api/export.csv")
    def export():
        with controller.lock:
            directory = Path(controller.config.state_dir)
        store = Store(directory)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "results.csv"
                export_csv(store, destination)
                content = destination.read_bytes()
        finally:
            store.close()
        return send_file(io.BytesIO(content), mimetype="text/csv", as_attachment=True, download_name="photo-ratings.csv")

    @app.get("/api/duplicates.json")
    def duplicates():
        with controller.lock:
            directory = Path(controller.config.state_dir)
        store = Store(directory)
        try:
            content = json.dumps(duplicate_groups(store), ensure_ascii=False, indent=2).encode()
        finally:
            store.close()
        return send_file(io.BytesIO(content), mimetype="application/json", as_attachment=True, download_name="similar-photos.json")

    @app.get("/api/preview/<identifier>")
    def preview(identifier):
        return send_file(io.BytesIO(controller.preview(identifier)), mimetype="image/jpeg")

    app.extensions["photo_controller"] = controller
    return app


def run_web(config, config_path, *, public=False, host=None, port=8765, public_url=None,
            password_env="PHOTO_SORT_WEB_PASSWORD", open_browser=True):
    from waitress import create_server
    if not 1 <= port <= 65535:
        raise ValueError("ポートは1〜65535にしてください。")
    host = host or ("0.0.0.0" if public else "127.0.0.1")
    if not public and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ローカル以外で待ち受ける場合は--publicを指定してください。")
    password = os.environ.get(password_env, "") if password_env else ""
    controller = WebController(config, config_path)
    app = create_app(controller, password=password, public_url=public_url, public=public)
    directory = Path(config.state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / "app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    server = create_server(app, host=host, port=port, threads=6, max_request_body_size=1_000_000,
                           clear_untrusted_proxy_headers=True)
    url = origin_url(public_url) if public_url else f"http://{'[::1]' if host == '::1' else '127.0.0.1'}:{port}"
    print(f"Photo Sort AI WebUI: {url}\n待ち受け: {host}:{port} / Ctrl+Cで終了", flush=True)
    previous = {}

    def shutdown(*_):
        controller.stop.set()
        raise KeyboardInterrupt

    for name in [signal.SIGINT, signal.SIGTERM]:
        previous[name] = signal.signal(name, shutdown)
    try:
        if open_browser and not public:
            webbrowser.open(url)
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        print("現在の評価の終了を待っています…", flush=True)
        controller.close()
        for name, previous_handler in previous.items():
            signal.signal(name, previous_handler)
        logging.getLogger().removeHandler(handler)
        handler.close()
