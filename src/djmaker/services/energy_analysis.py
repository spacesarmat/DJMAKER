"""Анализ энергетики (AIR) трека через Librosa: громкость, онсеты, яркость, шумность."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.audio_analysis import (
    ANALYSIS_SAMPLE_RATE,
    AudioAnalysisError,
    build_ffmpeg_command,
)

LOGGER = logging.getLogger(__name__)

DECODE_TIMEOUT_SECONDS = 120

# Нормировочные диапазоны подобраны эмпирически по типичной поп/электронной
# музыке; пороги можно уточнять независимо от формулы свёртки.
_RMS_DB_RANGE = (-40.0, -6.0)
_CENTROID_HZ_RANGE = (500.0, 6000.0)
_ONSET_RATE_RANGE = (0.0, 8.0)
_BPM_RANGE = (60.0, 180.0)

_WEIGHT_RMS = 0.4
_WEIGHT_ONSET = 0.3
_WEIGHT_BRIGHTNESS = 0.2
_WEIGHT_TEMPO = 0.1


@dataclass(frozen=True, slots=True)
class EnergyFeatures:
    """Сырые признаки Librosa до свёртки в единую шкалу энергии."""

    rms_db: float
    onset_rate_per_second: float
    spectral_centroid_hz: float
    spectral_flatness: float


def decode_mono_pcm(runtime: RuntimeDependencies, path: Path) -> "np.ndarray":
    """Декодирует файл управляемым FFmpeg в mono float32 numpy-массив.

    Отдельный проход декодирования от EssentiaAudioAnalyzer: там FFmpeg
    пишет напрямую в stdin другого процесса (zero-copy pipe), а Librosa
    нужен готовый массив в памяти Python.
    """
    ffmpeg = runtime.ffmpeg_path()
    if ffmpeg is None:
        raise AudioAnalysisError("FFmpeg недоступен. Запустите проверку аудио-компонентов.")

    command = build_ffmpeg_command(ffmpeg, path)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=DECODE_TIMEOUT_SECONDS,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError(
            "Декодирование для анализа энергии превысило лимит времени"
        ) from exc

    if result.returncode != 0:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or f"exit code {result.returncode}"
        )
        raise AudioAnalysisError(f"FFmpeg: {detail}")

    samples = np.frombuffer(result.stdout, dtype=np.float32)
    if samples.size == 0:
        raise AudioAnalysisError("FFmpeg вернул пустой поток PCM для анализа энергии")
    return samples


def compute_energy_features(
    samples: "np.ndarray", sample_rate: int = ANALYSIS_SAMPLE_RATE
) -> EnergyFeatures:
    """Считает сырые Librosa-признаки для массива mono float32 сэмплов."""
    import librosa  # ленивый импорт: тяжёлая зависимость, нужна только здесь

    if samples.size == 0:
        raise AudioAnalysisError("Пустой аудио-буфер для анализа энергии")

    rms = librosa.feature.rms(y=samples)[0]
    rms_mean = float(np.mean(rms))
    rms_db = 20.0 * float(np.log10(max(rms_mean, 1e-9)))

    centroid = librosa.feature.spectral_centroid(y=samples, sr=sample_rate)[0]
    centroid_hz = float(np.mean(centroid))

    flatness = librosa.feature.spectral_flatness(y=samples)[0]
    flatness_mean = float(np.clip(np.mean(flatness), 0.0, 1.0))

    onset_env = librosa.onset.onset_strength(y=samples, sr=sample_rate)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sample_rate)
    duration_seconds = samples.size / float(sample_rate)
    onset_rate = len(onset_frames) / duration_seconds if duration_seconds > 0 else 0.0

    return EnergyFeatures(
        rms_db=rms_db,
        onset_rate_per_second=onset_rate,
        spectral_centroid_hz=centroid_hz,
        spectral_flatness=flatness_mean,
    )


def energy_score(features: EnergyFeatures, bpm: float | None) -> float:
    """Взвешенная свёртка сырых признаков в единую шкалу энергии 0-100.

    Веса: громкость (RMS) 40%, плотность онсетов 30%, яркость спектра 20%,
    темп (BPM) 10%. При отсутствии BPM используется нейтральный вклад 0.5.
    """
    rms_component = _normalize(features.rms_db, *_RMS_DB_RANGE)
    onset_component = _normalize(features.onset_rate_per_second, *_ONSET_RATE_RANGE)
    brightness_component = _normalize(features.spectral_centroid_hz, *_CENTROID_HZ_RANGE)
    tempo_component = 0.5 if bpm is None else _normalize(bpm, *_BPM_RANGE)

    score = (
        _WEIGHT_RMS * rms_component
        + _WEIGHT_ONSET * onset_component
        + _WEIGHT_BRIGHTNESS * brightness_component
        + _WEIGHT_TEMPO * tempo_component
    )
    return round(100.0 * min(max(score, 0.0), 1.0), 1)


def analyze_energy(
    runtime: RuntimeDependencies, path: Path, bpm: float | None
) -> tuple[float, float]:
    """Полный проход: decode -> Librosa-признаки -> (energy_score, noisiness)."""
    samples = decode_mono_pcm(runtime, path)
    features = compute_energy_features(samples)
    return energy_score(features, bpm), features.spectral_flatness


def _normalize(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 0.5
    return min(max((value - minimum) / (maximum - minimum), 0.0), 1.0)
