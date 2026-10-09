"""Server for RoomRoller. Photos are decoded in this process; saved rooms belong to a Google account."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from app import auth, images
from app.catalog import coordinates as coordinate_colors
from app.catalog import search as search_colors
from app.collection import (
    delete_account,
    delete_room,
    import_rooms,
    list_rooms,
    load_favorites,
    load_room,
    record_consent,
    rename_room,
    revoke_sessions,
    save_favorites,
    session_epoch,
    save_room,
    thumb_bytes,
)
from app import shares
from app.color_math import blend_layers, paint_layer
from app.detect import Surface, detect_surfaces, magic_wand, prepare_image, surfaces_from_lines
from app.sample_room import make_sample_room

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
MAX_BYTES = 25 * 1024 * 1024
# Largest request accepted at all: a 25 MB photo plus form overhead, or a saved room with its wall masks.
MAX_REQUEST_BYTES = 32 * 1024 * 1024
# Open photos are kept in memory while people edit. Oldest-unused go first when this budget is full;
# the browser re-sends its copy if one it needs was dropped.
SESSION_BUDGET_BYTES = int(os.environ.get("ROOMHUE_SESSION_MB", "400")) * 1024 * 1024
SESSION_IDLE_SECONDS = 2 * 60 * 60
# Detection needs about 600 MB; running more than one at a time could exhaust a 2 GB server.
_heavy = threading.BoundedSemaphore(max(1, int(os.environ.get("ROOMHUE_DETECT_SLOTS", "1"))))
GOOGLE_SIGN_IN = "https://accounts.google.com/gsi/"
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' {GOOGLE_SIGN_IN}client",
        f"style-src 'self' 'unsafe-inline' {GOOGLE_SIGN_IN}style",
        "img-src 'self' data: blob:",
        f"connect-src 'self' {GOOGLE_SIGN_IN}",
        f"frame-src {GOOGLE_SIGN_IN}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)

auth.load_env_file()

app = FastAPI(title="RoomRoller", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="roomhue_session",
    max_age=auth.SESSION_DAYS * 24 * 60 * 60,
    same_site="lax",
    https_only=bool(os.environ.get("REPLIT_DEPLOYMENT")),
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class LimitBody:
    """Refuse oversized requests before they are read into memory, including ones sent without a length."""

    def __init__(self, inner, limit: int) -> None:
        self.inner = inner
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.limit:
            await JSONResponse({"detail": "That upload is too large."}, status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise HTTPException(status_code=413, detail="That upload is too large.")
            return message

        await self.inner(scope, limited, send)


app.add_middleware(LimitBody, limit=MAX_REQUEST_BYTES)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    # Google's sign-in opens a popup that reports back to this page.
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


class WandRequest(BaseModel):
    x: float
    y: float
    tolerance: float = 16


class LineRequest(BaseModel):
    segments: list[list[float]] = Field(min_length=1, max_length=200)


MASK_FIELD = Field(max_length=4_000_000)


class RenderSurface(BaseModel):
    mask_png_base64: str = MASK_FIELD
    color: str = Field(max_length=16)
    coverage: float = 0.92
    sheen: str = "eggshell"
    shade: float = 0


class RenderRequest(BaseModel):
    surfaces: list[RenderSurface] = Field(default_factory=list, max_length=40)


class SavedSurface(BaseModel):
    name: str = Field(max_length=80)
    kind: str = Field(max_length=20)
    color: str | None = Field(default=None, max_length=16)
    color_label: str = Field(default="", max_length=200)
    sheen: str = "eggshell"
    coverage: float = 0.92
    shade: float = 0
    included: bool = True
    mask_png_base64: str = MASK_FIELD


class GoogleSignIn(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)
    accepted_terms: bool = False
    terms_version: str = ""


class FavoriteColor(BaseModel):
    brand: str = Field(min_length=1, max_length=60)
    brandId: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class FavoritesRequest(BaseModel):
    favorites: list[FavoriteColor] = Field(default_factory=list, max_length=300)


class SaveRoomRequest(BaseModel):
    name: str = Field(default="Room", max_length=200)
    surfaces: list[SavedSurface] = Field(default_factory=list, max_length=40)


class RenameRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _evict_locked(now: float, incoming: int) -> None:
    for key in [key for key, value in _sessions.items() if now - value["used"] > SESSION_IDLE_SECONDS]:
        del _sessions[key]
    total = sum(value["image"].nbytes for value in _sessions.values()) + incoming
    for key, value in sorted(_sessions.items(), key=lambda item: item[1]["used"]):
        if total <= SESSION_BUDGET_BYTES:
            break
        total -= value["image"].nbytes
        del _sessions[key]


def _store(image: np.ndarray) -> str:
    session_id = uuid.uuid4().hex
    with _lock:
        now = time.time()
        _evict_locked(now, image.nbytes)
        _sessions[session_id] = {"image": image, "used": now}
    return session_id


def _image(session_id: str) -> np.ndarray:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            # 410 tells the browser to send its copy of the photo again and retry.
            raise HTTPException(status_code=410, detail="That photo is no longer loaded. Open it again.")
        session["used"] = time.time()
        return session["image"]


def _heavy_work(work, *args):
    """Run decode-and-detect work one at a time, so memory stays bounded."""
    if not _heavy.acquire(timeout=45):
        raise HTTPException(status_code=503, detail="RoomRoller is busy right now. Try again in a moment.")
    try:
        return work(*args)
    finally:
        _heavy.release()


def _decode_upload(data: bytes) -> np.ndarray:
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That photo is larger than 25 MB.")
    width, height = images.image_size(data)
    images.check_size(width, height)
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail="A surface mask was unreadable.") from exc
    return images.decode(raw, cv2.IMREAD_GRAYSCALE, expect=shape)


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


def _versioned(html: str) -> str:
    """Tag app.js and styles.css with a hash of their contents so browsers never run a stale copy."""
    for name in ("app.js", "styles.css"):
        digest = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={digest}"')
    return html


@app.get("/")
def index() -> HTMLResponse:
    html = _versioned((STATIC / "index.html").read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


LEGAL = STATIC / "legal"


@app.get("/privacy")
def privacy_page() -> FileResponse:
    return FileResponse(LEGAL / "privacy.html", headers={"Cache-Control": "no-cache"})


@app.get("/terms")
def terms_page() -> FileResponse:
    return FileResponse(LEGAL / "terms.html", headers={"Cache-Control": "no-cache"})


@app.get("/cookies")
def cookies_page() -> FileResponse:
    return FileResponse(LEGAL / "cookies.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/colors/coordinate")
def colors_coordinate(brand: str, code: str) -> dict:
    result = coordinate_colors(brand, code)
    if result is None:
        raise HTTPException(status_code=404, detail="That paint wasn't found.")
    return result


@app.get("/api/colors")
def colors(brand: str = "", q: str = "", limit: int = 60, offset: int = 0) -> dict:
    return search_colors(brand, q, limit, offset)


def _saved_surfaces(body: SaveRoomRequest) -> list[dict]:
    return [surface.model_dump() for surface in body.surfaces]


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"client_id": auth.client_id(), "user": auth.current_user(request), "legal_version": auth.LEGAL_VERSION}


@app.post("/api/auth/google")
def auth_google(request: Request, body: GoogleSignIn) -> dict:
    if not body.accepted_terms or body.terms_version != auth.LEGAL_VERSION:
        raise HTTPException(status_code=400, detail="Agree to the Terms of Service and Privacy Policy to sign in.")
    user = auth.verify_google_token(body.credential)
    record_consent(user["id"], auth.LEGAL_VERSION)
    request.session["user"] = {**user, "epoch": session_epoch(user["id"], fresh=True)}
    return {"user": user}


@app.delete("/api/account")
def account_delete(request: Request, user_id: str = Depends(auth.require_user)) -> dict:
    shares.delete_all_shares(user_id)
    removed = delete_account(user_id)
    request.session.clear()
    return {"ok": True, "removed": removed}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict:
    user = auth.current_user(request)
    if user:
        revoke_sessions(user["id"])
    request.session.clear()
    return {"ok": True}


@app.post("/api/shares")
async def share_create(
    before: UploadFile = File(...),
    after: UploadFile = File(...),
    meta: str = Form("{}"),
    user_id: str = Depends(auth.require_user),
) -> dict:
    try:
        details = json.loads(meta)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="The share details couldn't be read.") from exc
    if not isinstance(details, dict):
        details = {}
    before_bytes, after_bytes = await before.read(), await after.read()
    record = await run_in_threadpool(shares.create_share, user_id, details.get("name", ""), details.get("colors"), before_bytes, after_bytes)
    return {"token": record["token"], "name": record["name"], "path": f"/s/{record['token']}"}


@app.get("/api/shares")
def share_list(user_id: str = Depends(auth.require_user)) -> list[dict]:
    return shares.list_shares(user_id)


@app.delete("/api/shares/{token}")
def share_delete(token: str, user_id: str = Depends(auth.require_user)) -> dict:
    shares.delete_share(user_id, token)
    return {"ok": True}


def _public_base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@app.get("/s/{token}")
def share_view(token: str, request: Request) -> HTMLResponse:
    return HTMLResponse(
        shares.share_page(token, _public_base(request)),
        headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-cache"},
    )


@app.get("/s/{token}/{which}.jpg")
def share_image(token: str, which: str) -> Response:
    return Response(
        shares.share_image(token, which),
        media_type="image/jpeg",
        # Short-lived caching so a deleted link stops showing its photos within a minute.
        headers={"X-Robots-Tag": "noindex", "Cache-Control": "public, max-age=60"},
    )


@app.get("/api/favorites")
def favorites_list(user_id: str = Depends(auth.require_user)) -> dict:
    return {"favorites": load_favorites(user_id)}


@app.put("/api/favorites")
def favorites_save(body: FavoritesRequest, user_id: str = Depends(auth.require_user)) -> dict:
    return {"favorites": save_favorites(user_id, [color.model_dump() for color in body.favorites])}


@app.get("/api/collection")
def collection_list(user_id: str = Depends(auth.require_user)) -> list[dict]:
    return list_rooms(user_id)


@app.post("/api/collection/import")
async def collection_import(files: list[UploadFile] = File(...), user_id: str = Depends(auth.require_user)) -> dict:
    if len(files) > 400:
        raise HTTPException(status_code=413, detail="That's too many files at once. Upload fewer rooms at a time.")
    uploads = [(item.filename or "", await item.read()) for item in files]
    return {"rooms": await run_in_threadpool(_heavy_work, import_rooms, user_id, uploads)}


@app.patch("/api/collection/{room_id}")
def collection_rename(room_id: str, body: RenameRoomRequest, user_id: str = Depends(auth.require_user)) -> dict:
    return rename_room(user_id, room_id, body.name)


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

    def work() -> dict:
        image = _decode_upload(data)
        surfaces = detect_surfaces(image)
        return _payload(_store(image), image, surfaces)

    # Off the event loop, so one person's photo doesn't stall everyone else's requests.
    return await run_in_threadpool(_heavy_work, work)


@app.post("/api/sessions/restore")
async def restore_session(file: UploadFile = File(...)) -> dict:
    """Load a photo the browser already has (no detection), when the server dropped its copy."""
    data = await file.read()
    image = await run_in_threadpool(_decode_upload, data)
    return {"session_id": _store(image), "width": int(image.shape[1]), "height": int(image.shape[0])}


SAMPLE_PHOTO = ROOT / "data" / "sample" / "living-room.jpg"


@lru_cache(maxsize=1)
def _sample_room() -> tuple[np.ndarray, list[Surface]]:
    # A real CC0 photo shows what detection does on an actual room; the drawn room is the fallback.
    photo = cv2.imread(str(SAMPLE_PHOTO), cv2.IMREAD_COLOR) if SAMPLE_PHOTO.is_file() else None
    image = prepare_image(photo if photo is not None else make_sample_room().bgr)
    return image, detect_surfaces(image)


@app.post("/api/sample")
def sample() -> dict:
    image, surfaces = _sample_room() if _sample_room.cache_info().currsize else _heavy_work(_sample_room)
    image = image.copy()
    session_id = _store(image)
    return _payload(session_id, image, surfaces)


@app.post("/api/sessions/{session_id}/detect")
def detect_again(session_id: str) -> dict:
    image = _image(session_id)
    surfaces = _heavy_work(detect_surfaces, image)
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
    return _heavy_work(_render, session_id, body)


def _render(session_id: str, body: RenderRequest) -> Response:
    image = _image(session_id)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    layers = []
    for surface in body.surfaces:
        if not surface.color:
            continue
        mask = _mask_from_b64(surface.mask_png_base64, image.shape[:2])
        try:
            layer = paint_layer(rgb, mask, surface.color, surface.coverage, surface.sheen, surface.shade)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if layer:
            layers.append(layer)
    rgb = blend_layers(rgb, layers)
    ok, buffer = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise HTTPException(status_code=500, detail="Could not render the preview.")
    return Response(content=buffer.tobytes(), media_type="image/png")
