"""Сканирование музыкальных каталогов."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from djmaker.domain.models import EmbeddedArtwork, InspectedAudio, ScanStats
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.services.artwork import ArtworkCache, ArtworkCacheError
from djmaker.services.audio_tags import AudioTagError, AudioTagService
from djmaker.services.drop_import import (
    AUDIO_EXTENSIONS,
    DropImportPlan,
    plan_drop_import,
)
from djmaker.services.tasks import TaskControl


LOGGER = logging.getLogger(__name__)


class ScanError(RuntimeError):
    """Фатальная ошибка сканирования корневой папки."""


class LibraryScanner:
    """Индексирует музыкальные файлы в SQLite-медиатеку."""

    def __init__(
        self,
        database: LibraryDatabase,
        tags: AudioTagService,
        artwork_cache: ArtworkCache,
    ) -> None:
        self.database = database
        self.tags = tags
        self.artwork_cache = artwork_cache

    def scan(
        self,
        root: Path,
        *,
        task: TaskControl | None = None,
        progress: Callable[[ScanStats, Path], None] | None = None,
    ) -> ScanStats:
        """Рекурсивно сканирует папку и синхронизирует её с медиатекой."""
        root = root.expanduser().resolve()
        if not root.exists():
            raise ScanError(f"Папка не существует: {root}")
        if not root.is_dir():
            raise ScanError(f"Указан не каталог: {root}")

        if task is not None:
            task.checkpoint()
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
                    stats.ignored += 1
                    continue
                if task is not None:
                    task.checkpoint()
                stats.discovered += 1
                self._scan_file(root, path, token, stats, task=task)
                if progress is not None:
                    progress(stats, path)

        # Если os.walk не смог прочитать часть дерева, нельзя удалять из БД все
        # «неувиденные» файлы: они могли просто находиться в недоступной директории.
        if task is not None:
            task.checkpoint()

        if walk_errors:
            stats.errors += len(walk_errors)
            for error in walk_errors:
                self.database.record_scan_error(root, root, str(error))
        else:
            stats.removed = self.database.remove_stale(root, token)

        self.database.set_root_scanned(root)
        return stats

    def scan_paths(
        self,
        paths: tuple[Path, ...] | list[Path],
        *,
        task: TaskControl | None = None,
        progress: Callable[[ScanStats, Path], None] | None = None,
    ) -> ScanStats:
        """Импортирует набор dropped-файлов/папок, фильтруя лишнее."""
        plan = plan_drop_import(paths)
        stats = ScanStats(ignored=plan.ignored_count)

        for directory in plan.directories:
            if task is not None:
                task.checkpoint()
            baseline = ScanStats(
                discovered=stats.discovered,
                updated=stats.updated,
                unchanged=stats.unchanged,
                errors=stats.errors,
                removed=stats.removed,
                ignored=stats.ignored,
            )

            def directory_progress(
                local_stats: ScanStats,
                current_path: Path,
                *,
                base: ScanStats = baseline,
            ) -> None:
                if progress is None:
                    return
                progress(self._combined_stats(base, local_stats), current_path)

            local = self.scan(
                directory,
                task=task,
                progress=directory_progress,
            )
            stats = self._combined_stats(stats, local)

        roots = self.database.list_roots()
        for path in plan.files:
            if task is not None:
                task.checkpoint()
            stats.discovered += 1
            token = uuid.uuid4().hex
            root = self._root_for_file(path, roots)
            self._scan_file(root, path, token, stats, task=task)
            if progress is not None:
                progress(stats, path)

        return stats

    @staticmethod
    def plan_paths(paths: tuple[Path, ...] | list[Path]) -> DropImportPlan:
        """Возвращает план Drag&Drop без запуска индексации."""
        return plan_drop_import(paths)

    @staticmethod
    def _root_for_file(path: Path, roots: list[Path]) -> Path:
        """Сохраняет существующий library root при импорте одиночного файла."""
        matches = [root for root in roots if path.is_relative_to(root)]
        if not matches:
            return path.parent
        return max(matches, key=lambda root: len(root.parts))

    @staticmethod
    def _combined_stats(left: ScanStats, right: ScanStats) -> ScanStats:
        return ScanStats(
            discovered=left.discovered + right.discovered,
            updated=left.updated + right.updated,
            unchanged=left.unchanged + right.unchanged,
            errors=left.errors + right.errors,
            removed=left.removed + right.removed,
            ignored=left.ignored + right.ignored,
        )

    def inspect_and_hash(
        self,
        path: Path,
        task: TaskControl | None = None,
    ) -> tuple[InspectedAudio, str, os.stat_result]:
        """Полностью анализирует один файл и вычисляет SHA-256."""
        try:
            if task is not None:
                task.checkpoint()
            stat = path.stat()
        except OSError as exc:
            raise ScanError(f"Не удалось прочитать свойства {path}: {exc}") from exc
        if task is not None:
            task.checkpoint()
        inspected = self.tags.inspect(path)
        return inspected, self.sha256(path, task=task), stat

    @staticmethod
    def sha256(path: Path, task: TaskControl | None = None) -> str:
        """Вычисляет SHA-256 потоково с точками кооперативного прерывания."""
        digest = hashlib.sha256()
        try:
            with path.open("rb") as file_obj:
                while chunk := file_obj.read(1024 * 1024):
                    if task is not None:
                        task.checkpoint()
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError as exc:
            raise ScanError(f"Не удалось вычислить SHA-256 {path}: {exc}") from exc

    def _cache_artwork(
        self,
        source: Path,
        artwork: EmbeddedArtwork | None,
    ) -> tuple[Path | None, bool]:
        """Кэширует встроенную обложку, не ломая индексацию при ошибке кэша."""
        if artwork is None:
            return None, True
        try:
            return self.artwork_cache.store(artwork), True
        except ArtworkCacheError as exc:
            LOGGER.warning("Не удалось кэшировать обложку %s: %s", source, exc)
            return None, False

    def _scan_file(
        self,
        root: Path,
        path: Path,
        token: str,
        stats: ScanStats,
        *,
        task: TaskControl | None = None,
    ) -> None:
        try:
            if task is not None:
                task.checkpoint()
            stat = path.stat()
            signature = self.database.get_signature(path)
            current_signature = (stat.st_size, stat.st_mtime_ns)
            if signature == current_signature:
                self.database.touch_track(path, token)
                stats.unchanged += 1
                return

            inspected = self.tags.inspect(path)
            if task is not None:
                task.checkpoint()
            file_hash = self.sha256(path, task=task)
            artwork_path, artwork_checked = self._cache_artwork(
                path, inspected.artwork
            )
            self.database.upsert_track(
                path=path,
                root=root,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                extension=path.suffix.lower(),
                file_hash=file_hash,
                metadata=inspected.metadata,
                technical=inspected.technical,
                embedded_artwork_path=artwork_path,
                embedded_artwork_checked=artwork_checked,
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
