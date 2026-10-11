"""Optional integration test: pip install '.[onnx]' onnx, then run this file."""
import math
import tempfile
from pathlib import Path

import onnx
from onnx import TensorProto, helper
from PIL import Image

from photo_sort_ai.config import Config
from photo_sort_ai.scoring import create_scorer

with tempfile.TemporaryDirectory() as directory:
    for layout in ["NCHW", "NHWC"]:
        shape = [1, 3, 32, 32] if layout == "NCHW" else [1, 32, 32, 3]
        graph = helper.make_graph(
            [helper.make_node("ReduceMean", ["image"], ["score"], keepdims=0)],
            "mean-test",
            [helper.make_tensor_value_info("image", TensorProto.FLOAT, shape)],
            [helper.make_tensor_value_info("score", TensorProto.FLOAT, [])],
        )
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
        path = Path(directory) / f"mean-{layout}.onnx"
        onnx.save(model, path)
        cfg = Config(onnx_path=str(path), onnx_size=32, onnx_layout=layout,
                     onnx_mean=[0, 0, 0], onnx_std=[1, 1, 1])
        cfg.use_preset("onnx")
        scorer = create_scorer(cfg)
        result = scorer.evaluate(Image.new("RGB", (120, 80), (128, 128, 128)))
        assert math.isclose(result.score, 128 / 255 * 100, abs_tol=0.001), result
        assert result.rating == 3
        assert result.details["providers"] == ["CPUExecutionProvider"]
    print("ONNX CPU inference: NCHW and NHWC passed")
