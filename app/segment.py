"""Scene parsing for wall detection.

A UperNet / ConvNeXt-T network trained on ADE20K labels every pixel as one of
150 things (wall, ceiling, floor, window, sofa, ...). It knows a beige sofa in
front of a beige wall is a sofa, which color grouping alone cannot.

The network runs on a 576 x 576 letterboxed copy of the photo and answers at a
quarter of that. Its class probabilities are upsampled and then snapped to the
photo's own edges with a guided filter, so masks follow real corners and trim.

Weights: openmmlab/upernet-convnext-tiny (MIT), exported to ONNX and quantized
to int8. See docs/wall-detection.md.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "models" / "surfaces-upernet-convnext-tiny-int8.onnx"
INPUT_SIZE = 576
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

# ADE20K class indices.
ADE_WALL = 0
ADE_FLOOR = 3
ADE_CEILING = 5

OTHER, WALL, CEILING, FLOOR = 0, 1, 2, 3


@lru_cache(maxsize=1)
def _session():
    if os.environ.get("ROOMHUE_DETECTOR", "").strip().lower() == "classic" or not MODEL_PATH.is_file():
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Without the arena, memory is returned after each photo instead of held at the peak (~600 MB vs ~850 MB).
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    return ort.InferenceSession(str(MODEL_PATH), options, providers=["CPUExecutionProvider"])


def available() -> bool:
    return _session() is not None


def _letterbox(bgr: np.ndarray) -> tuple[np.ndarray, int, int]:
    height, width = bgr.shape[:2]
    scale = INPUT_SIZE / max(height, width)
    new_h, new_w = max(1, round(height * scale)), max(1, round(width * scale))
    small = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), np.float32)
    canvas[:new_h, :new_w] = (cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - MEAN) / STD
    return canvas.transpose(2, 0, 1)[None], new_h, new_w


def _guided(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """Edge-aware smoothing (He et al.): src takes on the edges of guide."""
    size = (2 * radius + 1, 2 * radius + 1)

    def mean(value: np.ndarray) -> np.ndarray:
        return cv2.boxFilter(value, -1, size, borderType=cv2.BORDER_REFLECT)

    mean_g = mean(guide)
    mean_s = mean(src)
    var_g = mean(guide * guide) - mean_g * mean_g
    cov = mean(guide * src) - mean_g * mean_s
    a = cov / (var_g + eps)
    b = mean_s - a * mean_g
    return mean(a) * guide + mean(b)


def surface_probabilities(bgr: np.ndarray) -> np.ndarray | None:
    """Per-pixel probability of [other, wall, ceiling, floor] at the photo's size, or None without a model."""
    session = _session()
    if session is None:
        return None
    height, width = bgr.shape[:2]
    tensor, new_h, new_w = _letterbox(bgr)
    logits = session.run(None, {"image": tensor})[0][0]
    out_h = max(1, int(np.ceil(new_h * logits.shape[1] / INPUT_SIZE)))
    out_w = max(1, int(np.ceil(new_w * logits.shape[2] / INPUT_SIZE)))
    logits = logits[:, :out_h, :out_w].astype(np.float32)
    logits -= logits.max(axis=0, keepdims=True)
    exp = np.exp(logits)
    probs = exp / exp.sum(axis=0, keepdims=True)
    wall, floor, ceiling = probs[ADE_WALL], probs[ADE_FLOOR], probs[ADE_CEILING]
    groups = np.stack([np.clip(1.0 - wall - floor - ceiling, 0, 1), wall, ceiling, floor])

    # Refine at a working size near 1000 px, then scale to the photo.
    work_scale = min(1.0, 1000.0 / max(height, width))
    work_w, work_h = max(1, round(width * work_scale)), max(1, round(height * work_scale))
    guide_bgr = bgr if work_scale == 1.0 else cv2.resize(bgr, (work_w, work_h), interpolation=cv2.INTER_AREA)
    guide = cv2.cvtColor(guide_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    radius = max(4, round(max(work_h, work_w) / 120))
    refined = []
    for channel in groups:
        up = cv2.resize(channel, (work_w, work_h), interpolation=cv2.INTER_LINEAR)
        refined.append(np.clip(_guided(guide, up, radius, 2e-3), 0, 1))
    refined = np.stack(refined)
    refined /= np.maximum(refined.sum(axis=0, keepdims=True), 1e-6)
    if (work_h, work_w) != (height, width):
        refined = np.stack([cv2.resize(channel, (width, height), interpolation=cv2.INTER_LINEAR) for channel in refined])
    return refined
