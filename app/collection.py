"""Saved rooms, kept per Google account in the store from app.storage."""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

import cv2
import numpy as np
from fastapi import HTTPException

from app import images
from app.storage import store

_ID = re.compile(r"^[a-f0-9]{12}$")
_USER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _prefix(user_id: str, room_id: str = "") -> str:
    if not _USER.fullmatch(user_id):
        raise HTTPException(status_code=401, detail="Sign in to use your collection.")
    if room_id and not _ID.fullmatch(room_id):
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    return f"users/{user_id}/{room_id}/" if room_id else f"users/{user_id}/"


def _meta(user_id: str, room_id: str) -> dict:
    raw = store().read(_prefix(user_id, room_id) + "room.json")
    if raw is None:
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    return json.loads(raw.decode("utf-8"))


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split()).strip()
    return (cleaned or "Room")[:80]


def _encode(image: np.ndarray, ext: str, params: list[int] | None = None) -> bytes:
    ok, buffer = cv2.imencode(ext, image, params or [])
    if not ok:
        raise HTTPException(status_code=500, detail="Could not save that photo.")
    return buffer.tobytes()


def _thumb(image: np.ndarray) -> bytes:
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if long_edge > 360:
        scale = 360 / long_edge
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    return _encode(image, ".jpg", [int(cv2.IMWRITE_JPEG_QUALITY), 80])


def _write_room(user_id: str, room_id: str, name: str, saved_at: str, photo: bytes, thumb: bytes, width: int, height: int, surfaces: list[dict], masks: list[bytes]) -> dict:
    prefix = _prefix(user_id, room_id)
    files = store()
    old_masks = [key for key in files.list(prefix) if PurePosixPath(key).name.startswith("mask-")]
    files.write(prefix + "photo.png", photo)
    files.write(prefix + "thumb.jpg", thumb)
    for index, raw in enumerate(masks):
        files.write(prefix + f"mask-{index}.png", raw)
    for key in old_masks:
        number = PurePosixPath(key).stem.split("-")[-1]
        if number.isdigit() and int(number) >= len(masks):
            files.delete(key)
    record = {
        "id": room_id,
        "name": _clean_name(name),
        "saved_at": saved_at,
        "width": width,
        "height": height,
        "surfaces": surfaces,
    }
    files.write(prefix + "room.json", json.dumps(record).encode("utf-8"))
    return {"id": room_id, "name": record["name"], "saved_at": saved_at}


def _surface_record(surface: dict, index: int) -> dict:
    return {
        "name": str(surface.get("name") or f"Wall {index + 1}")[:80],
        "kind": surface.get("kind") or "wall",
        "color": surface.get("color") or None,
        "color_label": surface.get("color_label") or "",
        "sheen": surface.get("sheen") or "eggshell",
        "coverage": surface.get("coverage", 0.92),
        "shade": surface.get("shade", 0),
        "included": surface.get("included", True),
        "mask": f"mask-{index}.png",
    }


def save_room(user_id: str, room_id: str, name: str, image: np.ndarray, surfaces: list[dict]) -> dict:
    _prefix(user_id, room_id or "0" * 12)
    height, width = image.shape[:2]
    masks = []
    for surface in surfaces:
        try:
            raw = base64.b64decode(surface["mask_png_base64"], validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="A wall mask was unreadable.") from exc
        images.decode(raw, cv2.IMREAD_UNCHANGED, expect=(height, width))
        masks.append(raw)
    stored = [_surface_record(surface, index) for index, surface in enumerate(surfaces)]
    saved_at = datetime.now(timezone.utc).isoformat()
    return _write_room(user_id, room_id, name, saved_at, _encode(image, ".png"), _thumb(image), width, height, stored, masks)


