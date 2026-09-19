from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from djmaker.services.audio_analysis import AudioAnalysisError, ANALYSIS_SAMPLE_RATE
from djmaker.services.energy_analysis import (
    EnergyFeatures,
    analyze_energy,
    compute_energy_features,
    decode_mono_pcm,
    energy_score,
)


class EnergyScoreFormulaTests(unittest.TestCase):
    def test_quiet_dark_slow_track_scores_low(self) -> None:
        features = EnergyFeatures(
            rms_db=-40.0,
            onset_rate_per_second=0.0,
            spectral_centroid_hz=500.0,
            spectral_flatness=0.05,
        )
        self.assertEqual(0.0, energy_score(features, bpm=60.0))

    def test_loud_bright_dense_fast_track_scores_high(self) -> None:
        features = EnergyFeatures(
            rms_db=-6.0,
            onset_rate_per_second=8.0,
            spectral_centroid_hz=6000.0,
            spectral_flatness=0.4,
        )
        self.assertEqual(100.0, energy_score(features, bpm=180.0))

    def test_missing_bpm_uses_neutral_tempo_contribution(self) -> None:
        features = EnergyFeatures(
            rms_db=-40.0,
            onset_rate_per_second=0.0,
            spectral_centroid_hz=500.0,
            spectral_flatness=0.0,
        )
        # Без остальных признаков должен остаться только нейтральный вклад темпа: 10%*0.5*100=5
        self.assertEqual(5.0, energy_score(features, bpm=None))

    def test_score_is_clamped_and_monotonic_in_rms(self) -> None:
        quiet = EnergyFeatures(-40.0, 1.0, 1000.0, 0.1)
        loud = EnergyFeatures(-6.0, 1.0, 1000.0, 0.1)
        self.assertLess(energy_score(quiet, bpm=120.0), energy_score(loud, bpm=120.0))

    def test_extreme_values_stay_within_bounds(self) -> None:
        extreme = EnergyFeatures(
            rms_db=50.0,  # выше диапазона нормализации
            onset_rate_per_second=999.0,
            spectral_centroid_hz=99_999.0,
            spectral_flatness=1.0,
        )
        score = energy_score(extreme, bpm=999.0)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)


class ComputeEnergyFeaturesRealLibrosaTests(unittest.TestCase):
    """Реальные (не мокнутые) вызовы Librosa на синтетических сигналах."""

    def test_quiet_pure_tone_has_low_flatness_and_low_rms(self) -> None:
        sr = ANALYSIS_SAMPLE_RATE
        t = np.linspace(0, 2.0, int(sr * 2), endpoint=False)
        quiet_tone = (0.02 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

        features = compute_energy_features(quiet_tone, sample_rate=sr)

        self.assertLess(features.spectral_flatness, 0.05)
        self.assertLess(features.rms_db, -20.0)

    def test_loud_noisy_signal_has_higher_energy_than_quiet_tone(self) -> None:
        sr = ANALYSIS_SAMPLE_RATE
        t = np.linspace(0, 2.0, int(sr * 2), endpoint=False)
        rng = np.random.default_rng(7)

        quiet_tone = (0.02 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        loud_noise = (0.8 * rng.standard_normal(t.shape[0])).astype(np.float32)

        quiet_features = compute_energy_features(quiet_tone, sample_rate=sr)
        loud_features = compute_energy_features(loud_noise, sample_rate=sr)

        quiet_score = energy_score(quiet_features, bpm=90.0)
        loud_score = energy_score(loud_features, bpm=150.0)

        self.assertGreater(loud_features.spectral_flatness, quiet_features.spectral_flatness)
        self.assertGreater(loud_score, quiet_score)

    def test_empty_buffer_raises(self) -> None:
        with self.assertRaises(AudioAnalysisError):
            compute_energy_features(np.array([], dtype=np.float32))


class DecodeMonoPcmTests(unittest.TestCase):
    def test_missing_ffmpeg_raises(self) -> None:
        runtime = Mock()
        runtime.ffmpeg_path = Mock(return_value=None)

        with self.assertRaisesRegex(AudioAnalysisError, "FFmpeg недоступен"):
            decode_mono_pcm(runtime, Path("/music/track.mp3"))

    def test_ffmpeg_failure_raises_with_stderr_detail(self) -> None:
        runtime = Mock()
        runtime.ffmpeg_path = Mock(return_value=Path("/opt/ffmpeg"))

        completed = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=1, stdout=b"", stderr=b"invalid data found"
        )
        with patch("djmaker.services.energy_analysis.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(AudioAnalysisError, "invalid data found"):
                decode_mono_pcm(runtime, Path("/music/track.mp3"))

    def test_empty_pcm_output_raises(self) -> None:
        runtime = Mock()
        runtime.ffmpeg_path = Mock(return_value=Path("/opt/ffmpeg"))

        completed = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=b"", stderr=b""
        )
        with patch("djmaker.services.energy_analysis.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(AudioAnalysisError, "пустой поток PCM"):
                decode_mono_pcm(runtime, Path("/music/track.mp3"))

    def test_successful_decode_returns_float32_array(self) -> None:
        runtime = Mock()
        runtime.ffmpeg_path = Mock(return_value=Path("/opt/ffmpeg"))
        payload = np.array([0.1, -0.2, 0.3], dtype=np.float32).tobytes()
        completed = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=payload, stderr=b""
        )
        with patch("djmaker.services.energy_analysis.subprocess.run", return_value=completed):
            samples = decode_mono_pcm(runtime, Path("/music/track.mp3"))

        self.assertEqual(3, samples.size)
        self.assertEqual(np.float32, samples.dtype)


class AnalyzeEnergyOrchestrationTests(unittest.TestCase):
    def test_decode_then_score_chain(self) -> None:
        runtime = Mock()
        fake_samples = np.zeros(1000, dtype=np.float32)
        fake_features = EnergyFeatures(
            rms_db=-6.0, onset_rate_per_second=8.0, spectral_centroid_hz=6000.0,
            spectral_flatness=0.33,
        )
        with (
            patch(
                "djmaker.services.energy_analysis.decode_mono_pcm",
                return_value=fake_samples,
            ) as decode,
            patch(
                "djmaker.services.energy_analysis.compute_energy_features",
                return_value=fake_features,
            ) as compute,
        ):
            score, noisiness = analyze_energy(runtime, Path("/music/track.mp3"), bpm=180.0)

        decode.assert_called_once_with(runtime, Path("/music/track.mp3"))
        compute.assert_called_once_with(fake_samples)
        self.assertEqual(100.0, score)
        self.assertEqual(0.33, noisiness)


if __name__ == "__main__":
    unittest.main()
