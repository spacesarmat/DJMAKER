"""Расчёты программной прокрутки интерфейса."""

from __future__ import annotations


def centered_scroll_offset(
    *,
    index: int,
    item_extent: float,
    viewport_extent: float,
    max_scroll_extent: float,
) -> float:
    """Возвращает offset, размещающий элемент максимально близко к центру.

    На краях списка результат ограничивается допустимым диапазоном прокрутки.
    """
    if index < 0:
        raise ValueError("index must be non-negative")
    if item_extent <= 0:
        raise ValueError("item_extent must be positive")
    if viewport_extent <= 0:
        raise ValueError("viewport_extent must be positive")

    maximum = max(0.0, float(max_scroll_extent))
    item_center = index * item_extent + item_extent / 2
    target = item_center - viewport_extent / 2
    return min(maximum, max(0.0, target))
