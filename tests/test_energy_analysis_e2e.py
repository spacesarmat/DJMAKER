"""Честный e2e-тест: реальный FFmpeg + реальный Librosa на настоящем MP3-файле.

В отличие от test_energy_analysis.py (юнит-тесты с моками/синтетическими
numpy-массивами), здесь декодирование идёт через реальный управляемый FFmpeg
и реальный MP3-файл на диске — проверяет саму проводку subprocess/PCM/буфер,
а не только формулу свёртки признаков.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.config import default_data_dir
from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.energy_analysis import (
    compute_energy_features,
    decode_mono_pcm,
    energy_score,
)


def _write_minimal_silent_mp3(path: Path, *, frame_count: int = 100) -> None:
    """Минимальный валидный MPEG1 Layer3 44.1kHz/128kbps файл (тишина).

    Тот же приём, что и в остальных честных e2e-тестах проекта: реальный
    заголовок фрейма + нулевая полезная нагрузка декодируется FFmpeg как
    валидная (хоть и тихая) дорожка, без необходимости в настоящем аудио-ассете.
    """
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    frame_length = 417
    frame = header + bytes(frame_length - len(header))
    path.write_bytes(frame * frame_count)


class RealFfmpegEnergyAnalysisE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = RuntimeDependencies(default_data_dir())
        status = cls.runtime.probe().ffmpeg
        if not status.available:
            raise unittest.SkipTest(
                "Управляемый FFmpeg не установлен на этой машине "
                "(запустите проверку аудио-компонентов в приложении)"
            )

    def test_real_decode_and_real_librosa_produce_bounded_energy_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            mp3_path = Path(temp) / "silent.mp3"
            _write_minimal_silent_mp3(mp3_path)

            samples = decode_mono_pcm(self.runtime, mp3_path)
            self.assertGreater(samples.size, 0)

            features = compute_energy_features(samples)
            score = energy_score(features, bpm=128.0)

            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_silent_real_file_scores_low_energy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            mp3_path = Path(temp) / "silent.mp3"
            _write_minimal_silent_mp3(mp3_path)

            samples = decode_mono_pcm(self.runtime, mp3_path)
            features = compute_energy_features(samples)
            score = energy_score(features, bpm=128.0)

            # Тишина не должна давать высокий энергетический скор.
            self.assertLess(score, 20.0)

    def test_longer_real_file_produces_more_pcm_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            short_path = Path(temp) / "short.mp3"
            long_path = Path(temp) / "long.mp3"
            _write_minimal_silent_mp3(short_path, frame_count=20)
            _write_minimal_silent_mp3(long_path, frame_count=200)

            short_samples = decode_mono_pcm(self.runtime, short_path)
            long_samples = decode_mono_pcm(self.runtime, long_path)

            self.assertGreater(long_samples.size, short_samples.size)


if __name__ == "__main__":
    unittest.main()
