"""Searchable paint colors from published brand color books."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.color_math import rgb_to_lab

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "data" / "brands"


def _code_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=1)
def _load() -> dict:
    payload = json.loads((CATALOG_DIR / "index.json").read_text(encoding="utf-8"))
    colors = []
    for brand in payload["brands"]:
        rows = json.loads((CATALOG_DIR / f"{brand['id']}.json").read_text(encoding="utf-8"))
        for color in rows:
            color["code_key"] = _code_key(color["code"])
            color["name_key"] = color["name"].lower()
        colors.extend(rows)
    payload["colors"] = colors
    return payload


def search(brand: str = "", query: str = "", limit: int = 60, offset: int = 0) -> dict:
    payload = _load()
    limit = max(0, min(int(limit), 20000))
    offset = max(0, int(offset))
    brand = brand.strip().lower()
    text = query.strip().lower()
    code = _code_key(text)
    brands = payload["brands"]
    if brand and brand not in {item["id"] for item in brands}:
        brand = ""

    if not brand and len(text) < 2:
        return {
            "brands": brands,
            "colors": [],
            "total": sum(item["count"] for item in brands),
            "offset": 0,
            "limit": limit,
            "note": payload["note"],
        }

    matches: list[tuple[int, bool, str, dict]] = []
    for color in payload["colors"]:
        if brand and color["brand"] != brand:
            continue
        rank = _rank(color, text, code)
        if rank is None:
            continue
        matches.append((rank, bool(color.get("archived")), color["name_key"], color))
    if text:
        matches.sort(key=lambda item: (item[0], item[1], item[2], item[3]["code"]))
        page = matches[offset : offset + limit]
    else:
        matches.sort(key=_color_sort)
        page = _round_robin(matches, limit, offset)
    return {
        "brands": brands,
        "colors": [_public(item[3], brands) for item in page],
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "note": payload["note"],
    }


GROUPS = (
    "Whites",
    "Grays",
    "Blacks",
    "Browns",
    "Reds",
    "Oranges",
    "Yellows",
    "Greens",
    "Blues",
    "Purples",
    "Pinks",
)
_GROUP_INDEX = {name: index for index, name in enumerate(GROUPS)}
_NEUTRALS = {"Whites", "Grays", "Blacks"}


def _hsl(hex_color: str) -> tuple[float, float, float]:
    text = hex_color.strip().lstrip("#")
    if len(text) != 6:
        return 0.0, 0.0, 0.5
    red = int(text[0:2], 16) / 255
    green = int(text[2:4], 16) / 255
    blue = int(text[4:6], 16) / 255
    high = max(red, green, blue)
    low = min(red, green, blue)
    light = (high + low) / 2
    delta = high - low
    if delta == 0:
        return 0.0, 0.0, light
    sat = delta / (2 - high - low) if light > 0.5 else delta / (high + low)
    if high == red:
        hue = ((green - blue) / delta) % 6
    elif high == green:
        hue = (blue - red) / delta + 2
    else:
        hue = (red - green) / delta + 4
    return hue * 60, sat, light


def color_group(hex_color: str) -> str:
    hue, sat, light = _hsl(hex_color)
    if light >= 0.88 and sat <= 0.42:
        return "Whites"
    if light <= 0.22 and sat <= 0.14:
        return "Blacks"
    if sat <= 0.08 or (sat <= 0.16 and light >= 0.62):
        return "Grays"
    if 12 <= hue <= 48 and 0.18 <= light <= 0.72 and sat <= 0.72:
        if sat >= 0.55 and light >= 0.5:
            return "Oranges"
        return "Browns"
    if hue < 12 or hue >= 345:
        return "Pinks" if light >= 0.62 else "Reds"
    if hue < 42:
        return "Oranges"
    if hue < 70:
        return "Yellows"
    if hue < 170:
        return "Greens"
    if hue < 255:
        return "Blues"
    if hue < 325:
        return "Purples"
    return "Pinks"


def _color_sort(item: tuple) -> tuple:
    _rank_value, archived, name, color = item
    group = color_group(color["hex"])
    hue, _sat, light = _hsl(color["hex"])
    tone = -light if group in _NEUTRALS else hue
    return (_GROUP_INDEX[group], archived, tone, -light, name, color["code"])


def _round_robin(matches: list[tuple], limit: int, offset: int) -> list[tuple]:
    buckets: list[list[tuple]] = []
    current = None
    for item in matches:
        group = color_group(item[3]["hex"])
        if not buckets or group != current:
            buckets.append([])
            current = group
        buckets[-1].append(item)
    flat: list[tuple] = []
    index = 0
    while True:
        grew = False
        for bucket in buckets:
            if index < len(bucket):
                flat.append(bucket[index])
                grew = True
        if not grew:
            break
        index += 1
    return flat[offset : offset + limit]


def _rank(color: dict, text: str, code: str) -> int | None:
    if not text:
        return 1
    if code and color["code_key"] == code:
        return 0
    if code and len(code) >= 3 and color["code_key"].endswith(code):
        return 1
    name = color["name_key"]
    if name == text:
        return 2
    if name.startswith(text):
        return 3
    if text in name:
        return 4
    if code and len(code) >= 3 and code in color["code_key"]:
        return 5
    return None


def _public(color: dict, brands: list[dict]) -> dict:
    names = {item["id"]: item["name"] for item in brands}
    row = {
        "brand": names.get(color["brand"], color["brand"]),
        "brandId": color["brand"],
        "code": color["code"],
        "name": color["name"],
        "hex": color["hex"],
        "group": color_group(color["hex"]),
    }
    if color.get("archived"):
        row["archived"] = True
    return row


# ---------------------------------------------------------------- coordinating colors

@lru_cache(maxsize=16)
def _brand_lab(brand: str) -> tuple[list[dict], np.ndarray]:
    """Current (non-archived) colors of one brand and their Lab values, for nearest-color lookups."""
    rows = [color for color in _load()["colors"] if color["brand"] == brand and not color.get("archived")]
    if not rows:
        return [], np.zeros((0, 3), np.float32)
    rgb = np.array([[int(color["hex"][i : i + 2], 16) for i in (1, 3, 5)] for color in rows], np.uint8)[None]
    L, A, B = rgb_to_lab(rgb)
    return rows, np.stack([L[0], A[0], B[0]], axis=1).astype(np.float32)


def _hex_lab(hex_color: str) -> np.ndarray:
    rgb = np.array([[[int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]]], np.uint8)
    L, A, B = rgb_to_lab(rgb)
    return np.array([float(L[0, 0]), float(A[0, 0]), float(B[0, 0])], np.float32)


def _lch_target(lightness: float, chroma: float, hue: float) -> np.ndarray:
    return np.array([lightness, chroma * np.cos(hue), chroma * np.sin(hue)], np.float32)


def coordinates(brand: str, code: str) -> dict | None:
    """Colors that go with one paint: the brand's own pairings when it publishes them, plus a
    matching trim white, a lighter and a darker shade, and two accents from the same brand."""
    payload = _load()
    brand = brand.strip().lower()
    key = _code_key(code)
    base = next((color for color in payload["colors"] if color["brand"] == brand and color["code_key"] == key), None)
    if base is None:
        return None
    brands = payload["brands"]
    by_code = {color["code"]: color for color in payload["colors"] if color["brand"] == brand}
    official = [by_code[pair] for pair in base.get("pairs", []) if pair in by_code]

    rows, labs = _brand_lab(brand)
    lightness, a, b = (float(value) for value in _hex_lab(base["hex"]))
    chroma = float(np.hypot(a, b))
    # Very gray colors have no reliable hue; lean slightly warm, as most whites and neutrals do.
    hue = float(np.arctan2(b, a)) if chroma > 2.5 else np.radians(80)
    neutral = chroma < 10

    targets = [
        ("Trim white", _lch_target(94.0, min(chroma * 0.25, 5.0), hue), lambda lab: lab[0] >= 88 and np.hypot(lab[1], lab[2]) <= 10),
        ("Lighter", _lch_target(min(96.0, lightness + 18), chroma * 0.75, hue), None),
        ("Darker", _lch_target(max(14.0, lightness - 24), min(chroma * 1.1, 60.0), hue), None),
    ]
    if neutral:
        # A neutral wall pairs well with a deep, quieter color opposite its undertone, and with a soft green.
        targets.append(("Accent", _lch_target(36.0, 22.0, hue + np.pi), None))
        targets.append(("Accent", _lch_target(55.0, 18.0, np.radians(135)), None))
    else:
        targets.append(("Accent", _lch_target(float(np.clip(lightness, 35, 62)), min(chroma, 32.0), hue + np.pi), None))
        targets.append(("Accent", _lch_target(max(25.0, lightness - 10), max(chroma, 15.0), hue + np.radians(35)), None))

    taken = {base["code"], *[color["code"] for color in official]}
    suggested = []
    for role, target, allowed in targets:
        if not rows:
            break
        distance = np.sqrt(((labs - target) ** 2).sum(axis=1))
        for index in np.argsort(distance)[:40]:
            color = rows[int(index)]
            if color["code"] in taken or (allowed and not allowed(labs[int(index)])):
                continue
            taken.add(color["code"])
            suggested.append({**_public(color, brands), "role": role})
            break

    def official_role(color: dict) -> str:
        lab = _hex_lab(color["hex"])
        return "Coordinating white" if lab[0] >= 88 and np.hypot(lab[1], lab[2]) <= 10 else "Designer pairing"

    return {
        "color": _public(base, brands),
        "official": [{**_public(color, brands), "role": official_role(color)} for color in official],
        "suggested": suggested,
    }
