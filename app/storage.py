"""Where saved rooms are kept.

On Replit the files go to Replit Object Storage, which survives redeploys.
Anywhere else they go to data/collection on this computer. Set
ROOMHUE_STORAGE to "disk" or "replit" to choose explicitly.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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


class ReplitStore:
    def __init__(self) -> None:
        from replit.object_storage import Client

        self.client = Client()

    def read(self, key: str) -> bytes | None:
        if not self.client.exists(key):
            return None
        return self.client.download_as_bytes(key)

    def write(self, key: str, data: bytes) -> None:
        self.client.upload_from_bytes(key, data)

    def list(self, prefix: str) -> list[str]:
        return [item.name for item in self.client.list(prefix=prefix)]

    def delete(self, key: str) -> None:
        self.client.delete(key, ignore_not_found=True)


@lru_cache(maxsize=1)
def store() -> DiskStore | ReplitStore:
    choice = os.environ.get("ROOMHUE_STORAGE", "").strip().lower()
    if not choice:
        choice = "replit" if os.environ.get("REPL_ID") else "disk"
    if choice == "replit":
        return ReplitStore()
    return DiskStore(DISK_ROOT)
