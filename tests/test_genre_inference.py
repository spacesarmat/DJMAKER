from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from djmaker.services.ast_preprocessing import AST_WINDOW_SAMPLES
from djmaker.services.genre_inference import (
    AMBIENT_LABEL_INDICES,
    GENRE_TAG_AMBIENT,
    GENRE_TAG_HEAVY_DARK,
    HEAVY_DARK_LABEL_INDICES,
    GenreClassifier,
    decide_genre_tag,
    select_onnx_providers,
    select_window_starts,
)


class SelectWindowStartsTests(unittest.TestCase):
    def test_empty_track_has_no_windows(self) -> None:
        self.assertEqual([], select_window_starts(0))

    def test_track_shorter_than_one_window_starts_at_zero(self) -> None:
        self.assertEqual([0], select_window_starts(1000, window_samples=AST_WINDOW_SAMPLES))

    def test_track_exactly_one_window_starts_at_zero(self) -> None:
        self.assertEqual(
            [0], select_window_starts(AST_WINDOW_SAMPLES, window_samples=AST_WINDOW_SAMPLES)
        )

    def test_long_track_is_capped_at_max_windows(self) -> None:
        total = AST_WINDOW_SAMPLES * 50
        starts = select_window_starts(total, window_samples=AST_WINDOW_SAMPLES, max_windows=12)
        self.assertEqual(12, len(starts))
        self.assertEqual(0, starts[0])
        self.assertEqual(sorted(starts), starts)

    def test_starts_never_exceed_track_bounds(self) -> None:
        total = AST_WINDOW_SAMPLES * 3 + 500
        starts = select_window_starts(total, window_samples=AST_WINDOW_SAMPLES, max_windows=12)
        for start in starts:
            self.assertLessEqual(start + AST_WINDOW_SAMPLES, total + AST_WINDOW_SAMPLES)
            self.assertGreaterEqual(start, 0)


class DecideGenreTagTests(unittest.TestCase):
    def test_confident_heavy_metal_wins(self) -> None:
        index = next(iter(HEAVY_DARK_LABEL_INDICES))
        probs = np.zeros(527, dtype=np.float64)
        probs[index] = 0.8
        self.assertEqual(GENRE_TAG_HEAVY_DARK, decide_genre_tag(probs))

    def test_confident_ambient_wins(self) -> None:
        index = next(iter(AMBIENT_LABEL_INDICES))
        probs = np.zeros(527, dtype=np.float64)
        probs[index] = 0.7
        self.assertEqual(GENRE_TAG_AMBIENT, decide_genre_tag(probs))

    def test_low_confidence_returns_empty(self) -> None:
        probs = np.zeros(527, dtype=np.float64)
        probs[220] = 0.1
        probs[246] = 0.05
        self.assertEqual("", decide_genre_tag(probs))

    def test_tie_prefers_heavy_dark(self) -> None:
        probs = np.zeros(527, dtype=np.float64)
        probs[220] = 0.5
        probs[246] = 0.5
        self.assertEqual(GENRE_TAG_HEAVY_DARK, decide_genre_tag(probs))


class SelectOnnxProvidersTests(unittest.TestCase):
    def test_gpu_disabled_forces_cpu_only(self) -> None:
        self.assertEqual(
            ["CPUExecutionProvider"],
            select_onnx_providers(["DmlExecutionProvider", "CPUExecutionProvider"], gpu_enabled=False),
        )

    def test_gpu_enabled_prefers_dml_then_cpu_fallback(self) -> None:
        available = ["CPUExecutionProvider", "DmlExecutionProvider"]
        self.assertEqual(
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            select_onnx_providers(available, gpu_enabled=True),
        )

    def test_gpu_enabled_without_gpu_provider_falls_back_to_cpu(self) -> None:
        self.assertEqual(
            ["CPUExecutionProvider"],
            select_onnx_providers(["CPUExecutionProvider"], gpu_enabled=True),
        )

    def test_coreml_preferred_over_cuda_when_both_present(self) -> None:
        available = ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
        result = select_onnx_providers(available, gpu_enabled=True)
        self.assertEqual(
            ["CoreMLExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"], result
        )


class GenreClassifierTests(unittest.TestCase):
    def test_returns_empty_when_model_not_available(self) -> None:
        runtime = Mock()
        runtime.ast_model_path = Mock(return_value=None)
        classifier = GenreClassifier(runtime, gpu_enabled=False)

        result = classifier.classify(np.zeros(AST_WINDOW_SAMPLES, dtype=np.float32), sample_rate=44100)

        self.assertEqual("", result)

    def test_returns_empty_on_session_creation_failure(self) -> None:
        runtime = Mock()
        runtime.ast_model_path = Mock(return_value=Path("/fake/model.onnx"))
        classifier = GenreClassifier(runtime, gpu_enabled=False)

        with patch(
            "djmaker.services.genre_inference._create_onnx_session",
            side_effect=RuntimeError("boom"),
        ):
            result = classifier.classify(
                np.zeros(AST_WINDOW_SAMPLES, dtype=np.float32), sample_rate=44100
            )

        self.assertEqual("", result)

    def test_session_is_created_only_once_across_calls(self) -> None:
        runtime = Mock()
        runtime.ast_model_path = Mock(return_value=Path("/fake/model.onnx"))
        classifier = GenreClassifier(runtime, gpu_enabled=False)

        fake_session = Mock()
        fake_session.run = Mock(return_value=[np.zeros((1, 527), dtype=np.float32)])

        with patch(
            "djmaker.services.genre_inference._create_onnx_session",
            return_value=fake_session,
        ) as create:
            waveform = np.zeros(AST_WINDOW_SAMPLES, dtype=np.float32)
            classifier.classify(waveform, sample_rate=16000)
            classifier.classify(waveform, sample_rate=16000)

        create.assert_called_once()

    def test_classify_aggregates_windows_and_calls_decide_genre_tag(self) -> None:
        runtime = Mock()
        runtime.ast_model_path = Mock(return_value=Path("/fake/model.onnx"))
        classifier = GenreClassifier(runtime, gpu_enabled=False)

        heavy_index = next(iter(HEAVY_DARK_LABEL_INDICES))
        logits = np.full((1, 527), -10.0, dtype=np.float32)
        logits[0, heavy_index] = 10.0  # sigmoid(10) ~ 1.0, уверенно "тяжёлый"
        fake_session = Mock()
        fake_session.run = Mock(return_value=[logits])

        with patch(
            "djmaker.services.genre_inference._create_onnx_session",
            return_value=fake_session,
        ):
            waveform = np.zeros(AST_WINDOW_SAMPLES * 2, dtype=np.float32)
            result = classifier.classify(waveform, sample_rate=16000)

        self.assertEqual(GENRE_TAG_HEAVY_DARK, result)
        self.assertGreaterEqual(fake_session.run.call_count, 1)

    def test_available_reflects_runtime_model_presence(self) -> None:
        runtime = Mock()
        runtime.ast_model_path = Mock(return_value=None)
        classifier = GenreClassifier(runtime, gpu_enabled=False)
        self.assertFalse(classifier.available())

        runtime.ast_model_path = Mock(return_value=Path("/fake/model.onnx"))
        self.assertTrue(classifier.available())


if __name__ == "__main__":
    unittest.main()
