"""Расчёт постоянной BPM-сетки, тактов и музыкальных квадратов."""

from __future__ import annotations

from dataclasses import dataclass

from djmaker.domain.models import BeatGridAnalysis


MIN_BPM = 30.0
MAX_BPM = 300.0
ALLOWED_BEATS_PER_BAR = (3, 4)
ALLOWED_SQUARE_BARS = (4, 8, 16, 32)


class BeatGridError(ValueError):
    """Некорректные параметры музыкальной сетки."""


@dataclass(frozen=True, slots=True)
class BeatGridMarker:
    """Одна вычисленная линия сетки в координатах аудиофайла."""

    position_ms: int
    beat_number: int
    beat_in_bar: int
    bar_number: int
    is_bar: bool
    is_square: bool


def validate_beat_grid(grid: BeatGridAnalysis) -> BeatGridAnalysis:
    """Проверяет диапазоны сетки перед сохранением или отображением."""
    if not MIN_BPM <= float(grid.bpm) <= MAX_BPM:
        raise BeatGridError(f"BPM сетки вне диапазона {MIN_BPM:g}–{MAX_BPM:g}")
    if grid.first_beat_ms < 0 or grid.downbeat_ms < 0:
        raise BeatGridError("Позиции сетки не могут быть отрицательными")
    if grid.beats_per_bar not in ALLOWED_BEATS_PER_BAR:
        raise BeatGridError("Поддерживаются размеры такта 3/4 и 4/4")
    if not grid.beat_ticks_ms:
        raise BeatGridError("Сетка не содержит обнаруженных долей")
    if any(position < 0 for position in grid.beat_ticks_ms):
        raise BeatGridError("Обнаруженные удары не могут быть отрицательными")
    if tuple(sorted(set(grid.beat_ticks_ms))) != grid.beat_ticks_ms:
        raise BeatGridError("Обнаруженные удары должны быть уникальны и упорядочены")
    if grid.first_beat_ms not in grid.beat_ticks_ms:
        raise BeatGridError("Первая доля отсутствует среди обнаруженных долей")
    if grid.downbeat_ms not in grid.beat_ticks_ms:
        raise BeatGridError("Сильная доля отсутствует среди обнаруженных долей")
    for value, label in (
        (grid.tempo_stability, "Стабильность темпа"),
        (grid.downbeat_confidence, "Уверенность сильной доли"),
    ):
        if value is not None and not 0 <= value <= 1:
            raise BeatGridError(f"{label} должна быть от 0 до 1")
    return grid


def regular_grid_markers(
    grid: BeatGridAnalysis,
    duration_ms: int,
    *,
    square_bars: int = 8,
) -> tuple[BeatGridMarker, ...]:
    """Строит линии долей по BPM и якорю первой сильной доли."""
    validate_beat_grid(grid)
    if duration_ms <= 0:
        return ()
    if square_bars not in ALLOWED_SQUARE_BARS:
        raise BeatGridError("Квадрат должен содержать 4, 8, 16 или 32 такта")

    beat_ms = 60_000 / grid.bpm
    beats_per_square = grid.beats_per_bar * square_bars
    first_index = int((0 - grid.downbeat_ms) // beat_ms) - 1
    last_index = int((duration_ms - grid.downbeat_ms) // beat_ms) + 1
    markers: list[BeatGridMarker] = []
    for beat_number in range(first_index, last_index + 1):
        position_ms = round(grid.downbeat_ms + beat_number * beat_ms)
        if not 0 <= position_ms <= duration_ms:
            continue
        beat_in_bar = beat_number % grid.beats_per_bar
        bar_number = beat_number // grid.beats_per_bar
        markers.append(
            BeatGridMarker(
                position_ms=position_ms,
                beat_number=beat_number,
                beat_in_bar=beat_in_bar,
                bar_number=bar_number,
                is_bar=beat_in_bar == 0,
                is_square=beat_number % beats_per_square == 0,
            )
        )
    return tuple(markers)


def nearest_grid_position(
    grid: BeatGridAnalysis,
    position_ms: int,
    *,
    beats: int = 1,
) -> int:
    """Привязывает позицию к ближайшей доле, такту или кратному шагу."""
    validate_beat_grid(grid)
    if beats < 1:
        raise BeatGridError("Шаг привязки должен быть положительным")
    step_ms = 60_000 / grid.bpm * beats
    relative = position_ms - grid.downbeat_ms
    return max(0, round(grid.downbeat_ms + round(relative / step_ms) * step_ms))
