from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select

from .config import get_settings


SAFE_EXTENSION = re.compile(r"^\.[a-zA-Z0-9]{1,8}$")


class StorageKeys:
    def media_key(self, project_id: str, asset_id: str, original_name: str) -> str:
        suffix = Path(original_name).suffix.lower()
        if not SAFE_EXTENSION.match(suffix):
            suffix = ""
        return f"media/{project_id}/{asset_id}{suffix}"

    def report_key(self, project_id: str, report_id: str) -> str:
        return f"reports/{project_id}/{report_id}.pdf"


class LocalDiskStorage(StorageKeys):
    provider = "local_disk"

    def __init__(self, root: Path | None = None):
        self.root = (root or get_settings().media_root).resolve()
        self.media_root = self.root / "media"
        self.reports_root = self.root / "reports"
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.reports_root.mkdir(parents=True, exist_ok=True)

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("Nieprawidłowy klucz pliku")
        return candidate

    def write_stream(
        self, storage_key: str, stream: BinaryIO, max_bytes: int
    ) -> tuple[int, str]:
        destination = self.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".uploading")
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Plik przekracza dozwolony rozmiar")
                    digest.update(chunk)
                    output.write(chunk)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    def write_bytes(self, storage_key: str, content: bytes) -> tuple[int, str]:
        destination = self.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return len(content), hashlib.sha256(content).hexdigest()

    def read_bytes(self, storage_key: str) -> bytes:
        return self.resolve(storage_key).read_bytes()

    def open(self, storage_key: str) -> BinaryIO:
        return self.resolve(storage_key).open("rb")

    def exists(self, storage_key: str) -> bool:
        return self.resolve(storage_key).is_file()

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)


class DatabaseStorage(StorageKeys):
    provider = "database"

    @staticmethod
    def _session():
        from .db import SessionLocal

        return SessionLocal()

    def write_stream(
        self, storage_key: str, stream: BinaryIO, max_bytes: int
    ) -> tuple[int, str]:
        buffer = io.BytesIO()
        size = 0
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Plik przekracza dozwolony rozmiar")
            digest.update(chunk)
            buffer.write(chunk)
        self._save(storage_key, buffer.getvalue(), size, digest.hexdigest())
        return size, digest.hexdigest()

    def write_bytes(self, storage_key: str, content: bytes) -> tuple[int, str]:
        digest = hashlib.sha256(content).hexdigest()
        self._save(storage_key, content, len(content), digest)
        return len(content), digest

    def _save(self, storage_key: str, content: bytes, size: int, digest: str) -> None:
        from . import models

        with self._session() as db:
            blob = db.get(models.StoredBlob, storage_key)
            if blob:
                blob.content = content
                blob.size_bytes = size
                blob.sha256 = digest
            else:
                db.add(
                    models.StoredBlob(
                        storage_key=storage_key,
                        content=content,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            db.commit()

    def read_bytes(self, storage_key: str) -> bytes:
        from . import models

        with self._session() as db:
            content = db.scalar(
                select(models.StoredBlob.content).where(
                    models.StoredBlob.storage_key == storage_key
                )
            )
            if content is None:
                raise FileNotFoundError(storage_key)
            return content

    def open(self, storage_key: str) -> BinaryIO:
        return io.BytesIO(self.read_bytes(storage_key))

    def exists(self, storage_key: str) -> bool:
        from . import models

        with self._session() as db:
            return (
                db.scalar(
                    select(models.StoredBlob.storage_key).where(
                        models.StoredBlob.storage_key == storage_key
                    )
                )
                is not None
            )

    def delete(self, storage_key: str) -> None:
        from . import models

        with self._session() as db:
            blob = db.get(models.StoredBlob, storage_key)
            if blob:
                db.delete(blob)
                db.commit()


settings = get_settings()
storage = (
    DatabaseStorage()
    if settings.storage_provider.lower() == "database"
    else LocalDiskStorage()
)
