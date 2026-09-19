from __future__ import annotations

import unittest

import numpy as np

from djmaker.services.ast_preprocessing import (
    AST_MAX_FRAMES,
    AST_NUM_MEL_BINS,
    AST_SAMPLE_RATE,
    AST_WINDOW_SAMPLES,
    build_kaldi_mel_filters,
    extract_ast_input,
    hann_window,
    log_mel_spectrogram,
    normalize,
    pad_or_truncate,
)


class MelFilterBankTests(unittest.TestCase):
    def test_filter_bank_shape_matches_fft_bins_and_mel_count(self) -> None:
        filters = build_kaldi_mel_filters()
        self.assertEqual((257, AST_NUM_MEL_BINS), filters.shape)

    def test_filters_are_non_negative(self) -> None:
        filters = build_kaldi_mel_filters()
        self.assertTrue((filters >= 0).all())

    def test_almost_all_filters_have_some_positive_weight(self) -> None:
        # У самых нижних mel-фильтров (около min_frequency=20Гц) на kaldi-шкале
        # ширина может быть уже одного FFT-бина — это ожидаемо (см. предупреждение
        # в оригинальной transformers.audio_utils.mel_filter_bank), а не баг порта.
        filters = build_kaldi_mel_filters()
        zero_filters = int((filters.max(axis=0) == 0).sum())
        self.assertLessEqual(zero_filters, 2)


class WindowFunctionTests(unittest.TestCase):
    def test_hann_window_has_expected_length_and_endpoints(self) -> None:
        window = hann_window(400)
        self.assertEqual(400, window.size)
        self.assertAlmostEqual(0.0, window[0], places=5)


class LogMelSpectrogramTests(unittest.TestCase):
    def test_window_sized_input_produces_max_frames(self) -> None:
        waveform = np.zeros(AST_WINDOW_SAMPLES, dtype=np.float32)
        spec = log_mel_spectrogram(waveform)
        self.assertEqual((AST_MAX_FRAMES, AST_NUM_MEL_BINS), spec.shape)

    def test_empty_waveform_produces_zero_frames(self) -> None:
        spec = log_mel_spectrogram(np.zeros(0, dtype=np.float32))
        self.assertEqual((0, AST_NUM_MEL_BINS), spec.shape)

    def test_no_nan_or_inf_for_real_signal(self) -> None:
        t = np.arange(AST_WINDOW_SAMPLES) / AST_SAMPLE_RATE
        waveform = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        spec = log_mel_spectrogram(waveform)
        self.assertFalse(np.isnan(spec).any())
        self.assertFalse(np.isinf(spec).any())

    def test_louder_signal_has_higher_log_mel_energy(self) -> None:
        t = np.arange(AST_WINDOW_SAMPLES) / AST_SAMPLE_RATE
        quiet = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        loud = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        self.assertGreater(
            log_mel_spectrogram(loud).mean(), log_mel_spectrogram(quiet).mean()
        )


class PadOrTruncateTests(unittest.TestCase):
    def test_pads_short_input_with_zeros(self) -> None:
        features = np.ones((10, AST_NUM_MEL_BINS), dtype=np.float32)
        padded = pad_or_truncate(features, max_length=20)
        self.assertEqual((20, AST_NUM_MEL_BINS), padded.shape)
        self.assertTrue((padded[10:] == 0).all())
        self.assertTrue((padded[:10] == 1).all())

    def test_truncates_long_input(self) -> None:
        features = np.ones((30, AST_NUM_MEL_BINS), dtype=np.float32)
        truncated = pad_or_truncate(features, max_length=20)
        self.assertEqual((20, AST_NUM_MEL_BINS), truncated.shape)

    def test_exact_length_is_unchanged(self) -> None:
        features = np.ones((20, AST_NUM_MEL_BINS), dtype=np.float32)
        result = pad_or_truncate(features, max_length=20)
        self.assertEqual((20, AST_NUM_MEL_BINS), result.shape)


class NormalizeTests(unittest.TestCase):
    def test_normalize_matches_formula(self) -> None:
        features = np.array([[0.0, -4.2677393]], dtype=np.float32)
        result = normalize(features, mean=-4.2677393, std=4.5689974)
        expected = (features - (-4.2677393)) / (4.5689974 * 2)
        np.testing.assert_allclose(result, expected, rtol=1e-5)


class ExtractAstInputTests(unittest.TestCase):
    def test_full_pipeline_produces_expected_shape_and_finite_values(self) -> None:
        t = np.arange(AST_WINDOW_SAMPLES) / AST_SAMPLE_RATE
        waveform = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = extract_ast_input(waveform)
        self.assertEqual((AST_MAX_FRAMES, AST_NUM_MEL_BINS), result.shape)
        self.assertEqual(np.float32, result.dtype)
        self.assertTrue(np.isfinite(result).all())

    def test_shorter_window_is_padded_before_normalization(self) -> None:
        waveform = np.zeros(1000, dtype=np.float32)
        result = extract_ast_input(waveform)
        self.assertEqual((AST_MAX_FRAMES, AST_NUM_MEL_BINS), result.shape)


if __name__ == "__main__":
    unittest.main()
