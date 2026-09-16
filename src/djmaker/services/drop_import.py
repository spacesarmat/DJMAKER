"""Подготовка путей, полученных через desktop Drag&Drop."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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


@dataclass(frozen=True, slots=True)
class DropImportPlan:
    """Нормализованный набор объектов одного drop-события."""

    directories: tuple[Path, ...] = ()
    files: tuple[Path, ...] = ()
    ignored: tuple[Path, ...] = ()

    @property
    def accepted_count(self) -> int:
        return len(self.directories) + len(self.files)

    @property
    def ignored_count(self) -> int:
        return len(self.ignored)


def is_supported_audio(path: Path) -> bool:
    """Проверяет расширение файла без чтения содержимого."""
    return path.suffix.lower() in AUDIO_EXTENSIONS


def plan_drop_import(paths: Iterable[Path | str]) -> DropImportPlan:
    """Дедуплицирует drop-пути и заранее отбрасывает неподдерживаемые файлы."""
    unique: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        try:
            path = path.resolve(strict=False)
        except OSError:
            path = path.absolute()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    directories = [path for path in unique if path.is_dir()]
    top_directories: list[Path] = []
    for directory in directories:
        if any(
            directory != parent and directory.is_relative_to(parent)
            for parent in directories
        ):
            continue
        top_directories.append(directory)

    files: list[Path] = []
    ignored: list[Path] = []
    for path in unique:
        if path in top_directories:
            continue
        if any(path.is_relative_to(directory) for directory in top_directories):
            continue
        if path.is_file() and is_supported_audio(path):
            files.append(path)
        else:
            ignored.append(path)

    return DropImportPlan(
        directories=tuple(top_directories),
        files=tuple(files),
        ignored=tuple(ignored),
    )
