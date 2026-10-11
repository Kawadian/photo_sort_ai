import copy
import csv
import json
import os
import shutil
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageFilter, TiffImagePlugin

from photo_sort_ai.cli import main
from photo_sort_ai.config import Config, Metric, load_config, save_config
from photo_sort_ai.engine import Engine
from photo_sort_ai.images import ImagePreparer, orient
from photo_sort_ai.metadata import (
    ExifTool,
    MetadataWriter,
    read_sidecar_rating,
    sidecar_path,
)
from photo_sort_ai.scoring import (
    Evaluation,
    HeuristicScorer,
    OllamaScorer,
    normalized,
    star_rating,
)
from photo_sort_ai.state import RunLock, Store, duplicate_groups, export_csv


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.cfg = Config(folders=[str(self.photos)], state_dir=str(self.root / "state"),
                          output="report", low_priority=False, settle_seconds=0, poll_seconds=0.01)

    def photo(self, name="写真.png", color=None):
        path = self.photos / name
        rng = np.random.default_rng(10)
        img = Image.fromarray(rng.integers(0, 256, (128, 192, 3), dtype=np.uint8)) if color is None else Image.new("RGB", (192, 128), color)
        img.save(path)
        return path.resolve()


class ConfigTests(TempCase):
    def test_roundtrip_and_preset_canonicalization(self):
        self.cfg.use_preset("balanced")
        path = self.root / "config.json"
        save_config(self.cfg, path)
        loaded = load_config(path)
        self.assertEqual(loaded.analysis_signature(), self.cfg.analysis_signature())
        data = json.loads(path.read_text())
        data["preset"] = "topiq"
        path.write_text(json.dumps(data))
        loaded = load_config(path)
        self.assertEqual(loaded.metrics[0].name, "topiq_nr")

    def test_custom_metrics_are_preserved(self):
        self.cfg.preset = "custom"
        self.cfg.backend = "pyiqa"
        self.cfg.metrics = [Metric("nima", 3, 7, 2)]
        path = self.root / "config.json"
        save_config(self.cfg, path)
        self.assertEqual(load_config(path).metrics[0].minimum, 3)

    def test_output_mode_does_not_invalidate_analysis(self):
        sig = self.cfg.analysis_signature()
        self.cfg.output = "embedded"
        self.assertEqual(sig, self.cfg.analysis_signature())
        self.cfg.max_side = 512
        self.assertNotEqual(sig, self.cfg.analysis_signature())

    def test_invalid_thresholds_and_nonfinite_values(self):
        for thresholds in [[20, 10, 60, 80], [20, 20, 60, 80], [0, 40, 60, 80], [10, 50, float("nan"), 80]]:
            with self.subTest(thresholds=thresholds):
                cfg = copy.deepcopy(self.cfg)
                cfg.thresholds = thresholds
                with self.assertRaises(ValueError):
                    cfg.validate()

    def test_ollama_never_allows_remote_endpoints(self):
        self.cfg.use_preset("ollama")
        for url in ["https://example.com", "http://192.168.1.2:11434", "http://localhost@evil.test", "file:///tmp/foo"]:
            self.cfg.ollama_url = url
            with self.assertRaises(ValueError):
                self.cfg.validate()

    def test_relative_paths_follow_config_location(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"folders": ["photos"], "state_dir": "state"}))
        loaded = load_config(path)
        self.assertEqual(loaded.folders, [str(self.photos)])

    def test_rejects_unknown_config(self):
        path = self.root / "config.json"
        path.write_text('{"typo": true}')
        with self.assertRaises(ValueError):
            load_config(path)


