import numpy as np

import pytest

from pathlib import Path

import cv2

from app import segment
from app.detect import _classic_surfaces, _semantic_surfaces, detect_surfaces, magic_wand, prepare_image, surfaces_from_lines
from app.sample_room import make_sample_room

SAMPLE_PHOTO = Path(__file__).resolve().parents[1] / "data" / "sample" / "living-room.jpg"
needs_model = pytest.mark.skipif(not segment.available(), reason="scene-parsing model not available")


def _recall(mask: np.ndarray, truth: np.ndarray) -> float:
    truth = truth.astype(bool)
    if not np.any(truth):
        return 0.0
    return float(((mask > 0) & truth).sum() / truth.sum())


def _summary(surfaces) -> str:
    parts = []
    for surface in surfaces:
        parts.append(
            f"{surface.kind} area={surface.area_ratio:.3f} "
            f"center=({surface.centroid[0]:.2f},{surface.centroid[1]:.2f}) "
            f"conf={surface.confidence:.2f}"
        )
    return "; ".join(parts) or "no surfaces"


def test_classic_detector_finds_walls_ceiling_and_floor():
    room = make_sample_room()
    surfaces = _classic_surfaces(room.bgr)
    walls = [surface for surface in surfaces if surface.kind == "wall"]
    kinds = {surface.kind for surface in surfaces}
    assert "floor" in kinds, _summary(surfaces)
    assert "ceiling" in kinds, _summary(surfaces)
    assert len(walls) >= 3, _summary(surfaces)

    for name in ("left", "back", "right"):
        recalls = [_recall(surface.mask, room.labels[name]) for surface in walls]
        assert max(recalls) > 0.55, f"{name} not covered; { _summary(surfaces) }"

    wall_mask = np.zeros(room.bgr.shape[:2], dtype=bool)
    for surface in walls:
        wall_mask |= surface.mask > 0
    sofa_spill = _recall(wall_mask.astype(np.uint8) * 255, room.labels["sofa"])
    window_spill = _recall(wall_mask.astype(np.uint8) * 255, room.labels["window"])
    assert sofa_spill < 0.2, sofa_spill
    assert window_spill < 0.25, window_spill


