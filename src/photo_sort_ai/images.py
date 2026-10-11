from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .config import Config
from .metadata import ExifTool

RASTER = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


@dataclass
class Prepared:
    image: Image.Image
    source: str


def orient(image: Image.Image, orientation: int) -> Image.Image:
    operations = {
        2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90,
    }
    return image.transpose(operations[orientation]) if orientation in operations else image


class ImagePreparer:
    def __init__(self, config: Config):
        self.config = config
        self.exiftool = ExifTool(config.exiftool_path)

    @staticmethod
    def raster(path: Path | io.BytesIO) -> Image.Image:
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).convert("RGB")

    def companion(self, path: Path) -> Image.Image:
        for candidate in sorted(path.parent.iterdir()):
            if candidate.stem.casefold() == path.stem.casefold() and candidate.suffix.lower() in {".jpg", ".jpeg"}:
                return self.raster(candidate)
        raise ValueError("同名のJPEGがありません。")

    def embedded_preview(self, path: Path) -> Image.Image:
        errors = []
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(thumb.data)) as loaded:
                        image = loaded.convert("RGB")
                else:
                    image = Image.fromarray(thumb.data).convert("RGB")
                # rawpy's thumb has not necessarily applied the camera orientation.
                if min(image.size) >= self.config.preview_min_side:
                    return self.source_orientation(image, path)
        except Exception as error:
            errors.append(str(error))
        for tag in ["JpgFromRaw", "PreviewImage", "ThumbnailImage"]:
            try:
                data = self.exiftool.preview(path, tag)
                if data:
                    # Apply source orientation once rather than thumbnail EXIF twice.
                    with Image.open(io.BytesIO(data)) as loaded:
                        image = loaded.convert("RGB")
                    if min(image.size) >= self.config.preview_min_side:
                        return self.source_orientation(image, path)
            except Exception as error:
                errors.append(str(error))
        raise RuntimeError("十分なサイズの埋め込みプレビューを取得できませんでした。" + " / ".join(errors[-2:]))

    def source_orientation(self, image: Image.Image, path: Path) -> Image.Image:
        try:
            orientation = self.exiftool.metadata(path).get("Orientation", 1)
            return orient(image, int(orientation))
        except RuntimeError:
            # Without ExifTool, use LibRaw's orientation flag for the common cases.
            try:
                import rawpy
                with rawpy.imread(str(path)) as raw:
                    flip = raw.sizes.flip
                return orient(image, {3: 3, 5: 8, 6: 6}.get(flip, 1))
            except Exception:
                return image

    @staticmethod
    def render_raw(path: Path) -> Image.Image:
        try:
            import rawpy
        except ImportError as error:
            raise RuntimeError("RAW現像にはrawpyが必要です。pip install '.[raw]' を実行してください。") from error
        with rawpy.imread(str(path)) as raw:
            array = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
        return Image.fromarray(array).convert("RGB")

    def prepare(self, path: Path) -> Prepared:
        if path.suffix.lower() in RASTER:
            image, source = self.raster(path), "raster"
        elif self.config.image_source == "companion":
            image, source = self.companion(path), "companion"
        elif self.config.image_source == "raw":
            image, source = self.render_raw(path), "raw"
        else:
            try:
                image, source = self.embedded_preview(path), "preview"
            except RuntimeError:
                if self.config.image_source == "preview":
                    raise
                image, source = self.render_raw(path), "raw"
        if min(image.size) < 8:
            raise ValueError("画像が小さすぎます。")
        image.thumbnail((self.config.max_side, self.config.max_side), Image.Resampling.LANCZOS)
        return Prepared(image, source)


def perceptual_hash(image: Image.Image) -> str:
    """64-bit difference hash for candidate grouping, not a deletion decision."""
    small = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    bits = (small[:, 1:] > small[:, :-1]).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"