class ImageAndScoringTests(TempCase):
    def test_lower_is_better_and_finite_validation(self):
        self.assertEqual(normalized(10, 0, 100, True), 90)
        with self.assertRaises(ValueError):
            normalized(float("nan"), 0, 100)
        self.assertEqual(star_rating(45, [25, 45, 65, 85]), 3)

    def test_resize_preserves_aspect_and_orientation(self):
        path = self.photo()
        self.cfg.max_side = 128
        prepared = ImagePreparer(self.cfg).prepare(path)
        self.assertEqual(prepared.image.width, 128)
        self.assertEqual(orient(Image.new("RGB", (10, 20)), 6).size, (20, 10))

    def test_blur_reduces_heuristic_score(self):
        img = Image.open(self.photo()).convert("RGB")
        scorer = HeuristicScorer(self.cfg)
        self.assertGreater(scorer.evaluate(img).score, scorer.evaluate(img.filter(ImageFilter.GaussianBlur(8))).score)

    def test_companion_missing_is_error_not_raw_fallback(self):
        raw = self.photos / "RAW.dng"
        raw.write_bytes(b"placeholder")
        self.cfg.image_source = "companion"
        with self.assertRaisesRegex(ValueError, "JPEG"):
            ImagePreparer(self.cfg).prepare(raw)

    def test_companion_jpeg_selection(self):
        raw = self.photos / "sample.dng"
        raw.write_bytes(b"placeholder")
        self.photo("SAMPLE.JPG")
        self.cfg.image_source = "companion"
        self.assertEqual(ImagePreparer(self.cfg).prepare(raw).source, "companion")

    def test_ollama_schema_cpu_and_invalid_answer(self):
        self.cfg.use_preset("ollama")
        scorer = OllamaScorer(self.cfg)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return json.dumps({"message": {"content": '{"rating":4,"reason":"構図がよい"}'}}).encode()
        with patch.object(scorer.opener, "open", return_value=Response()) as request:
            result = scorer.evaluate(Image.new("RGB", (64, 64)))
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["options"]["num_gpu"], 0)
            self.assertTrue(payload["messages"][0]["images"])
            self.assertEqual(result.rating, 4)
        with patch.object(Response, "read", return_value=b'{"message":{"content":"{\\"rating\\":9,\\"reason\\":\\"bad\\"}"}}'):
            with patch.object(scorer.opener, "open", return_value=Response()):
                with self.assertRaises(ValueError):
                    scorer.evaluate(Image.new("RGB", (64, 64)))


class MetadataTests(TempCase):
    def test_sidecar_preserves_develop_settings_and_existing_rating(self):
        path = self.photos / "test.arw"
        path.write_bytes(b"raw-not-modified")
        xmp = sidecar_path(path)
        xmp.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                       'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">'
                       '<rdf:RDF><rdf:Description crs:Exposure2012="1.25"><xmp:Rating>4</xmp:Rating>'
                       '</rdf:Description></rdf:RDF></x:xmpmeta>')
        self.cfg.output = "sidecar"
        writer = MetadataWriter(self.cfg)
        self.assertEqual(writer.apply(path, 2), "protected")
        self.cfg.existing_policy = "overwrite"
        self.assertEqual(writer.apply(path, 2), "sidecar")
        self.assertEqual(read_sidecar_rating(xmp), 2)
        root = ET.parse(xmp).getroot()
        self.assertTrue(any(e.get("{http://ns.adobe.com/camera-raw-settings/1.0/}Exposure2012") == "1.25" for e in root.iter()))
        self.assertEqual(path.read_bytes(), b"raw-not-modified")
        self.assertEqual(len(list((self.root / "state" / "backups").glob("*.xmp"))), 1)

    def test_corrupt_sidecar_is_not_overwritten(self):
        path = self.photos / "a.arw"
        path.write_bytes(b"x")
        sidecar_path(path).write_text("<broken")
        self.cfg.output, self.cfg.existing_policy = "sidecar", "overwrite"
        with self.assertRaises(ET.ParseError):
            MetadataWriter(self.cfg).apply(path, 3)
        self.assertEqual(sidecar_path(path).read_text(), "<broken")

    def test_raw_and_jpeg_sidecars_do_not_collide(self):
        self.assertNotEqual(sidecar_path(Path("a.dng")), sidecar_path(Path("a.jpg")))
        self.assertEqual(sidecar_path(Path("a.arw")), Path("a.xmp"))

    def test_report_does_not_require_exiftool(self):
        path = self.photo("photo.jpg")
        before = path.read_bytes()
        self.cfg.exiftool_path = "does-not-exist"
        self.assertEqual(MetadataWriter(self.cfg).apply(path, 4), "report")
        self.assertEqual(before, path.read_bytes())

    @unittest.skipUnless(shutil.which("exiftool") or os.environ.get("PHOTO_SORT_EXIFTOOL"), "ExifTool is not installed")
    def test_real_embedded_jpeg_and_tiff_dng_keep_pixels_and_develop_metadata(self):
        tool = ExifTool(os.environ.get("PHOTO_SORT_EXIFTOOL", "exiftool"))
        for suffix in [".jpg", ".dng"]:
            with self.subTest(suffix=suffix):
                path = self.photos / ("実写テスト" + suffix)
                image = Image.new("RGB", (100, 80), (120, 50, 20))
                if suffix == ".dng":
                    # TIFF container tagged DNG: validates XMP/pixel preservation,
                    # not sensor-RAW decoding or a specific camera's DNG compatibility.
                    tags = TiffImagePlugin.ImageFileDirectory_v2()
                    tags[50706] = bytes([1, 4, 0, 0])
                    image.save(path, format="TIFF", tiffinfo=tags)
                else:
                    image.save(path)
                tool.run("-overwrite_original", "-XMP-crs:Exposure2012=1.25", str(path))
                with Image.open(path) as loaded:
                    pixels = loaded.tobytes()
                self.cfg.output = "embedded"
                self.cfg.exiftool_path = tool.executable
                writer = MetadataWriter(self.cfg)
                self.assertEqual(writer.apply(path, 4), "written")
                self.assertEqual(tool.metadata(path)["Rating"], 4)
                self.assertEqual(writer.apply(path, 1), "protected")
                settings = json.loads(tool.run("-j", "-XMP-crs:Exposure2012", str(path)))[0]
                self.assertEqual(float(settings["Exposure2012"]), 1.25)
                with Image.open(path) as loaded:
                    self.assertEqual(loaded.tobytes(), pixels)


