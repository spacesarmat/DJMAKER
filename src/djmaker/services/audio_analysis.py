"""DSP-анализ BPM/тональности через FFmpeg и собственный Essentia runtime."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from djmaker.domain.models import AudioAnalysis
from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.tasks import TaskControl, TaskInterrupted


LOGGER = logging.getLogger(__name__)
ANALYSIS_SAMPLE_RATE = 44_100
ANALYSIS_TIMEOUT_SECONDS = 60 * 30


def recommended_analysis_concurrency(cpu_count: int | None = None) -> int:
    """Возвращает безопасное число параллельных FFmpeg/Essentia задач."""
    processors = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    if processors <= 1:
        return 1
    return min(4, max(2, processors // 2))


class AudioAnalysisError(RuntimeError):
    """Ошибка декодирования или DSP-анализа аудиофайла."""


class EssentiaAudioAnalyzer:
    """Соединяет FFmpeg stdout с stdin собственного ``djmaker-essentia``."""

    def __init__(self, runtime: RuntimeDependencies) -> None:
        self.runtime = runtime

    def analyze(
        self,
        path: Path,
        task: TaskControl | None = None,
    ) -> AudioAnalysis:
        """Возвращает BPM, Key и Camelot для одного аудиофайла.

        ``task`` позволяет менеджеру задач остановить активные FFmpeg/Essentia
        процессы, а затем продолжить пакет с оставшихся треков.
        """
        if task is not None:
            task.checkpoint()

        source = path.expanduser().resolve()
        if not source.is_file():
            raise AudioAnalysisError(f"Аудиофайл не найден: {source}")

        ffmpeg = self.runtime.ffmpeg_path()
        essentia = self.runtime.essentia_analyzer_path()
        if ffmpeg is None:
            raise AudioAnalysisError("FFmpeg недоступен. Запустите проверку аудио-компонентов.")
        if essentia is None:
            raise AudioAnalysisError(
                "DJMAKER Essentia runtime недоступен. Запустите проверку аудио-компонентов."
            )

        ffmpeg_command = build_ffmpeg_command(ffmpeg, source)
        essentia_command = build_essentia_command(essentia)
        creationflags = _creation_flags()

        with tempfile.TemporaryFile() as ffmpeg_stderr:
            decoder = subprocess.Popen(
                ffmpeg_command,
                stdout=subprocess.PIPE,
                stderr=ffmpeg_stderr,
                creationflags=creationflags,
            )
            if decoder.stdout is None:
                decoder.kill()
                raise AudioAnalysisError("FFmpeg не открыл PCM pipe")

            try:
                analyzer = subprocess.Popen(
                    essentia_command,
                    stdin=decoder.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                )
            except BaseException:
                decoder.stdout.close()
                _terminate_process(decoder)
                raise
            finally:
                # После запуска Essentia родительская копия read-end больше не нужна.
                decoder.stdout.close()

            deadline = time.monotonic() + ANALYSIS_TIMEOUT_SECONDS
            try:
                while True:
                    if task is not None:
                        task.checkpoint()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(
                            essentia_command, ANALYSIS_TIMEOUT_SECONDS
                        )
                    try:
                        analysis_stdout, analysis_stderr = analyzer.communicate(
                            timeout=min(0.25, remaining)
                        )
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except TaskInterrupted:
                _terminate_process(analyzer)
                _terminate_process(decoder)
                raise
            except subprocess.TimeoutExpired as exc:
                _terminate_process(analyzer)
                _terminate_process(decoder)
                raise AudioAnalysisError(
                    f"Анализ превысил лимит {ANALYSIS_TIMEOUT_SECONDS // 60} минут"
                ) from exc

            try:
                decoder_code = decoder.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                _terminate_process(decoder)
                raise AudioAnalysisError("FFmpeg не завершился после анализа") from exc

            ffmpeg_stderr.seek(0)
            decoder_error = ffmpeg_stderr.read().decode(
                "utf-8", errors="replace"
            ).strip()

        analyzer_error = analysis_stderr.decode("utf-8", errors="replace").strip()
        if analyzer.returncode != 0:
            detail = analyzer_error or f"exit code {analyzer.returncode}"
            raise AudioAnalysisError(f"Essentia: {detail}")
        if decoder_code != 0:
            detail = decoder_error or f"exit code {decoder_code}"
            raise AudioAnalysisError(f"FFmpeg: {detail}")

        return parse_analysis_payload(analysis_stdout)


def build_ffmpeg_command(executable: Path, source: Path) -> list[str]:
    """Строит команду декодирования в mono float32 PCM 44.1 kHz."""
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
        "-ac",
        "1",
        "-ar",
        str(ANALYSIS_SAMPLE_RATE),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]


def build_essentia_command(executable: Path) -> list[str]:
    """Строит команду собственного Essentia bridge."""
    return [
        str(executable),
        "analyze",
        "--input",
        "-",
        "--sample-rate",
        str(ANALYSIS_SAMPLE_RATE),
        "--method",
        "multifeature",
        "--min-tempo",
        "40",
        "--max-tempo",
        "208",
    ]


def parse_analysis_payload(payload: bytes | str) -> AudioAnalysis:
    """Проверяет JSON собственного Essentia bridge и строит доменный результат."""
    text = payload.decode("utf-8", errors="strict") if isinstance(payload, bytes) else payload
    try:
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioAnalysisError(f"Essentia вернула некорректный JSON: {text[:300]!r}") from exc
    if not isinstance(data, dict):
        raise AudioAnalysisError("Essentia вернула JSON неожиданного типа")

    bpm = _optional_float(data.get("bpm"), "bpm")
    bpm_confidence = _optional_float(data.get("bpm_confidence"), "bpm_confidence")
    musical_key = str(data.get("key") or "").strip()
    scale = str(data.get("scale") or "").strip().lower()
    key_strength = _optional_float(data.get("key_strength"), "key_strength")

    if bpm is not None and bpm <= 0:
        bpm = None
    if scale not in {"major", "minor"}:
        scale = ""

    return AudioAnalysis(
        bpm=bpm,
        bpm_confidence=bpm_confidence,
        musical_key=musical_key,
        scale=scale,
        key_strength=key_strength,
        camelot=camelot_code(musical_key, scale),
    )


def camelot_code(musical_key: str, scale: str) -> str:
    """Конвертирует обычную тональность Essentia в Camelot wheel."""
    semitone = _pitch_class(musical_key)
    normalized_scale = scale.strip().lower()
    if semitone is None or normalized_scale not in {"major", "minor"}:
        return ""

    minor = {
        8: "1A",
        3: "2A",
        10: "3A",
        5: "4A",
        0: "5A",
        7: "6A",
        2: "7A",
        9: "8A",
        4: "9A",
        11: "10A",
        6: "11A",
        1: "12A",
    }
    major = {
        11: "1B",
        6: "2B",
        1: "3B",
        8: "4B",
        3: "5B",
        10: "6B",
        5: "7B",
        0: "8B",
        7: "9B",
        2: "10B",
        9: "11B",
        4: "12B",
    }
    return (minor if normalized_scale == "minor" else major)[semitone]


def _pitch_class(value: str) -> int | None:
    key = value.strip().replace("♯", "#").replace("♭", "b")
    aliases = {
        "C": 0,
        "B#": 0,
        "C#": 1,
        "Db": 1,
        "D": 2,
        "D#": 3,
        "Eb": 3,
        "E": 4,
        "Fb": 4,
        "E#": 5,
        "F": 5,
        "F#": 6,
        "Gb": 6,
        "G": 7,
        "G#": 8,
        "Ab": 8,
        "A": 9,
        "A#": 10,
        "Bb": 10,
        "B": 11,
        "Cb": 11,
    }
    return aliases.get(key)


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AudioAnalysisError(f"Поле {field} имеет неверный тип")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AudioAnalysisError(f"Поле {field} имеет неверный тип") from exc


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Завершает дочерний процесс и гарантированно собирает его exit status."""
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _creation_flags() -> int:
    """Не показывает консольные окна FFmpeg/Essentia в Windows GUI."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

