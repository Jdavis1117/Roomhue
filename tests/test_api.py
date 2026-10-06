import base64
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import DiskStore
from app.sample_room import make_sample_room

client = TestClient(app)


def test_sample_session_detects_surfaces():
    response = client.post("/api/sample")
    assert response.status_code == 200
    body = response.json()
    kinds = {surface["kind"] for surface in body["surfaces"]}
    assert {"wall", "floor", "ceiling"} <= kinds
    assert body["width"] == 1200
    assert body["session_id"]


def test_upload_and_recolor():
    room = make_sample_room(640, 400)
    ok, encoded = cv2.imencode(".png", room.bgr)
    assert ok
    response = client.post(
        "/api/sessions",
        files={"file": ("room.png", encoded.tobytes(), "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    wall = next(surface for surface in body["surfaces"] if surface["kind"] == "wall")
    rendered = client.post(
        f"/api/sessions/{body['session_id']}/render",
        json={
            "surfaces": [
                {
                    "mask_png_base64": wall["mask_png_base64"],
                    "color": "#6E7F62",
                    "coverage": 1,
                    "sheen": "matte",
                    "shade": 0,
                }
            ]
        },
    )
    assert rendered.status_code == 200
    assert rendered.headers["content-type"] == "image/png"
    assert len(rendered.content) > 1000


def test_colors_and_index():
    colors = client.get("/api/colors")
    assert colors.status_code == 200
    body = colors.json()
    assert body["colors"] == []
    assert any(brand["id"] == "sherwin-williams" for brand in body["brands"])
    browse = client.get("/api/colors", params={"brand": "sherwin-williams", "limit": 40})
    groups = {color["group"] for color in browse.json()["colors"]}
    assert "Whites" in groups and "Reds" in groups and len(groups) >= 6
    gray = next(color for color in client.get("/api/colors", params={"q": "Agreeable Gray", "brand": "sherwin-williams"}).json()["colors"] if color["code"] == "SW7029")
    assert gray["group"] == "Grays"
    found = client.get("/api/colors", params={"q": "Agreeable Gray", "brand": "sherwin-williams"})
    match = next(color for color in found.json()["colors"] if color["code"] == "SW7029")
    assert match["hex"] == "#D1CBC1"
    assert match["name"] == "Agreeable Gray"
    moore = client.get("/api/colors", params={"q": "Chantilly Lace", "brand": "benjamin-moore"})
    lace = next(color for color in moore.json()["colors"] if color["code"] == "OC-65")
    assert lace["hex"] == "#F4F6F1"
    behr = client.get("/api/colors", params={"q": "Blank Canvas", "brand": "behr"})
    canvas = next(color for color in behr.json()["colors"] if color["code"] == "DC-003")
    assert canvas["hex"] == "#F1EDE1"
    page = client.get("/")
    assert page.status_code == 200
    assert "RoomRoller" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert '"/static/app.js?v=' in page.text and '"/static/styles.css?v=' in page.text


def test_lines_split_the_photo():
    room = make_sample_room(640, 400)
    ok, encoded = cv2.imencode(".png", room.bgr)
    assert ok
    created = client.post(
        "/api/sessions",
        files={"file": ("room.png", encoded.tobytes(), "image/png")},
    )
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    split = client.post(
        f"/api/sessions/{session_id}/lines",
        json={"segments": [[320, 20, 320, 380]]},
    )
    assert split.status_code == 200, split.text
    assert len(split.json()["surfaces"]) >= 2


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    monkeypatch.setattr("app.collection.store", lambda: DiskStore(tmp_path))

    def sign_in(user_id="user-1"):
        monkeypatch.setattr(
            "app.auth.verify_google_token",
            lambda credential: {"id": user_id, "email": f"{user_id}@example.com", "name": user_id, "picture": ""},
        )
        browser = TestClient(app)
        response = browser.post("/api/auth/google", json={"credential": "token"})
        assert response.status_code == 200, response.text
        return browser

    return sign_in


def _room_payload(height=80, width=120):
    mask = np.zeros((height, width), np.uint8)
    mask[10:50, 10:60] = 255
    ok, mask_png = cv2.imencode(".png", mask)
    assert ok
    return {
        "name": "North wall",
        "surfaces": [
            {
                "name": "Wall 1",
                "kind": "wall",
                "color": "#6E7F62",
                "color_label": "sample",
                "sheen": "matte",
                "coverage": 0.8,
                "shade": -2,
                "included": True,
                "mask_png_base64": base64.b64encode(mask_png.tobytes()).decode("ascii"),
            }
        ],
    }


def _session(browser):
    image = np.full((80, 120, 3), 180, np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    created = browser.post("/api/sessions", files={"file": ("room.png", encoded.tobytes(), "image/png")})
    assert created.status_code == 200
    return created.json()["session_id"]


def test_collection_needs_sign_in(signed_in):
    browser = TestClient(app)
    assert browser.get("/api/auth/me").json()["user"] is None
    assert browser.get("/api/collection").status_code == 401
    session_id = _session(browser)
    assert browser.post(f"/api/sessions/{session_id}/collection", json=_room_payload()).status_code == 401


def test_collection_saves_and_reopens(signed_in):
    browser = signed_in()
    assert browser.get("/api/auth/me").json()["user"]["id"] == "user-1"
    session_id = _session(browser)
    payload = _room_payload()
    saved = browser.post(f"/api/sessions/{session_id}/collection", json=payload)
    assert saved.status_code == 200, saved.text
    room_id = saved.json()["id"]
    listed = browser.get("/api/collection")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "North wall"
    assert listed.json()[0]["walls"] == 1
    thumb = browser.get(f"/api/collection/{room_id}/thumb")
    assert thumb.status_code == 200
    opened = browser.post(f"/api/collection/{room_id}/open")
    assert opened.status_code == 200
    surface = opened.json()["surfaces"][0]
    assert surface["color"] == "#6E7F62"
    assert surface["sheen"] == "matte"
    assert surface["shade"] == -2
    payload["surfaces"][0]["color"] = "#112233"
    updated = browser.put(f"/api/collection/{room_id}?session_id={opened.json()['session_id']}", json=payload)
    assert updated.status_code == 200
    again = browser.post(f"/api/collection/{room_id}/open")
    assert again.json()["surfaces"][0]["color"] == "#112233"
    removed = browser.delete(f"/api/collection/{room_id}")
    assert removed.status_code == 200
    assert browser.get("/api/collection").json() == []


def test_collection_survives_sign_out_and_back_in(signed_in):
    browser = signed_in("user-1")
    saved = browser.post(f"/api/sessions/{_session(browser)}/collection", json=_room_payload())
    assert saved.status_code == 200
    assert browser.post("/api/auth/logout").status_code == 200
    assert browser.get("/api/collection").status_code == 401
    later = signed_in("user-1")
    assert [room["id"] for room in later.get("/api/collection").json()] == [saved.json()["id"]]


def test_collections_are_private_to_each_account(signed_in):
    first = signed_in("user-1")
    room_id = first.post(f"/api/sessions/{_session(first)}/collection", json=_room_payload()).json()["id"]
    second = signed_in("user-2")
    assert second.get("/api/collection").json() == []
    assert second.post(f"/api/collection/{room_id}/open").status_code == 404
    assert second.get(f"/api/collection/{room_id}/thumb").status_code == 404
    assert second.delete(f"/api/collection/{room_id}").status_code == 404
    assert len(first.get("/api/collection").json()) == 1


def test_collection_imports_saved_folders(signed_in):
    browser = signed_in()
    image = np.full((80, 120, 3), 200, np.uint8)
    mask = np.zeros((80, 120), np.uint8)
    mask[:, :60] = 255
    uploads = []
    for room_id, name, walls in (("0ee9bea3eb40", "LivingRoom1", 1), ("9d228c0aae6a", "LivingRoom2", 4)):
        meta = {
            "id": room_id,
            "name": name,
            "saved_at": "2026-01-01T00:00:00+00:00",
            "surfaces": [{"name": f"Wall {index + 1}", "kind": "wall", "mask": f"mask-{index}.png"} for index in range(walls)],
        }
        uploads.append(("files", (f"{room_id}/room.json", json.dumps(meta).encode("utf-8"))))
        uploads.append(("files", (f"{room_id}/photo.png", cv2.imencode(".png", image)[1].tobytes())))
        for index in range(walls):
            uploads.append(("files", (f"{room_id}/mask-{index}.png", cv2.imencode(".png", mask)[1].tobytes())))
    imported = browser.post("/api/collection/import", files=uploads)
    assert imported.status_code == 200, imported.text
    names = {room["name"] for room in imported.json()["rooms"]}
    assert names == {"LivingRoom1", "LivingRoom2"}
    listed = {room["name"]: room for room in browser.get("/api/collection").json()}
    assert listed["LivingRoom2"]["walls"] == 4
    assert listed["LivingRoom1"]["walls"] == 1
    opened = browser.post(f"/api/collection/{listed['LivingRoom2']['id']}/open")
    assert opened.status_code == 200
    assert len(opened.json()["surfaces"]) == 4
