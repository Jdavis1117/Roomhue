"""Find paintable surfaces in a room photo.

Walls, ceiling, and floor are large, fairly even regions. Corners are treated
as hard edges so two walls painted the same color can still be selected
separately. Furniture, windows, and pictures are left out when they look
small, bright, or interior.

This is a classical detector with a single entry point, `detect_surfaces`,
so a learned segmenter can replace the body later without changing the app.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MAX_EDGE = 1400
CLUSTER_EDGE = 520
CLUSTERS = 10
SPATIAL_WEIGHT = 0.58
EDGE_PERCENTILE = 90.5
MERGE_DELTA_E = 16.0
STRONG_EDGE = 90.0
MIN_AREA_RATIO = 0.028
TINY_AREA_RATIO = 0.012


@dataclass
class Surface:
    kind: str
    confidence: float
    mask: np.ndarray
    centroid: tuple[float, float]
    area_ratio: float


def prepare_image(bgr: np.ndarray, max_edge: int = MAX_EDGE) -> np.ndarray:
    height, width = bgr.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_edge:
        return bgr
    scale = max_edge / long_edge
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)


def _delta_e(mean_a: np.ndarray, mean_b: np.ndarray) -> float:
    # OpenCV Lab stores L in 0..255 and a/b centered at 128.
    # Lightness is down-weighted so one painted wall split by a sun patch can merge,
    # while a real change of color still stays apart.
    dl = (float(mean_a[0]) - float(mean_b[0])) * (100.0 / 255.0)
    da = float(mean_a[1]) - float(mean_b[1])
    db = float(mean_a[2]) - float(mean_b[2])
    return (dl * dl * 0.15 + da * da + db * db) ** 0.5


def _gradient(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _strong_edges(gray: np.ndarray, cutoff: float | None = None) -> np.ndarray:
    mag = _gradient(gray)
    if cutoff is None:
        cutoff = max(36.0, float(np.percentile(mag, EDGE_PERCENTILE)))
    edges = np.where(mag >= cutoff, 255, 0).astype(np.uint8)
    # Bridge small gaps in baseboards and corners so a selection cannot slip through.
    horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 1))
    vertical = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 11))
    joined = cv2.bitwise_or(
        cv2.morphologyEx(edges, cv2.MORPH_CLOSE, horizontal),
        cv2.morphologyEx(edges, cv2.MORPH_CLOSE, vertical),
    )
    # Thicken so a flood cannot slip through a diagonal corner.
    joined = cv2.dilate(joined, np.ones((3, 3), np.uint8), iterations=1)
    return np.where(joined > 0, 1, 0).astype(np.uint8)


def _segment(bgr: np.ndarray) -> np.ndarray:
    """Label map. 0 means a boundary pixel that belongs to no region yet."""
    height, width = bgr.shape[:2]
    smooth = cv2.bilateralFilter(bgr, 9, 55, 55)
    lab = cv2.cvtColor(smooth, cv2.COLOR_BGR2LAB)
    long_edge = max(height, width)
    if long_edge > CLUSTER_EDGE:
        scale = CLUSTER_EDGE / long_edge
        small = cv2.resize(lab, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        small_gray = cv2.resize(
            cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY),
            (small.shape[1], small.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = lab
        small_gray = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)

    sh, sw = small.shape[:2]
    k = min(CLUSTERS, max(2, (sh * sw) // 800))
    ys, xs = np.mgrid[0:sh, 0:sw]
    features = np.stack(
        [
            small[:, :, 0].astype(np.float32) / 255.0,
            (small[:, :, 1].astype(np.float32) - 128.0) / 128.0,
            (small[:, :, 2].astype(np.float32) - 128.0) / 128.0,
            xs.astype(np.float32) / max(1, sw) * SPATIAL_WEIGHT,
            ys.astype(np.float32) / max(1, sh) * SPATIAL_WEIGHT,
        ],
        axis=-1,
    ).reshape(-1, 5)
    cv2.setRNGSeed(42)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 24, 0.7)
    _compact, labels, _centers = cv2.kmeans(features, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    clustered = labels.reshape(sh, sw).astype(np.int32)

    edges = _strong_edges(small_gray)
    pieces = np.zeros((sh, sw), dtype=np.int32)
    next_id = 1
    for cluster in range(k):
        mask = ((clustered == cluster) & (edges == 0)).astype(np.uint8)
        count, components = cv2.connectedComponents(mask)
        for component in range(1, count):
            pieces[components == component] = next_id
            next_id += 1

    if (sh, sw) != (height, width):
        pieces = cv2.resize(pieces.astype(np.float32), (width, height), interpolation=cv2.INTER_NEAREST).astype(np.int32)
    return pieces


def _means_and_areas(lab: np.ndarray, regions: np.ndarray) -> tuple[dict[int, np.ndarray], dict[int, int]]:
    means: dict[int, np.ndarray] = {}
    areas: dict[int, int] = {}
    for region_id in np.unique(regions):
        if region_id <= 0:
            continue
        mask = regions == region_id
        areas[int(region_id)] = int(mask.sum())
        means[int(region_id)] = lab[mask].mean(axis=0)
    return means, areas


def _boundary_stats(regions: np.ndarray, mag: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    max_id = int(regions.max()) + 1
    sum_mag = np.zeros((max_id, max_id), dtype=np.float64)
    counts = np.zeros((max_id, max_id), dtype=np.int32)

    def add(left: np.ndarray, right: np.ndarray, edge_mag: np.ndarray) -> None:
        valid = (left != right) & (left > 0) & (right > 0)
        if not np.any(valid):
            return
        a = left[valid].astype(np.int32)
        b = right[valid].astype(np.int32)
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        np.add.at(sum_mag, (lo, hi), edge_mag[valid])
        np.add.at(counts, (lo, hi), 1)

    add(regions[:, :-1], regions[:, 1:], mag[:, :-1])
    add(regions[:-1, :], regions[1:, :], mag[:-1, :])
    return sum_mag, counts


def _merge(regions: np.ndarray, smooth_bgr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(smooth_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    mag = _gradient(cv2.cvtColor(smooth_bgr, cv2.COLOR_BGR2GRAY))
    means, areas = _means_and_areas(lab, regions)
    if len(means) <= 1:
        return regions
    sum_mag, counts = _boundary_stats(regions, mag)
    pairs: list[tuple[float, float, int, int]] = []
    ys, xs = np.nonzero(np.triu(counts, 1))
    for a, b in zip(ys.tolist(), xs.tolist()):
        if a not in means or b not in means:
            continue
        strength = float(sum_mag[a, b] / max(1, counts[a, b]))
        pairs.append((_delta_e(means[a], means[b]), strength, a, b))
    pairs.sort(key=lambda item: (item[0], item[1]))

    parent = {region_id: region_id for region_id in means}

    def find(region_id: int) -> int:
        while parent[region_id] != region_id:
            parent[region_id] = parent[parent[region_id]]
            region_id = parent[region_id]
        return region_id

    def union(first: int, second: int) -> None:
        ra, rb = find(first), find(second)
        if ra == rb:
            return
        if areas[ra] < areas[rb]:
            ra, rb = rb, ra
        total = areas[ra] + areas[rb]
        means[ra] = (means[ra] * areas[ra] + means[rb] * areas[rb]) / total
        areas[ra] = total
        parent[rb] = ra

    image_area = regions.shape[0] * regions.shape[1]
    for delta, strength, a, b in pairs:
        if strength >= STRONG_EDGE:
            continue
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        smaller = min(areas[ra], areas[rb]) / image_area
        if delta <= MERGE_DELTA_E or smaller < TINY_AREA_RATIO:
            union(a, b)

    merged = np.zeros_like(regions)
    for region_id in means:
        merged[regions == region_id] = find(region_id)
    return merged


def _structure_barrier(gray: np.ndarray) -> np.ndarray:
    """Long straight edges: ceiling lines, corners, and baseboards.

    These stay even when every surface is the same paint color, which is when
    color grouping would otherwise fuse the ceiling into the walls.
    """
    height, width = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 28, 84)
    min_length = int(round(min(height, width) * 0.16))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=48, minLineLength=min_length, maxLineGap=18)
    barrier = np.zeros((height, width), dtype=np.uint8)
    if lines is None:
        return barrier
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        cv2.line(barrier, (int(x1), int(y1)), (int(x2), int(y2)), 255, thickness=4)
    return barrier


def _split_on_structure(regions: np.ndarray, gray: np.ndarray) -> np.ndarray:
    barrier = _structure_barrier(gray)
    if int(barrier.max()) == 0:
        return regions
    cut = regions.copy()
    cut[barrier > 0] = 0
    height, width = regions.shape
    minimum = int(height * width * 0.015)
    output = np.zeros_like(regions)
    next_id = 1
    for region_id in [int(value) for value in np.unique(cut) if value > 0]:
        count, components, stats, _centroids = cv2.connectedComponentsWithStats((cut == region_id).astype(np.uint8))
        for component in range(1, count):
            if int(stats[component, cv2.CC_STAT_AREA]) < minimum:
                continue
            output[components == component] = next_id
            next_id += 1
    return output


def _peel_ceiling(mask: np.ndarray, gray: np.ndarray) -> list[np.ndarray]:
    """Cut the top band off a wall when a ceiling line runs across it."""
    height, width = mask.shape
    ys, xs = np.where(mask > 0)
    if len(ys) == 0 or int(ys.min()) > int(height * 0.04):
        return [mask]
    if (int(ys.max()) - int(ys.min())) / float(height) < 0.48:
        return [mask]
    gradient_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    chosen = None
    for y in range(int(height * 0.06), int(height * 0.38)):
        columns = np.where(mask[y] > 0)[0]
        if len(columns) < width * 0.22:
            continue
        span = (int(columns.max()) - int(columns.min()) + 1) / float(width)
        strength = float(np.percentile(gradient_y[y, columns], 55))
        if span >= 0.42 and strength >= 12:
            chosen = y
            break
    if chosen is None:
        return [mask]
    above = mask.copy()
    above[chosen:, :] = 0
    below = mask.copy()
    below[:chosen, :] = 0
    minimum = height * width * 0.02
    if int((above > 0).sum()) < minimum or int((below > 0).sum()) < minimum:
        return [mask]
    return [above, below]


def _strip_texture(mask: np.ndarray, gray: np.ndarray, limit: float = 15.0) -> np.ndarray:
    """Drop fabric, plants, and other busy pixels that got pulled into a wall."""
    values = gray.astype(np.float32)
    mean = cv2.blur(values, (15, 15))
    square = cv2.blur(values * values, (15, 15))
    deviation = np.sqrt(np.clip(square - mean * mean, 0, None))
    cleaned = mask.copy()
    cleaned[deviation > limit] = 0
    count, components, stats, _centroids = cv2.connectedComponentsWithStats((cleaned > 0).astype(np.uint8))
    if count <= 1:
        return cleaned
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(components == largest, 255, 0).astype(np.uint8)


def _fill_small_holes(mask: np.ndarray, gray: np.ndarray) -> np.ndarray:
    inverse = np.where(mask > 0, 0, 255).astype(np.uint8)
    count, components = cv2.connectedComponents(inverse)
    height, width = mask.shape
    filled = mask.copy()
    limit = max(400, int(height * width * 0.004))
    for component in range(1, count):
        hole = components == component
        area = int(hole.sum())
        if area > limit:
            continue
        ys, xs = np.where(hole)
        touches_border = ys.min() == 0 or xs.min() == 0 or ys.max() == height - 1 or xs.max() == width - 1
        if touches_border:
            continue
        if float(gray[hole].mean()) > 232:
            continue
        filled[hole] = 255
    return filled


def _classify(mask: np.ndarray, lab: np.ndarray, gray: np.ndarray) -> tuple[str, float] | None:
    height, width = mask.shape
    area = int((mask > 0).sum())
    area_ratio = area / float(height * width)
    if area_ratio < MIN_AREA_RATIO:
        return None

    ys, xs = np.where(mask > 0)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    cy = float(ys.mean() / height)
    touches_top = y0 <= int(height * 0.03)
    touches_bottom = y1 >= int(height * 0.97)
    touches_left = x0 <= int(width * 0.03)
    touches_right = x1 >= int(width * 0.97)
    eroded = cv2.erode(mask, np.ones((5, 5), np.uint8), iterations=1)
    texture_source = eroded if int(eroded.sum()) > 40 else mask
    texture = float(gray[texture_source > 0].std())
    mean_L = float(lab[:, :, 0][mask > 0].mean())
    chroma = float(
        np.hypot(lab[:, :, 1][mask > 0].astype(np.float32) - 128.0, lab[:, :, 2][mask > 0].astype(np.float32) - 128.0).mean()
    )

    # Windows and lamps: bright, smooth, and compact. A pale ceiling is wide and
    # touches the top, so it is not treated as a window.
    bbox_w = (x1 - x0 + 1) / float(width)
    if mean_L > 230 and area_ratio < 0.16 and texture < 20 and bbox_w < 0.5 and not touches_top:
        return None

    bottom_fraction = float((ys > height * 0.8).mean())
    upper_fraction = float((ys < height * 0.45).mean())
    vertical_extent = (y1 - y0 + 1) / float(height)
    interior = not (touches_left or touches_right or touches_top or touches_bottom)

    # A dark door, a television, or an opening. A whole wall painted dark is larger.
    if mean_L < 52 and area_ratio < 0.09:
        return None

    if touches_bottom and cy > 0.60 and bottom_fraction > 0.22 and area_ratio > 0.05:
        kind = "floor"
    elif (
        touches_top
        and cy < 0.30
        and vertical_extent < 0.42
        and float((ys < height * 0.34).mean()) > 0.5
        and area_ratio > 0.035
    ):
        kind = "ceiling"
    elif area_ratio > 0.035 and texture < 48 and 20 < mean_L < 242:
        if interior and cy > 0.55 and upper_fraction < 0.12 and area_ratio < 0.24:
            return None
        if chroma > 48 and area_ratio < 0.1 and interior:
            return None
        kind = "wall"
    else:
        return None

    confidence = 0.42 + min(0.28, area_ratio)
    if texture < 16:
        confidence += 0.14
    elif texture < 28:
        confidence += 0.06
    if kind == "wall" and (touches_left or touches_right or upper_fraction > 0.2):
        confidence += 0.08
    if kind == "floor" and touches_bottom:
        confidence += 0.1
    if kind == "ceiling" and touches_top:
        confidence += 0.1
    return kind, float(min(0.96, confidence))


def detect_surfaces(bgr: np.ndarray) -> list[Surface]:
    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValueError("Expected a BGR image")
    height, width = bgr.shape[:2]
    if height < 32 or width < 32:
        return []

    smooth = cv2.bilateralFilter(bgr, 9, 55, 55)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    regions = _split_on_structure(_merge(_segment(bgr), smooth), gray)
    lab = cv2.cvtColor(smooth, cv2.COLOR_BGR2LAB)

    surfaces: list[Surface] = []
    for region_id in [int(value) for value in np.unique(regions) if value > 0]:
        raw = np.where(regions == region_id, 255, 0).astype(np.uint8)
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        raw = _fill_small_holes(raw, gray)
        count, components, stats, _centroids = cv2.connectedComponentsWithStats(raw)
        if count <= 1:
            continue
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = np.where(components == largest, 255, 0).astype(np.uint8)
        for piece in _peel_ceiling(mask, gray):
            _append_surface(surfaces, piece, lab, gray, height, width)
    return _sort_surfaces(surfaces)


def _append_surface(
    surfaces: list[Surface],
    mask: np.ndarray,
    lab: np.ndarray,
    gray: np.ndarray,
    height: int,
    width: int,
) -> None:
    classified = _classify(mask, lab, gray)
    if classified is None:
        return
    kind, confidence = classified
    if kind == "wall":
        mask = _strip_texture(mask, gray)
        if int((mask > 0).sum()) < height * width * MIN_AREA_RATIO:
            return
    ys, xs = np.where(mask > 0)
    if len(ys) == 0:
        return
    surfaces.append(
        Surface(
            kind=kind,
            confidence=confidence,
            mask=mask,
            centroid=(float(xs.mean() / width), float(ys.mean() / height)),
            area_ratio=float((mask > 0).mean()),
        )
    )


def _sort_surfaces(surfaces: list[Surface]) -> list[Surface]:
    kind_order = {"ceiling": 0, "wall": 1, "floor": 2}
    surfaces.sort(key=lambda surface: (kind_order.get(surface.kind, 9), surface.centroid[0]))
    return surfaces


def magic_wand(bgr: np.ndarray, x: int, y: int, tolerance: float = 16) -> np.ndarray:
    """Select the surface under a click, stopping at hard edges.

    Flood fill walks a lighting gradient (so one wall still selects) but
    strong edges are barriers, which keeps the fill from crossing a corner.
    """
    height, width = bgr.shape[:2]
    x = int(np.clip(x, 0, width - 1))
    y = int(np.clip(y, 0, height - 1))
    smooth = cv2.bilateralFilter(bgr, 9, 50, 50)
    lab = cv2.cvtColor(smooth, cv2.COLOR_BGR2LAB)
    # Higher reach ignores softer edges, so a shadow line can be crossed on purpose.
    # Wall corners in a smoothed photo are softer than a window frame, so the
    # default stays low enough to catch them. Raising reach lifts this cutoff.
    edge_cutoff = float(np.clip(6 + tolerance * 1.05, 12, 80))
    barrier = _strong_edges(cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY), edge_cutoff)
    mask = np.zeros((height + 2, width + 2), dtype=np.uint8)
    mask[1:-1, 1:-1] = barrier
    mask[y + 1, x + 1] = 0

    chroma = int(np.clip(tolerance, 3, 48))
    light = int(np.clip(tolerance * 1.7, 6, 80))
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    cv2.floodFill(lab, mask, (x, y), 0, (light, chroma, chroma), (light, chroma, chroma), flags)
    selected = np.where(mask[1:-1, 1:-1] == 255, 255, 0).astype(np.uint8)
    if int(selected.sum()) < 80:
        mask[:, :] = 0
        mask[1:-1, 1:-1] = barrier
        mask[max(0, y - 2) + 1 : y + 4, max(0, x - 2) + 1 : x + 4] = 0
        wider = int(np.clip(chroma * 1.8, 8, 64))
        cv2.floodFill(
            lab,
            mask,
            (x, y),
            0,
            (min(90, light * 2), wider, wider),
            (min(90, light * 2), wider, wider),
            flags,
        )
        selected = np.where(mask[1:-1, 1:-1] == 255, 255, 0).astype(np.uint8)

    selected = cv2.dilate(selected, np.ones((3, 3), np.uint8), iterations=1)
    count, components, stats, _centroids = cv2.connectedComponentsWithStats((selected > 0).astype(np.uint8))
    if count <= 1:
        return selected
    seed_label = int(components[y, x])
    if seed_label == 0:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        seed_label = largest
    return np.where(components == seed_label, 255, 0).astype(np.uint8)


def surfaces_from_lines(bgr: np.ndarray, segments: list[tuple[float, float, float, float]]) -> list[Surface]:
    """Split the photo along lines the user drew for each wall edge.

    A line that stops a short way from the border or from another line is
    extended to it. Each enclosed region becomes its own surface, and a
    region the user closed off is kept as a wall even if the automatic
    classifier would have skipped it.
    """
    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValueError("Expected a BGR image")
    height, width = bgr.shape[:2]
    if height < 32 or width < 32:
        raise ValueError("That photo is too small to split.")
    short = float(min(height, width))
    snap = 28.0 if short >= 280 else max(8.0, short * 0.08)

    cleaned: list[list[float]] = []
    for x1, y1, x2, y2 in segments:
        if not all(np.isfinite(value) for value in (x1, y1, x2, y2)):
            continue
        ax, ay = _snap_border(float(x1), float(y1), width, height, snap)
        bx, by = _snap_border(float(x2), float(y2), width, height, snap)
        if (ax - bx) ** 2 + (ay - by) ** 2 < 4:
            continue
        cleaned.append([ax, ay, bx, by])
    if not cleaned:
        raise ValueError("Draw a line along each wall edge first.")

    _join_segments(cleaned, width, height, snap)
    barrier = np.zeros((height, width), np.uint8)
    for x1, y1, x2, y2 in cleaned:
        cv2.line(
            barrier,
            (int(round(x1)), int(round(y1))),
            (int(round(x2)), int(round(y2))),
            255,
            5,
            cv2.LINE_8,
        )
    barrier = cv2.dilate(barrier, np.ones((3, 3), np.uint8), iterations=1)

    open_space = np.where(barrier > 0, 0, 255).astype(np.uint8)
    count, components, stats, _centroids = cv2.connectedComponentsWithStats(open_space)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(cv2.bilateralFilter(bgr, 9, 55, 55), cv2.COLOR_BGR2LAB)
    min_area = height * width * 0.012
    surfaces: list[Surface] = []
    order = sorted(range(1, count), key=lambda index: int(stats[index, cv2.CC_STAT_AREA]), reverse=True)
    for index in order:
        if int(stats[index, cv2.CC_STAT_AREA]) < min_area:
            continue
        mask = np.where(components == index, 255, 0).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        mask = _fill_small_holes(mask, gray)
        _append_user_surface(surfaces, mask, lab, gray, height, width)
        if len(surfaces) >= 24:
            break
    if len(surfaces) < 2:
        raise ValueError(
            "Those lines didn't split the photo. Draw each edge all the way to the border or to another line."
        )
    return _sort_surfaces(surfaces)


def _snap_border(x: float, y: float, width: int, height: int, snap: float) -> tuple[float, float]:
    x = min(max(x, 0.0), width - 1.0)
    y = min(max(y, 0.0), height - 1.0)
    if x <= snap:
        x = 0.0
    elif x >= width - 1 - snap:
        x = width - 1.0
    if y <= snap:
        y = 0.0
    elif y >= height - 1 - snap:
        y = height - 1.0
    return x, y


def _project(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float]:
    dx = x2 - x1
    dy = y2 - y1
    length2 = dx * dx + dy * dy
    if length2 < 1:
        return x1, y1, ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = min(1.0, max(0.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return qx, qy, ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _join_segments(segments: list[list[float]], width: int, height: int, join: float) -> None:
    """Pull endpoints that nearly meet onto each other or onto a nearby line."""

    def find(parents: list[int], index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for _ in range(3):
        changed = False
        points = []
        for segment in segments:
            points.append((segment[0], segment[1]))
            points.append((segment[2], segment[3]))
        parents = list(range(len(points)))
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                if (points[i][0] - points[j][0]) ** 2 + (points[i][1] - points[j][1]) ** 2 <= join * join:
                    parents[find(parents, i)] = find(parents, j)
        groups: dict[int, list[tuple[float, float]]] = {}
        for index, point in enumerate(points):
            groups.setdefault(find(parents, index), []).append(point)
        centroids: dict[int, tuple[float, float]] = {}
        for key, group in groups.items():
            centroids[key] = _snap_border(
                sum(point[0] for point in group) / len(group),
                sum(point[1] for point in group) / len(group),
                width,
                height,
                join,
            )
        for index, segment in enumerate(segments):
            for end in (0, 1):
                cx, cy = centroids[find(parents, index * 2 + end)]
                if abs(segment[end * 2] - cx) > 0.5 or abs(segment[end * 2 + 1] - cy) > 0.5:
                    segment[end * 2] = cx
                    segment[end * 2 + 1] = cy
                    changed = True
        for index, segment in enumerate(segments):
            for end in (0, 1):
                x = segment[end * 2]
                y = segment[end * 2 + 1]
                if x <= 1 or y <= 1 or x >= width - 2 or y >= height - 2:
                    continue
                best: tuple[float, float] | None = None
                best_distance = join
                for other_index, other in enumerate(segments):
                    if other_index == index:
                        continue
                    qx, qy, distance = _project(x, y, other[0], other[1], other[2], other[3])
                    if 0.5 < distance < best_distance:
                        best = (qx, qy)
                        best_distance = distance
                if best is not None:
                    segment[end * 2], segment[end * 2 + 1] = best
                    changed = True
        if not changed:
            break


def _append_user_surface(
    surfaces: list[Surface],
    mask: np.ndarray,
    lab: np.ndarray,
    gray: np.ndarray,
    height: int,
    width: int,
) -> None:
    if int((mask > 0).sum()) < height * width * 0.012:
        return
    classified = _classify(mask, lab, gray)
    if classified is None:
        kind, confidence = "wall", 0.62
    else:
        kind, confidence = classified
    if kind == "wall":
        stripped = _strip_texture(mask, gray)
        if int((stripped > 0).sum()) >= height * width * 0.012:
            mask = stripped
    ys, xs = np.where(mask > 0)
    if len(ys) == 0:
        return
    surfaces.append(
        Surface(
            kind=kind,
            confidence=confidence,
            mask=mask,
            centroid=(float(xs.mean() / width), float(ys.mean() / height)),
            area_ratio=float((mask > 0).mean()),
        )
    )
