"""Пользовательские настройки DJMAKER с хранением в JSON."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

THEME_MODES = ("system", "light", "dark")
THEME_PALETTES = ("djmaker_blue", "violet", "emerald", "amber", "graphite")
DEFAULT_THEME_MODE = "system"
DEFAULT_THEME_PALETTE = "djmaker_blue"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Настройки, которые пользователь может менять из интерфейса."""

    theme_mode: str = DEFAULT_THEME_MODE
    theme_palette: str = DEFAULT_THEME_PALETTE

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AppSettings":
        """Создаёт настройки из словаря, заменяя неизвестные значения дефолтами."""
        mode = data.get("theme_mode", DEFAULT_THEME_MODE)
        palette = data.get("theme_palette", DEFAULT_THEME_PALETTE)

        if mode not in THEME_MODES:
            mode = DEFAULT_THEME_MODE
        if palette not in THEME_PALETTES:
            palette = DEFAULT_THEME_PALETTE

        return cls(theme_mode=mode, theme_palette=palette)


class SettingsStore:
    """Читает и атомарно сохраняет локальные настройки приложения."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppSettings:
        """Загружает настройки; при отсутствии/повреждении возвращает значения по умолчанию."""
        if not self.path.exists():
            return AppSettings()

        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Не удалось прочитать настройки %s: %s", self.path, exc)
            return AppSettings()

        if not isinstance(data, dict):
            LOGGER.warning("Файл настроек %s не содержит JSON-объект", self.path)
            return AppSettings()

        return AppSettings.from_mapping(data)

    def save(self, settings: AppSettings) -> None:
        """Атомарно сохраняет настройки, чтобы не оставить повреждённый JSON при сбое."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2, sort_keys=True)

        try:
            temporary.write_text(f"{payload}\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError as cleanup_exc:
                    LOGGER.warning(
                        "Не удалось удалить временный файл настроек %s: %s",
                        temporary,
                        cleanup_exc,
                    )
            raise
