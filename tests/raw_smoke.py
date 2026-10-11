"""Optional: pip install '.[raw]' tifffile; also requires ExifTool on PATH."""
import os
import tempfile
from pathlib import Path

import numpy as np
import rawpy
import tifffile

from photo_sort_ai.config import Config
from photo_sort_ai.images import ImagePreparer
from photo_sort_ai.metadata import ExifTool, MetadataWriter

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "センサーRAW.dng"
    pixels = np.random.default_rng(42).integers(64, 3900, (256, 256), dtype=np.uint16)
    tags = [
        (50706, "B", 4, (1, 4, 0, 0), False),  # DNGVersion
        (50707, "B", 4, (1, 1, 0, 0), False),  # DNGBackwardVersion
        (50708, "s", 0, "PhotoSort Test", False),
        (33421, "H", 2, (2, 2), False),  # CFARepeatPatternDim
        (33422, "B", 4, (0, 1, 1, 2), False),  # RGGB mosaic
        (50714, "H", 1, 64, False), (50717, "I", 1, 4095, False),
        (50721, "d", 9, (1, 0, 0, 0, 1, 0, 0, 0, 1), False),
        (50778, "H", 1, 21, False),
    ]
    tifffile.imwrite(path, pixels, photometric=32803, extratags=tags, metadata=None)
    cfg = Config(state_dir=str(Path(directory) / "state"), existing_policy="overwrite",
                 exiftool_path=os.environ.get("PHOTO_SORT_EXIFTOOL", "exiftool"))
    tool = ExifTool(cfg.exiftool_path)
    tool.run("-overwrite_original", "-XMP-crs:Exposure2012=1.25", str(path))
    for source in ["raw", "auto"]:
        cfg.image_source = source
        prepared = ImagePreparer(cfg).prepare(path)
        assert prepared.source == "raw" and prepared.image.size == (128, 128)
    with rawpy.imread(str(path)) as raw:
        assert np.array_equal(pixels, raw.raw_image)
    assert MetadataWriter(cfg).apply(path, 4) == "written"
    assert tool.metadata(path)["Rating"] == 4
    with rawpy.imread(str(path)) as raw:
        assert np.array_equal(pixels, raw.raw_image)
    print("Synthetic CFA DNG: RAW decode, fallback, rating write and raw pixels passed")
