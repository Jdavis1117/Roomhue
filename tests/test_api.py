import base64
import json
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.auth import LEGAL_VERSION
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
    assert body["width"] == 1400
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
    test_store = DiskStore(tmp_path)
    for module in ("app.collection", "app.shares"):
        monkeypatch.setattr(f"{module}.store", lambda: test_store)
    monkeypatch.setattr("app.collection._epochs", {})

    def sign_in(user_id="user-1"):
        monkeypatch.setattr(
            "app.auth.verify_google_token",
            lambda credential: {"id": user_id, "name": user_id},
        )
        browser = TestClient(app)
        response = browser.post(
            "/api/auth/google",
            json={"credential": "token", "accepted_terms": True, "terms_version": LEGAL_VERSION},
        )
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


def test_sign_in_requires_agreeing_to_terms(signed_in, monkeypatch):
    monkeypatch.setattr("app.auth.verify_google_token", lambda credential: {"id": "user-9", "name": "Pat"})
    browser = TestClient(app)
    refused = browser.post("/api/auth/google", json={"credential": "token"})
    assert refused.status_code == 400
    stale = browser.post("/api/auth/google", json={"credential": "token", "accepted_terms": True, "terms_version": "2000-01-01"})
    assert stale.status_code == 400
    assert browser.get("/api/auth/me").json()["user"] is None


def test_sign_in_keeps_only_id_and_name_and_records_consent(signed_in, tmp_path):
    browser = signed_in("user-1")
    me = browser.get("/api/auth/me").json()
    assert me["user"] == {"id": "user-1", "name": "user-1"}
    assert me["legal_version"] == LEGAL_VERSION
    record = json.loads((tmp_path / "users" / "user-1" / "account.json").read_text())
    assert record["terms_version"] == LEGAL_VERSION
    assert record["accepted_at"] and record["first_accepted_at"]


def test_old_session_fields_are_not_exposed(signed_in, monkeypatch):
    monkeypatch.setattr(
        "app.auth.verify_google_token",
        lambda credential: {"id": "user-3", "name": "Sam", "email": "sam@example.com", "picture": "https://x"},
    )
    browser = TestClient(app)
    browser.post("/api/auth/google", json={"credential": "t", "accepted_terms": True, "terms_version": LEGAL_VERSION})
    assert browser.get("/api/auth/me").json()["user"] == {"id": "user-3", "name": "Sam"}


def test_delete_account_removes_everything_and_signs_out(signed_in, tmp_path):
    browser = signed_in("user-1")
    browser.post(f"/api/sessions/{_session(browser)}/collection", json=_room_payload())
    other = signed_in("user-2")
    other.post(f"/api/sessions/{_session(other)}/collection", json=_room_payload())
    deleted = browser.delete("/api/account")
    assert deleted.status_code == 200 and deleted.json()["removed"] >= 5
    assert not [path for path in (tmp_path / "users" / "user-1").rglob("*") if path.is_file()]
    assert browser.get("/api/auth/me").json()["user"] is None
    assert browser.delete("/api/account").status_code == 401
    assert len(other.get("/api/collection").json()) == 1


def test_legal_pages_are_served():
    for path, title in (("/privacy", "Privacy Policy"), ("/terms", "Terms of Service"), ("/cookies", "Cookie Policy")):
        page = client.get(path)
        assert page.status_code == 200
        assert f"<h1>{title}</h1>" in page.text
        assert "privacy@roomroller.com" in page.text


def test_home_page_does_not_load_google_until_sign_in():
    page = client.get("/").text
    assert "accounts.google.com" not in page
    assert 'id="consent"' in page and 'id="agree"' in page


FAVORITE = {"brand": "Sherwin-Williams", "brandId": "sherwin-williams", "code": "SW7029", "name": "Agreeable Gray", "hex": "#D1CBC1"}


def test_favorites_need_sign_in():
    browser = TestClient(app)
    assert browser.get("/api/favorites").status_code == 401
    assert browser.put("/api/favorites", json={"favorites": [FAVORITE]}).status_code == 401