class EngineTests(TempCase):
    def test_scan_cache_and_restart(self):
        self.photo()
        with Engine(self.cfg) as engine:
            self.assertEqual(engine.scan()["processed"], 1)
            self.assertEqual(engine.scan()["processed"], 0)
        with Engine(self.cfg) as engine:
            self.assertEqual(engine.scan()["processed"], 0)
            self.assertEqual(engine.scan(force=True)["processed"], 1)

    def test_output_change_reuses_evaluation(self):
        path = self.photo()
        with Engine(self.cfg) as engine:
            engine.scan()
        self.cfg.output = "sidecar"
        with Engine(self.cfg) as engine, patch("photo_sort_ai.engine.create_scorer", side_effect=AssertionError("must use cache")):
            self.assertEqual(engine.scan()["processed"], 1)
            self.assertEqual(engine.store.get(path)["status"], "sidecar")
        self.assertTrue(sidecar_path(path).is_file())

    def test_changed_input_and_stopped_worker_do_not_write(self):
        path = self.photo()
        self.cfg.output = "sidecar"
        with Engine(self.cfg) as engine:
            class ChangingScorer:
                def evaluate(_, image):
                    path.write_bytes(b"replacement")
                    return Evaluation(70, 4, "changed")
            engine.scorer = ChangingScorer()
            engine.scan()
            self.assertIsNone(engine.store.get(path))
            self.assertFalse(sidecar_path(path).exists())

    def test_stop_after_evaluation_skips_write(self):
        path = self.photo()
        self.cfg.output = "sidecar"
        with Engine(self.cfg) as engine:
            class StoppingScorer:
                def evaluate(_, image):
                    engine.stop.set()
                    return Evaluation(70, 4, "stop")
            engine.scorer = StoppingScorer()
            engine.scan()
            self.assertFalse(sidecar_path(path).exists())

    def test_benchmark_never_changes_photos_or_results(self):
        path = self.photo()
        before = path.read_bytes()
        self.cfg.output = "embedded"
        self.cfg.exiftool_path = "not-installed"
        with Engine(self.cfg) as engine:
            result = engine.benchmark(1)
            self.assertEqual(result["measured"], 1)
            self.assertEqual(engine.store.rows(), [])
        self.assertEqual(path.read_bytes(), before)

    def test_failures_are_retried_with_backoff_and_changed_files_reset(self):
        store = Store(self.root / "state")
        self.addCleanup(store.close)
        path = self.photos / "test.dng"
        store.fail(path, "old", "sig", "bad", 60)
        self.assertFalse(store.can_retry(path, "old", "sig", 3))
        self.assertTrue(store.can_retry(path, "new", "sig", 3))
        store.clear_failures()
        self.assertTrue(store.can_retry(path, "old", "sig", 3))

    def test_discovery_excludes_own_state_and_deduplicates_roots(self):
        self.photo()
        self.cfg.state_dir = str(self.photos / "state")
        Path(self.cfg.state_dir).mkdir()
        Image.new("RGB", (64, 64)).save(Path(self.cfg.state_dir) / "backup.jpg")
        self.cfg.folders.append(str(self.photos))
        with Engine(self.cfg) as engine:
            self.assertEqual(len(engine.discover()), 1)

    def test_os_lock_blocks_another_worker_and_releases(self):
        directory = self.root / "state"
        lock = RunLock(directory)
        try:
            with self.assertRaises(RuntimeError):
                RunLock(directory)
        finally:
            lock.close()
        RunLock(directory).close()

    def test_watch_new_file_does_not_repeat_after_own_write(self):
        path = self.photo()
        self.cfg.output = "sidecar"
        events = []
        stop = threading.Event()
        def callback(event):
            events.append(event)
            if event["kind"] == "result":
                # Let watcher run another pass, then stop it.
                threading.Timer(0.04, stop.set).start()
        with Engine(self.cfg, callback, stop) as engine:
            engine.watch()
        self.assertEqual(sum(e["kind"] == "result" for e in events), 1)
        self.assertTrue(sidecar_path(path).exists())

    def test_companion_change_invalidates_cache(self):
        raw = self.photos / "sample.dng"
        raw.write_bytes(b"raw")
        jpeg = self.photo("sample.jpg")
        self.cfg.image_source = "companion"
        self.cfg.extensions = [".dng"]
        with Engine(self.cfg) as engine:
            self.assertEqual(engine.scan()["processed"], 1)
            Image.new("RGB", (120, 80)).save(jpeg)
            self.assertEqual(engine.scan()["processed"], 1)


