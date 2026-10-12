import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image, TiffImagePlugin

from photo_sort_ai.captions import evaluation_caption
from photo_sort_ai.config import Config
from photo_sort_ai.engine import Engine
from photo_sort_ai.metadata import (
    DC,
    RDF,
    XML,
    MetadataWriter,
    read_sidecar_captions,
    read_sidecar_rating,
    sidecar_path,
)
from photo_sort_ai.scoring import Evaluation


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.cfg = Config(folders=[str(self.photos)], state_dir=str(self.root / "state"),
                          low_priority=False, settle_seconds=0, output="sidecar")

    def tearDown(self):
        self.temporary.cleanup()

    def test_common_format_includes_models_and_available_details(self):
        for backend, details, model in [
            ("heuristic", {"backend": "heuristic", "sharpness": .12}, "AIモデルなし"),
            ("pyiqa", {"backend": "pyiqa", "musiq": {"raw": 70, "normalized": 70, "weight": 1}}, "musiq"),
            ("onnx", {"backend": "onnx", "raw": .8, "providers": ["CPUExecutionProvider"]}, "quality.onnx"),
            ("openai", {"backend": "openai", "model": "gpt-test", "api_mode": "responses", "endpoint": "https://private", "usage": {"tokens": 10}}, "gpt-test"),
            ("ollama", {"backend": "ollama", "model": "vision-test"}, "vision-test")]:
            with self.subTest(backend=backend):
                self.cfg.use_preset("quality")
                self.cfg.backend, self.cfg.onnx_path = backend, "/models/quality.onnx"
                text = evaluation_caption(self.cfg, Evaluation(75, 4, "構図がよい\n明暗も良好", details).to_dict())
                self.assertIn(model, text)
                self.assertIn("★4", text)
                self.assertIn("構図がよい\n明暗も良好", text)
                self.assertNotIn("https://private", text)

    def test_decisions_records_probabilities_without_fake_prose(self):
        result = Evaluation(75, 4, "文章ではない仮の理由", {"backend": "openai", "model": "luna-decisions",
                            "api_mode": "openrouter_decisions", "score_is_star_conversion": True,
                            "decision": {"choice": "4", "confidence": .8, "probabilities": {"4": .9, "5": .1}}}).to_dict()
        text = evaluation_caption(self.cfg, result)
        self.assertIn("luna-decisions", text)
        self.assertIn('"confidence": 0.8', text)
        self.assertIn('"probabilities"', text)
        self.assertIn("星からの換算値", text)
        self.assertNotIn("仮の理由", text)

    def test_sidecar_caption_independent_of_star_protection_and_translation_preservation(self):
        photo = self.photos / "写真.arw"
        photo.write_bytes(b"raw-pixels")
        writer = MetadataWriter(self.cfg)
        self.assertEqual(writer.apply(photo, 4, "AIコメント\n二行目<&>"), "sidecar")
        xmp = sidecar_path(photo)
        self.assertIn("AIコメント\n二行目<&>", read_sidecar_captions(xmp))
        root = ET.parse(xmp).getroot()
        alt = root.find(f".//{{{DC}}}description/{{{RDF}}}Alt")
        ET.SubElement(alt, f"{{{RDF}}}li", {f"{{{XML}}}lang": "fr"}).text = "Texte original"
        description = root.find(f".//{{{RDF}}}Description")
        description.set("{http://ns.adobe.com/camera-raw-settings/1.0/}Exposure2012", "1.25")
        xmp.write_bytes(ET.tostring(root))
        before = xmp.read_bytes()
        self.assertEqual(writer.apply(photo, 2, "新しいコメント"), "protected")
        self.assertEqual(xmp.read_bytes(), before)
        self.cfg.caption_policy = "overwrite"
        self.assertEqual(writer.apply(photo, 2, "新しいコメント"), "caption")
        self.assertEqual(read_sidecar_rating(xmp), 4)
        self.assertIn("Texte original", read_sidecar_captions(xmp))
        self.assertIn("新しいコメント", read_sidecar_captions(xmp))
        self.assertEqual(photo.read_bytes(), b"raw-pixels")
        self.assertTrue(any(e.get("{http://ns.adobe.com/camera-raw-settings/1.0/}Exposure2012") == "1.25" for e in ET.parse(xmp).getroot().iter()))
        self.assertTrue(list((self.root / "state" / "backups").glob("*.xmp")))

    def test_preserve_caption_while_updating_star_and_disabled_or_report_leave_caption_untouched(self):
        photo = self.photos / "a.arw"
        photo.write_bytes(b"raw")
        writer = MetadataWriter(self.cfg)
        writer.apply(photo, 3, "手動の説明")
        self.cfg.existing_policy = "overwrite"
        writer.apply(photo, 5, "AIコメント")
        self.assertEqual(read_sidecar_rating(sidecar_path(photo)), 5)
        self.assertEqual(read_sidecar_captions(sidecar_path(photo)), ["手動の説明"])
        self.cfg.caption_policy = "off"
        writer.apply(photo, 4, "保存しないコメント")
        self.assertEqual(read_sidecar_captions(sidecar_path(photo)), ["手動の説明"])
        before = sidecar_path(photo).read_bytes()
        self.cfg.output = "report"
        self.assertEqual(writer.apply(photo, 1, "変更しない"), "report")
        self.assertEqual(sidecar_path(photo).read_bytes(), before)

    def test_caption_added_to_previously_rated_photo_without_new_api_call(self):
        photo = self.photos / "a.png"
        Image.new("RGB", (128, 128), (150, 170, 200)).save(photo)
        self.cfg.caption_policy = "off"
        with Engine(self.cfg) as engine:
            self.assertEqual(engine.scan()["processed"], 1)
        self.cfg.caption_policy = "preserve"
        with patch("photo_sort_ai.engine.create_scorer", side_effect=AssertionError("must reuse cache")), Engine(self.cfg) as engine:
            self.assertEqual(engine.scan()["processed"], 1)
            self.assertEqual(engine.scan()["processed"], 0)
        self.assertIn("軽量統計", read_sidecar_captions(sidecar_path(photo))[0])

    @unittest.skipUnless(shutil.which("exiftool") or os.environ.get("PHOTO_SORT_EXIFTOOL"), "ExifTool required")
    def test_real_embedded_dng_jpeg_multiline_utf8_caption_and_star_protection(self):
        self.cfg.output = "embedded"
        self.cfg.exiftool_path = os.environ.get("PHOTO_SORT_EXIFTOOL", "exiftool")
        for suffix in [".jpg", ".dng"]:
            with self.subTest(suffix=suffix):
                path = self.photos / ("日本語" + suffix)
                image = Image.new("RGB", (128, 128), (40, 130, 200))
                if suffix == ".dng":
                    tags = TiffImagePlugin.ImageFileDirectory_v2()
                    tags[50706] = b"\x01\x04\x00\x00"
                    image.save(path, format="TIFF", tiffinfo=tags)
                else:
                    image.save(path)
                pixels = Image.open(path).tobytes()
                writer = MetadataWriter(self.cfg)
                caption = "モデル: 日本語AI\n構図がよい 😊\n--flag<&>"
                self.assertEqual(writer.apply(path, 4, caption), "written")
                self.assertEqual(writer.exiftool.metadata(path)["Description"], caption)
                writer.exiftool.run("-overwrite_original", "-XMP-dc:Description-fr=Texte original", str(path))
                self.assertEqual(Image.open(path).tobytes(), pixels)
                self.cfg.caption_policy = "overwrite"
                self.assertEqual(writer.apply(path, 1, "更新された説明\n星は保持"), "caption")
                self.assertEqual(writer.exiftool.metadata(path)["Rating"], 4)
                self.assertEqual(writer.exiftool.metadata(path)["Description"], "更新された説明\n星は保持")
                self.assertEqual(writer.exiftool.metadata(path)["Description-fr"], "Texte original")
                self.cfg.caption_policy = "preserve"

