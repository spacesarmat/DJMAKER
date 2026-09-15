"""Сканирование музыкальных каталогов."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path

from djmaker.domain.models import InspectedAudio, ScanStats
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.services.audio_tags import AudioTagError, AudioTagService


LOGGER = logging.getLogger(__name__)

# Mutagen умеет читать больше форматов, чем базовый набор DJMAKER. Здесь перечислены
# расширения, которые имеет смысл считать музыкальными при обходе файловой системы.
AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".ac3",
        ".aif",
        ".aiff",
        ".ape",
        ".asf",
        ".dff",
        ".dsf",
        ".flac",
        ".m4a",
        ".m4b",
        ".mp3",
        ".mp4",
        ".mpc",
        ".mpp",
        ".ofr",
        ".ofs",
        ".oga",
        ".ogg",
        ".opus",
        ".spx",
        ".tak",
        ".tta",
        ".wav",
        ".wave",
        ".wma",
        ".wv",
    }
)


class ScanError(RuntimeError):
    """Фатальная ошибка сканирования корневой папки."""


class LibraryScanner:
    """Индексирует музыкальные файлы в SQLite-медиатеку."""

    def __init__(self, database: LibraryDatabase, tags: AudioTagService) -> None:
        self.database = database
        self.tags = tags

    def scan(self, root: Path) -> ScanStats:
        """Рекурсивно сканирует папку и синхронизирует её с медиатекой."""
        root = root.expanduser().resolve()
        if not root.exists():
            raise ScanError(f"Папка не существует: {root}")
        if not root.is_dir():
            raise ScanError(f"Указан не каталог: {root}")

        self.database.add_root(root)
        token = uuid.uuid4().hex
        stats = ScanStats()
        walk_errors: list[OSError] = []

        def on_walk_error(error: OSError) -> None:
            walk_errors.append(error)
            LOGGER.warning("Ошибка обхода %s: %s", root, error)

        for directory, _, filenames in os.walk(root, onerror=on_walk_error):
            directory_path = Path(directory)
            for filename in filenames:
                path = directory_path / filename
                if path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                stats.discovered += 1
                self._scan_file(root, path, token, stats)

        # Если os.walk не смог прочитать часть дерева, нельзя удалять из БД все
        # «неувиденные» файлы: они могли просто находиться в недоступной директории.
        if walk_errors:
            stats.errors += len(walk_errors)
            for error in walk_errors:
                self.database.record_scan_error(root, root, str(error))
        else:
            stats.removed = self.database.remove_stale(root, token)

        self.database.set_root_scanned(root)
        return stats

    def inspect_and_hash(self, path: Path) -> tuple[InspectedAudio, str, os.stat_result]:
        """Полностью анализирует один файл и вычисляет SHA-256."""
        try:
            stat = path.stat()
        except OSError as exc:
            raise ScanError(f"Не удалось прочитать свойства {path}: {exc}") from exc
        inspected = self.tags.inspect(path)
        return inspected, self.sha256(path), stat

    @staticmethod
    def sha256(path: Path) -> str:
        """Вычисляет полный SHA-256 файла потоково, без загрузки в память."""
        try:
            with path.open("rb") as file_obj:
                return hashlib.file_digest(file_obj, "sha256").hexdigest()
        except OSError as exc:
            raise ScanError(f"Не удалось вычислить SHA-256 {path}: {exc}") from exc

    def _scan_file(
        self,
        root: Path,
        path: Path,
        token: str,
        stats: ScanStats,
    ) -> None:
        try:
            stat = path.stat()
            signature = self.database.get_signature(path)
            current_signature = (stat.st_size, stat.st_mtime_ns)
            if signature == current_signature:
                self.database.touch_track(path, token)
                stats.unchanged += 1
                return

            inspected = self.tags.inspect(path)
            file_hash = self.sha256(path)
            self.database.upsert_track(
                path=path,
                root=root,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                extension=path.suffix.lower(),
                file_hash=file_hash,
                metadata=inspected.metadata,
                technical=inspected.technical,
                scan_token=token,
            )
            stats.updated += 1
        except (OSError, AudioTagError, ScanError, RuntimeError) as exc:
            stats.errors += 1
            LOGGER.exception("Не удалось проиндексировать %s", path)
            try:
                self.database.record_scan_error(root, path, str(exc))
            except RuntimeError:
                LOGGER.exception("Не удалось сохранить ошибку сканирования %s", path)
