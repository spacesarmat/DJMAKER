"""Музыкальная сетка и параметры перехода между соседними треками."""

from __future__ import annotations

from dataclasses import dataclass


BEATS_PER_BAR = 4
DEFAULT_BARS_PER_SQUARE = 8
DEFAULT_SQUARE_COUNT = 1
ALLOWED_BARS_PER_SQUARE = (4, 8, 16)
ALLOWED_SQUARE_COUNTS = (1, 2, 4)


class SetTimelineError(ValueError):
    """Некорректные параметры музыкальной сетки или перехода."""


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    """Рассчитанный фрагмент для точного предпрослушивания перехода."""

    outgoing_cue_ms: int
    incoming_cue_ms: int
    target_bpm: float
    incoming_bpm: float
    bars_per_square: int = DEFAULT_BARS_PER_SQUARE
    square_count: int = DEFAULT_SQUARE_COUNT
    preview_bars: int = 2

    @property
    def beat_ms(self) -> float:
        return 60_000 / self.target_bpm

    @property
    def overlap_ms(self) -> int:
        beats = self.bars_per_square * BEATS_PER_BAR * self.square_count
        return round(beats * self.beat_ms)

    @property
    def preview_margin_ms(self) -> int:
        return round(self.preview_bars * BEATS_PER_BAR * self.beat_ms)

    @property
    def incoming_tempo(self) -> float:
        return self.target_bpm / self.incoming_bpm


def validate_bpm(value: float | int | None, label: str = "BPM") -> float:
    try:
        bpm = float(value)
    except (TypeError, ValueError) as exc:
        raise SetTimelineError(f"{label} не определён") from exc
    if not 30 <= bpm <= 300:
        raise SetTimelineError(f"{label} должен быть от 30 до 300")
    return bpm


def snap_to_beat(position_ms: int, bpm: float, *, anchor_ms: int = 0) -> int:
    """Привязывает позицию к ближайшей доле относительно ручного якоря."""
    bpm = validate_bpm(bpm)
    beat_ms = 60_000 / bpm
    relative = max(0, position_ms - anchor_ms)
    return max(0, round(anchor_ms + round(relative / beat_ms) * beat_ms))


def move_by_beats(position_ms: int, beats: int, bpm: float) -> int:
    """Сдвигает точку на целое число долей и не уходит левее начала."""
    bpm = validate_bpm(bpm)
    return max(0, round(position_ms + beats * 60_000 / bpm))


def build_transition_plan(
    *,
    outgoing_cue_ms: int,
    incoming_cue_ms: int,
    outgoing_bpm: float | None,
    incoming_bpm: float | None,
    outgoing_duration_ms: int,
    incoming_duration_ms: int,
    bars_per_square: int = DEFAULT_BARS_PER_SQUARE,
    square_count: int = DEFAULT_SQUARE_COUNT,
) -> TransitionPlan:
    """Проверяет границы треков и создаёт план beatmatched-preview."""
    target_bpm = validate_bpm(outgoing_bpm, "BPM первого трека")
    incoming_bpm_value = validate_bpm(incoming_bpm, "BPM второго трека")
    if bars_per_square not in ALLOWED_BARS_PER_SQUARE:
        raise SetTimelineError("Размер квадрата должен быть 4, 8 или 16 тактов")
    if square_count not in ALLOWED_SQUARE_COUNTS:
        raise SetTimelineError("Наложение должно занимать 1, 2 или 4 квадрата")
    if outgoing_cue_ms < 0 or incoming_cue_ms < 0:
        raise SetTimelineError("Точка перехода не может быть отрицательной")

    plan = TransitionPlan(
        outgoing_cue_ms=outgoing_cue_ms,
        incoming_cue_ms=incoming_cue_ms,
        target_bpm=target_bpm,
        incoming_bpm=incoming_bpm_value,
        bars_per_square=bars_per_square,
        square_count=square_count,
    )
    if plan.incoming_tempo < 0.5 or plan.incoming_tempo > 2:
        raise SetTimelineError("Разница BPM слишком велика для предпрослушивания")
    if outgoing_cue_ms + plan.overlap_ms > outgoing_duration_ms:
        raise SetTimelineError("После точки первого трека не хватает места для наложения")
    incoming_source_ms = round(
        (plan.overlap_ms + plan.preview_margin_ms) * plan.incoming_tempo
    )
    if incoming_cue_ms + incoming_source_ms > incoming_duration_ms:
        raise SetTimelineError("После точки второго трека не хватает места для preview")
    return plan