def test_favorites_save_and_reload_per_account(signed_in):
    first = signed_in("user-1")
    assert first.get("/api/favorites").json() == {"favorites": []}
    custom = {"brand": "Custom", "brandId": "custom", "code": "#336699", "name": "#336699", "hex": "#336699"}
    saved = first.put("/api/favorites", json={"favorites": [FAVORITE, custom, dict(FAVORITE)]})
    assert saved.status_code == 200
    assert saved.json()["favorites"] == [FAVORITE, custom]
    later = signed_in("user-1")
    assert later.get("/api/favorites").json()["favorites"] == [FAVORITE, custom]
    other = signed_in("user-2")
    assert other.get("/api/favorites").json()["favorites"] == []


def test_favorites_reject_bad_colors(signed_in):
    browser = signed_in()
    assert browser.put("/api/favorites", json={"favorites": [{**FAVORITE, "hex": "red"}]}).status_code == 422
    assert browser.put("/api/favorites", json={"favorites": [{**FAVORITE, "name": "x" * 200}]}).status_code == 422
    too_many = [{**FAVORITE, "code": f"SW{i}"} for i in range(301)]
    assert browser.put("/api/favorites", json={"favorites": too_many}).status_code == 422


def test_deleting_account_removes_favorites(signed_in):
    browser = signed_in("user-1")
    browser.put("/api/favorites", json={"favorites": [FAVORITE]})
    browser.delete("/api/account")
    again = signed_in("user-1")
    assert again.get("/api/favorites").json()["favorites"] == []


def test_coordinating_colors_include_official_pairings_and_suggestions():
    body = client.get("/api/colors/coordinate", params={"brand": "sherwin-williams", "code": "SW7029"}).json()
    assert body["color"]["name"] == "Agreeable Gray"
    assert [color["code"] for color in body["official"]] == ["SW7006", "SW9004", "SW7028"]
    roles = [color["role"] for color in body["suggested"]]
    assert roles[:3] == ["Trim white", "Lighter", "Darker"] and roles.count("Accent") == 2
    codes = [color["code"] for color in body["official"] + body["suggested"]]
    assert len(codes) == len(set(codes)) and "SW7029" not in codes
    assert all(color["brandId"] == "sherwin-williams" for color in body["suggested"])


def test_coordinating_colors_work_for_brands_without_official_pairings():
    body = client.get("/api/colors/coordinate", params={"brand": "behr", "code": "N240-1"}).json()
    assert body["official"] == []
    assert len(body["suggested"]) == 5
    assert client.get("/api/colors/coordinate", params={"brand": "behr", "code": "NOPE"}).status_code == 404


def _jpeg(color, size=(60, 90)):
    image = np.zeros((size[0], size[1], 3), np.uint8)
    image[:] = color
    return cv2.imencode(".jpg", image)[1].tobytes()


def _share(browser, name="Living room", colors=None):
    meta = {"name": name, "colors": colors if colors is not None else [{"surface": "Wall 1", "name": "Agreeable Gray", "label": "Sherwin-Williams SW7029 Agreeable Gray", "hex": "#D1CBC1"}]}
    return browser.post(
        "/api/shares",
        files={"before": ("before.jpg", _jpeg((200, 200, 200)), "image/jpeg"), "after": ("after.jpg", _jpeg((90, 120, 160)), "image/jpeg")},
        data={"meta": json.dumps(meta)},
    )


def test_sharing_needs_sign_in(signed_in):
    assert _share(TestClient(app)).status_code == 401


def test_shared_link_is_public_and_can_be_deleted(signed_in):
    owner = signed_in("user-1")
    created = _share(owner, name="Living <room>")
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    stranger = TestClient(app)
    page = stranger.get(f"/s/{token}")
    assert page.status_code == 200
    assert "Living &lt;room&gt;" in page.text and "<room>" not in page.text
    assert "Agreeable Gray" in page.text and "#D1CBC1" in page.text
    assert "noindex" in page.headers["x-robots-tag"]
    assert stranger.get(f"/s/{token}/after.jpg").headers["content-type"] == "image/jpeg"
    assert stranger.get(f"/s/{token}/secret.jpg").status_code == 404
    assert [share["token"] for share in owner.get("/api/shares").json()] == [token]
    assert signed_in("user-2").delete(f"/api/shares/{token}").status_code == 404
    assert owner.delete(f"/api/shares/{token}").status_code == 200
    assert stranger.get(f"/s/{token}").status_code == 404


