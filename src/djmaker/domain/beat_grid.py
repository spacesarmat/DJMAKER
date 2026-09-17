"""Расчёт постоянной BPM-сетки, тактов и музыкальных квадратов."""

from __future__ import annotations

import math
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


@dataclass(frozen=True, slots=True)
class BeatGridAnchor:
    """Warp-якорь: позиция аудио, жёстко привязанная к номеру доли."""

    track_id: int
    source_ms: int
    beat_number: float


def validate_beat_grid(grid: BeatGridAnalysis) -> BeatGridAnalysis:
    """Проверяет диапазоны сетки перед сохранением или отображением."""
    if not MIN_BPM <= float(grid.bpm) <= MAX_BPM:
        raise BeatGridError(f"BPM сетки вне диапазона {MIN_BPM:g}–{MAX_BPM:g}")
    if grid.first_beat_ms < 0 or grid.downbeat_ms < 0:
        raise BeatGridError("Позиции сетки не могут быть отрицательными")
    if grid.beats_per_bar not in ALLOWED_BEATS_PER_BAR:
        raise BeatGridError("Поддерживаются размеры такта 3/4 и 4/4")
    if grid.source not in {"auto", "manual"}:
        raise BeatGridError("Источник сетки должен быть auto или manual")
    if not grid.beat_ticks_ms:
        raise BeatGridError("Сетка не содержит обнаруженных долей")
    if any(position < 0 for position in grid.beat_ticks_ms):
        raise BeatGridError("Обнаруженные удары не могут быть отрицательными")
    if tuple(sorted(set(grid.beat_ticks_ms))) != grid.beat_ticks_ms:
        raise BeatGridError("Обнаруженные удары должны быть уникальны и упорядочены")
    if grid.source == "auto":
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


def validate_anchor_mapping(
    grid: BeatGridAnalysis,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor],
) -> tuple[BeatGridAnchor, ...]:
    """Проверяет, что Warp-якоря образуют монотонную музыкальную шкалу."""
    validate_beat_grid(grid)
    ordered = tuple(sorted(anchors, key=lambda item: item.beat_number))
    seen_sources: set[int] = set()
    seen_beats: set[float] = set()
    previous: BeatGridAnchor | None = None
    for anchor in ordered:
        if anchor.source_ms < 0 or not math.isfinite(anchor.beat_number):
            raise BeatGridError("Warp-якорь содержит некорректную позицию")
        if anchor.source_ms in seen_sources or anchor.beat_number in seen_beats:
            raise BeatGridError("Позиции и номера долей Warp-якорей должны быть уникальны")
        if previous is not None:
            source_delta = anchor.source_ms - previous.source_ms
            beat_delta = anchor.beat_number - previous.beat_number
            if source_delta <= 0 or beat_delta <= 0:
                raise BeatGridError("Warp-якоря должны идти вперёд по времени и долям")
            segment_bpm = 60_000 * beat_delta / source_delta
            if not MIN_BPM <= segment_bpm <= MAX_BPM:
                raise BeatGridError(
                    f"Темп между Warp-якорями вне диапазона "
                    f"{MIN_BPM:g}–{MAX_BPM:g} BPM"
                )
        seen_sources.add(anchor.source_ms)
        seen_beats.add(anchor.beat_number)
        previous = anchor

    _mapping_anchors(grid, ordered)
    return ordered


def _mapping_anchors(
    grid: BeatGridAnalysis,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor],
) -> tuple[tuple[float, float], ...]:
    pairs = [(float(item.beat_number), float(item.source_ms)) for item in anchors]
    if not any(math.isclose(beat, 0.0, abs_tol=1e-9) for beat, _ in pairs):
        pairs.append((0.0, float(grid.downbeat_ms)))
    pairs.sort(key=lambda item: item[0])
    for (left_beat, left_ms), (right_beat, right_ms) in zip(pairs, pairs[1:]):
        if right_beat <= left_beat or right_ms <= left_ms:
            raise BeatGridError(
                "Warp-якоря конфликтуют с положением первой сильной доли"
            )
        segment_bpm = 60_000 * (right_beat - left_beat) / (right_ms - left_ms)
        if not MIN_BPM <= segment_bpm <= MAX_BPM:
            raise BeatGridError(
                f"Темп Warp-сегмента вне диапазона {MIN_BPM:g}–{MAX_BPM:g} BPM"
            )
    return tuple(pairs)


