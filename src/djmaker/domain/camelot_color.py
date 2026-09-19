"""Цвет тональности по колесу Camelot (для вертикальной полосы в строке трека).

12 позиций колеса -> 12 равномерных оттенков (hue), минор (A) и мажор (B)
одной позиции — тот же оттенок, но разная светлота/насыщенность.
"""

from __future__ import annotations

import colorsys

from djmaker.domain.library_sort import camelot_order

_MINOR_LIGHTNESS = 0.42
_MINOR_SATURATION = 0.62
_MAJOR_LIGHTNESS = 0.55
_MAJOR_SATURATION = 0.70


def camelot_to_color(camelot: str) -> str | None:
    """Возвращает hex-цвет (#RRGGBB) для кода Camelot, либо None, если код неизвестен."""
    index = camelot_order(camelot)
    if index is None:
        return None

    camelot_number = index // 2 + 1  # 1..12
    is_major = index % 2 == 1  # чётный индекс = A (минор), нечётный = B (мажор)
    hue = (camelot_number - 1) / 12.0
    lightness = _MAJOR_LIGHTNESS if is_major else _MINOR_LIGHTNESS
    saturation = _MAJOR_SATURATION if is_major else _MINOR_SATURATION

    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02X}{:02X}{:02X}".format(round(red * 255), round(green * 255), round(blue * 255))
