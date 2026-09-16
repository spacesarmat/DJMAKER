from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.services.audio_analysis import (
    ANALYSIS_SAMPLE_RATE,
    AudioAnalysisError,
    build_essentia_command,
    build_ffmpeg_command,
    camelot_code,
    parse_analysis_payload,
    recommended_analysis_concurrency,
)


class AudioAnalysisHelpersTests(unittest.TestCase):
    def test_camelot_mapping_supports_enharmonics(self) -> None:
        self.assertEqual("8A", camelot_code("A", "minor"))
        self.assertEqual("12A", camelot_code("Db", "minor"))
        self.assertEqual("12A", camelot_code("C#", "minor"))
        self.assertEqual("8B", camelot_code("C", "major"))
        self.assertEqual("2B", camelot_code("Gb", "major"))

    def test_parse_bridge_payload_builds_analysis(self) -> None:
        result = parse_analysis_payload(
            '{"bpm":127.98,"bpm_confidence":4.2,"key":"A",'
            '"scale":"minor","key_strength":0.81,'
            '"beat_ticks":[0.25,0.719,1.188,1.657],'
            '"first_beat":0.25,"first_downbeat":0.719,'
            '"tempo_stability":0.97,"downbeat_confidence":0.62}'
        )

        self.assertAlmostEqual(127.98, result.bpm or 0.0)
        self.assertEqual("A", result.musical_key)
        self.assertEqual("minor", result.scale)
        self.assertEqual("8A", result.camelot)
        self.assertAlmostEqual(0.81, result.key_strength or 0.0)
        self.assertIsNotNone(result.beat_grid)
        assert result.beat_grid is not None
        self.assertEqual(250, result.beat_grid.first_beat_ms)
        self.assertEqual(719, result.beat_grid.downbeat_ms)
        self.assertEqual((250, 719, 1188, 1657), result.beat_grid.beat_ticks_ms)
        self.assertAlmostEqual(0.97, result.beat_grid.tempo_stability or 0.0)
        self.assertAlmostEqual(0.62, result.beat_grid.downbeat_confidence or 0.0)

    def test_old_runtime_payload_requires_grid_capable_update(self) -> None:
        with self.assertRaisesRegex(AudioAnalysisError, "Обновите аудио-компоненты"):
            parse_analysis_payload(
                '{"bpm":128,"bpm_confidence":4.2,"key":"A",'
                '"scale":"minor","key_strength":0.81}'
            )

    def test_grid_confidence_must_be_normalized(self) -> None:
        with self.assertRaisesRegex(AudioAnalysisError, "должно быть от 0 до 1"):
            parse_analysis_payload(
                '{"bpm":128,"beat_ticks":[0.1],"first_beat":0.1,'
                '"first_downbeat":0.1,"tempo_stability":1.5}'
            )

    def test_non_finite_grid_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(AudioAnalysisError, "конечным числом"):
            parse_analysis_payload(
                '{"bpm":128,"beat_ticks":[NaN],"first_beat":0.1,'
                '"first_downbeat":0.1}'
            )

    def test_zero_bpm_is_stored_as_unavailable_not_error(self) -> None:
        result = parse_analysis_payload(
            '{"bpm":0,"bpm_confidence":0,"key":"C",'
            '"scale":"major","key_strength":0.5}'
        )
        self.assertIsNone(result.bpm)
        self.assertEqual("8B", result.camelot)

    def test_ffmpeg_command_outputs_expected_pcm_pipe(self) -> None:
        command = build_ffmpeg_command(Path("ffmpeg"), Path("track.flac"))
        self.assertIn("f32le", command)
        self.assertIn(str(ANALYSIS_SAMPLE_RATE), command)
        self.assertEqual("pipe:1", command[-1])
        self.assertIn("track.flac", command)

    def test_recommended_concurrency_uses_multiple_workers_but_is_capped(self) -> None:
        self.assertEqual(1, recommended_analysis_concurrency(1))
        self.assertEqual(2, recommended_analysis_concurrency(2))
        self.assertEqual(2, recommended_analysis_concurrency(4))
        self.assertEqual(4, recommended_analysis_concurrency(16))

    def test_essentia_command_reads_stdin(self) -> None:
        command = build_essentia_command(Path("djmaker-essentia"))
        self.assertEqual("analyze", command[1])
        self.assertIn("-", command)
        self.assertIn("multifeature", command)


if __name__ == "__main__":
    unittest.main()
