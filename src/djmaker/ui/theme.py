"""Централизованное оформление и цветовые темы DJMAKER."""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from djmaker.settings import AppSettings
from djmaker.ui.density import COMPACT_UI


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


def _surface_text_style(size: int, *, bold: bool = False) -> ft.TextStyle:
    """Создаёт читаемый стиль текста на основной поверхности темы.

    Цвет задаётся семантическим токеном ``ON_SURFACE``, а не фиксированным
    белым/чёрным значением. Поэтому один и тот же стиль корректно работает
    в светлой и тёмной теме и с любой цветовой палитрой приложения.
    """
    return ft.TextStyle(
        size=size,
        color=ft.Colors.ON_SURFACE,
        weight=ft.FontWeight.BOLD if bold else None,
    )


def _compact_text_theme() -> ft.TextTheme:
    """Возвращает компактную и контрастную Material-типографику."""
    return ft.TextTheme(
        body_large=_surface_text_style(COMPACT_UI.font_md),
        body_medium=_surface_text_style(COMPACT_UI.font_sm),
        body_small=_surface_text_style(COMPACT_UI.font_xs),
        display_large=_surface_text_style(20),
        display_medium=_surface_text_style(18),
        display_small=_surface_text_style(16),
        headline_large=_surface_text_style(16),
        headline_medium=_surface_text_style(14),
        headline_small=_surface_text_style(12),
        title_large=_surface_text_style(COMPACT_UI.font_lg),
        title_medium=_surface_text_style(COMPACT_UI.font_md),
        title_small=_surface_text_style(COMPACT_UI.font_sm),
        label_large=_surface_text_style(COMPACT_UI.font_sm),
        label_medium=_surface_text_style(COMPACT_UI.font_xs),
        label_small=_surface_text_style(COMPACT_UI.font_micro),
    )


def _compact_data_table_theme() -> ft.DataTableTheme:
    """Задаёт плотность будущих и текущих DataTable во всём приложении."""
    return ft.DataTableTheme(
        column_spacing=COMPACT_UI.table_column_spacing,
        data_row_min_height=COMPACT_UI.table_row_min_height,
        data_row_max_height=COMPACT_UI.table_row_max_height,
        horizontal_margin=COMPACT_UI.table_horizontal_margin,
        heading_row_height=COMPACT_UI.table_heading_height,
        data_text_style=_surface_text_style(COMPACT_UI.font_xs),
        heading_text_style=_surface_text_style(COMPACT_UI.font_xs, bold=True),
    )


def _build_theme(seed: ft.Colors) -> ft.Theme:
    """Создаёт тему с общей палитрой и компактной плотностью."""
    return ft.Theme(
        color_scheme_seed=seed,
        visual_density=ft.VisualDensity.COMPACT,
        text_theme=_compact_text_theme(),
        data_table_theme=_compact_data_table_theme(),
    )


def apply_app_theme(page: ft.Page, settings: AppSettings) -> None:
    """Применяет выбранный режим и цветовую схему ко всей странице Flet."""
    palette = _PALETTE_BY_KEY.get(settings.theme_palette, THEME_PALETTES[0])

    page.theme = _build_theme(palette.seed)
    page.dark_theme = _build_theme(palette.seed)
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
