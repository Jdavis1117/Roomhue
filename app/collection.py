"""Saved rooms live in data/collection on the computer running the app."""

from __future__ import annotations

import base64
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
COLLECTION = ROOT / "data" / "collection"
_ID = re.compile(r"^[a-f0-9]{12}$")


def _folder(room_id: str) -> Path:
    if not _ID.fullmatch(room_id):
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    folder = COLLECTION / room_id
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    return folder


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split()).strip()
    return (cleaned or "Room")[:80]


def save_room(room_id: str, name: str, image: np.ndarray, surfaces: list[dict]) -> dict:
    if not _ID.fullmatch(room_id):
        raise HTTPException(status_code=400, detail="That saved room was not found.")
    folder = COLLECTION / room_id
    folder.mkdir(parents=True, exist_ok=True)
    height, width = image.shape[:2]
    if not cv2.imwrite(str(folder / "photo.png"), image):
        raise HTTPException(status_code=500, detail="Could not save that photo.")
    thumb = image
    long_edge = max(height, width)
    if long_edge > 360:
        scale = 360 / long_edge
        thumb = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(folder / "thumb.jpg"), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

    stored = []
    for index, surface in enumerate(surfaces):
        raw = base64.b64decode(surface["mask_png_base64"], validate=True)
        mask = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if mask is None or mask.shape[0] != height or mask.shape[1] != width:
            raise HTTPException(status_code=400, detail="A wall mask doesn't match this photo.")
        (folder / f"mask-{index}.png").write_bytes(raw)
        stored.append(
            {
                "name": surface["name"],
                "kind": surface["kind"],
                "color": surface.get("color") or None,
                "color_label": surface.get("color_label") or "",
                "sheen": surface.get("sheen") or "eggshell",
                "coverage": surface.get("coverage", 0.92),
                "shade": surface.get("shade", 0),
                "included": surface.get("included", True),
                "mask": f"mask-{index}.png",
            }
        )
    for leftover in folder.glob("mask-*.png"):
        number = leftover.stem.split("-")[-1]
        if number.isdigit() and int(number) >= len(stored):
            leftover.unlink()

    saved_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": room_id,
        "name": _clean_name(name),
        "saved_at": saved_at,
        "width": width,
        "height": height,
        "surfaces": stored,
    }
    (folder / "room.json").write_text(json.dumps(record), encoding="utf-8")
    return {"id": room_id, "name": record["name"], "saved_at": saved_at}


def list_rooms() -> list[dict]:
    if not COLLECTION.is_dir():
        return []
    rooms = []
    for folder in COLLECTION.iterdir():
        meta_path = folder / "room.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
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


def load_room(room_id: str) -> tuple[np.ndarray, dict]:
    folder = _folder(room_id)
    image = cv2.imread(str(folder / "photo.png"), cv2.IMREAD_COLOR)
    meta = json.loads((folder / "room.json").read_text(encoding="utf-8"))
    if image is None:
        raise HTTPException(status_code=500, detail="That saved photo could not be read.")
    surfaces = []
    for surface in meta["surfaces"]:
        raw = (folder / surface["mask"]).read_bytes()
        surfaces.append({**surface, "mask_png_base64": base64.b64encode(raw).decode("ascii")})
    meta["surfaces"] = surfaces
    return image, meta


def delete_room(room_id: str) -> None:
    shutil.rmtree(_folder(room_id))


_ROOM_FILE = re.compile(r"^(room\.json|photo\.png|thumb\.jpg|mask-\d+\.png)$")


def import_rooms(uploads: list[tuple[str, bytes]]) -> list[dict]:
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
    return [_store_imported(files) for files in ready]


def _store_imported(files: dict[str, bytes]) -> dict:
    try:
        meta = json.loads(files["room.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="That room.json could not be read.") from exc
    image = cv2.imdecode(np.frombuffer(files["photo.png"], dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="That saved photo could not be read.")
    height, width = image.shape[:2]
    surfaces = meta.get("surfaces")
    if not isinstance(surfaces, list):
        raise HTTPException(status_code=400, detail="That room.json has no walls.")
    stored = []
    for index, surface in enumerate(surfaces):
        mask_name = Path(str(surface.get("mask") or "")).name
        raw = files.get(mask_name)
        if raw is None or not _ROOM_FILE.fullmatch(mask_name):
            raise HTTPException(status_code=400, detail="A wall mask is missing from that saved room.")
        mask = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if mask is None or mask.shape[0] != height or mask.shape[1] != width:
            raise HTTPException(status_code=400, detail="A wall mask doesn't match this photo.")
        stored.append(
            {
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
        )
        files[f"mask-{index}.png"] = raw

    room_id = str(meta.get("id") or "")
    if not _ID.fullmatch(room_id):
        room_id = uuid.uuid4().hex[:12]
    folder = COLLECTION / room_id
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "photo.png").write_bytes(files["photo.png"])
    for index in range(len(stored)):
        (folder / f"mask-{index}.png").write_bytes(files[f"mask-{index}.png"])
    if "thumb.jpg" in files:
        (folder / "thumb.jpg").write_bytes(files["thumb.jpg"])
    else:
        thumb = image
        long_edge = max(height, width)
        if long_edge > 360:
            scale = 360 / long_edge
            thumb = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(folder / "thumb.jpg"), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    saved_at = str(meta.get("saved_at") or datetime.now(timezone.utc).isoformat())
    record = {
        "id": room_id,
        "name": _clean_name(str(meta.get("name") or "Room")),
        "saved_at": saved_at,
        "width": width,
        "height": height,
        "surfaces": stored,
    }
    (folder / "room.json").write_text(json.dumps(record), encoding="utf-8")
    return {"id": room_id, "name": record["name"], "saved_at": saved_at}


def thumb_file(room_id: str) -> Path:
    path = _folder(room_id) / "thumb.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That saved room was not found.")
    return path
