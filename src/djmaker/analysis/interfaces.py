"""Интерфейсы будущих DSP-модулей DJMAKER."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LoudnessResult:
    """Результат будущего анализа громкости."""

    integrated_lufs: float
    true_peak_dbfs: float


@dataclass(slots=True)
class MusicalAnalysisResult:
    """Результат будущего анализа BPM и тональности."""

    bpm: float
    musical_key: str
    camelot: str


class Normalizer(ABC):
    """Контракт отдельного модуля нормализации."""

    @abstractmethod
    def normalize(self, source: Path, destination: Path, target_lufs: float = -11.5) -> Path:
        """Создаёт нормализованную копию аудиофайла."""
        raise NotImplementedError


class MusicalAnalyzer(ABC):
    """Контракт будущего движка BPM/Key."""

    @abstractmethod
    def analyze(self, source: Path) -> MusicalAnalysisResult:
        """Определяет BPM, Key и Camelot-код."""
        raise NotImplementedError
