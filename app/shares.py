"""Public before/after links.

A share is two JPEGs (the photo and the painted version) plus the colors used,
stored under the owner's account so deleting the account deletes its shares.
A small pointer at shares/<token>.json lets anyone with the link find it.
"""

from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import HTTPException

from app.storage import store

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "static" / "share.html"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{12}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_SHARES = 50
MAX_BYTES = 6 * 1024 * 1024
MAX_EDGE = 2000


def _owner_prefix(user_id: str, token: str = "") -> str:
    return f"users/{user_id}/shares/{token}/" if token else f"users/{user_id}/shares/"


def _pointer(token: str) -> str:
    return f"shares/{token}.json"


def _clean_jpeg(data: bytes) -> tuple[bytes, int, int]:
    """Decode and re-encode, which also drops any embedded metadata."""
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That image is too large to share.")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="That image couldn't be read.")
    height, width = image.shape[:2]
    if max(height, width) > MAX_EDGE:
        scale = MAX_EDGE / max(height, width)
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    if not ok:
        raise HTTPException(status_code=500, detail="Couldn't prepare that image.")
    return encoded.tobytes(), width, height


def _clean_colors(raw: object) -> list[dict]:
    colors = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not _HEX.fullmatch(str(item.get("hex", ""))):
            continue
        colors.append(
            {
                "surface": str(item.get("surface") or "Wall")[:40],
                "name": str(item.get("name") or item["hex"])[:80],
                "label": str(item.get("label") or "")[:120],
                "hex": str(item["hex"]).upper(),
            }
        )
        if len(colors) >= 24:
            break
    return colors


def create_share(user_id: str, name: str, colors: object, before: bytes, after: bytes) -> dict:
    files = store()
    existing = {key.split("/")[3] for key in files.list(_owner_prefix(user_id)) if key.count("/") >= 4}
    if len(existing) >= MAX_SHARES:
        raise HTTPException(status_code=400, detail=f"You have {MAX_SHARES} shared links. Delete one in Collection to share another.")
    before_jpg, width, height = _clean_jpeg(before)
    after_jpg, after_w, after_h = _clean_jpeg(after)
    if (after_w, after_h) != (width, height):
        raise HTTPException(status_code=400, detail="The before and after images don't match.")
    token = secrets.token_urlsafe(9)
    record = {
        "token": token,
        "name": (" ".join(str(name or "").split()) or "My room")[:80],
        "colors": _clean_colors(colors),
        "width": width,
        "height": height,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    prefix = _owner_prefix(user_id, token)
    files.write(prefix + "before.jpg", before_jpg)
    files.write(prefix + "after.jpg", after_jpg)
    files.write(prefix + "share.json", json.dumps(record).encode("utf-8"))
    files.write(_pointer(token), json.dumps({"owner": user_id}).encode("utf-8"))
    return record


def _owner(token: str) -> str:
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404, detail="That link doesn't exist or was deleted.")
    raw = store().read(_pointer(token))
    if raw is None:
        raise HTTPException(status_code=404, detail="That link doesn't exist or was deleted.")
    return str(json.loads(raw.decode("utf-8"))["owner"])


def load_share(token: str) -> dict:
    raw = store().read(_owner_prefix(_owner(token), token) + "share.json")
    if raw is None:
        raise HTTPException(status_code=404, detail="That link doesn't exist or was deleted.")
    return json.loads(raw.decode("utf-8"))


def share_image(token: str, which: str) -> bytes:
    if which not in ("before", "after"):
        raise HTTPException(status_code=404, detail="Not found.")
    raw = store().read(_owner_prefix(_owner(token), token) + f"{which}.jpg")
    if raw is None:
        raise HTTPException(status_code=404, detail="That link doesn't exist or was deleted.")
    return raw


def list_shares(user_id: str) -> list[dict]:
    files = store()
    shares = []
    for key in files.list(_owner_prefix(user_id)):
        if not key.endswith("/share.json"):
            continue
        try:
            record = json.loads((files.read(key) or b"{}").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        shares.append({"token": record["token"], "name": record["name"], "created": record["created"]})
    shares.sort(key=lambda share: share["created"], reverse=True)
    return shares


def delete_share(user_id: str, token: str) -> None:
    if _owner(token) != user_id:
        raise HTTPException(status_code=404, detail="That link doesn't exist or was deleted.")
    files = store()
    for key in files.list(_owner_prefix(user_id, token)):
        files.delete(key)
    files.delete(_pointer(token))


def delete_all_shares(user_id: str) -> None:
    """Remove the public pointers; the share files go with the rest of the account's folder."""
    files = store()
    for key in files.list(_owner_prefix(user_id)):
        if key.endswith("/share.json"):
            files.delete(_pointer(key.split("/")[3]))


def share_page(token: str, base_url: str) -> str:
    record = load_share(token)
    esc = html.escape
    def brand_code(color: dict) -> str:
        # Labels read "Brand CODE Name"; the name is already shown in bold.
        label = color["label"]
        if label.endswith(" " + color["name"]):
            label = label[: -len(color["name"]) - 1]
        return "" if not label or label == color["name"] else " · " + esc(label)

    rows = "".join(
        f'<li><span class="chip" style="background:{esc(color["hex"])}"></span>'
        f'<span><strong>{esc(color["name"])}</strong><small>{esc(color["surface"])}'
        f"{brand_code(color)} · {esc(color['hex'])}</small></span></li>"
        for color in record["colors"]
    ) or "<li><span>No paint colors were listed.</span></li>"
    base = base_url.rstrip("/")
    values = {
        "{{TITLE}}": esc(record["name"]),
        "{{TOKEN}}": esc(token),
        "{{IMAGE}}": esc(f"{base}/s/{token}/after.jpg"),
        "{{URL}}": esc(f"{base}/s/{token}"),
        "{{WIDTH}}": str(int(record["width"])),
        "{{HEIGHT}}": str(int(record["height"])),
        "{{COLORS}}": rows,
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace(key, value)
    return page
