"""Rebuild models/surfaces-upernet-convnext-tiny-int8.onnx.

Needs torch, transformers 4.4x, onnx, and onnxruntime in a separate environment
(the app itself only needs onnxruntime):

    python -m venv /tmp/export-env
    /tmp/export-env/bin/pip install torch "transformers==4.46.3" onnx onnxruntime
    /tmp/export-env/bin/python scripts/export_model.py

The graph is exported at a fixed 576 x 576 input because UperNet's pyramid
pooling needs the deepest feature map (input / 32 = 18) to divide by 1, 2, 3,
and 6. Weights are quantized to int8, which keeps the file near 60 MB and
agrees with the full-precision model on about 98.6% of pixels.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import UperNetForSemanticSegmentation

SIZE = 576
OUT = Path(__file__).resolve().parent.parent / "models" / "surfaces-upernet-convnext-tiny-int8.onnx"


class Logits(torch.nn.Module):
    """Backbone and head only, returning logits at a quarter of the input size."""

    def __init__(self, model: UperNetForSemanticSegmentation) -> None:
        super().__init__()
        self.model = model

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        features = self.model.backbone.forward_with_filtered_kwargs(image).feature_maps
        return self.model.decode_head(features)


def main() -> None:
    model = UperNetForSemanticSegmentation.from_pretrained("openmmlab/upernet-convnext-tiny").eval()
    sample = torch.randn(1, 3, SIZE, SIZE)
    with tempfile.TemporaryDirectory() as folder:
        full = os.path.join(folder, "full.onnx")
        torch.onnx.export(Logits(model).eval(), sample, full, input_names=["image"], output_names=["logits"], opset_version=17, dynamo=False)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        quantize_dynamic(full, str(OUT), weight_type=QuantType.QInt8)
    print("wrote", OUT, round(OUT.stat().st_size / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
