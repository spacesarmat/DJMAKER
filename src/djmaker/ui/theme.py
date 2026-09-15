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
    description: str


THEME_PALETTES: tuple[ThemePalette, ...] = (
    ThemePalette(
        "djmaker_blue",
        "DJMAKER Blue",
        ft.Colors.BLUE_600,
        "Фирменная синяя палитра DJMAKER.",
    ),
    ThemePalette(
        "violet",
        "Violet",
        ft.Colors.DEEP_PURPLE_500,
        "Фиолетовый Material-акцент.",
    ),
    ThemePalette(
        "emerald",
        "Emerald",
        ft.Colors.TEAL_500,
        "Спокойная зелёно-бирюзовая палитра.",
    ),
    ThemePalette(
        "amber",
        "Amber",
        ft.Colors.AMBER_700,
        "Тёплый янтарный акцент.",
    ),
    ThemePalette(
        "graphite",
        "Graphite",
        ft.Colors.BLUE_GREY_600,
        "Базовая графитовая Material-палитра.",
    ),
    ThemePalette(
        "clean_graphene",
        "Чистый графен",
        ft.Colors.BLUE_GREY_700,
        "Монохромный графит, холодные поверхности и минимум цветового шума.",
    ),
    ThemePalette(
        "strict",
        "Строгий",
        ft.Colors.BLUE_GREY_800,
        "Высокий контраст, нейтральные поверхности и сдержанный стальной акцент.",
    ),
)

_PALETTE_BY_KEY = {palette.key: palette for palette in THEME_PALETTES}

THEME_MODE_LABELS = {
    "system": "Системная",
    "light": "Светлая",
    "dark": "Тёмная",
}