def list_rooms(user_id: str) -> list[dict]:
    rooms = []
    files = store()
    for key in files.list(_prefix(user_id)):
        if not key.endswith("/room.json"):
            continue
        try:
            meta = json.loads((files.read(key) or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        rooms.append(
            {
                "id": meta["id"],
                "name": meta["name"],
                "saved_at": meta["saved_at"],
                "walls": sum(1 for surface in meta.get("surfaces", []) if surface.get("kind") == "wall"),
            }
        )
    rooms.sort(key=lambda room: room["saved_at"], reverse=True)
    return rooms


def load_room(user_id: str, room_id: str) -> tuple[np.ndarray, dict]:
    meta = _meta(user_id, room_id)
    prefix = _prefix(user_id, room_id)
    files = store()
    raw = files.read(prefix + "photo.png")
    image = None if raw is None else cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=500, detail="That saved photo could not be read.")
    surfaces = []
    for surface in meta["surfaces"]:
        mask = files.read(prefix + PurePosixPath(surface["mask"]).name) or b""
        surfaces.append({**surface, "mask_png_base64": base64.b64encode(mask).decode("ascii")})
    meta["surfaces"] = surfaces
    return image, meta


def rename_room(user_id: str, room_id: str, name: str) -> dict:
    meta = _meta(user_id, room_id)
    meta["name"] = _clean_name(name)
    store().write(_prefix(user_id, room_id) + "room.json", json.dumps(meta).encode("utf-8"))
    return {"id": room_id, "name": meta["name"], "saved_at": meta["saved_at"]}


def delete_room(user_id: str, room_id: str) -> None:
    _meta(user_id, room_id)
    files = store()
    for key in files.list(_prefix(user_id, room_id)):
        files.delete(key)


MAX_FAVORITES = 300


def load_favorites(user_id: str) -> list[dict]:
    raw = store().read(_prefix(user_id) + "favorites.json")
    if raw is None:
        return []
    try:
        favorites = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    return favorites if isinstance(favorites, list) else []


def save_favorites(user_id: str, favorites: list[dict]) -> list[dict]:
    """Replace the account's favorite colors, dropping duplicates and keeping the newest first."""
    seen: set[str] = set()
    kept = []
    for color in favorites:
        key = f"{color['brandId']}|{color['code']}".lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append({field: color[field] for field in ("brand", "brandId", "code", "name", "hex")})
        if len(kept) >= MAX_FAVORITES:
            break
    store().write(_prefix(user_id) + "favorites.json", json.dumps(kept).encode("utf-8"))
    return kept


_epochs: dict[str, tuple[int | None, float]] = {}
_EPOCH_SECONDS = 30.0


def _account(user_id: str) -> dict | None:
    raw = store().read(_prefix(user_id) + "account.json")
    if raw is None:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return record if isinstance(record, dict) else {}


def session_epoch(user_id: str, fresh: bool = False) -> int | None:
    """Which sign-ins are still valid. A cookie from an older epoch, or for a deleted account (None), is refused.

    Cached briefly so ordinary requests don't each read storage.
    """
    cached = _epochs.get(user_id)
    if cached and not fresh and time.time() - cached[1] < _EPOCH_SECONDS:
        return cached[0]
    record = _account(user_id)
    epoch = None if record is None else int(record.get("session_epoch", 0))
    _epochs[user_id] = (epoch, time.time())
    return epoch


def revoke_sessions(user_id: str) -> None:
    """Sign this account out everywhere: every existing cookie stops working."""
    record = _account(user_id)
    if record is None:
        return
    record["session_epoch"] = int(record.get("session_epoch", 0)) + 1
    store().write(_prefix(user_id) + "account.json", json.dumps(record).encode("utf-8"))
    _epochs[user_id] = (record["session_epoch"], time.time())


def record_consent(user_id: str, version: str) -> None:
    """Keep proof of which Terms and Privacy Policy version the account agreed to, and when."""
    key = _prefix(user_id) + "account.json"
    files = store()
    now = datetime.now(timezone.utc).isoformat()
    try:
        record = json.loads((files.read(key) or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        record = {}
    if record.get("terms_version") == version:
        return
    record.update({"terms_version": version, "accepted_at": now})
    record.setdefault("session_epoch", 0)
    record.setdefault("first_accepted_at", now)
    files.write(key, json.dumps(record).encode("utf-8"))


def delete_account(user_id: str) -> int:
    """Remove every saved room and the consent record for this account. Returns the number of files removed."""
    files = store()
    keys = files.list(_prefix(user_id))
    for key in keys:
        files.delete(key)
    _epochs[user_id] = (None, time.time())
    return len(keys)


def thumb_bytes(user_id: str, room_id: str) -> bytes:
    raw = store().read(_prefix(user_id, room_id) + "thumb.jpg")
    if raw is None:
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    return raw


_ROOM_FILE = re.compile(r"^(room\.json|photo\.png|thumb\.jpg|mask-\d+\.png)$")


def import_rooms(user_id: str, uploads: list[tuple[str, bytes]]) -> list[dict]:
    groups: dict[str, dict[str, bytes]] = {}
    for raw_name, data in uploads:
        parts = [part for part in PurePosixPath(raw_name.replace("\\", "/")).parts if part not in ("", ".", "..")]
        if not parts or not _ROOM_FILE.fullmatch(parts[-1]):
            continue
        parent = parts[-2] if len(parts) > 1 else ""
        groups.setdefault(parent, {})[parts[-1]] = data
    ready = [files for files in groups.values() if "room.json" in files and "photo.png" in files]
    if not ready:
        raise HTTPException(status_code=400, detail="Choose a saved room folder with room.json and photo.png.")
    return [_store_imported(user_id, files) for files in ready]


def _store_imported(user_id: str, files: dict[str, bytes]) -> dict:
    try:
        meta = json.loads(files["room.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="That room.json could not be read.") from exc
    image = images.decode(files["photo.png"], cv2.IMREAD_COLOR)
    height, width = image.shape[:2]
    surfaces = meta.get("surfaces")
    if not isinstance(surfaces, list):
        raise HTTPException(status_code=400, detail="That room.json has no walls.")
    stored, masks = [], []
    for index, surface in enumerate(surfaces):
        mask_name = PurePosixPath(str(surface.get("mask") or "")).name
        raw = files.get(mask_name)
        if raw is None or not _ROOM_FILE.fullmatch(mask_name):
            raise HTTPException(status_code=400, detail="A wall mask is missing from that saved room.")
        images.decode(raw, cv2.IMREAD_UNCHANGED, expect=(height, width))
        stored.append(_surface_record(surface, index))
        masks.append(raw)

    room_id = str(meta.get("id") or "")
    if not _ID.fullmatch(room_id):
        room_id = uuid.uuid4().hex[:12]
    thumb = files.get("thumb.jpg") or _thumb(image)
    saved_at = str(meta.get("saved_at") or datetime.now(timezone.utc).isoformat())
    name = str(meta.get("name") or "Room")
    return _write_room(user_id, room_id, name, saved_at, files["photo.png"], thumb, width, height, stored, masks)
