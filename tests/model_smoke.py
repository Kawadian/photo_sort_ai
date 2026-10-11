"""Optional: downloads genuine weights and runs the selected models on CPU."""
import json
import math
import os
import time

import numpy as np
from PIL import Image

from photo_sort_ai.config import Config
from photo_sort_ai.scoring import create_scorer

cfg = Config(cpu_threads=2)
cfg.use_preset(os.environ.get("PRESET", "balanced"))
start = time.perf_counter()
scorer = create_scorer(cfg)
setup = time.perf_counter() - start
image = Image.fromarray(np.random.default_rng(42).integers(0, 256, (512, 768, 3), dtype=np.uint8))
start = time.perf_counter()
result = scorer.evaluate(image)
assert math.isfinite(result.score) and 1 <= result.rating <= 5
print(json.dumps({"setup_seconds": setup, "inference_seconds": time.perf_counter() - start, "result": result.to_dict()}, indent=2))
