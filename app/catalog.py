"""Searchable paint colors from published brand color books."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

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
