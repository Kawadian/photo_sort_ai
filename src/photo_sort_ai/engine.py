from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import threading
import time
from pathlib import Path

from .config import Config
from .images import ImagePreparer, perceptual_hash
from .metadata import MetadataWriter, sidecar_path
from .scoring import create_scorer
from .state import RunLock, Store

logger = logging.getLogger(__name__)


def lower_priority():
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        logger.info("低優先度への切り替えを行えませんでした。")


class Engine:
    def __init__(self, config: Config, callback=None, stop: threading.Event | None = None):
        config.validate()
        self.config = config
        self.lock = RunLock(Path(config.state_dir))
        try:
            self.store = Store(Path(config.state_dir))
        except Exception:
            self.lock.close()
            raise
        self.callback = callback or (lambda event: None)
        self.stop = stop or threading.Event()
        self.preparer = ImagePreparer(config)
        self.writer = MetadataWriter(config)
        self.scorer = None
        self.signature = config.analysis_signature()
        self.output_signature = config.output_signature()
        self.observed: dict[Path, tuple[str, float]] = {}
        if config.low_priority:
            lower_priority()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.store.close()
        self.lock.close()

    def emit(self, kind: str, **data):
        event = {"kind": kind, **data}
        logger.info(json.dumps(event, ensure_ascii=False))
        self.callback(event)

    def discover(self) -> list[Path]:
        found = set()
        state_dir = Path(self.config.state_dir).resolve()
        extensions = {ext.lower() for ext in self.config.extensions}
        for folder in self.config.folders:
            base = Path(folder).resolve()
            try:
                iterator = base.rglob("*") if self.config.recursive else base.iterdir()
                for path in iterator:
                    if self.stop.is_set():
                        break
                    if path.suffix.lower() in extensions and not path.is_symlink() and path.is_file():
                        path = path.resolve()
                        if not path.is_relative_to(state_dir):
                            found.add(path)
            except OSError as error:
                self.emit("error", path=str(base), error=str(error))
        return sorted(found)

    def stamp(self, path: Path) -> str:
        s = path.stat()
        items = [[str(path), s.st_size, s.st_mtime_ns]]
        if self.config.image_source == "companion" and path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
            for companion in sorted(path.parent.iterdir()):
                if companion.stem.casefold() == path.stem.casefold() and companion.suffix.lower() in {".jpg", ".jpeg"}:
                    cs = companion.stat()
                    items.append([str(companion), cs.st_size, cs.st_mtime_ns])
                    break
        if self.writer.effective_mode(path) == "sidecar":
            sidecar = sidecar_path(path)
            if sidecar.exists():
                ss = sidecar.stat()
                items.append([str(sidecar), ss.st_size, ss.st_mtime_ns])
        return json.dumps(items, ensure_ascii=False)

    def should_process(self, path: Path, stamp: str, force: bool) -> bool:
        old = self.store.get(path)
        if not force and old and old["stamp"] == stamp and old["signature"] == self.signature and old["output_signature"] == self.output_signature:
            return False
        return self.store.can_retry(path, stamp, self.signature + self.output_signature, self.config.max_retries)

    def ready(self, path: Path, stamp: str) -> bool:
        now = time.monotonic()
        previous = self.observed.get(path)
        if not previous or previous[0] != stamp:
            self.observed[path] = (stamp, now)
        return now - self.observed[path][1] >= self.config.settle_seconds

    def process(self, path: Path, before: str, force: bool = False) -> bool:
        start = time.perf_counter()
        self.emit("processing", path=str(path))
        try:
            old = self.store.get(path)
            if not force and old and old["stamp"] == before and old["signature"] == self.signature:
                result = json.loads(old["result"])
                self.emit("log", message="保存方法だけ変更されたため、評価結果を再利用します。")
            else:
                prepared = self.preparer.prepare(path)
                if self.stamp(path) != before:
                    self.emit("deferred", path=str(path), reason="画像準備中にファイルが変更されました。")
                    return False
                if self.scorer is None:
                    self.emit("log", message=f"評価方式を準備: {self.config.preset} / {self.config.device}（初回はモデル取得が必要な場合があります）")
                    self.scorer = create_scorer(self.config)
                evaluation = self.scorer.evaluate(prepared.image)
                result = evaluation.to_dict()
                result.update({"source": prepared.source, "size": list(prepared.image.size),
                               "hash64": perceptual_hash(prepared.image),
                               "pixels_sha256": hashlib.sha256(prepared.image.tobytes()).hexdigest()})
            if self.stop.is_set():
                self.emit("deferred", path=str(path), reason="停止要求を受けたため書き込み前で中断しました。")
                return False
            if self.stamp(path) != before:
                self.emit("deferred", path=str(path), reason="評価中にファイルが変更されました。")
                return False
            status = self.writer.apply(path, result["rating"])
            result["seconds"] = round(time.perf_counter() - start, 4)
            self.store.save(path, self.stamp(path), self.signature, self.output_signature, result, status)
            self.observed.pop(path, None)
            self.emit("result", path=str(path), status=status, **result)
            self.stop.wait(self.config.delay_seconds)
            return True
        except Exception as error:
            self.store.fail(path, before, self.signature + self.output_signature, str(error), self.config.retry_seconds)
            self.emit("error", path=str(path), error=str(error))
            return False

    def scan(self, force: bool = False, limit: int | None = None) -> dict:
        candidates = self.discover()
        pending = set(candidates)
        processed, errors = 0, 0
        deadline = time.monotonic() + self.config.settle_timeout
        # Observe every candidate together, rather than waiting settle_seconds per file.
        for path in candidates:
            try:
                self.ready(path, self.stamp(path))
            except OSError:
                pending.discard(path)
        while pending and not self.stop.is_set() and (limit is None or processed < limit):
            for path in sorted(pending):
                if self.stop.is_set() or (limit is not None and processed >= limit):
                    break
                try:
                    stamp = self.stamp(path)
                    if not self.should_process(path, stamp, force):
                        pending.remove(path)
                    elif self.ready(path, stamp):
                        successful = self.process(path, stamp, force)
                        if successful:
                            processed += 1
                        else:
                            errors += 1
                        pending.remove(path)
                except OSError as error:
                    self.emit("error", path=str(path), error=str(error))
                    errors += 1
                    pending.remove(path)
            if pending and time.monotonic() >= deadline:
                break
            if pending:
                self.stop.wait(min(1.0, self.config.poll_seconds))
        summary = {"found": len(candidates), "processed": processed, "errors": errors,
                   "remaining": len(pending), "stopped": self.stop.is_set()}
        self.emit("summary", **summary)
        return summary

    def watch(self):
        self.emit("log", message="フォルダ監視を開始しました。停止ボタンまたはCtrl+Cで終了できます。")
        while not self.stop.is_set():
            candidates = self.discover()
            live = set(candidates)
            self.observed = {p: observation for p, observation in self.observed.items() if p in live}
            for path in candidates:
                if self.stop.is_set():
                    break
                try:
                    stamp = self.stamp(path)
                    if self.should_process(path, stamp, False) and self.ready(path, stamp):
                        self.process(path, stamp)
                except OSError as error:
                    self.emit("error", path=str(path), error=str(error))
            self.stop.wait(self.config.poll_seconds)

    def benchmark(self, limit: int = 20) -> dict:
        """Never write ratings or cache results. Include setup separately from warm runs."""
        paths = self.discover()[:limit]
        if not paths:
            raise ValueError("評価できる写真がありません。")
        start = time.perf_counter()
        if self.scorer is None:
            self.scorer = create_scorer(self.config)
        setup_seconds = time.perf_counter() - start
        timings, errors = [], []
        for path in paths:
            if self.stop.is_set():
                break
            try:
                before = self.stamp(path)
                start = time.perf_counter()
                prepared = self.preparer.prepare(path)
                self.scorer.evaluate(prepared.image)
                if self.stamp(path) != before:
                    raise ValueError("計測中にファイルが変更されました。")
                seconds = time.perf_counter() - start
                timings.append(seconds)
                self.emit("benchmark_item", path=str(path), seconds=round(seconds, 4))
            except Exception as error:
                errors.append({"path": str(path), "error": str(error)})
                self.emit("error", **errors[-1])
        if not timings:
            raise RuntimeError("全画像の計測に失敗しました。")
        warm = timings[1:] or timings
        mean = statistics.mean(warm)
        result = {"measured": len(timings), "failed": len(errors), "setup_seconds": setup_seconds,
                  "first_image_seconds": timings[0], "mean_seconds": mean,
                  "median_seconds": statistics.median(warm), "warm_samples": len(warm),
                  "estimated_1000_hours": (mean + self.config.delay_seconds) * 1000 / 3600,
                  "estimated_10000_hours": (mean + self.config.delay_seconds) * 10000 / 3600,
                  "includes": "画像準備＋評価。メタデータ保存・監視待機・将来のディスク競合は含まない。",
                  "preset": self.config.preset, "device": self.config.device}
        self.emit("benchmark", **result)
        return result
