"""Жанровое уточнение цвета энергии через AST (AudioSet), ONNX Runtime.

Опциональный шаг: если модель не скачана/выключена/упала — classify()
возвращает "" (нет сигнала), решение о цвете остаётся за DSP-эвристикой
(см. energy_color в domain-слое).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from djmaker.domain.energy_color import GENRE_TAG_AMBIENT, GENRE_TAG_HEAVY_DARK
from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.ast_preprocessing import AST_SAMPLE_RATE, AST_WINDOW_SAMPLES, extract_ast_input

LOGGER = logging.getLogger(__name__)

# Индексы классов AudioSet (config.json id2label модели onnx-community/
# ast-finetuned-audioset-10-10-0.4593-ONNX) для двух жанровых бакетов,
# которые чистая энергия/шумность не различают надёжно.
HEAVY_DARK_LABEL_INDICES: dict[int, str] = {220: "Heavy metal", 221: "Punk rock"}
AMBIENT_LABEL_INDICES: dict[int, str] = {246: "Ambient music", 253: "New-age music"}
GENRE_CONFIDENCE_THRESHOLD = 0.3
MAX_WINDOWS_PER_TRACK = 12


def select_window_starts(
    total_samples: int,
    window_samples: int = AST_WINDOW_SAMPLES,
    max_windows: int = MAX_WINDOWS_PER_TRACK,
) -> list[int]:
    """Равномерно расставляет не более max_windows стартов окон по треку."""
    if total_samples <= 0:
        return []
    last_start = max(0, total_samples - window_samples)
    windows_needed = total_samples // window_samples + (
        1 if total_samples % window_samples else 0
    )
    count = min(max_windows, max(1, windows_needed))
    if count <= 1:
        return [0]
    step = last_start / (count - 1)
    return [round(i * step) for i in range(count)]


def decide_genre_tag(mean_probs: np.ndarray) -> str:
    """Выбирает жанровый бакет по усреднённым по окнам вероятностям AudioSet."""
    heavy_score = max(float(mean_probs[i]) for i in HEAVY_DARK_LABEL_INDICES)
    ambient_score = max(float(mean_probs[i]) for i in AMBIENT_LABEL_INDICES)
    if heavy_score < GENRE_CONFIDENCE_THRESHOLD and ambient_score < GENRE_CONFIDENCE_THRESHOLD:
        return ""
    return GENRE_TAG_HEAVY_DARK if heavy_score >= ambient_score else GENRE_TAG_AMBIENT


def select_onnx_providers(available: Sequence[str], *, gpu_enabled: bool) -> list[str]:
    """Выбирает провайдеры ONNX Runtime: GPU (если включён и доступен) + CPU-фоллбек."""
    if not gpu_enabled:
        return ["CPUExecutionProvider"]
    preferred = (
        "DmlExecutionProvider",  # Windows: DirectML, покрывает AMD и NVIDIA
        "CoreMLExecutionProvider",  # macOS: Apple Silicon/AMD через CoreML
        "CUDAExecutionProvider",
        "ROCMExecutionProvider",
    )
    ordered = [name for name in preferred if name in available]
    ordered.append("CPUExecutionProvider")
    return ordered


def _sigmoid(logits: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-logits))


class GenreClassifier:
    """Ленивый ONNX-инференс AST поверх уже декодированного 44.1 kHz PCM."""

    def __init__(self, runtime: RuntimeDependencies, *, gpu_enabled: bool = True) -> None:
        self.runtime = runtime
        self.gpu_enabled = gpu_enabled
        self._session = None
        self._session_attempted = False

    def available(self) -> bool:
        return self.runtime.ast_model_path() is not None

    def classify(self, waveform: np.ndarray, sample_rate: int) -> str:
        """Возвращает жанровый бакет ("heavy_dark"/"ambient"/"") для mono PCM.

        Никогда не бросает исключение: любая ошибка инференса — это просто
        отсутствие жанрового сигнала, а не повод останавливать анализ трека.
        """
        session = self._session_or_none()
        if session is None or waveform.size == 0:
            return ""

        try:
            return self._classify_with_session(session, waveform, sample_rate)
        except Exception as exc:  # noqa: BLE001 - см. докстринг: никогда не роняем анализ
            LOGGER.warning("Ошибка жанрового AST-инференса: %s", exc)
            return ""

    def _classify_with_session(self, session, waveform: np.ndarray, sample_rate: int) -> str:
        import librosa  # ленивый импорт: тяжёлая зависимость

        waveform_16k = librosa.resample(
            waveform.astype(np.float32), orig_sr=sample_rate, target_sr=AST_SAMPLE_RATE
        )
        starts = select_window_starts(waveform_16k.size)
        if not starts:
            return ""

        probability_sum = np.zeros(527, dtype=np.float64)
        for start in starts:
            chunk = waveform_16k[start : start + AST_WINDOW_SAMPLES]
            if chunk.size < AST_WINDOW_SAMPLES:
                chunk = np.pad(chunk, (0, AST_WINDOW_SAMPLES - chunk.size))
            features = extract_ast_input(chunk)
            logits = session.run(None, {"input_values": features[np.newaxis, :, :]})[0][0]
            probability_sum += _sigmoid(logits)

        mean_probs = probability_sum / len(starts)
        return decide_genre_tag(mean_probs)

    def _session_or_none(self):
        if self._session is not None:
            return self._session
        if self._session_attempted:
            return None
        self._session_attempted = True

        model_path = self.runtime.ast_model_path()
        if model_path is None:
            return None
        try:
            self._session = _create_onnx_session(model_path, gpu_enabled=self.gpu_enabled)
        except Exception as exc:  # noqa: BLE001 - любая ошибка ONNX не должна ронять анализ
            LOGGER.warning("Не удалось инициализировать AST ONNX сессию: %s", exc)
            return None
        return self._session


def _create_onnx_session(model_path: Path, *, gpu_enabled: bool):
    import onnxruntime as ort

    options = ort.SessionOptions()
    # ORT_ENABLE_ALL ломает загрузку этой модели (баг слияния LayerNorm в
    # графе onnxruntime); ORT_ENABLE_EXTENDED — максимум без этого бага.
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED

    available = ort.get_available_providers()
    providers = select_onnx_providers(available, gpu_enabled=gpu_enabled)
    try:
        return ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
    except Exception:
        if providers == ["CPUExecutionProvider"]:
            raise
        LOGGER.warning("GPU-провайдер ONNX недоступен, переключаюсь на CPU")
        return ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
