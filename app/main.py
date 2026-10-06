"""Server for Roomhue. Photos are decoded in this process; saved rooms belong to a Google account."""

from __future__ import annotations

import base64
import io
import os
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.catalog import search as search_colors
from app.collection import delete_room, import_rooms, list_rooms, load_room, save_room, thumb_bytes
from app.color_math import recolor_rgb
from app.detect import Surface, detect_surfaces, magic_wand, prepare_image, surfaces_from_lines
from app.sample_room import make_sample_room

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
MAX_BYTES = 25 * 1024 * 1024
MAX_SESSIONS = 6

auth.load_env_file()

app = FastAPI(title="Roomhue", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="roomhue_session",
    max_age=auth.SESSION_DAYS * 24 * 60 * 60,
    same_site="lax",
    https_only=bool(os.environ.get("REPLIT_DEPLOYMENT")),
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


class WandRequest(BaseModel):
    x: float
    y: float
    tolerance: float = 16


class LineRequest(BaseModel):
    segments: list[list[float]] = Field(min_length=1, max_length=200)


class RenderSurface(BaseModel):
    mask_png_base64: str
    color: str
    coverage: float = 0.92
    sheen: str = "eggshell"
    shade: float = 0


class RenderRequest(BaseModel):
    surfaces: list[RenderSurface] = Field(default_factory=list)


class SavedSurface(BaseModel):
    name: str
    kind: str
    color: str | None = None
    color_label: str = ""
    sheen: str = "eggshell"
    coverage: float = 0.92
    shade: float = 0
    included: bool = True
    mask_png_base64: str


class GoogleSignIn(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)


class SaveRoomRequest(BaseModel):
    name: str = "Room"
    surfaces: list[SavedSurface] = Field(default_factory=list)


def _evict_locked(now: float) -> None:
    stale = [key for key, value in _sessions.items() if now - value["created"] > 60 * 60 * 2]
    for key in stale:
        del _sessions[key]
    if len(_sessions) <= MAX_SESSIONS:
        return
    for key, _value in sorted(_sessions.items(), key=lambda item: item[1]["created"])[: len(_sessions) - MAX_SESSIONS]:
        del _sessions[key]


def _store(image: np.ndarray) -> str:
    session_id = uuid.uuid4().hex[:12]
    with _lock:
        _evict_locked(time.time())
        _sessions[session_id] = {"image": image, "created": time.time()}
    return session_id


def _image(session_id: str) -> np.ndarray:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="That photo is no longer loaded. Import it again.")
        session["created"] = time.time()
        return session["image"]


def _decode_upload(data: bytes) -> np.ndarray:
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That photo is larger than 25 MB.")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode == "RGBA":
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            rgb = np.array(image)
    except Exception as exc:  # Pillow raises several error types for bad files.
        raise HTTPException(status_code=400, detail="That file isn't a photo I can read.") from exc
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return prepare_image(bgr)


def _png_b64(image: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode an image.")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _mask_from_b64(value: str, shape: tuple[int, int]) -> np.ndarray:
    try:
        raw = base64.b64decode(value, validate=True)
        array = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="A surface mask was unreadable.") from exc
    if array is None or array.shape[:2] != shape:
        raise HTTPException(status_code=400, detail="A surface mask doesn't match this photo.")
    return array


def _names(surfaces: list[Surface]) -> list[str]:
    totals: dict[str, int] = {}
    for surface in surfaces:
        totals[surface.kind] = totals.get(surface.kind, 0) + 1
    seen: dict[str, int] = {}
    labels = {"wall": "Wall", "ceiling": "Ceiling", "floor": "Floor"}
    names: list[str] = []
    for surface in surfaces:
        seen[surface.kind] = seen.get(surface.kind, 0) + 1
        label = labels.get(surface.kind, "Surface")
        names.append(f"{label} {seen[surface.kind]}" if totals[surface.kind] > 1 else label)
    return names


def _payload(session_id: str, image: np.ndarray, surfaces: list[Surface]) -> dict:
    names = _names(surfaces)
    return {
        "session_id": session_id,
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "image_png_base64": _png_b64(image),
        "surfaces": [
            {
                "id": f"s{index + 1}",
                "name": names[index],
                "kind": surface.kind,
                "confidence": round(surface.confidence, 2),
                "area": round(surface.area_ratio, 4),
                "mask_png_base64": _png_b64(surface.mask),
            }
            for index, surface in enumerate(surfaces)
        ],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/colors")
def colors(brand: str = "", q: str = "", limit: int = 60, offset: int = 0) -> dict:
    return search_colors(brand, q, limit, offset)


def _saved_surfaces(body: SaveRoomRequest) -> list[dict]:
    return [surface.model_dump() for surface in body.surfaces]


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"client_id": auth.client_id(), "user": auth.current_user(request)}


@app.post("/api/auth/google")
def auth_google(request: Request, body: GoogleSignIn) -> dict:
    user = auth.verify_google_token(body.credential)
    request.session["user"] = user
    return {"user": user}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/collection")
