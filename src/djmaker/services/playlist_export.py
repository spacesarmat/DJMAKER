"""Экспорт снимка плейлиста с оригинальными файлами либо абсолютными ссылками."""

from __future__ import annotations

import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from djmaker.domain.models import TrackRecord
from djmaker.services.tasks import TaskControl


class PlaylistExportError(RuntimeError):
    """Экспорт не завершён; готовый результат не выдан."""


@dataclass(frozen=True, slots=True)
class ExportTrack:
    path: Path
    title: str
    duration: float | None

    @classmethod
    def from_track(cls, track: TrackRecord) -> ExportTrack:
        title = " - ".join(
            filter(None, (track.metadata.artist.strip(), track.metadata.title.strip()))
        )
        return cls(track.path, title or track.path.stem, track.technical.duration)


@dataclass(frozen=True, slots=True)
class PlaylistExportRequest:
    name: str
    tracks: tuple[ExportTrack, ...]
    destination: Path
    copy_files: bool


def safe_component(value: str) -> str:
    """Безопасное имя для Windows/macOS с ограниченной длиной."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:80].rstrip(" .")
    if not name:
        return "Playlist"
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def _new_folder(parent: Path, name: str) -> Path:
    for index in range(1, 10000):
        candidate = parent / (name if index == 1 else f"{name} ({index})")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise PlaylistExportError("Не удалось подобрать свободное имя папки экспорта")


def export_playlist(
    request: PlaylistExportRequest,
    *,
    task: TaskControl,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """Создаёт новую папку; при ошибке/остановке удаляет только свои копии.

    Копирование без перекодирования. Проверки отмены выполняются между блоками
    по 1 МиБ. Возобновление после остановки запускает экспорт снимка с начала.
    """
    task.checkpoint()
    if not request.tracks:
        raise PlaylistExportError("В плейлисте нет треков")
    parent = request.destination.expanduser().resolve()
    if not parent.is_dir():
        raise PlaylistExportError("Папка назначения недоступна")
    sources: list[Path] = []
    for track in request.tracks:
        task.checkpoint()
        source = track.path.expanduser().resolve()
        if not source.is_file():
            raise PlaylistExportError(f"Исходный файл недоступен: {source}")
        if "\n" in str(source) or "\r" in str(source):
            raise PlaylistExportError(f"Путь содержит перенос строки: {source!s}")
        sources.append(source)

    folder = _new_folder(parent, safe_component(request.name))
    try:
        music = folder / "Music"
        if request.copy_files:
            music.mkdir()
        lines = ["#EXTM3U"]
        for index, (track, source) in enumerate(zip(request.tracks, sources), 1):
            task.checkpoint()
            if progress:
                progress(index - 1, f"{index}/{len(sources)} · {source.name}")
            if request.copy_files:
                # Порядковый префикс исключает коллизии одинаковых имён и регистра.
                suffix = re.sub(r"[^a-zA-Z0-9.]", "_", source.suffix)[:16]
                target = music / f"{index:04d}_{safe_component(source.stem)}{suffix}"
                before = source.stat()
                copied = 0
                with source.open("rb") as reader, target.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        task.checkpoint()
                        writer.write(chunk)
                        copied += len(chunk)
                after = source.stat()
                if copied != before.st_size or (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise PlaylistExportError(
                        f"Файл изменился во время копирования: {source}"
                    )
                reference = target.relative_to(folder).as_posix()
            else:
                reference = str(source)
            duration = track.duration
            seconds = (
                int(duration)
                if duration is not None and math.isfinite(duration) and duration >= 0
                else -1
            )
            title = track.title.replace("\r", " ").replace("\n", " ")
            lines.extend([f"#EXTINF:{seconds},{title}", reference])
            if progress:
                progress(index, f"{index}/{len(sources)} · {source.name}")
        task.checkpoint()
        output = folder / f"{safe_component(request.name)}.m3u8"
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        task.checkpoint()
        return output
    except BaseException as exc:
        try:
            shutil.rmtree(folder)
        except OSError as cleanup_error:
            raise PlaylistExportError(
                f"Экспорт прерван: {exc}. Не удалось удалить неполную папку {folder}: {cleanup_error}"
            ) from exc
        raise
