"""Конфигурация приложения и платформенные пути."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "DJMAKER"
DEFAULT_ORGANIZE_TEMPLATE = "{artist}/{album}/{track:02d} - {title}.{ext}"
DEFAULT_TARGET_LUFS = -11.5


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Набор путей, используемых DJMAKER."""

    data_dir: Path
    database: Path
    log_file: Path
    settings_file: Path
    artwork_dir: Path


def default_data_dir() -> Path:
    """Возвращает платформенный каталог данных приложения."""
    override = os.getenv("DJMAKER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    system = platform.system()
    home = Path.home()

    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", home / "AppData" / "Local"))
        return base / APP_NAME
    if system == "Darwin":
        return home / "Library" / "Application Support" / APP_NAME

    base = Path(os.getenv("XDG_DATA_HOME", home / ".local" / "share"))
    return base / APP_NAME.lower()


def get_app_paths() -> AppPaths:
    """Создаёт каталог данных и возвращает пути приложения."""
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return AppPaths(
        data_dir=data_dir,
        database=data_dir / "library.sqlite3",
        log_file=data_dir / "djmaker.log",
        settings_file=data_dir / "settings.json",
        artwork_dir=data_dir / "artwork",
    )
