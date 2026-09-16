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


@dataclass(frozen=True, slots=True)
class TimelineTransition:
    """Точки соседней пары, которые должны совпасть на общей шкале."""

    outgoing_track_id: int
    incoming_track_id: int
    outgoing_cue_ms: int
    incoming_cue_ms: int
    overlap_ms: int


@dataclass(frozen=True, slots=True)
class TimelineClip:
    """Положение одного трека на верхней или нижней монтажной дорожке."""

    track_id: int
    index: int
    lane: int
    start_ms: int
    duration_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


@dataclass(frozen=True, slots=True)
class TimelineLayout:
    clips: tuple[TimelineClip, ...]
    transition_positions_ms: tuple[int, ...]
    duration_ms: int


def build_playlist_timeline(
    tracks: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    transitions: list[TimelineTransition] | tuple[TimelineTransition, ...],
) -> TimelineLayout:
    """Совмещает Cue соседних треков и чередует клипы по двум дорожкам."""
    if not tracks:
        return TimelineLayout((), (), 0)
    transition_by_pair = {
        (item.outgoing_track_id, item.incoming_track_id): item
        for item in transitions
    }
    clips = [
        TimelineClip(
            track_id=tracks[0][0],
            index=0,
            lane=0,
            start_ms=0,
            duration_ms=max(1, tracks[0][1]),
        )
    ]
    positions: list[int] = []
    for index, ((previous_id, _), (track_id, duration_ms)) in enumerate(
        zip(tracks, tracks[1:])
    ):
        transition = transition_by_pair.get((previous_id, track_id))
        if transition is None:
            raise SetTimelineError(
                f"Не заданы точки перехода {previous_id} → {track_id}"
            )
        previous = clips[-1]
        position = previous.start_ms + transition.outgoing_cue_ms
        start_ms = position - transition.incoming_cue_ms
        positions.append(position)
        clips.append(
            TimelineClip(
                track_id=track_id,
                index=index + 1,
                lane=(index + 1) % 2,
                start_ms=start_ms,
                duration_ms=max(1, duration_ms),
            )
        )
    shift_ms = max(0, -min(item.start_ms for item in clips))
    if shift_ms:
        clips = [
            TimelineClip(
                track_id=item.track_id,
                index=item.index,
                lane=item.lane,
                start_ms=item.start_ms + shift_ms,
                duration_ms=item.duration_ms,
            )
            for item in clips
        ]
        positions = [value + shift_ms for value in positions]
    duration_ms = max(item.end_ms for item in clips)
    return TimelineLayout(tuple(clips), tuple(positions), duration_ms)


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


def snap_to_square(
    position_ms: int,
    bpm: float,
    bars_per_square: int,
    *,
    anchor_ms: int = 0,
) -> int:
    """Привязывает целый клип к ближайшей границе музыкального квадрата."""
    bpm = validate_bpm(bpm)
    if bars_per_square not in ALLOWED_BARS_PER_SQUARE:
        raise SetTimelineError("Размер квадрата должен быть 4, 8 или 16 тактов")
    square_ms = 60_000 / bpm * BEATS_PER_BAR * bars_per_square
    relative = position_ms - anchor_ms
    return max(0, round(anchor_ms + round(relative / square_ms) * square_ms))


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
