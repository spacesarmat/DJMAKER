"""Единые параметры плотности интерфейса DJMAKER.

Значения собраны в одном месте, чтобы масштаб интерфейса можно было менять
без правок десятков экранов. Текущий профиль ориентирован примерно на 50%
меньшую занимаемую площадь по сравнению с первоначальным UI.
"""

from __future__ import annotations

from dataclasses import dataclass


UI_SCALE = 0.5


@dataclass(frozen=True, slots=True)
class UIDensity:
    """Размеры компактного desktop-интерфейса."""

    scale: float = UI_SCALE

    # Шрифты. Минимальные размеры оставлены читаемыми на обычных desktop-DPI.
    font_micro: int = 7
    font_xs: int = 8
    font_sm: int = 9
    font_md: int = 10
    font_lg: int = 12
    font_title: int = 14

    # Базовые интервалы и геометрия.
    space_xs: int = 2
    space_sm: int = 4
    space_md: int = 6
    space_lg: int = 10
    radius: int = 7
    card_padding: int = 7
    content_padding: int = 10

    # Навигация и заголовок.
    nav_min_width: int = 52
    nav_extended_width: int = 154
    header_horizontal_padding: int = 10
    header_vertical_padding: int = 6
    status_horizontal_padding: int = 10
    status_vertical_padding: int = 4

    # Строки медиатеки.
    track_icon_box: int = 40
    track_icon_size: int = 16
    action_icon_size: int = 16
    waveform_bar_count: int = 60
    waveform_bar_width: int = 5
    waveform_bar_gap: int = 1
    waveform_height: int = 34
    waveform_min_bar_height: int = 3

    # Настоящие DataTable, которые будут использоваться далее.
    table_heading_height: int = 28
    table_row_min_height: int = 26
    table_row_max_height: int = 32
    table_horizontal_margin: int = 8
    table_column_spacing: int = 12


COMPACT_UI = UIDensity()
