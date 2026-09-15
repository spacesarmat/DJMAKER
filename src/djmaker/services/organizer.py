"""Безопасное переименование и организация музыкальных файлов."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from string import Formatter

from djmaker.domain.models import TrackRecord


class OrganizerError(RuntimeError):
    """Ошибка построения или выполнения операции организации."""


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ALLOWED_FIELDS = {
    "artist",
    "album",
    "album_artist",
    "title",
    "year",
    "track",
    "disc",
    "ext",
}


class SafeValue:
    """Значение шаблона, которое поддерживает числовой формат и fallback."""

    def __init__(self, value: str | int | None, fallback: str = "Unknown") -> None:
        self.value = value
        self.fallback = fallback

    def __format__(self, spec: str) -> str:
        if self.value is None or self.value == "":
            rendered = self.fallback
        elif isinstance(self.value, int) and spec:
            rendered = format(self.value, spec)
        else:
            rendered = str(self.value)
        # Значения тегов не должны иметь возможность добавлять собственные
        # разделители каталогов поверх структуры, заданной самим шаблоном.
        return FileOrganizer.sanitize_component(rendered)


class FileOrganizer:
    """Строит кроссплатформенные пути и переносит файлы без перезаписи."""

    def build_target(self, track: TrackRecord, destination: Path, template: str) -> Path:
        """Строит целевой путь по шаблону и метаданным трека."""
        self._validate_template(template)
        metadata = track.metadata
        values = {
            "artist": SafeValue(metadata.artist, "Unknown Artist"),
            "album": SafeValue(metadata.album, "Unknown Album"),
            "album_artist": SafeValue(metadata.album_artist or metadata.artist, "Unknown Artist"),
            "title": SafeValue(metadata.title or track.path.stem, "Unknown Title"),
            "year": SafeValue(metadata.year, "Unknown Year"),
            "track": SafeValue(metadata.track_number, "00"),
            "disc": SafeValue(metadata.disc_number, "1"),
            "ext": SafeValue(track.path.suffix.lower().lstrip("."), "audio"),
        }
        try:
            relative = template.format_map(values)
        except (KeyError, ValueError) as exc:
            raise OrganizerError(f"Некорректный шаблон: {exc}") from exc

        parts = [self.sanitize_component(part) for part in Path(relative).parts]
        if not parts:
            raise OrganizerError("Шаблон сформировал пустой путь")
        return destination.expanduser().resolve().joinpath(*parts)

    def move(self, source: Path, target: Path) -> Path:
        """Перемещает файл, создавая уникальное имя при конфликте."""
        source = source.expanduser().resolve()
        target = target.expanduser().resolve()
        if not source.exists():
            raise OrganizerError(f"Исходный файл не существует: {source}")
        if source == target:
            return source

        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._unique_path(target)
        try:
            moved = shutil.move(str(source), str(target))
        except (OSError, shutil.Error) as exc:
            raise OrganizerError(f"Не удалось переместить {source} -> {target}: {exc}") from exc
        return Path(moved).resolve()

    @staticmethod
    def sanitize_component(value: str) -> str:
        """Очищает один компонент имени для совместимости Windows/macOS."""
        cleaned = _INVALID_CHARS.sub("_", value).strip().rstrip(".")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            cleaned = "Unknown"
        stem = cleaned.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            cleaned = f"_{cleaned}"
        return cleaned[:240]

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise OrganizerError(f"Не удалось подобрать свободное имя для {path}")

    @staticmethod
    def _validate_template(template: str) -> None:
        if not template.strip():
            raise OrganizerError("Шаблон не может быть пустым")
        for _, field_name, _, _ in Formatter().parse(template):
            if field_name and field_name not in _ALLOWED_FIELDS:
                raise OrganizerError(f"Неизвестное поле шаблона: {field_name}")