class ReportsAndCliTests(TempCase):
    def test_duplicates_recommend_best_without_mutation(self):
        store = Store(self.root / "state")
        self.addCleanup(store.close)
        for i, (h, score) in enumerate([("0000000000000000", 30), ("0000000000000001", 80), ("ffffffffffffffff", 90)]):
            path = self.photo(f"{i}.png")
            result = Evaluation(score, 3, "reason").to_dict()
            result["hash64"] = h
            store.save(path, "stamp", "sig", "output", result, "report")
        groups = duplicate_groups(store, 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["members"]), 2)
        self.assertTrue(groups[0]["recommended"].endswith("1.png"))
        self.assertEqual(len(store.rows()), 3)

    def test_csv_formula_text_is_escaped(self):
        store = Store(self.root / "state")
        self.addCleanup(store.close)
        store.save(self.photos / "a.png", "s", "sig", "out", Evaluation(60, 3, "=cmd").to_dict(), "report")
        dest = self.root / "results.csv"
        export_csv(store, dest)
        with dest.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[1][6], "'=cmd")

    def test_cli_init_and_light_report_scan(self):
        self.photo()
        cfgpath = self.root / "config.json"
        with patch("photo_sort_ai.cli.output"):
            self.assertEqual(main(["--config", str(cfgpath), "init", "--folder", str(self.photos)]), 0)
            cfg = load_config(cfgpath)
            cfg.state_dir = self.cfg.state_dir
            cfg.settle_seconds, cfg.low_priority, cfg.output = 0, False, "report"
            save_config(cfg, cfgpath)
            self.assertEqual(main(["--config", str(cfgpath), "scan"]), 0)
            self.assertEqual(main(["--config", str(cfgpath), "export", str(self.root / "out.csv")]), 0)
        self.assertTrue((self.root / "out.csv").exists())


if __name__ == "__main__":
    unittest.main()
