"""sRGB ↔ CIE Lab conversions and lighting-preserving recolor.

The same constants are mirrored in static/app.js so the on-screen preview
matches a server-side render.
"""

from __future__ import annotations

import numpy as np

# How far the wall's median lightness moves toward the paint chip.
LIGHTNESS_PULL = 0.72
# How much of the wall's own a/b variation (texture, slight color noise) remains.
TEXTURE_KEEP = 0.18
SHEEN_AMOUNT = {
    "matte": 0.0,
    "eggshell": 0.035,
    "satin": 0.08,
    "semi-gloss": 0.15,
}

_D65 = (0.95047, 1.0, 1.08883)


def _srgb_channel_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb_channel(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055)


def _lab_f(t: np.ndarray) -> np.ndarray:
    delta = 6 / 29
    return np.where(t > delta**3, np.cbrt(t), t / (3 * delta**2) + 4 / 29)


def _lab_f_inv(t: np.ndarray) -> np.ndarray:
    delta = 6 / 29
    return np.where(t > delta, t**3, 3 * delta**2 * (t - 4 / 29))


def rgb_to_lab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """rgb uint8 or float HxWx3 in 0..255 → L, a, b float arrays."""
    x = rgb.astype(np.float32) / 255.0
    r = _srgb_channel_to_linear(x[..., 0])
    g = _srgb_channel_to_linear(x[..., 1])
    b = _srgb_channel_to_linear(x[..., 2])
    X = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / _D65[0]
    Y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) / _D65[1]
    Z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / _D65[2]
    fx, fy, fz = _lab_f(X), _lab_f(Y), _lab_f(Z)
    L = 116 * fy - 16
    A = 500 * (fx - fy)
    B = 200 * (fy - fz)
    return L, A, B


def lab_to_rgb(L: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    fy = (L + 16) / 116
    fx = fy + A / 500
    fz = fy - B / 200
    X = _lab_f_inv(fx) * _D65[0]
    Y = _lab_f_inv(fy) * _D65[1]
    Z = _lab_f_inv(fz) * _D65[2]
    r = X * 3.2404542 + Y * -1.5371385 + Z * -0.4985314
    g = X * -0.9692660 + Y * 1.8760108 + Z * 0.0415560
    b = X * 0.0556434 + Y * -0.2040259 + Z * 1.0572252
    rgb = np.stack(
        [
            _linear_to_srgb_channel(r),
            _linear_to_srgb_channel(g),
            _linear_to_srgb_channel(b),
        ],
        axis=-1,
    )
    return np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)


def parse_hex(color: str) -> tuple[int, int, int]:
    value = color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got {color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def paint_lab(color: str, shade: float = 0.0) -> tuple[float, float, float]:
    rgb = np.array([[parse_hex(color)]], dtype=np.uint8)
    L, A, B = rgb_to_lab(rgb)
    return float(L[0, 0]) + shade, float(A[0, 0]), float(B[0, 0])


def feather_alpha(mask: np.ndarray, coverage: float) -> np.ndarray:
    """mask uint8 0..255 → float alpha 0..1, softened at the edges."""
    import cv2

    blurred = cv2.GaussianBlur(mask, (0, 0), 1.5)
    alpha = blurred.astype(np.float32) / 255.0
    alpha[alpha < 0.04] = 0
    return np.clip(alpha * float(coverage), 0.0, 1.0)


def recolor_rgb(
    rgb: np.ndarray,
    mask: np.ndarray,
    color: str,
    coverage: float = 0.92,
    sheen: str = "eggshell",
    shade: float = 0.0,
) -> np.ndarray:
    """Replace masked pixels with paint, keeping shadows and texture.

    Lightness is shifted toward the chip but the wall's light/dark pattern
    stays. Chroma moves to the paint color, with a little of the original
    variation left so flat color doesn't erase plaster and light falloff.
    """
    if sheen not in SHEEN_AMOUNT:
        raise ValueError(f"Unknown sheen {sheen!r}")
    coverage = float(np.clip(coverage, 0.0, 1.0))
    shade = float(np.clip(shade, -20.0, 20.0))
    if mask.shape[:2] != rgb.shape[:2]:
        raise ValueError("Mask and image sizes differ")

    alpha = feather_alpha(mask, coverage)
    region = mask >= 128
    if not np.any(region) or coverage <= 0:
        return rgb.copy()

    L, A, B = rgb_to_lab(rgb)
    med_L = float(np.median(L[region]))
    med_A = float(np.median(A[region]))
    med_B = float(np.median(B[region]))
    paint_L, paint_A, paint_B = paint_lab(color, shade)

    L2 = L + (paint_L - med_L) * LIGHTNESS_PULL
    A2 = paint_A + (A - med_A) * TEXTURE_KEEP
    B2 = paint_B + (B - med_B) * TEXTURE_KEEP
    L2 = np.clip(L2, 0, 100)

    painted = lab_to_rgb(L2, A2, B2).astype(np.float32)
    shine = SHEEN_AMOUNT[sheen]
    if shine:
        highlight = np.clip((L - med_L) / 28.0, 0, 2.0) ** 2
        spec = (highlight * shine).astype(np.float32)
        painted += (255.0 - painted) * spec[..., None]

    base = rgb.astype(np.float32)
    out = base * (1.0 - alpha[..., None]) + painted * alpha[..., None]
    return np.clip(np.round(out), 0, 255).astype(np.uint8)