def beat_position_ms(
    grid: BeatGridAnalysis,
    beat_number: float,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor] = (),
) -> int:
    """Переводит музыкальную долю в позицию аудио с учётом Warp-якорей."""
    validate_beat_grid(grid)
    pairs = _mapping_anchors(grid, validate_anchor_mapping(grid, anchors))
    beat_ms = 60_000 / grid.bpm
    if len(pairs) == 1 or beat_number <= pairs[0][0]:
        anchor_beat, anchor_ms = pairs[0]
        return round(anchor_ms + (beat_number - anchor_beat) * beat_ms)
    for (left_beat, left_ms), (right_beat, right_ms) in zip(pairs, pairs[1:]):
        if beat_number <= right_beat:
            fraction = (beat_number - left_beat) / (right_beat - left_beat)
            return round(left_ms + fraction * (right_ms - left_ms))
    anchor_beat, anchor_ms = pairs[-1]
    return round(anchor_ms + (beat_number - anchor_beat) * beat_ms)


def beat_number_at_position(
    grid: BeatGridAnalysis,
    position_ms: int,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor] = (),
) -> float:
    """Переводит позицию аудио в дробный номер музыкальной доли."""
    validate_beat_grid(grid)
    pairs = _mapping_anchors(grid, validate_anchor_mapping(grid, anchors))
    beat_ms = 60_000 / grid.bpm
    if len(pairs) == 1 or position_ms <= pairs[0][1]:
        anchor_beat, anchor_ms = pairs[0]
        return anchor_beat + (position_ms - anchor_ms) / beat_ms
    for (left_beat, left_ms), (right_beat, right_ms) in zip(pairs, pairs[1:]):
        if position_ms <= right_ms:
            fraction = (position_ms - left_ms) / (right_ms - left_ms)
            return left_beat + fraction * (right_beat - left_beat)
    anchor_beat, anchor_ms = pairs[-1]
    return anchor_beat + (position_ms - anchor_ms) / beat_ms


def warped_grid_markers(
    grid: BeatGridAnalysis,
    duration_ms: int,
    *,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor] = (),
    square_bars: int = 8,
) -> tuple[BeatGridMarker, ...]:
    """Строит сетку с линейной интерполяцией между Warp-якорями."""
    validate_beat_grid(grid)
    validate_anchor_mapping(grid, anchors)
    if duration_ms <= 0:
        return ()
    if square_bars not in ALLOWED_SQUARE_BARS:
        raise BeatGridError("Квадрат должен содержать 4, 8, 16 или 32 такта")
    first_beat = math.floor(beat_number_at_position(grid, 0, anchors)) - 1
    last_beat = math.ceil(beat_number_at_position(grid, duration_ms, anchors)) + 1
    beats_per_square = grid.beats_per_bar * square_bars
    markers: list[BeatGridMarker] = []
    for beat_number in range(first_beat, last_beat + 1):
        position_ms = beat_position_ms(grid, beat_number, anchors)
        if not 0 <= position_ms <= duration_ms:
            continue
        beat_in_bar = beat_number % grid.beats_per_bar
        markers.append(
            BeatGridMarker(
                position_ms=position_ms,
                beat_number=beat_number,
                beat_in_bar=beat_in_bar,
                bar_number=beat_number // grid.beats_per_bar,
                is_bar=beat_in_bar == 0,
                is_square=beat_number % beats_per_square == 0,
            )
        )
    return tuple(markers)


def nearest_warped_grid_position(
    grid: BeatGridAnalysis,
    position_ms: int,
    anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor] = (),
) -> tuple[int, int]:
    """Возвращает позицию и номер ближайшей доли редактируемой сетки."""
    beat_number = round(beat_number_at_position(grid, position_ms, anchors))
    return beat_position_ms(grid, beat_number, anchors), beat_number


def regular_grid_markers(
    grid: BeatGridAnalysis,
    duration_ms: int,
    *,
    square_bars: int = 8,
) -> tuple[BeatGridMarker, ...]:
    """Строит линии долей по BPM и якорю первой сильной доли."""
    return warped_grid_markers(grid, duration_ms, square_bars=square_bars)


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
