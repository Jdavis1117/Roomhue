"""Decode untrusted image bytes safely.

A small, highly compressed file can describe an enormous image (a
"decompression bomb"): a 0.45 MB PNG can need over 1 GB of memory to decode.
The header is read first, and anything larger than MAX_PIXELS is refused
before a single pixel is decoded.
"""

from __future__ import annotations

import io
import warnings

import cv2
import numpy as np
from fastapi import HTTPException
from PIL import Image

MAX_PIXELS = 40_000_000  # 40 megapixels; phones send far less after the app shrinks them.
MAX_SIDE = 12_000

# Pillow refuses images above twice this on its own; we also check every size explicitly.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def image_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the file header, without decoding the pixels."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as opened:
                return opened.size
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=413, detail="That image is too large. Use one under 40 megapixels.") from exc
    except Exception as exc:  # Pillow raises several error types for unreadable files.
        raise HTTPException(status_code=400, detail="That file isn't an image I can read.") from exc


def check_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise HTTPException(status_code=413, detail="That image is too large. Use one under 40 megapixels.")


def decode(data: bytes, flags: int = cv2.IMREAD_COLOR, expect: tuple[int, int] | None = None) -> np.ndarray:
    """Decode with OpenCV after checking the header. `expect` is (height, width) when the size is known."""
    width, height = image_size(data)
    check_size(width, height)
    if expect is not None and (height, width) != tuple(expect):
        raise HTTPException(status_code=400, detail="A wall mask doesn't match this photo.")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags)
    if image is None:
        raise HTTPException(status_code=400, detail="That file isn't an image I can read.")
    return image