# Полностью заданные палитры нужны для вариантов, которые должны отличаться
# не только Material seed-цветом, но и характером поверхностей/контраста.
_CUSTOM_COLOR_SCHEMES: dict[str, tuple[ft.ColorScheme, ft.ColorScheme]] = {
    "clean_graphene": (
        ft.ColorScheme(
            primary="#434A50",
            on_primary="#FFFFFF",
            primary_container="#DDE1E4",
            on_primary_container="#1B1F22",
            secondary="#5D656B",
            on_secondary="#FFFFFF",
            secondary_container="#E1E5E8",
            on_secondary_container="#1B1F22",
            tertiary="#687177",
            on_tertiary="#FFFFFF",
            tertiary_container="#E3E7E9",
            on_tertiary_container="#1C2023",
            error="#BA1A1A",
            on_error="#FFFFFF",
            error_container="#FFDAD6",
            on_error_container="#410002",
            surface="#F7F8F8",
            on_surface="#171A1C",
            on_surface_variant="#4D555A",
            outline="#777F84",
            outline_variant="#CDD2D5",
            inverse_surface="#2C3033",
            on_inverse_surface="#EEF0F1",
            inverse_primary="#C3C9CD",
            surface_tint="#434A50",
            surface_bright="#FFFFFF",
            surface_container_lowest="#FFFFFF",
            surface_container_low="#F1F3F4",
            surface_container="#EAECED",
            surface_container_high="#E3E6E8",
            surface_container_highest="#DCE0E2",
            surface_dim="#D8DCDE",
        ),
        ft.ColorScheme(
            primary="#C3C9CD",
            on_primary="#252A2E",
            primary_container="#3A4045",
            on_primary_container="#E4E7E9",
            secondary="#AEB5B9",
            on_secondary="#293034",
            secondary_container="#41484C",
            on_secondary_container="#E0E4E6",
            tertiary="#B8C0C4",
            on_tertiary="#2A3034",
            tertiary_container="#42494D",
            on_tertiary_container="#E5E8EA",
            error="#FFB4AB",
            on_error="#690005",
            error_container="#93000A",
            on_error_container="#FFDAD6",
            surface="#101214",
            on_surface="#E1E4E6",
            on_surface_variant="#BFC5C9",
            outline="#899196",
            outline_variant="#3C4246",
            inverse_surface="#E1E4E6",
            on_inverse_surface="#2E3134",
            inverse_primary="#5A6268",
            surface_tint="#C3C9CD",
            surface_bright="#363A3D",
            surface_container_lowest="#0B0D0E",
            surface_container_low="#171A1C",
            surface_container="#1D2023",
            surface_container_high="#25292C",
            surface_container_highest="#2D3235",
            surface_dim="#101214",
        ),
    ),
    "strict": (
        ft.ColorScheme(
            primary="#304A66",
            on_primary="#FFFFFF",
            primary_container="#D6E4F3",
            on_primary_container="#0D2235",
            secondary="#4F5D6B",
            on_secondary="#FFFFFF",
            secondary_container="#DDE3EA",
            on_secondary_container="#172431",
            tertiary="#606775",
            on_tertiary="#FFFFFF",
            tertiary_container="#E2E3EB",
            on_tertiary_container="#1D2029",
            error="#BA1A1A",
            on_error="#FFFFFF",
            error_container="#FFDAD6",
            on_error_container="#410002",
            surface="#FAFAF9",
            on_surface="#151719",
            on_surface_variant="#44484D",
            outline="#6F7479",
            outline_variant="#C8CDD2",
            inverse_surface="#2A2E32",
            on_inverse_surface="#F1F2F3",
            inverse_primary="#AEC9E6",
            surface_tint="#304A66",
            surface_bright="#FFFFFF",
            surface_container_lowest="#FFFFFF",
            surface_container_low="#F4F5F5",
            surface_container="#EEEFEF",
            surface_container_high="#E7E8E9",
            surface_container_highest="#DFE1E2",
            surface_dim="#DADBDB",
        ),
        ft.ColorScheme(
            primary="#AEC9E6",
            on_primary="#17324A",
            primary_container="#304A66",
            on_primary_container="#D6E4F3",
            secondary="#BEC8D2",
            on_secondary="#29333D",
            secondary_container="#3F4954",
            on_secondary_container="#DCE3EA",
            tertiary="#C5C7D2",
            on_tertiary="#2E303A",
            tertiary_container="#454752",
            on_tertiary_container="#E3E3EC",
            error="#FFB4AB",
            on_error="#690005",
            error_container="#93000A",
            on_error_container="#FFDAD6",
            surface="#0D1013",
            on_surface="#E3E6E9",
            on_surface_variant="#C1C7CD",
            outline="#8B9298",
            outline_variant="#3C4248",
            inverse_surface="#E3E6E9",
            on_inverse_surface="#2A2E32",
            inverse_primary="#48627E",
            surface_tint="#AEC9E6",
            surface_bright="#34383D",
            surface_container_lowest="#090B0D",
            surface_container_low="#14171A",
            surface_container="#1A1D20",
            surface_container_high="#22262A",
            surface_container_highest="#2B2F34",
            surface_dim="#0D1013",
        ),
    ),
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


def _build_theme(palette: ThemePalette, *, dark: bool) -> ft.Theme:
    """Создаёт тему с выбранной палитрой и компактной плотностью."""
    custom_pair = _CUSTOM_COLOR_SCHEMES.get(palette.key)
    kwargs: dict[str, object]
    if custom_pair is None:
        kwargs = {"color_scheme_seed": palette.seed}
    else:
        kwargs = {"color_scheme": custom_pair[1 if dark else 0]}

    return ft.Theme(
        **kwargs,
        visual_density=ft.VisualDensity.COMPACT,
        text_theme=_compact_text_theme(),
        data_table_theme=_compact_data_table_theme(),
    )


def apply_app_theme(page: ft.Page, settings: AppSettings) -> None:
    """Применяет выбранный режим и цветовую схему ко всей странице Flet."""
    palette = _PALETTE_BY_KEY.get(settings.theme_palette, THEME_PALETTES[0])

    page.theme = _build_theme(palette, dark=False)
    page.dark_theme = _build_theme(palette, dark=True)
    page.theme_mode = {
        "light": ft.ThemeMode.LIGHT,
        "dark": ft.ThemeMode.DARK,
    }.get(settings.theme_mode, ft.ThemeMode.SYSTEM)
    page.bgcolor = ft.Colors.SURFACE


def palette_title(key: str) -> str:
    """Возвращает человекочитаемое название цветовой схемы."""
    return _PALETTE_BY_KEY.get(key, THEME_PALETTES[0]).title


def palette_description(key: str) -> str:
    """Возвращает краткое описание характера цветовой схемы."""
    return _PALETTE_BY_KEY.get(key, THEME_PALETTES[0]).description


def theme_mode_icon(mode: str) -> object:
    """Возвращает иконку для текущего режима темы."""
    return {
        "light": ft.Icons.LIGHT_MODE,
        "dark": ft.Icons.DARK_MODE,
    }.get(mode, ft.Icons.BRIGHTNESS_AUTO)
