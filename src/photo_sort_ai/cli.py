from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import logging
import shutil
import sys
import threading
from pathlib import Path

from .config import PRESETS, Config, load_config, save_config
from .engine import Engine
from .scoring import create_scorer
from .state import Store, duplicate_groups, export_csv


def output(value):
    if sys.stdout is not None:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ローカル写真評価・フォルダ監視・星評価保存")
    p.add_argument("--config", type=Path, default=Path("config.json"), help="設定JSON（サブコマンドの前に指定）")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="設定ファイルを作成")
    init.add_argument("--folder", action="append")
    init.add_argument("--preset", choices=PRESETS, default="light")
    init.add_argument("--overwrite", action="store_true")
    web = sub.add_parser("web", help="ブラウザーで操作するWebUIを起動")
    web.add_argument("--public", action="store_true", help="外部アクセスを許可（パスワードと公開URLが必須）")
    web.add_argument("--host", help="待ち受け先。既定は127.0.0.1、--public時は0.0.0.0")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--public-url", help="アクセスする公開URL（例: https://photos.example.com）")
    web.add_argument("--password-env", default="PHOTO_SORT_WEB_PASSWORD", help="WebUIパスワードの環境変数名")
    web.add_argument("--no-browser", action="store_true", help="起動時にブラウザーを開かない")
    sub.add_parser("doctor", help="依存・ExifTool・設定の確認")
    sub.add_parser("models", help="利用できる評価プリセットを一覧")
    sub.add_parser("prepare-models", help="選択したモデルを事前取得・初期化")
    for command in ["scan", "watch", "benchmark"]:
        s = sub.add_parser(command, help={"scan": "既存写真を一括処理", "watch": "フォルダを継続監視", "benchmark": "画像準備＋評価時間の実測（写真を変更しません）"}[command])
        s.add_argument("--folder", action="append", help="設定内の対象フォルダを置き換える")
        s.add_argument("--preset", choices=PRESETS)
        s.add_argument("--device", choices=["cpu", "cuda", "auto"])
        s.add_argument("--output", choices=["embedded", "sidecar", "report"])
        s.add_argument("--api-url", help="OpenAI互換APIのベースURLまたは完全URL")
        s.add_argument("--api-model", help="OpenAI互換APIの画像対応モデルID")
        s.add_argument("--api-mode", choices=["chat_completions", "responses", "decisions", "openrouter_decisions"])
        s.add_argument("--api-key-env", help="APIキーの環境変数名。匿名APIは空文字")
        s.add_argument("--api-json-mode", choices=["json_schema", "json_object", "none"])
        s.add_argument("--api-concurrency", type=int, help="API／Ollamaの同時リクエスト数（1〜16）")
        s.add_argument("--retry-errors", action="store_true")
        s.add_argument("--overwrite-rated", action="store_true", help="既存の星評価の上書きを許可")
        if command != "watch":
            s.add_argument("--limit", type=int, default=20 if command == "benchmark" else None)
        if command == "scan":
            s.add_argument("--force", action="store_true", help="評価キャッシュを無視（既存評価の保護は別設定）")
    export = sub.add_parser("export", help="結果をCSVへ出力")
    export.add_argument("destination", type=Path)
    duplicates = sub.add_parser("duplicates", help="類似写真の候補一覧をJSONへ出力（写真は変更しません）")
    duplicates.add_argument("destination", type=Path)
    duplicates.add_argument("--distance", type=int, default=4)
    return p


def doctor(config: Config) -> dict:
    installed = {}
    for name in ["numpy", "PIL", "torch", "pyiqa", "rawpy", "onnxruntime", "flask", "waitress"]:
        installed[name] = importlib.util.find_spec(name) is not None
    versions = {}
    for name in ["numpy", "Pillow", "torch", "pyiqa", "rawpy", "onnxruntime", "onnxruntime-directml"]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    config.validate(require_folders=False)
    return {"python": sys.version.split()[0], "dependencies": installed, "versions": versions,
            "exiftool": shutil.which(config.exiftool_path), "preset": config.preset,
            "backend": config.backend, "device": config.device, "folders": config.folders,
            "state_dir": config.state_dir, "output": config.output}


