"""Централизованное оформление и цветовые темы DJMAKER."""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from djmaker.settings import AppSettings


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Описание доступной цветовой схемы интерфейса."""

    key: str
    title: str
    seed: ft.Colors


THEME_PALETTES: tuple[ThemePalette, ...] = (
    ThemePalette("djmaker_blue", "DJMAKER Blue", ft.Colors.BLUE_600),
    ThemePalette("violet", "Violet", ft.Colors.DEEP_PURPLE_500),
    ThemePalette("emerald", "Emerald", ft.Colors.TEAL_500),
    ThemePalette("amber", "Amber", ft.Colors.AMBER_700),
    ThemePalette("graphite", "Graphite", ft.Colors.BLUE_GREY_600),
)

_PALETTE_BY_KEY = {palette.key: palette for palette in THEME_PALETTES}

THEME_MODE_LABELS = {
    "system": "Системная",
    "light": "Светлая",
    "dark": "Тёмная",
}


def apply_app_theme(page: ft.Page, settings: AppSettings) -> None:
    """Применяет выбранный режим и цветовую схему ко всей странице Flet."""
    palette = _PALETTE_BY_KEY.get(settings.theme_palette, THEME_PALETTES[0])
    theme = ft.Theme(color_scheme_seed=palette.seed)
    dark_theme = ft.Theme(color_scheme_seed=palette.seed)

    page.theme = theme
    page.dark_theme = dark_theme
    page.theme_mode = {
        "light": ft.ThemeMode.LIGHT,
        "dark": ft.ThemeMode.DARK,
    }.get(settings.theme_mode, ft.ThemeMode.SYSTEM)
    page.bgcolor = ft.Colors.SURFACE


def palette_title(key: str) -> str:
    """Возвращает человекочитаемое название цветовой схемы."""
    return _PALETTE_BY_KEY.get(key, THEME_PALETTES[0]).title


def theme_mode_icon(mode: str) -> object:
    """Возвращает иконку для текущего режима темы."""
    return {
        "light": ft.Icons.LIGHT_MODE,
        "dark": ft.Icons.DARK_MODE,
    }.get(mode, ft.Icons.BRIGHTNESS_AUTO)
