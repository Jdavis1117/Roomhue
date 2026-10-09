import numpy as np

from app.color_math import blend_layers, lab_to_rgb, paint_lab, paint_layer, recolor_rgb, rgb_to_lab


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


def test_paint_lab_accepts_shade():
    base, _, _ = paint_lab("#F3F0E8", 0)
    lighter, _, _ = paint_lab("#F3F0E8", 8)
    assert lighter > base


def test_two_walls_meeting_leave_no_seam_of_the_original_photo():
    rgb = np.zeros((40, 80, 3), np.uint8)
    rgb[:, :40] = (200, 190, 180)
    rgb[:, 40:] = (150, 140, 130)
    left = np.zeros((40, 80), np.uint8)
    left[:, :40] = 255
    right = 255 - left
    layers = [paint_layer(rgb, mask, "#2F4A5C", coverage=1, sheen="matte") for mask in (left, right)]
    out = blend_layers(rgb, layers).astype(np.float32)
    painted = recolor_rgb(rgb, left, "#2F4A5C", coverage=1, sheen="matte").astype(np.float32)
    seam = out[:, 38:42]
    inside = painted[:, 10:30].mean(axis=(0, 1))
    # Both sides are dark blue paint; the seam must not lighten toward the pale original walls.
    assert float(np.abs(seam.mean(axis=(0, 1)) - inside).max()) < 25
    assert float(seam.mean()) < 120


def test_one_layer_matches_recolor():
    rgb = np.full((20, 20, 3), 180, np.uint8)
    mask = np.zeros((20, 20), np.uint8)
    mask[5:15, 5:15] = 255
    layer = paint_layer(rgb, mask, "#884422", coverage=0.7, sheen="satin")
    assert np.array_equal(blend_layers(rgb, [layer]), recolor_rgb(rgb, mask, "#884422", coverage=0.7, sheen="satin"))