def main(argv=None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = parser().parse_args(argv)
    log_handler = None
    try:
        if args.command == "init":
            if args.config.exists() and not args.overwrite:
                raise ValueError("設定ファイルが既にあります。置き換える場合は --overwrite を指定してください。")
            cfg = Config(folders=[str(Path(f).resolve()) for f in args.folder or []])
            cfg.use_preset(args.preset)
            save_config(cfg, args.config)
            output({"config": str(args.config.resolve())})
            return 0
        cfg = load_config(args.config) if args.config.exists() else Config()
        if args.command == "web":
            from .web import run_web
            run_web(cfg, args.config, public=args.public, host=args.host, port=args.port,
                    public_url=args.public_url, password_env=args.password_env, open_browser=not args.no_browser)
            return 0
        if args.command == "models":
            for name, (backend, metrics) in PRESETS.items():
                output({"preset": name, "backend": backend, "models": [m.name for m in metrics]})
            output({"preset": "custom", "note": "JSONのbackend・metricsでNRモデルの組み合わせと重みを指定できます。"})
            return 0
        if args.command == "doctor":
            output(doctor(cfg))
            return 0
        state_dir = Path(cfg.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        log_handler = logging.FileHandler(state_dir / "app.log", encoding="utf-8")
        log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger().addHandler(log_handler)
        if args.command == "prepare-models":
            create_scorer(cfg)
            output({"status": "ready", "preset": cfg.preset,
                    "note": ("API設定・キー取得を確認しました。API呼び出しは行っていません。"
                             if cfg.backend == "openai" else "Ollamaのモデル取得は ollama pull、ONNXはローカルモデル配置を別途行ってください。")})
            return 0
        if args.command in {"export", "duplicates"}:
            store = Store(state_dir)
            try:
                if args.command == "export":
                    export_csv(store, args.destination)
                else:
                    groups = duplicate_groups(store, args.distance)
                    args.destination.parent.mkdir(parents=True, exist_ok=True)
                    args.destination.write_text(json.dumps(groups, indent=2, ensure_ascii=False), encoding="utf-8")
                output({"saved": str(args.destination.resolve())})
            finally:
                store.close()
            return 0
        if args.folder:
            cfg.folders = [str(Path(f).resolve()) for f in args.folder]
        if args.preset:
            cfg.use_preset(args.preset)
        if args.device:
            cfg.device = args.device
        if args.output:
            cfg.output = args.output
        api_overrides = {"api_url": "openai_base_url", "api_model": "openai_model",
                         "api_mode": "openai_api_mode", "api_key_env": "openai_api_key_env",
                         "api_json_mode": "openai_response_format"}
        for option, key in api_overrides.items():
            value = getattr(args, option)
            if value is not None:
                if cfg.backend != "openai":
                    raise ValueError("--api-*を使う場合は --preset openai、またはAPI方式の設定を指定してください。")
                setattr(cfg, key, value)
        if getattr(args, "api_concurrency", None) is not None:
            if cfg.backend not in {"openai", "ollama"}:
                raise ValueError("--api-concurrencyはAPI方式またはOllamaでのみ指定できます。")
            cfg.api_concurrency = args.api_concurrency
        if args.overwrite_rated:
            cfg.existing_policy = "overwrite"
        limit = getattr(args, "limit", None)
        if limit is not None and limit < 1:
            raise ValueError("--limitは1以上にしてください。")
        stop = threading.Event()
        import signal
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
        with Engine(cfg, output, stop) as engine:
            if args.retry_errors:
                engine.store.clear_failures()
            if args.command == "watch":
                engine.watch()
                return 0
            if args.command == "benchmark":
                result = engine.benchmark(args.limit)
                (state_dir / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                return 1 if result["failed"] else 0
            summary = engine.scan(force=args.force, limit=args.limit)
            return 1 if summary["errors"] or engine.store.failures() else 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        logging.exception("処理に失敗しました")
        output({"kind": "fatal", "error": str(error)})
        return 1
    finally:
        if log_handler is not None:
            logging.getLogger().removeHandler(log_handler)
            log_handler.close()