def _point_inside(mask: np.ndarray) -> tuple[int, int]:
    ys, xs = np.where(mask)
    cx, cy = float(xs.mean()), float(ys.mean())
    order = np.argsort((xs - cx) ** 2 + (ys - cy) ** 2)
    for index in order[:: max(1, len(order) // 40)][:40]:
        y, x = int(ys[index]), int(xs[index])
        patch = mask[max(0, y - 10) : y + 11, max(0, x - 10) : x + 11]
        if float(patch.mean()) > 0.95:
            return x, y
    return int(xs[order[0]]), int(ys[order[0]])


def test_classic_detector_keeps_same_paint_ceiling_separate():
    room = make_sample_room()
    paint = np.array([176, 186, 198], dtype=np.float32)
    gains = {"ceiling": 1.12, "left": 0.8, "back": 1.0, "right": 0.78}
    bgr = room.bgr.copy()
    light = room.bgr.astype(np.float32).mean(axis=2, keepdims=True)
    for name, gain in gains.items():
        mask = room.labels[name]
        scale = light / (float(light[mask].mean()) + 1e-6)
        colored = paint * gain * np.clip(scale, 0.82, 1.18)
        bgr[mask] = np.clip(colored, 0, 255).astype(np.uint8)[mask]

    surfaces = _classic_surfaces(bgr)
    walls = [surface for surface in surfaces if surface.kind == "wall"]
    ceilings = [surface for surface in surfaces if surface.kind == "ceiling"]
    assert ceilings, _summary(surfaces)
    assert len(walls) >= 3, _summary(surfaces)
    ceiling_mask = np.zeros(bgr.shape[:2], dtype=bool)
    for surface in ceilings:
        ceiling_mask |= surface.mask > 0
    assert _recall(ceiling_mask.astype(np.uint8) * 255, room.labels["ceiling"]) > 0.45
    wall_on_ceiling = 0.0
    for surface in walls:
        wall_on_ceiling = max(wall_on_ceiling, _recall(surface.mask, room.labels["ceiling"]))
    assert wall_on_ceiling < 0.35, _summary(surfaces)


def test_wand_selects_back_wall_without_the_floor():
    room = make_sample_room()
    x, y = _point_inside(room.labels["back"])
    mask = magic_wand(room.bgr, x, y, tolerance=16)
    assert _recall(mask, room.labels["back"]) > 0.7
    assert _recall(mask, room.labels["floor"]) < 0.08
    assert _recall(mask, room.labels["left"]) < 0.12
    assert _recall(mask, room.labels["right"]) < 0.12


def test_wand_on_left_wall_stays_there():
    room = make_sample_room()
    x, y = _point_inside(room.labels["left"])
    mask = magic_wand(room.bgr, x, y, tolerance=16)
    assert _recall(mask, room.labels["left"]) > 0.65
    assert _recall(mask, room.labels["back"]) < 0.15


def test_lines_split_when_they_stop_just_short_of_the_border():
    image = np.full((200, 300, 3), 180, np.uint8)
    surfaces = surfaces_from_lines(image, [(150, 15, 150, 185)])
    assert len(surfaces) >= 2
    centers = sorted(surface.centroid[0] for surface in surfaces)
    assert centers[0] < 0.45
    assert centers[-1] > 0.55


def test_lines_join_a_gap_and_reject_a_line_that_does_not_cut():
    image = np.full((200, 320, 3), 180, np.uint8)
    surfaces = surfaces_from_lines(
        image,
        [
            (0, 100, 140, 100),
            (155, 0, 155, 199),
        ],
    )
    assert len(surfaces) >= 3
    with pytest.raises(ValueError):
        surfaces_from_lines(image, [(160, 40, 160, 80)])


def _perfect_probabilities(room) -> np.ndarray:
    """What a flawless scene parser would say about the drawn room."""
    height, width = room.bgr.shape[:2]
    probs = np.zeros((4, height, width), np.float32)
    wall = room.labels["left"] | room.labels["back"] | room.labels["right"]
    for name in ("window", "frame", "sofa", "art"):
        if name in room.labels:
            wall &= ~room.labels[name]
    probs[segment.WALL][wall] = 1
    probs[segment.CEILING][room.labels["ceiling"]] = 1
    probs[segment.FLOOR][room.labels["floor"] & ~room.labels.get("sofa", np.zeros_like(wall))] = 1
    probs[segment.OTHER][probs.sum(axis=0) == 0] = 1
    return probs


def test_walls_split_at_corners_with_no_gap_between_them():
    room = make_sample_room()
    probs = _perfect_probabilities(room)
    surfaces = _semantic_surfaces(room.bgr, probs)
    walls = [surface for surface in surfaces if surface.kind == "wall"]
    assert len(walls) == 3, _summary(surfaces)
    for name in ("left", "back", "right"):
        assert max(_recall(surface.mask, room.labels[name]) for surface in walls) > 0.9, name

    claimed = np.zeros(room.bgr.shape[:2], np.int32)
    for surface in surfaces:
        claimed += (surface.mask > 0).astype(np.int32)
    assert claimed.max() == 1, "two surfaces claim the same pixel"
    wall_class = probs.argmax(axis=0) == segment.WALL
    unclaimed = wall_class & (claimed == 0)
    assert int(unclaimed.sum()) == 0, f"{int(unclaimed.sum())} wall pixels belong to no wall"


@needs_model
def test_real_photo_finds_walls_and_leaves_out_furniture_and_windows():
    photo = prepare_image(cv2.imread(str(SAMPLE_PHOTO)))
    height, width = photo.shape[:2]
    surfaces = detect_surfaces(photo)
    kinds = [surface.kind for surface in surfaces]
    assert "ceiling" in kinds and "floor" in kinds, _summary(surfaces)
    assert kinds.count("wall") >= 3, _summary(surfaces)

    claimed = np.zeros((height, width), np.int32)
    walls = np.zeros((height, width), bool)
    for surface in surfaces:
        claimed += (surface.mask > 0).astype(np.int32)
        if surface.kind == "wall":
            walls |= surface.mask > 0
    assert claimed.max() == 1, "two surfaces claim the same pixel"

    def wall_share(x0: float, y0: float, x1: float, y1: float) -> float:
        return float(walls[int(y0 * height) : int(y1 * height), int(x0 * width) : int(x1 * width)].mean())

    assert wall_share(0.12, 0.74, 0.28, 0.88) < 0.05, "left sofa painted as wall"
    assert wall_share(0.62, 0.72, 0.80, 0.86) < 0.05, "right sofa painted as wall"
    assert wall_share(0.47, 0.22, 0.60, 0.42) < 0.05, "window painted as wall"
    assert wall_share(0.30, 0.15, 0.40, 0.45) > 0.5, "back wall missed"


def test_falls_back_to_classic_detector_without_a_model(monkeypatch):
    monkeypatch.setattr(segment, "surface_probabilities", lambda bgr: None)
    room = make_sample_room()
    kinds = {surface.kind for surface in detect_surfaces(room.bgr)}
    assert {"wall", "ceiling", "floor"} <= kinds
