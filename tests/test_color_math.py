import numpy as np

from app.color_math import lab_to_rgb, paint_lab, recolor_rgb, rgb_to_lab


def test_red_lab_roundtrip():
    red = np.array([[[255, 0, 0]]], dtype=np.uint8)
    L, A, B = rgb_to_lab(red)
    assert 50 < float(L[0, 0]) < 56
    assert float(A[0, 0]) > 70
    assert float(B[0, 0]) > 50
    back = lab_to_rgb(L, A, B)
    assert np.max(np.abs(back.astype(int) - red.astype(int))) <= 1


def test_recolor_stays_inside_the_mask():
    rgb = np.full((40, 60, 3), (180, 170, 150), dtype=np.uint8)
    mask = np.zeros((40, 60), dtype=np.uint8)
    mask[8:32, 10:40] = 255
    out = recolor_rgb(rgb, mask, "#2F4A5C", coverage=1, sheen="matte", shade=0)
    assert np.array_equal(out[0:3], rgb[0:3])
    assert np.array_equal(out[:, 0:4], rgb[:, 0:4])
    def lab_of(pixel):
        L, A, B = rgb_to_lab(np.array([[pixel]], dtype=np.uint8))
        return float(L[0, 0]), float(A[0, 0]), float(B[0, 0])

    original = lab_of(rgb[18, 24])
    painted = lab_of(out[18, 24])
    target = paint_lab("#2F4A5C")
    for channel in range(3):
        assert abs(painted[channel] - target[channel]) < abs(original[channel] - target[channel])


def test_dark_paint_lowers_lightness():
    rgb = np.full((30, 30, 3), 210, dtype=np.uint8)
    mask = np.full((30, 30), 255, dtype=np.uint8)
    before_L, _, _ = rgb_to_lab(rgb)
    out = recolor_rgb(rgb, mask, "#243044", coverage=1, sheen="matte")
    after_L, _, _ = rgb_to_lab(out)
    assert float(after_L.mean()) < float(before_L.mean()) - 8


def test_abutting_masks_hide_the_original_at_the_seam():
    rgb = np.full((40, 80, 3), (200, 180, 160), dtype=np.uint8)
    left = np.zeros((40, 80), np.uint8)
    right = np.zeros((40, 80), np.uint8)
    left[:, :40] = 255
    right[:, 40:] = 255
    out = recolor_rgb(rgb, left, "#2244AA", coverage=1, sheen="matte")
    out = recolor_rgb(out, right, "#AA4422", coverage=1, sheen="matte")
    seam = out[:, 37:43].astype(int)
    original = rgb[:, 37:43].astype(int)
    diff = np.abs(seam - original).sum(axis=2)
    assert int(diff.min()) > 40


def test_paint_lab_accepts_shade():
    base, _, _ = paint_lab("#F3F0E8", 0)
    lighter, _, _ = paint_lab("#F3F0E8", 8)
    assert lighter > base
