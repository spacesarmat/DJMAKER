"""Централизованное оформление и цветовые темы DJMAKER."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace

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
        "Углеродная база, металлические структурные тона и один лазерно-циановый акцент.",
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
        # Light: лабораторная инверсия той же графеновой палитры. Акцент
        # остаётся один и тот же, а поверхности строятся из холодного серебра.
        ft.ColorScheme(
            primary="#00A9B5",
            on_primary="#001416",
            primary_container="#B6F7FA",
            on_primary_container="#002F33",
            secondary="#61656A",
            on_secondary="#FFFFFF",
            secondary_container="#D1D5DB",
            on_secondary_container="#242529",
            tertiary="#383A40",
            on_tertiary="#FFFFFF",
            tertiary_container="#E2E5E9",
            on_tertiary_container="#242529",
            error="#BA1A1A",
            on_error="#FFFFFF",
            error_container="#FFDAD6",
            on_error_container="#410002",
            surface="#F3F5F7",
            on_surface="#0F0F11",
            on_surface_variant="#383A40",
            outline="#8A8D91",
            outline_variant="#D1D5DB",
            inverse_surface="#242529",
            on_inverse_surface="#D1D5DB",
            inverse_primary="#00F0FF",
            surface_tint="#00A9B5",
            surface_bright="#FFFFFF",
            surface_container_lowest="#FFFFFF",
            surface_container_low="#EFF1F3",
            surface_container="#E7EAED",
            surface_container_high="#DDE1E5",
            surface_container_highest="#D1D5DB",
            surface_dim="#C7CBD0",
        ),
        # Dark: буквальная «чистая графеновая» база пользователя — Carbon
        # Black / Graphene Grey / Anthracite + Titanium / Silver и только
        # Laser Cyan как функциональный неоновый маркер.
        ft.ColorScheme(
            primary="#00F0FF",
            on_primary="#001416",
            primary_container="#0A363B",
            on_primary_container="#B6F7FA",
            secondary="#8A8D91",
            on_secondary="#0F0F11",
            secondary_container="#242529",
            on_secondary_container="#D1D5DB",
            tertiary="#D1D5DB",
            on_tertiary="#0F0F11",
            tertiary_container="#383A40",
            on_tertiary_container="#F1F3F5",
            error="#FFB4AB",
            on_error="#690005",
            error_container="#93000A",
            on_error_container="#FFDAD6",
            surface="#0F0F11",
            on_surface="#D1D5DB",
            on_surface_variant="#8A8D91",
            outline="#8A8D91",
            outline_variant="#383A40",
            inverse_surface="#D1D5DB",
            on_inverse_surface="#242529",
            inverse_primary="#007E87",
            surface_tint="#00F0FF",
            surface_bright="#383A40",
            surface_container_lowest="#09090A",
            surface_container_low="#171719",
            surface_container="#242529",
            surface_container_high="#2D2F34",
            surface_container_highest="#383A40",
            surface_dim="#0F0F11",
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


def _build_theme(
    palette: ThemePalette,
    *,
    dark: bool,
    overrides: dict[str, str] | None = None,
) -> ft.Theme:
    """Создаёт тему и накладывает пользовательские цветовые override-роли."""
    custom_pair = _CUSTOM_COLOR_SCHEMES.get(palette.key)
    color_overrides = overrides or {}
    kwargs: dict[str, object]
    if custom_pair is None:
        kwargs = {"color_scheme_seed": palette.seed}
        if color_overrides:
            # Flet/Flutter сначала строит ColorScheme из seed, после чего
            # частичный ColorScheme копирует только заданные пользователем роли.
            kwargs["color_scheme"] = ft.ColorScheme(**color_overrides)
    else:
        scheme = custom_pair[1 if dark else 0]
        if color_overrides:
            scheme = dataclass_replace(scheme, **color_overrides)
        kwargs = {"color_scheme": scheme}

    return ft.Theme(
        **kwargs,
        visual_density=ft.VisualDensity.COMPACT,
        text_theme=_compact_text_theme(),
        data_table_theme=_compact_data_table_theme(),
    )


def apply_app_theme(page: ft.Page, settings: AppSettings) -> None:
    """Применяет выбранный режим и цветовую схему ко всей странице Flet."""
    palette = _PALETTE_BY_KEY.get(settings.theme_palette, THEME_PALETTES[0])

    page.theme = _build_theme(
        palette,
        dark=False,
        overrides=settings.theme_light_overrides,
    )
    page.dark_theme = _build_theme(
        palette,
        dark=True,
        overrides=settings.theme_dark_overrides,
    )
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
