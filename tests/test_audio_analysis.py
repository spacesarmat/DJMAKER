from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.services.audio_analysis import (
    ANALYSIS_SAMPLE_RATE,
    build_essentia_command,
    build_ffmpeg_command,
    camelot_code,
    parse_analysis_payload,
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
            '"scale":"minor","key_strength":0.81}'
        )

        self.assertAlmostEqual(127.98, result.bpm or 0.0)
        self.assertEqual("A", result.musical_key)
        self.assertEqual("minor", result.scale)
        self.assertEqual("8A", result.camelot)
        self.assertAlmostEqual(0.81, result.key_strength or 0.0)

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

    def test_essentia_command_reads_stdin(self) -> None:
        command = build_essentia_command(Path("djmaker-essentia"))
        self.assertEqual("analyze", command[1])
        self.assertIn("-", command)
        self.assertIn("multifeature", command)


if __name__ == "__main__":
    unittest.main()
