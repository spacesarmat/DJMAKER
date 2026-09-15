"""Доменные модели DJMAKER."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class AudioMetadata:
    """Редактируемые музыкальные метаданные трека."""

    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    year: str = ""
    track_number: int | None = None
    disc_number: int | None = None
    bpm: float | None = None
    musical_key: str = ""


@dataclass(slots=True)
class AudioTechnicalInfo:
    """Технические свойства аудиофайла."""

    duration: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(slots=True)
class AudioAnalysis:
    """Результат DSP-анализа трека через FFmpeg + Essentia."""

    bpm: float | None = None
    bpm_confidence: float | None = None
    musical_key: str = ""
    scale: str = ""
    key_strength: float | None = None
    camelot: str = ""
    analyzed_at: str = ""


@dataclass(slots=True)
class InspectedAudio:
    """Результат локального анализа одного аудиофайла."""

    path: Path
    metadata: AudioMetadata
    technical: AudioTechnicalInfo


@dataclass(slots=True)
class TrackRecord:
    """Запись трека, загруженная из медиатеки."""

    id: int
    path: Path
    root_path: Path
    size: int
    mtime_ns: int
    extension: str
    file_hash: str | None
    metadata: AudioMetadata
    technical: AudioTechnicalInfo
    artwork_url: str | None = None
    analysis: AudioAnalysis | None = None


@dataclass(slots=True)
class DuplicateGroup:
    """Группа файлов с одинаковым точным хэшем."""

    file_hash: str
    tracks: list[TrackRecord] = field(default_factory=list)


@dataclass(slots=True)
class ScanStats:
    """Статистика одного сканирования каталога."""

    discovered: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    removed: int = 0


@dataclass(slots=True)
class MetadataCandidate:
    """Кандидат метаданных, найденный внешним провайдером."""

    provider_id: str
    external_id: str
    title: str
    artist: str
    album: str = ""
    year: str = ""
    release_id: str = ""
    artwork_url: str = ""
