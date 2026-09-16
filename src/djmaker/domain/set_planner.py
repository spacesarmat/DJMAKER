"""Детерминированный локальный подбор продолжения DJ-сета."""

from __future__ import annotations

from dataclasses import dataclass

from djmaker.domain.library_sort import camelot_order, positive_number
from djmaker.domain.models import TrackRecord


MATCH_MODE_LABELS = {
    "strict": "Строгий",
    "balanced": "Сбалансированный",
    "free": "Свободный",
}
DEFAULT_MATCH_MODE = "balanced"


@dataclass(frozen=True, slots=True)
class SetRecommendation:
    """Кандидат с прозрачной оценкой совместимости."""

    track: TrackRecord
    score: float
    bpm_difference_percent: float | None
    adjusted_bpm: float | None
    bpm_transform: str
    harmonic_rank: int | None
    reason: str


def track_bpm(track: TrackRecord) -> float | None:
    """DSP имеет приоритет над файловым BPM-тегом."""
    analysis_bpm = track.analysis.bpm if track.analysis is not None else None
    return positive_number(analysis_bpm) or positive_number(track.metadata.bpm)


def track_camelot_order(track: TrackRecord) -> int | None:
    """Возвращает позицию Camelot из DSP или обычного Key-тега."""
    analysis = track.analysis
    if analysis is not None:
        for value in (
            analysis.camelot,
            " ".join(part for part in (analysis.musical_key, analysis.scale) if part),
        ):
            order = camelot_order(value)
            if order is not None:
                return order
    return camelot_order(track.metadata.musical_key)


def camelot_code(order: int | None) -> str:
    if order is None or not 0 <= order < 24:
        return "—"
    return f"{order // 2 + 1}{'B' if order % 2 else 'A'}"


def _bpm_match(reference: float, candidate: float) -> tuple[float, float, str]:
    variants = (
        (candidate, ""),
        (candidate * 2, "×2"),
        (candidate / 2, "÷2"),
    )
    adjusted, transform = min(
        variants,
        key=lambda item: (abs(item[0] - reference) / reference, item[1] != ""),
    )
    difference = abs(adjusted - reference) / reference * 100
    return difference, adjusted, transform


def _harmonic_match(reference: int, candidate: int) -> tuple[int, str]:
    if reference == candidate:
        return 0, "та же тональность"
    reference_number, reference_mode = divmod(reference, 2)
    candidate_number, candidate_mode = divmod(candidate, 2)
    if reference_number == candidate_number and reference_mode != candidate_mode:
        return 1, "параллельный лад"
    distance = abs(reference_number - candidate_number)
    circular_distance = min(distance, 12 - distance)
    if reference_mode == candidate_mode and circular_distance == 1:
        return 1, "соседняя тональность"
    if reference_mode == candidate_mode and circular_distance == 2:
        return 2, "энергетический переход"
    return 4, "дальняя тональность"


def _recommendation(
    reference: TrackRecord,
    candidate: TrackRecord,
    mode: str,
) -> SetRecommendation | None:
    reference_bpm = track_bpm(reference)
    candidate_bpm = track_bpm(candidate)
    bpm_difference: float | None = None
    adjusted_bpm: float | None = None
    bpm_transform = ""
    if reference_bpm is not None and candidate_bpm is not None:
        bpm_difference, adjusted_bpm, bpm_transform = _bpm_match(
            reference_bpm, candidate_bpm
        )

    reference_key = track_camelot_order(reference)
    candidate_key = track_camelot_order(candidate)
    harmonic_rank: int | None = None
    harmonic_reason = "тональность неизвестна"
    if reference_key is not None and candidate_key is not None:
        harmonic_rank, harmonic_reason = _harmonic_match(reference_key, candidate_key)

    if mode == "strict":
        if (
            bpm_difference is None
            or bpm_difference > 3
            or harmonic_rank is None
            or harmonic_rank > 1
        ):
            return None
    elif mode == "balanced":
        if bpm_difference is None or bpm_difference > 6:
            return None
        if harmonic_rank is not None and harmonic_rank > 2:
            return None
    else:
        bpm_compatible = bpm_difference is not None and bpm_difference <= 12
        key_compatible = harmonic_rank is not None and harmonic_rank <= 1
        if not bpm_compatible and not key_compatible:
            return None

    reason_parts: list[str] = []
    if bpm_difference is not None and candidate_bpm is not None:
        transform = f" {bpm_transform}" if bpm_transform else ""
        reason_parts.append(
            f"BPM {candidate_bpm:.1f}{transform} · разница {bpm_difference:.1f}%"
        )
    else:
        reason_parts.append("BPM неизвестен")
    if reference_key is not None and candidate_key is not None:
        reason_parts.append(
            f"Camelot {camelot_code(reference_key)} → "
            f"{camelot_code(candidate_key)} · {harmonic_reason}"
        )
    else:
        reason_parts.append(harmonic_reason)

    score = bpm_difference if bpm_difference is not None else 14.0
    score += (harmonic_rank if harmonic_rank is not None else 4) * 2.5
    if bpm_transform:
        score += 0.25
    return SetRecommendation(
        track=candidate,
        score=score,
        bpm_difference_percent=bpm_difference,
        adjusted_bpm=adjusted_bpm,
        bpm_transform=bpm_transform,
        harmonic_rank=harmonic_rank,
        reason=" · ".join(reason_parts),
    )


def recommend_tracks(
    reference: TrackRecord,
    candidates: list[TrackRecord] | tuple[TrackRecord, ...],
    *,
    mode: str = DEFAULT_MATCH_MODE,
    excluded_track_ids: set[int] | frozenset[int] = frozenset(),
    limit: int = 50,
) -> list[SetRecommendation]:
    """Ранжирует кандидатов, не изменяя библиотеку и плейлист."""
    if mode not in MATCH_MODE_LABELS:
        mode = DEFAULT_MATCH_MODE
    excluded = set(excluded_track_ids)
    excluded.add(reference.id)
    recommendations = [
        item
        for track in candidates
        if track.id not in excluded
        if (item := _recommendation(reference, track, mode)) is not None
    ]
    recommendations.sort(
        key=lambda item: (
            item.score,
            item.track.metadata.artist.casefold(),
            item.track.metadata.title.casefold(),
            item.track.id,
        )
    )
    return recommendations[: max(0, limit)]
