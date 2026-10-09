"""Where saved rooms are kept.

On Replit the files go to Replit Object Storage, which survives redeploys.
Anywhere else they go to data/collection on this computer. Set
ROOMHUE_STORAGE to "disk" or "replit" to choose explicitly, and
ROOMHUE_BUCKET_ID to name the Replit bucket instead of using the default one.
"""

from __future__ import annotations

import functools
import logging
import os
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

log = logging.getLogger("roomhue.storage")
# The full error goes to the server log; people see only this.
UNAVAILABLE = "Saved rooms are temporarily unavailable. Try again in a minute."
ROOT = Path(__file__).resolve().parent.parent
DISK_ROOT = ROOT / "data" / "collection"


class DiskStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return [path.relative_to(self.root).as_posix() for path in base.rglob("*") if path.is_file()]

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def _reported(method):
    """Turn an Object Storage failure into a message that says what went wrong."""

    @functools.wraps(method)
    def run(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("Object Storage %s failed", method.__name__)
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc

    return run


class ReplitStore:
    def __init__(self) -> None:
        from replit.object_storage import Client

        self.client = Client(bucket_id=os.environ.get("ROOMHUE_BUCKET_ID", "").strip() or None)

    @_reported
    def read(self, key: str) -> bytes | None:
        if not self.client.exists(key):
            return None
        return self.client.download_as_bytes(key)

    @_reported
    def write(self, key: str, data: bytes) -> None:
        self.client.upload_from_bytes(key, data)

    @_reported
    def list(self, prefix: str) -> list[str]:
        return [item.name for item in self.client.list(prefix=prefix)]

    @_reported
    def delete(self, key: str) -> None:
        self.client.delete(key, ignore_not_found=True)


@lru_cache(maxsize=1)
def store() -> DiskStore | ReplitStore:
    choice = os.environ.get("ROOMHUE_STORAGE", "").strip().lower()
    if not choice:
        choice = "replit" if os.environ.get("REPL_ID") else "disk"
    if choice == "replit":
        try:
            return ReplitStore()
        except Exception as exc:
            log.exception("Object Storage could not start")
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
    return DiskStore(DISK_ROOT)
