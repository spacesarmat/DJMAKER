"""Log-mel спектрограмма для AST (Audio Spectrogram Transformer), без torch.

Воспроизводит numpy-фолбэк алгоритм ``transformers.ASTFeatureExtractor``
(mel_filter_bank/window_function/spectrogram из ``transformers.audio_utils``,
Apache-2.0) один в один, чтобы не тянуть torch/torchaudio ради одной
фичи-экстракции: kaldi mel-шкала, окно Hann 400 сэмплов, шаг 160, FFT 512,
преэмфаза 0.97, лог по натуральному логарифму, нормализация (x-mean)/(std*2).
"""

from __future__ import annotations

import numpy as np

AST_SAMPLE_RATE = 16_000
AST_NUM_MEL_BINS = 128
AST_MAX_FRAMES = 1024
AST_FRAME_LENGTH = 400
AST_HOP_LENGTH = 160
AST_FFT_LENGTH = 512
AST_PREEMPHASIS = 0.97
AST_MEL_FLOOR = 1.192092955078125e-07
AST_MEAN = -4.2677393
AST_STD = 4.5689974

# Ровно столько сэмплов даёт AST_MAX_FRAMES кадров при center=False:
# (n_frames-1)*hop + frame_length.
AST_WINDOW_SAMPLES = (AST_MAX_FRAMES - 1) * AST_HOP_LENGTH + AST_FRAME_LENGTH


def _hertz_to_mel_kaldi(freq: np.ndarray | float) -> np.ndarray | float:
    return 1127.0 * np.log(1.0 + (freq / 700.0))


def _mel_to_hertz_kaldi(mels: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (np.exp(mels / 1127.0) - 1.0)


def _triangular_filter_bank(fft_freqs: np.ndarray, filter_freqs: np.ndarray) -> np.ndarray:
    filter_diff = np.diff(filter_freqs)
    slopes = np.expand_dims(filter_freqs, 0) - np.expand_dims(fft_freqs, 1)
    down_slopes = -slopes[:, :-2] / filter_diff[:-1]
    up_slopes = slopes[:, 2:] / filter_diff[1:]
    return np.maximum(np.zeros(1), np.minimum(down_slopes, up_slopes))


def build_kaldi_mel_filters(
    num_frequency_bins: int = AST_FFT_LENGTH // 2 + 1,
    num_mel_filters: int = AST_NUM_MEL_BINS,
    min_frequency: float = 20.0,
    max_frequency: float = AST_SAMPLE_RATE / 2,
    sampling_rate: int = AST_SAMPLE_RATE,
) -> np.ndarray:
    """Треугольный mel-фильтробанк на kaldi-шкале, триангуляция в mel-пространстве."""
    mel_min = _hertz_to_mel_kaldi(min_frequency)
    mel_max = _hertz_to_mel_kaldi(max_frequency)
    mel_freqs = np.linspace(mel_min, mel_max, num_mel_filters + 2)
    fft_bin_width = sampling_rate / ((num_frequency_bins - 1) * 2)
    fft_freqs = _hertz_to_mel_kaldi(fft_bin_width * np.arange(num_frequency_bins))
    return _triangular_filter_bank(fft_freqs, mel_freqs)


def hann_window(length: int = AST_FRAME_LENGTH) -> np.ndarray:
    """Периодическое окно Hann той же длины, что и фрейм (как в HF window_function)."""
    window = np.hanning(length + 1)
    return window[:-1]


_MEL_FILTERS = build_kaldi_mel_filters()
_WINDOW = hann_window()


def log_mel_spectrogram(waveform: np.ndarray) -> np.ndarray:
    """Log-mel спектрограмма формы (n_frames, AST_NUM_MEL_BINS), n_frames<=AST_MAX_FRAMES.

    ``waveform`` — mono float32/float64 сэмплы на AST_SAMPLE_RATE (16 kHz).
    """
    waveform = waveform.astype(np.float64)
    frame_length = AST_FRAME_LENGTH
    hop_length = AST_HOP_LENGTH
    fft_length = AST_FFT_LENGTH

    num_frames = max(0, 1 + (waveform.size - frame_length) // hop_length)
    if num_frames == 0:
        return np.zeros((0, AST_NUM_MEL_BINS), dtype=np.float32)

    num_frequency_bins = fft_length // 2 + 1
    spectrum = np.empty((num_frames, num_frequency_bins), dtype=np.complex128)
    buffer = np.zeros(fft_length)
    timestep = 0
    for frame_idx in range(num_frames):
        buffer[:frame_length] = waveform[timestep : timestep + frame_length]
        buffer[:frame_length] -= buffer[:frame_length].mean()  # remove_dc_offset
        buffer[1:frame_length] -= AST_PREEMPHASIS * buffer[: frame_length - 1]
        buffer[0] *= 1 - AST_PREEMPHASIS
        buffer[:frame_length] *= _WINDOW
        spectrum[frame_idx] = np.fft.rfft(buffer)
        timestep += hop_length

    power = np.abs(spectrum, dtype=np.float64) ** 2.0
    mel_power = np.maximum(AST_MEL_FLOOR, np.dot(_MEL_FILTERS.T, power.T))
    log_mel = np.log(mel_power).T
    return log_mel.astype(np.float32)


def pad_or_truncate(features: np.ndarray, max_length: int = AST_MAX_FRAMES) -> np.ndarray:
    """Дополняет нулями или обрезает по числу временных кадров до max_length."""
    difference = max_length - features.shape[0]
    if difference > 0:
        return np.pad(features, ((0, difference), (0, 0)))
    if difference < 0:
        return features[:max_length, :]
    return features


def normalize(features: np.ndarray, mean: float = AST_MEAN, std: float = AST_STD) -> np.ndarray:
    return (features - mean) / (std * 2)


def extract_ast_input(waveform_16k: np.ndarray) -> np.ndarray:
    """Полный проход: waveform (16kHz mono) -> нормализованный вход AST (1024, 128)."""
    features = log_mel_spectrogram(waveform_16k)
    features = pad_or_truncate(features)
    return normalize(features).astype(np.float32)