def collection_list(user_id: str = Depends(auth.require_user)) -> list[dict]:
    return list_rooms(user_id)


@app.post("/api/collection/import")
async def collection_import(files: list[UploadFile] = File(...), user_id: str = Depends(auth.require_user)) -> dict:
    uploads = [(item.filename or "", await item.read()) for item in files]
    return {"rooms": import_rooms(user_id, uploads)}


@app.post("/api/sessions/{session_id}/collection")
def collection_save(session_id: str, body: SaveRoomRequest, user_id: str = Depends(auth.require_user)) -> dict:
    image = _image(session_id)
    return save_room(user_id, uuid.uuid4().hex[:12], body.name, image, _saved_surfaces(body))


@app.put("/api/collection/{room_id}")
def collection_update(room_id: str, body: SaveRoomRequest, session_id: str, user_id: str = Depends(auth.require_user)) -> dict:
    image = _image(session_id)
    return save_room(user_id, room_id, body.name, image, _saved_surfaces(body))


@app.post("/api/collection/{room_id}/open")
def collection_open(room_id: str, user_id: str = Depends(auth.require_user)) -> dict:
    image, meta = load_room(user_id, room_id)
    session_id = _store(image)
    return {
        "session_id": session_id,
        "collection_id": meta["id"],
        "name": meta["name"],
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "image_png_base64": _png_b64(image),
        "surfaces": [
            {
                "id": f"s{index + 1}",
                "name": surface["name"],
                "kind": surface["kind"],
                "confidence": 1,
                "color": surface.get("color"),
                "color_label": surface.get("color_label") or "",
                "sheen": surface.get("sheen") or "eggshell",
                "coverage": surface.get("coverage", 0.92),
                "shade": surface.get("shade", 0),
                "included": surface.get("included", True),
                "mask_png_base64": surface["mask_png_base64"],
            }
            for index, surface in enumerate(meta["surfaces"])
        ],
    }


@app.delete("/api/collection/{room_id}")
def collection_delete(room_id: str, user_id: str = Depends(auth.require_user)) -> dict:
    delete_room(user_id, room_id)
    return {"ok": True}


@app.get("/api/collection/{room_id}/thumb")
def collection_thumb(room_id: str, user_id: str = Depends(auth.require_user)) -> Response:
    return Response(thumb_bytes(user_id, room_id), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


@app.post("/api/sessions")
async def create_session(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    image = _decode_upload(data)
    session_id = _store(image)
    return _payload(session_id, image, detect_surfaces(image))


@app.post("/api/sample")
def sample() -> dict:
    image = prepare_image(make_sample_room().bgr)
    session_id = _store(image)
    return _payload(session_id, image, detect_surfaces(image))


@app.post("/api/sessions/{session_id}/detect")
def detect_again(session_id: str) -> dict:
    image = _image(session_id)
    surfaces = detect_surfaces(image)
    names = _names(surfaces)
    return {
        "surfaces": [
            {
                "id": f"s{index + 1}",
                "name": names[index],
                "kind": surface.kind,
                "confidence": round(surface.confidence, 2),
                "area": round(surface.area_ratio, 4),
                "mask_png_base64": _png_b64(surface.mask),
            }
            for index, surface in enumerate(surfaces)
        ]
    }


@app.post("/api/sessions/{session_id}/lines")
def apply_lines(session_id: str, body: LineRequest) -> dict:
    image = _image(session_id)
    segments: list[tuple[float, float, float, float]] = []
    for segment in body.segments:
        if len(segment) != 4 or not all(np.isfinite(value) for value in segment):
            raise HTTPException(status_code=400, detail="Each line needs two points.")
        segments.append((float(segment[0]), float(segment[1]), float(segment[2]), float(segment[3])))
    try:
        surfaces = surfaces_from_lines(image, segments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    names = _names(surfaces)
    return {
        "surfaces": [
            {
                "id": f"s{index + 1}",
                "name": names[index],
                "kind": surface.kind,
                "confidence": round(surface.confidence, 2),
                "area": round(surface.area_ratio, 4),
                "mask_png_base64": _png_b64(surface.mask),
            }
            for index, surface in enumerate(surfaces)
        ]
    }


@app.post("/api/sessions/{session_id}/wand")
def wand(session_id: str, body: WandRequest) -> dict:
    image = _image(session_id)
    mask = magic_wand(image, int(round(body.x)), int(round(body.y)), body.tolerance)
    return {"mask_png_base64": _png_b64(mask), "area": round(float((mask > 0).mean()), 4)}


@app.post("/api/sessions/{session_id}/render")
def render(session_id: str, body: RenderRequest) -> Response:
    image = _image(session_id)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    for surface in body.surfaces:
        if not surface.color:
            continue
        mask = _mask_from_b64(surface.mask_png_base64, image.shape[:2])
        try:
            rgb = recolor_rgb(rgb, mask, surface.color, surface.coverage, surface.sheen, surface.shade)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok, buffer = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise HTTPException(status_code=500, detail="Could not render the preview.")
    return Response(content=buffer.tobytes(), media_type="image/png")