def test_shares_reject_bad_input(signed_in):
    browser = signed_in()
    bad = browser.post("/api/shares", files={"before": ("b.jpg", b"nope", "image/jpeg"), "after": ("a.jpg", _jpeg((1, 2, 3)), "image/jpeg")})
    assert bad.status_code == 400
    mismatch = browser.post(
        "/api/shares",
        files={"before": ("b.jpg", _jpeg((1, 2, 3), (60, 90)), "image/jpeg"), "after": ("a.jpg", _jpeg((1, 2, 3), (40, 90)), "image/jpeg")},
    )
    assert mismatch.status_code == 400
    weird = _share(browser, colors=[{"hex": "javascript:alert(1)"}, {"hex": "#123456", "name": "<b>x</b>"}])
    page = TestClient(app).get(f"/s/{weird.json()['token']}").text
    assert "javascript:" not in page and "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
    assert TestClient(app).get("/s/../../etc").status_code == 404


def test_deleting_account_removes_shared_links(signed_in):
    owner = signed_in("user-1")
    token = _share(owner).json()["token"]
    owner.delete("/api/account")
    assert TestClient(app).get(f"/s/{token}").status_code == 404



def _bomb_png(side=9000):
    """A uniform image: tiny on disk, huge in memory."""
    row = np.full((1, side, 3), 200, np.uint8)
    ok, buf = cv2.imencode(".png", np.repeat(row, side, axis=0), [cv2.IMWRITE_PNG_COMPRESSION, 9])
    return buf.tobytes()


def test_decompression_bombs_are_refused_before_decoding(signed_in):
    bomb = _bomb_png()
    assert len(bomb) < 400_000
    upload = client.post("/api/sessions", files={"file": ("bomb.png", bomb, "image/png")})
    assert upload.status_code == 413, upload.text
    restore = client.post("/api/sessions/restore", files={"file": ("bomb.png", bomb, "image/png")})
    assert restore.status_code == 413

    session_id = _session(client)
    mask = base64.b64encode(bomb).decode("ascii")
    render = client.post(f"/api/sessions/{session_id}/render", json={"surfaces": [{"mask_png_base64": mask, "color": "#336699"}]})
    assert render.status_code in (400, 413), render.text

    browser = signed_in()
    payload = _room_payload()
    payload["surfaces"][0]["mask_png_base64"] = mask
    assert browser.post(f"/api/sessions/{_session(browser)}/collection", json=payload).status_code in (400, 413)
    shared = browser.post(
        "/api/shares",
        files={"before": ("b.png", bomb, "image/png"), "after": ("a.png", bomb, "image/png")},
        data={"meta": "{}"},
    )
    assert shared.status_code == 413
    meta = {"id": "0ee9bea3eb40", "name": "Bomb", "surfaces": []}
    imported = browser.post(
        "/api/collection/import",
        files=[("files", ("r/room.json", json.dumps(meta).encode(), "application/json")), ("files", ("r/photo.png", bomb, "image/png"))],
    )
    assert imported.status_code == 413


def test_oversized_requests_are_refused():
    big = b"x" * (33 * 1024 * 1024)
    response = client.post("/api/sessions", files={"file": ("big.jpg", big, "image/jpeg")})
    assert response.status_code == 413

    def chunks():
        for _ in range(40):
            yield b"y" * (1024 * 1024)

    # Streamed without a length, as a photo upload; the limit has to catch it while reading.
    streamed = client.post("/api/sessions/restore", content=chunks(), headers={"content-type": "multipart/form-data; boundary=xyz"})
    assert streamed.status_code == 413


def test_too_many_walls_or_huge_masks_are_refused(signed_in):
    session_id = _session(client)
    surface = {"mask_png_base64": "A" * 10, "color": "#123456"}
    assert client.post(f"/api/sessions/{session_id}/render", json={"surfaces": [surface] * 41}).status_code == 422
    huge = {"mask_png_base64": "A" * 4_000_004, "color": "#123456"}
    assert client.post(f"/api/sessions/{session_id}/render", json={"surfaces": [huge]}).status_code == 422


