"""Пользовательские настройки DJMAKER с хранением в JSON."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


from djmaker.domain.library_sort import DEFAULT_LIBRARY_SORT, LIBRARY_SORT_LABELS


LOGGER = logging.getLogger(__name__)

THEME_MODES = ("system", "light", "dark")
THEME_PALETTES = (
    "djmaker_blue",
    "violet",
    "emerald",
    "amber",
    "graphite",
    "clean_graphene",
    "strict",
)
DEFAULT_THEME_MODE = "system"
DEFAULT_THEME_PALETTE = "djmaker_blue"

KNOWN_METADATA_PROVIDERS = ("musicbrainz", "spotify")
DEFAULT_METADATA_PROVIDERS: tuple[str, ...] = ("musicbrainz",)
METADATA_AUTO_APPLY_THRESHOLD_MIN = 50
METADATA_AUTO_APPLY_THRESHOLD_MAX = 100
DEFAULT_METADATA_AUTO_APPLY_THRESHOLD = 90

LIBRARY_SCALE_MIN = 70
LIBRARY_SCALE_MAX = 150
LIBRARY_SCALE_STEP = 10
DEFAULT_LIBRARY_SCALE_PERCENT = 100

THEME_COLOR_ROLES = (
    "primary",
    "on_primary",
    "primary_container",
    "on_primary_container",
    "surface",
    "surface_container_low",
    "surface_container",
    "surface_container_high",
    "surface_container_highest",
    "on_surface",
    "on_surface_variant",
    "outline",
    "outline_variant",
    "error",
)
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_theme_overrides(value: object) -> dict[str, str]:
    """Возвращает безопасные пользовательские цвета темы из JSON-значения."""
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, str] = {}
    for role in THEME_COLOR_ROLES:
        color = value.get(role)
        if isinstance(color, str) and _HEX_COLOR_RE.fullmatch(color.strip()):
            normalized[role] = color.strip().upper()
    return normalized


def is_valid_theme_color(value: str) -> bool:
    """Проверяет пользовательский цвет в формате ``#RRGGBB``."""
    return bool(_HEX_COLOR_RE.fullmatch(value.strip()))


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Настройки, которые пользователь может менять из интерфейса."""

    theme_mode: str = DEFAULT_THEME_MODE
    theme_palette: str = DEFAULT_THEME_PALETTE
    theme_light_overrides: dict[str, str] = field(default_factory=dict)
    theme_dark_overrides: dict[str, str] = field(default_factory=dict)
    library_scale_percent: int = DEFAULT_LIBRARY_SCALE_PERCENT

    library_sort: str = DEFAULT_LIBRARY_SORT
    library_sort_descending: bool = False
    metadata_providers: tuple[str, ...] = DEFAULT_METADATA_PROVIDERS
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    metadata_auto_apply_threshold: int = DEFAULT_METADATA_AUTO_APPLY_THRESHOLD

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AppSettings":
        """Создаёт настройки из словаря, заменяя неизвестные значения дефолтами."""
        mode = data.get("theme_mode", DEFAULT_THEME_MODE)
        palette = data.get("theme_palette", DEFAULT_THEME_PALETTE)

        if mode not in THEME_MODES:
            mode = DEFAULT_THEME_MODE
        if palette not in THEME_PALETTES:
            palette = DEFAULT_THEME_PALETTE

        raw_scale = data.get(
            "library_scale_percent", DEFAULT_LIBRARY_SCALE_PERCENT
        )
        try:
            scale = int(raw_scale)
        except (TypeError, ValueError):
            scale = DEFAULT_LIBRARY_SCALE_PERCENT
        scale = min(LIBRARY_SCALE_MAX, max(LIBRARY_SCALE_MIN, scale))
        scale = LIBRARY_SCALE_MIN + (
            (scale - LIBRARY_SCALE_MIN + LIBRARY_SCALE_STEP // 2)
            // LIBRARY_SCALE_STEP
        ) * LIBRARY_SCALE_STEP
        scale = min(LIBRARY_SCALE_MAX, max(LIBRARY_SCALE_MIN, scale))

        sort = data.get("library_sort", DEFAULT_LIBRARY_SORT)
        if not isinstance(sort, str) or sort not in LIBRARY_SORT_LABELS:
            sort = DEFAULT_LIBRARY_SORT
        descending = data.get("library_sort_descending", False)
        if not isinstance(descending, bool):
            descending = False

        raw_providers = data.get("metadata_providers")
        if isinstance(raw_providers, list):
            providers = tuple(
                dict.fromkeys(p for p in raw_providers if p in KNOWN_METADATA_PROVIDERS)
            )
        else:
            providers = ()
        if not providers:
            providers = DEFAULT_METADATA_PROVIDERS

        spotify_client_id = data.get("spotify_client_id", "")
        if not isinstance(spotify_client_id, str):
            spotify_client_id = ""
        spotify_client_secret = data.get("spotify_client_secret", "")
        if not isinstance(spotify_client_secret, str):
            spotify_client_secret = ""

        raw_threshold = data.get(
            "metadata_auto_apply_threshold", DEFAULT_METADATA_AUTO_APPLY_THRESHOLD
        )
        try:
            auto_apply_threshold = int(raw_threshold)
        except (TypeError, ValueError):
            auto_apply_threshold = DEFAULT_METADATA_AUTO_APPLY_THRESHOLD
        auto_apply_threshold = min(
            METADATA_AUTO_APPLY_THRESHOLD_MAX,
            max(METADATA_AUTO_APPLY_THRESHOLD_MIN, auto_apply_threshold),
        )

        return cls(
            theme_mode=mode,
            theme_palette=palette,
            theme_light_overrides=normalize_theme_overrides(
                data.get("theme_light_overrides")
            ),
            theme_dark_overrides=normalize_theme_overrides(
                data.get("theme_dark_overrides")
            ),
            library_scale_percent=scale,
            library_sort=sort,
            library_sort_descending=descending,
            metadata_providers=providers,
            spotify_client_id=spotify_client_id.strip(),
            spotify_client_secret=spotify_client_secret.strip(),
            metadata_auto_apply_threshold=auto_apply_threshold,
        )


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
