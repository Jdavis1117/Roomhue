import numpy as np

import pytest

from app.detect import detect_surfaces, magic_wand, surfaces_from_lines
from app.sample_room import make_sample_room


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


def test_sample_room_finds_walls_ceiling_and_floor():
    room = make_sample_room()
    surfaces = detect_surfaces(room.bgr)
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


def test_same_paint_keeps_ceiling_separate_from_walls():
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

    surfaces = detect_surfaces(bgr)
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
