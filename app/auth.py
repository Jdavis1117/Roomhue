"""Sign in with Google.

The browser gets an ID token from Google's sign-in button and posts it here.
The token is checked against GOOGLE_CLIENT_ID, and the account's stable Google
id is kept in a signed session cookie. Saved rooms are filed under that id.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parent.parent
SESSION_DAYS = 60


def load_env_file() -> None:
    """Read KEY=value lines from .env without overriding real environment variables."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def session_secret() -> str:
    value = os.environ.get("SESSION_SECRET", "").strip()
    if value:
        return value
    path = ROOT / "data" / ".session_secret"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    return value


def verify_google_token(credential: str) -> dict:
    audience = client_id()
    if not audience:
        raise HTTPException(status_code=503, detail="Google sign-in isn't set up on this server yet.")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(credential, google_requests.Request(), audience)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Google sign-in didn't go through. Try again.") from exc
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Google sign-in didn't go through. Try again.")
    return {
        "id": str(claims["sub"]),
        "email": claims.get("email", ""),
        "name": claims.get("name") or claims.get("email", ""),
        "picture": claims.get("picture", ""),
    }


def current_user(request: Request) -> dict | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) and user.get("id") else None


def require_user(request: Request) -> str:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in with Google to use your collection.")
    return user["id"]
