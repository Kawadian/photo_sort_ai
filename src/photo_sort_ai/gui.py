from __future__ import annotations

import copy
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import Config, save_config
from .engine import Engine
from .state import Store, duplicate_groups, export_csv

PRESET_LABELS = {
    "light": "軽量解析（AIなし）", "quality": "MUSIQ：画質", "aesthetic": "NIMA：見栄え",
    "balanced": "MUSIQ＋NIMA：合算", "topiq": "TOPIQ：画質", "clip": "CLIPIQA：画質",
    "laion": "LAION：見栄え", "technical": "BRISQUE＋NIQE：技術評価",
    "ollama": "Ollama：ローカル画像AI", "openai": "OpenAI互換API", "onnx": "ONNX：独自モデル", "custom": "カスタム（JSON設定）",
}
STATUS_LABELS = {"written": "星を保存", "sidecar": "XMPへ保存", "report": "結果のみ", "protected": "既存の星を保護"}


class App:
    def __init__(self, root: tk.Tk, config: Config, config_path: Path):
        self.root, self.config, self.config_path = root, config, config_path.resolve()
        self.events = queue.Queue()
        self.worker = None
        self.stop = threading.Event()
        self.closing = False
        self.root.title("Photo Sort AI — ローカル写真評価")
        self.root.geometry("1050x760")
        self.root.minsize(820, 640)
        self.vars = {}
        self.row_paths = {}
        self.buttons = []
        self.build()
        self.refresh_history()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.drain)

    def build(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="写真を置くだけで、星評価を自動で付ける", font=("", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="CPU・GPU・APIで評価。写真の削除は行いません。").pack(anchor="w", pady=(4, 12))
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        settings = ttk.Frame(notebook, padding=12)
        api_settings = ttk.Frame(notebook, padding=12)
        results = ttk.Frame(notebook, padding=12)
        logs = ttk.Frame(notebook, padding=12)
        notebook.add(settings, text="設定・実行")
        notebook.add(api_settings, text="API設定")
        notebook.add(results, text="評価結果")
        notebook.add(logs, text="ログ")

        folder_frame = ttk.LabelFrame(settings, text="対象フォルダ（複数追加できます）", padding=8)
        folder_frame.pack(fill="x")
        self.folders = tk.Listbox(folder_frame, height=3)
        self.folders.pack(side="left", fill="x", expand=True)
        for f in self.config.folders:
            self.folders.insert("end", f)
        controls = ttk.Frame(folder_frame)
        controls.pack(side="right", padx=8)
        ttk.Button(controls, text="追加", command=self.add_folder).pack(fill="x")
        ttk.Button(controls, text="削除", command=self.remove_folder).pack(fill="x", pady=4)
        grid = ttk.Frame(settings)
        grid.pack(fill="x", pady=10)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)
        self.field(grid, 0, 0, "評価方法", "preset", PRESET_LABELS.get(self.config.preset, self.config.preset), list(PRESET_LABELS.values()))
        self.field(grid, 0, 2, "実行デバイス", "device", self.config.device, ["cpu", "cuda", "auto"])
        self.field(grid, 1, 0, "保存方法", "output", self.config.output, ["embedded", "sidecar", "report"])
        self.field(grid, 1, 2, "画像の取得方法", "image_source", self.config.image_source, ["auto", "preview", "raw", "companion"])
        self.field(grid, 2, 0, "画像の最大辺", "max_side", self.config.max_side)
        self.field(grid, 2, 2, "CPUスレッド数", "cpu_threads", self.config.cpu_threads)
        self.field(grid, 3, 0, "監視間隔（秒）", "poll_seconds", self.config.poll_seconds)
        self.field(grid, 3, 2, "コピー安定待ち（秒）", "settle_seconds", self.config.settle_seconds)
        self.field(grid, 4, 0, "写真ごとの休止（秒）", "delay_seconds", self.config.delay_seconds)
        self.field(grid, 4, 2, "星の境界（0〜100）", "thresholds", ",".join(map(str, self.config.thresholds)))
        self.field(grid, 5, 0, "ExifToolの場所", "exiftool_path", self.config.exiftool_path)
        self.field(grid, 5, 2, "Ollamaモデル", "ollama_model", self.config.ollama_model)
        self.field(grid, 6, 0, "ONNXモデルの場所", "onnx_path", self.config.onnx_path)
        self.field(grid, 6, 2, "ONNX実行方式", "onnx_provider", self.config.onnx_provider,
                   ["CPUExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"])
        checks = ttk.Frame(settings)
        checks.pack(fill="x")
        for key, label, value in [
            ("recursive", "サブフォルダも対象", self.config.recursive),
            ("low_priority", "低優先度で実行", self.config.low_priority),
            ("backup", "書き込み前にバックアップ", self.config.backup),
            ("preserve", "既存の星を保護", self.config.existing_policy == "preserve"),
        ]:
            var = tk.BooleanVar(value=value)
            self.vars[key] = var
            ttk.Checkbutton(checks, text=label, variable=var).pack(side="left", padx=(0, 10))
        ttk.Label(settings, text="embedded：DNG/JPEG/TIFF内部へ保存。他の形式はXMPへ。report：写真を変更せず結果だけ保存。\n"
                  "preview：埋め込み画像のみ。raw：RAWを現像。companion：同名JPEG。auto：プレビュー優先でRAW現像へ切替。\n"
                  "カスタムモデル・重み・ONNXの入出力設定は設定JSONで編集できます。", wraplength=930).pack(anchor="w", pady=10)
        actions = ttk.Frame(settings)
        actions.pack(fill="x")
        for label, task in [("設定を保存", self.save), ("一括評価", lambda: self.start("scan")),
                            ("監視開始", lambda: self.start("watch")), ("時間を実測（20枚）", lambda: self.start("benchmark")),
                            ("モデル準備", lambda: self.start("prepare"))]:
            button = ttk.Button(actions, text=label, command=task)
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        ttk.Button(actions, text="停止", command=lambda: self.stop.set()).pack(side="left")
        ttk.Button(settings, text="失敗した写真の再試行を許可", command=self.retry_errors).pack(anchor="w", pady=8)

        ttk.Label(api_settings, text="評価方法で「OpenAI互換API」を選ぶと、縮小したJPEGを指定先へ送信して評価します。\n"
                  "APIキーは環境変数から取得、またはこの起動中だけ入力して使えます。設定JSONには保存しません。",
                  wraplength=930).pack(anchor="w", pady=(0, 12))
        api_grid = ttk.Frame(api_settings)
        api_grid.pack(fill="x")
        api_grid.columnconfigure(1, weight=1)
        api_grid.columnconfigure(3, weight=1)
        self.field(api_grid, 0, 0, "API URL", "openai_base_url", self.config.openai_base_url)
        self.field(api_grid, 0, 2, "画像対応モデル名", "openai_model", self.config.openai_model)
        self.field(api_grid, 1, 0, "API方式", "openai_api_mode", self.config.openai_api_mode, ["chat_completions", "responses", "decisions", "openrouter_decisions"])
        self.field(api_grid, 1, 2, "JSON方式", "openai_response_format", self.config.openai_response_format, ["json_object", "json_schema", "none"])
        self.field(api_grid, 2, 0, "APIキーの環境変数", "openai_api_key_env", self.config.openai_api_key_env)
        ttk.Label(api_grid, text="APIキー（起動中のみ）").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=4)
        self.api_key = tk.StringVar()
        ttk.Entry(api_grid, textvariable=self.api_key, show="*").grid(row=2, column=3, sticky="ew", padx=(0, 16), pady=4)
        self.field(api_grid, 3, 0, "画像詳細", "openai_image_detail", self.config.openai_image_detail, ["auto", "low", "high", "omit"])
        self.field(api_grid, 3, 2, "API待機時間（秒）", "openai_timeout", self.config.openai_timeout)
        self.field(api_grid, 4, 0, "出力トークン上限", "openai_max_output_tokens", self.config.openai_max_output_tokens)
        store = "omit" if self.config.openai_store is None else str(self.config.openai_store).lower()
        self.field(api_grid, 4, 2, "APIのstore指定", "openai_store", store, ["false", "true", "omit"])
        self.field(api_grid, 5, 0, "同時リクエスト数", "api_concurrency", self.config.api_concurrency)
        self.field(api_grid, 5, 2, "429/503の再試行回数", "api_rate_limit_retries", self.config.api_rate_limit_retries)
        ttk.Label(api_settings, text="評価プロンプト（Chat／Responsesは星と理由のJSON、Decisionsは星1〜5の選択結果を取得）").pack(anchor="w", pady=(14, 4))
        self.api_prompt = tk.Text(api_settings, height=6, wrap="word")
        self.api_prompt.insert("1.0", self.config.openai_prompt)
        self.api_prompt.pack(fill="x")
        ttk.Label(api_settings, text="追加パラメータ（JSON。例: reasoning_effort、temperature。対応は指定先・モデルに依存）").pack(anchor="w", pady=(12, 4))
        self.api_extra = tk.Text(api_settings, height=3, wrap="word")
        self.api_extra.insert("1.0", json.dumps(self.config.openai_extra_body, ensure_ascii=False))
        self.api_extra.pack(fill="x")
        ttk.Label(api_settings, text="URLは /v1 等のベースURL、または選択したAPIの完全URL。互換サーバーに合わせてJSON方式・store指定を変更できます。\n"
                  "DecisionsはAPI方式で明示選択。JSON方式・出力上限・storeは送信せず、文章の理由は生成しません。\n"
                  "同時リクエスト数（1〜16）はAPI／Ollamaで写真を並列に評価します。429/503はRetry-Afterに従い全体で待機して再試行します。\n"
                  "キー不要のローカルAPIでは環境変数名を空欄にします。変更後は「設定・実行」タブから保存・再開してください。",
                  wraplength=930).pack(anchor="w", pady=12)

        columns = ("file", "rating", "score", "status", "seconds")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", selectmode="browse")
        for key, label, width in [("file", "ファイル", 410), ("rating", "AIの星", 70), ("score", "点数", 80),
                                  ("status", "保存結果", 160), ("seconds", "秒", 80)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        result_actions = ttk.Frame(results)
        result_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(result_actions, text="CSVへ出力", command=self.export).pack(side="left")
        ttk.Button(result_actions, text="類似写真の候補を書き出す", command=self.duplicates).pack(side="left", padx=8)
        ttk.Button(result_actions, text="選択した写真の場所を開く", command=self.open_selected).pack(side="left")
        ttk.Label(results, text="AIの星とファイル内の既存の星は、保護設定により異なる場合があります。").pack(anchor="w", pady=6)
        self.log = tk.Text(logs, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="待機中")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(8, 0))

    def field(self, grid, row, col, label, key, value, choices=None):
        ttk.Label(grid, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
        var = tk.StringVar(value=str(value))
        self.vars[key] = var
        widget = ttk.Combobox(grid, textvariable=var, values=choices, state="readonly") if choices else ttk.Entry(grid, textvariable=var)
        widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 16), pady=4)

    def add_folder(self):
        folder = filedialog.askdirectory()
        if folder and folder not in self.folders.get(0, "end"):
            self.folders.insert("end", folder)

    def remove_folder(self):
        for i in reversed(self.folders.curselection()):
            self.folders.delete(i)

    def collect(self, require_folders=True) -> Config:
        cfg = copy.deepcopy(self.config)
        cfg.folders = list(self.folders.get(0, "end"))
        preset = next(key for key, label in PRESET_LABELS.items() if label == self.vars["preset"].get())
        if preset != "custom":
            cfg.use_preset(preset)
        else:
            cfg.preset = "custom"
        for key in ["device", "output", "image_source", "exiftool_path", "ollama_model", "onnx_path", "onnx_provider",
                    "openai_base_url", "openai_model", "openai_api_mode", "openai_response_format",
                    "openai_api_key_env", "openai_image_detail"]:
            setattr(cfg, key, self.vars[key].get().strip())
        for key in ["max_side", "cpu_threads"]:
            setattr(cfg, key, int(self.vars[key].get()))
        for key in ["poll_seconds", "settle_seconds", "delay_seconds"]:
            setattr(cfg, key, float(self.vars[key].get()))
        cfg.settle_timeout = max(cfg.settle_timeout, cfg.settle_seconds + 30)
        cfg.thresholds = [float(s.strip()) for s in self.vars["thresholds"].get().split(",")]
        for key in ["recursive", "low_priority", "backup"]:
            setattr(cfg, key, self.vars[key].get())
        cfg.existing_policy = "preserve" if self.vars["preserve"].get() else "overwrite"
        cfg.openai_timeout = float(self.vars["openai_timeout"].get())
        cfg.openai_max_output_tokens = int(self.vars["openai_max_output_tokens"].get())
        cfg.api_concurrency = int(self.vars["api_concurrency"].get())
        cfg.api_rate_limit_retries = int(self.vars["api_rate_limit_retries"].get())
        cfg.openai_store = {"false": False, "true": True, "omit": None}[self.vars["openai_store"].get()]
        cfg.openai_prompt = self.api_prompt.get("1.0", "end-1c").strip()
        cfg.openai_extra_body = json.loads(self.api_extra.get("1.0", "end-1c"))
        if cfg.onnx_path and not Path(cfg.onnx_path).is_absolute():
            cfg.onnx_path = str((self.config_path.parent / cfg.onnx_path).resolve())
        cfg.validate(require_folders)
        if cfg.backend == "openai" and self.api_key.get().strip():
            if not cfg.openai_api_key_env:
                raise ValueError("APIキーを入力する場合は環境変数名も指定してください。")
            os.environ[cfg.openai_api_key_env] = self.api_key.get().strip()
        return cfg

    def save(self):
        try:
            self.config = self.collect(require_folders=False)
            save_config(self.config, self.config_path)
            self.status.set(f"設定を保存しました: {self.config_path}")
        except Exception as error:
            messagebox.showerror("設定を確認してください", str(error))

    def start(self, mode):
        if self.worker and self.worker.is_alive():
            return
        try:
            cfg = self.collect(require_folders=mode != "prepare")
            save_config(cfg, self.config_path)
            self.config = cfg
        except Exception as error:
            messagebox.showerror("設定を確認してください", str(error))
            return
        self.stop = threading.Event()
        for button in self.buttons:
            button.configure(state="disabled")
        self.status.set("準備中…")

        def work():
            try:
                directory = Path(cfg.state_dir)
                directory.mkdir(parents=True, exist_ok=True)
                logging.basicConfig(filename=directory / "app.log", level=logging.INFO,
                                    format="%(asctime)s %(message)s", encoding="utf-8")
                if mode == "prepare":
                    from .scoring import create_scorer
                    create_scorer(cfg)
                    message = ("APIの設定・キー取得を確認しました。接続確認は評価または時間の実測で行ってください。"
                               if cfg.backend == "openai" else "モデルの初期化が完了しました。Ollamaのモデルは別途ollama pullで取得してください。")
                    self.events.put({"kind": "log", "message": message})
                    return
                with Engine(cfg, self.events.put, self.stop) as engine:
                    if mode == "watch":
                        engine.watch()
                    elif mode == "scan":
                        engine.scan()
                    elif mode == "benchmark":
                        result = engine.benchmark(20)
                        (directory / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as error:
                self.events.put({"kind": "error", "error": str(error)})
            finally:
                self.events.put({"kind": "finished"})
        self.worker = threading.Thread(target=work, daemon=False)
        self.worker.start()

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        # Bound memory for long-running monitoring.
        if int(self.log.index("end-1c").split(".")[0]) > 2000:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def insert_result(self, path, result, status):
        item = self.row_paths.get(path)
        values = (Path(path).name, "★" * result["rating"], f"{result['score']:.1f}",
                  STATUS_LABELS.get(status, status), f"{result.get('seconds', 0):.2f}")
        if item:
            self.tree.item(item, values=values)
            self.tree.move(item, "", 0)
        else:
            self.row_paths[path] = self.tree.insert("", 0, values=values)
        while len(self.row_paths) > 1000:
            oldest = self.tree.get_children()[-1]
            self.tree.delete(oldest)
            old_path = next(p for p, item in self.row_paths.items() if item == oldest)
            del self.row_paths[old_path]

    def refresh_history(self):
        store = Store(Path(self.config.state_dir))
        try:
            for row in reversed(store.rows(limit=1000)):
                self.insert_result(row["path"], json.loads(row["result"]), row["status"])
        finally:
            store.close()

    def drain(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["kind"]
            if kind == "finished":
                for button in self.buttons:
                    button.configure(state="normal")
                self.status.set("停止しました" if self.stop.is_set() else "処理が終了しました")
            elif kind == "processing":
                self.status.set(f"評価中: {Path(event['path']).name}")
            elif kind == "result":
                self.insert_result(event["path"], event, event["status"])
                self.append_log(f"{Path(event['path']).name}: ★{event['rating']} / {event['score']:.1f} / {STATUS_LABELS.get(event['status'])}")
            elif kind == "benchmark":
                text = (f"実測平均 {event['mean_seconds']:.2f}秒/枚、1,000枚 約{event['estimated_1000_hours']:.2f}時間、"
                        f"1万枚 約{event['estimated_10000_hours']:.2f}時間（保存時間を除く）")
                self.append_log(text)
                if not self.closing:
                    messagebox.showinfo("ベンチマーク", text)
            else:
                self.append_log(event.get("message", json.dumps(event, ensure_ascii=False)))
                if kind == "error":
                    self.status.set("エラー：ログを確認してください")
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.root.destroy()
        else:
            self.root.after(150, self.drain)

    def with_store(self, action):
        store = Store(Path(self.config.state_dir))
        try:
            action(store)
        except Exception as error:
            messagebox.showerror("処理に失敗しました", str(error))
        finally:
            store.close()

    def export(self):
        name = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if name:
            self.with_store(lambda store: export_csv(store, Path(name)))

    def duplicates(self):
        name = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if name:
            self.with_store(lambda store: Path(name).write_text(json.dumps(duplicate_groups(store), indent=2, ensure_ascii=False), encoding="utf-8"))

    def retry_errors(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("停止してから操作してください", "処理を停止してから再試行を許可してください。")
            return
        self.with_store(lambda store: store.clear_failures())
        self.status.set("次回の処理で失敗した写真を再試行します")

    def open_selected(self):
        selected = self.tree.selection()
        path = next((p for p, item in self.row_paths.items() if selected and item == selected[0]), None)
        if path:
            try:
                directory = str(Path(path).parent)
                if os.name == "nt":
                    os.startfile(directory)
                elif shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", directory], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    messagebox.showinfo("写真の場所", directory)
            except OSError as error:
                messagebox.showerror("場所を開けませんでした", str(error))

    def close(self):
        self.closing = True
        self.stop.set()
        self.status.set("現在の評価が終了するまで待っています…")


def run_gui(config: Config, config_path: Path):
    root = tk.Tk()
    App(root, config, config_path)
    root.mainloop()
