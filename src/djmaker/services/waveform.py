"""Анализ компактной формы волны через FFmpeg."""

from __future__ import annotations

import math
import struct
import subprocess
import time
from pathlib import Path

from djmaker.domain.models import WaveformAnalysis
from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.audio_analysis import _creation_flags, _terminate_process
from djmaker.services.tasks import TaskControl, TaskInterrupted


WAVEFORM_BAR_COUNT = 60
WAVEFORM_SAMPLE_RATE = 800
WAVEFORM_TIMEOUT_SECONDS = 60 * 10


class WaveformAnalysisError(RuntimeError):
    """Ошибка декодирования или построения формы волны."""


class WaveformAnalyzer:
    """Строит нормализованные пики формы волны для одного аудиофайла."""

    def __init__(self, runtime: RuntimeDependencies) -> None:
        self.runtime = runtime

    def analyze(
        self,
        path: Path,
        task: TaskControl | None = None,
    ) -> WaveformAnalysis:
        """Декодирует mono float32 PCM и сворачивает его в фиксированный набор пиков."""
        if task is not None:
            task.checkpoint()

        source = path.expanduser().resolve()
        if not source.is_file():
            raise WaveformAnalysisError(f"Аудиофайл не найден: {source}")

        ffmpeg = self.runtime.ffmpeg_path()
        if ffmpeg is None:
            raise WaveformAnalysisError(
                "FFmpeg недоступен. Запустите проверку аудио-компонентов."
            )

        command = build_waveform_command(ffmpeg, source)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        deadline = time.monotonic() + WAVEFORM_TIMEOUT_SECONDS
        try:
            while True:
                if task is not None:
                    task.checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, WAVEFORM_TIMEOUT_SECONDS)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except TaskInterrupted:
            _terminate_process(process)
            raise
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            raise WaveformAnalysisError(
                f"Анализ waveform превысил лимит {WAVEFORM_TIMEOUT_SECONDS // 60} минут"
            ) from exc

        error = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise WaveformAnalysisError(
                f"FFmpeg: {error or f'exit code {process.returncode}'}"
            )
        return WaveformAnalysis(peaks=extract_waveform_peaks(stdout))


def build_waveform_command(executable: Path, source: Path) -> list[str]:
    """Строит дешёвый mono PCM поток для визуальной формы волны."""
    return [
        str(executable),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-threads",
        "1",
        "-ac",
        "1",
        "-ar",
        str(WAVEFORM_SAMPLE_RATE),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]


def extract_waveform_peaks(
    payload: bytes,
    bar_count: int = WAVEFORM_BAR_COUNT,
) -> tuple[float, ...]:
    """Сворачивает float32 PCM в пики без промежуточного списка сэмплов."""
    if bar_count < 1:
        raise ValueError("bar_count должен быть >= 1")
    usable = len(payload) - (len(payload) % 4)
    sample_count = usable // 4
    if sample_count == 0:
        return tuple(0.0 for _ in range(bar_count))

    peaks = [0.0] * bar_count
    for index, (raw_sample,) in enumerate(
        struct.iter_unpack("<f", payload[:usable])
    ):
        sample = abs(raw_sample)
        if not math.isfinite(sample):
            continue
        bucket = min(bar_count - 1, (index * bar_count) // sample_count)
        if sample > peaks[bucket]:
            peaks[bucket] = sample

    ceiling = max(peaks, default=0.0)
    if ceiling <= 0:
        return tuple(0.0 for _ in peaks)

    normalized = []
    for peak in peaks:
        ratio = min(1.0, max(0.0, peak / ceiling))
        # Квадратный корень делает тихие фрагменты визуально читаемыми,
        # сохраняя реальные пики выше фоновой части.
        normalized.append(round(math.sqrt(ratio), 4))
    return tuple(normalized)