def test_a_slow_photo_does_not_freeze_other_requests(monkeypatch):
    def slow(image):
        time.sleep(1.5)
        return []

    monkeypatch.setattr("app.main.detect_surfaces", slow)
    image = np.full((80, 120, 3), 180, np.uint8)
    photo = cv2.imencode(".png", image)[1].tobytes()
    worker = threading.Thread(target=lambda: client.post("/api/sessions", files={"file": ("room.png", photo, "image/png")}))
    worker.start()
    time.sleep(0.2)
    started = time.perf_counter()
    assert client.get("/api/auth/me").status_code == 200
    waited = time.perf_counter() - started
    worker.join()
    assert waited < 0.8, f"another request waited {waited:.2f}s behind the upload"


def test_dropped_photos_can_be_restored(monkeypatch):
    image = np.full((80, 120, 3), 180, np.uint8)
    photo = cv2.imencode(".png", image)[1].tobytes()
    monkeypatch.setattr("app.main.SESSION_BUDGET_BYTES", image.nbytes * 2)
    first = _session(client)
    second = _session(client)
    assert client.post(f"/api/sessions/{first}/wand", json={"x": 10, "y": 10}).status_code == 200
    _session(client)  # pushes out the least recently used one, which is now `second`
    assert client.post(f"/api/sessions/{second}/wand", json={"x": 10, "y": 10}).status_code == 410
    assert client.post(f"/api/sessions/{first}/wand", json={"x": 10, "y": 10}).status_code == 200
    restored = client.post("/api/sessions/restore", files={"file": ("photo.png", photo, "image/png")})
    assert restored.status_code == 200
    body = restored.json()
    assert (body["width"], body["height"]) == (120, 80)
    assert client.post(f"/api/sessions/{body['session_id']}/wand", json={"x": 10, "y": 10}).status_code == 200


def test_security_headers_are_sent(signed_in):
    for path in ("/", "/privacy", "/api/colors"):
        headers = client.get(path).headers
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "https://accounts.google.com/gsi/client" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" in client.get("/", headers={"x-forwarded-proto": "https"}).headers
    browser = signed_in()
    token = _share(browser).json()["token"]
    assert TestClient(app).get(f"/s/{token}/after.jpg").headers["cache-control"] == "public, max-age=60"


def test_signing_out_revokes_copied_cookies(signed_in):
    browser = signed_in("user-1")
    stolen = TestClient(app)
    stolen.cookies.update(browser.cookies)
    assert stolen.get("/api/collection").status_code == 200
    browser.post("/api/auth/logout")
    assert stolen.get("/api/collection").status_code == 401
    assert signed_in("user-1").get("/api/collection").status_code == 200


def test_deleting_the_account_revokes_copied_cookies(signed_in):
    browser = signed_in("user-1")
    stolen = TestClient(app)
    stolen.cookies.update(browser.cookies)
    browser.delete("/api/account")
    assert stolen.get("/api/collection").status_code == 401
    assert stolen.put("/api/favorites", json={"favorites": [FAVORITE]}).status_code == 401


def test_storage_errors_show_a_generic_message():
    from fastapi import HTTPException as Raised

    from app.storage import UNAVAILABLE, _reported

    @_reported
    def broken():
        raise ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=1106)")

    try:
        broken()
    except Raised as exc:
        assert exc.status_code == 503 and exc.detail == UNAVAILABLE and "127.0.0.1" not in exc.detail
    else:
        raise AssertionError("expected a 503")


def test_rooms_can_be_renamed(signed_in):
    browser = signed_in("user-1")
    room_id = browser.post(f"/api/sessions/{_session(browser)}/collection", json=_room_payload()).json()["id"]
    renamed = browser.patch(f"/api/collection/{room_id}", json={"name": "  Upstairs   hall  "})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Upstairs hall"
    assert browser.get("/api/collection").json()[0]["name"] == "Upstairs hall"
    assert browser.post(f"/api/collection/{room_id}/open").json()["name"] == "Upstairs hall"
    assert signed_in("user-2").patch(f"/api/collection/{room_id}", json={"name": "Mine now"}).status_code == 404
    assert browser.patch(f"/api/collection/{room_id}", json={"name": ""}).status_code == 422
    assert browser.patch(f"/api/collection/{room_id}", json={"name": "x" * 201}).status_code == 422
    assert TestClient(app).patch(f"/api/collection/{room_id}", json={"name": "Nope"}).status_code == 401
