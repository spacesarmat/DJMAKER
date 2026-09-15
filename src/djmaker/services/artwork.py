"""Локальный content-addressed кэш встроенных обложек."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from djmaker.domain.models import EmbeddedArtwork


MAX_ARTWORK_BYTES = 32 * 1024 * 1024


class ArtworkCacheError(RuntimeError):
    """Ошибка сохранения встроенной обложки в локальный кэш."""


class ArtworkCache:
    """Сохраняет обложки по SHA-256 и возвращает стабильный локальный путь."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._write_lock = threading.Lock()

    def store(self, artwork: EmbeddedArtwork | None) -> Path | None:
        """Сохраняет обложку атомарно; одинаковые данные не дублируются."""
        if artwork is None or not artwork.data:
            return None
        if len(artwork.data) > MAX_ARTWORK_BYTES:
            raise ArtworkCacheError(
                f"Встроенная обложка слишком большая: {len(artwork.data)} байт"
            )

        extension = _image_extension(artwork.mime_type, artwork.data)
        digest = hashlib.sha256(artwork.data).hexdigest()
        target = self.root / f"{digest}{extension}"

        with self._write_lock:
            if target.is_file():
                return target

            temporary: Path | None = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.root / (
                    f".{digest}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                temporary.write_bytes(artwork.data)
                temporary.replace(target)
            except OSError as exc:
                raise ArtworkCacheError(
                    f"Не удалось сохранить встроенную обложку: {exc}"
                ) from exc
            finally:
                try:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
                except OSError:
                    pass

        return target


def _image_extension(mime_type: str, data: bytes) -> str:
    mime = mime_type.strip().lower()
    by_mime = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if mime in by_mime:
        return by_mime[mime]
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    return ".img"
