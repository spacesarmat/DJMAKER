from __future__ import annotations

import struct
import unittest
from pathlib import Path

from djmaker.services.tasks import TaskKind, TaskManager, TaskPaused
from djmaker.services.waveform import (
    WAVEFORM_BAR_COUNT,
    WAVEFORM_SAMPLE_RATE,
    WaveformAnalyzer,
    build_waveform_command,
    extract_waveform_peaks,
    resample_waveform_peaks,
)


class WaveformHelpersTests(unittest.TestCase):
    def test_ffmpeg_command_outputs_low_rate_mono_float32(self) -> None:
        command = build_waveform_command(Path("ffmpeg"), Path("track.flac"))

        self.assertIn("-ac", command)
        self.assertIn("1", command)
        self.assertIn("-ar", command)
        self.assertIn(str(WAVEFORM_SAMPLE_RATE), command)
        self.assertIn("-threads", command)
        thread_index = command.index("-threads")
        self.assertEqual("1", command[thread_index + 1])
        self.assertEqual("pipe:1", command[-1])

    def test_extract_waveform_peaks_returns_fixed_normalized_bar_count(self) -> None:
        samples = [0.0, 0.25, -0.5, 1.0] * 120
        payload = b"".join(struct.pack("<f", value) for value in samples)

        peaks = extract_waveform_peaks(payload)

        self.assertEqual(WAVEFORM_BAR_COUNT, len(peaks))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks))
        self.assertEqual(1.0, max(peaks))

    def test_silent_payload_produces_zero_waveform(self) -> None:
        payload = b"".join(struct.pack("<f", 0.0) for _ in range(120))
        peaks = extract_waveform_peaks(payload, bar_count=12)
        self.assertEqual((0.0,) * 12, peaks)

    def test_resample_keeps_loudest_peak_in_each_visual_bucket(self) -> None:
        peaks = resample_waveform_peaks((0.1, 0.8, 0.2, 1.0), 2)
        self.assertEqual((0.8, 1.0), peaks)
        self.assertEqual((0.0, 0.0, 0.0), resample_waveform_peaks((), 3))


class WaveformCancellationTests(unittest.TestCase):
    def test_analyzer_checks_task_before_runtime_probe(self) -> None:
        class RuntimeShouldNotBeUsed:
            def ffmpeg_path(self) -> Path:
                raise AssertionError("runtime must not be probed after stop")

        task = TaskManager().create(
            kind=TaskKind.WAVEFORM_ANALYSIS,
            title="Waveform",
        )
        task.request_pause()
        analyzer = WaveformAnalyzer(RuntimeShouldNotBeUsed())  # type: ignore[arg-type]

        with self.assertRaises(TaskPaused):
            analyzer.analyze(Path("missing.mp3"), task=task)


if __name__ == "__main__":
    unittest.main()
