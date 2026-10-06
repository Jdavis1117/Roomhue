"""A drawn room with known surfaces, used as the in-app sample and as a test fixture."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# BGR colors. Walls are intentionally different so a person (and the detector)
# can tell them apart, but close enough that a careless color threshold merges them.
COLORS_BGR = {
    "ceiling": (232, 228, 222),
    "floor": (78, 112, 148),
    "left": (196, 178, 158),
    "right": (214, 198, 176),
    "back": (176, 160, 142),
    "window": (255, 248, 242),
    "frame": (210, 205, 198),
    "sofa": (62, 78, 58),
    "art": (78, 92, 176),
    "trim": (96, 108, 122),
}


@dataclass
class SampleRoom:
    bgr: np.ndarray
    labels: dict[str, np.ndarray]


def _poly(points: list[tuple[int, int]]) -> np.ndarray:
    return np.array(points, dtype=np.int32)


def make_sample_room(width: int = 1200, height: int = 800) -> SampleRoom:
    w, h = width, height
    ceiling_y = int(h * 0.24)
    floor_y = int(h * 0.70)
    left_x = int(w * 0.22)
    right_x = int(w * 0.78)
    inset = int(w * 0.025)

    ceiling = _poly([(0, 0), (w - 1, 0), (right_x, ceiling_y), (left_x, ceiling_y)])
    floor = _poly([(0, h - 1), (w - 1, h - 1), (right_x + inset, floor_y), (left_x - inset, floor_y)])
    left = _poly([(0, 0), (left_x, ceiling_y), (left_x - inset, floor_y), (0, h - 1)])
    right = _poly([(w - 1, 0), (right_x, ceiling_y), (right_x + inset, floor_y), (w - 1, h - 1)])
    back = _poly(
        [
            (left_x, ceiling_y),
            (right_x, ceiling_y),
            (right_x + inset, floor_y),
            (left_x - inset, floor_y),
        ]
    )

    order = [
        ("ceiling", ceiling),
        ("floor", floor),
        ("left", left),
        ("right", right),
        ("back", back),
    ]
    owner = np.zeros((h, w), dtype=np.int32)
    for index, (_name, polygon) in enumerate(order, start=1):
        cv2.fillPoly(owner, [polygon], index)

    bgr = np.zeros((h, w, 3), dtype=np.uint8)
    for index, (name, _polygon) in enumerate(order, start=1):
        bgr[owner == index] = COLORS_BGR[name]

    yy = np.linspace(1.06, 0.74, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.9, 1.02, w, dtype=np.float32)[None, :]
    light = yy * xx
    # A soft pool of light on the back wall, as if from the window.
    ys, xs = np.mgrid[0:h, 0:w]
    blob = np.exp(-(((ys - h * 0.40) ** 2) / (2 * (h * 0.34) ** 2) + ((xs - w * 0.5) ** 2) / (2 * (w * 0.28) ** 2)))
    light = np.clip(light + blob.astype(np.float32) * 0.18, 0.55, 1.18)
    shaded = np.clip(bgr.astype(np.float32) * light[..., None], 0, 255)

    rng = np.random.default_rng(7)
    noise = rng.normal(0, 2.2, shaded.shape).astype(np.float32)
    shaded = np.clip(shaded + noise, 0, 255).astype(np.uint8)

    window = (int(w * 0.40), int(h * 0.30), int(w * 0.56), int(h * 0.50))
    art = (int(w * 0.60), int(h * 0.32), int(w * 0.70), int(h * 0.48))
    sofa = (int(w * 0.34), int(h * 0.60), int(w * 0.66), int(h * 0.86))

    def fill_rect(rect, color):
        x0, y0, x1, y1 = rect
        cv2.rectangle(shaded, (x0, y0), (x1, y1), color, thickness=-1)

    fill_rect(window, COLORS_BGR["window"])
    cv2.rectangle(shaded, (window[0], window[1]), (window[2], window[3]), COLORS_BGR["frame"], thickness=8)
    fill_rect(art, COLORS_BGR["art"])
    cv2.ellipse(
        shaded,
        ((sofa[0] + sofa[2]) // 2, (sofa[1] + sofa[3]) // 2),
        ((sofa[2] - sofa[0]) // 2, (sofa[3] - sofa[1]) // 2),
        0,
        0,
        360,
        COLORS_BGR["sofa"],
        thickness=-1,
    )
    # Baseboard along the back wall / floor joint.
    cv2.line(
        shaded,
        (left_x - inset, floor_y),
        (right_x + inset, floor_y),
        COLORS_BGR["trim"],
        thickness=5,
    )

    labels: dict[str, np.ndarray] = {}
    names = ["ceiling", "floor", "left", "right", "back"]
    for index, name in enumerate(names, start=1):
        labels[name] = owner == index

    window_mask = np.zeros((h, w), dtype=bool)
    window_mask[window[1] : window[3], window[0] : window[2]] = True
    art_mask = np.zeros((h, w), dtype=bool)
    art_mask[art[1] : art[3], art[0] : art[2]] = True
    sofa_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(
        sofa_mask,
        ((sofa[0] + sofa[2]) // 2, (sofa[1] + sofa[3]) // 2),
        ((sofa[2] - sofa[0]) // 2, (sofa[3] - sofa[1]) // 2),
        0,
        0,
        360,
        255,
        thickness=-1,
    )
    labels["window"] = window_mask
    labels["art"] = art_mask
    labels["sofa"] = sofa_mask.astype(bool)
    # Paintable wall ground truth excludes things sitting on the wall or floor.
    for name in ("left", "right", "back", "floor"):
        labels[name] = labels[name] & ~labels["window"] & ~labels["art"] & ~labels["sofa"]

    return SampleRoom(bgr=shaded, labels=labels)
