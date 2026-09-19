"""Вертикальная полоса тональности (колесо Camelot) в строке трека."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from djmaker.domain.camelot_color import camelot_to_color
from djmaker.domain.models import AudioAnalysis, AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.settings import AppSettings
from djmaker.ui.track_row_controls import (
    TONALITY_BAR_WIDTH,
    TrackRowController,
    _readable_text_color,
)


def _track(track_id: int = 1, *, analysis: AudioAnalysis | None = None) -> TrackRecord:
    return TrackRecord(
        id=track_id,
        path=Path(f"/music/{track_id}.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash=str(track_id),
        metadata=AudioMetadata(title="T", artist="A"),
        technical=AudioTechnicalInfo(),
        analysis=analysis,
    )


class TrackTonalityBarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings()
        self.controller = TrackRowController(self.app)

    def test_no_analysis_uses_neutral_placeholder_color_and_tooltip(self) -> None:
        bar = self.controller.track_tonality_bar(_track(analysis=None))

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_HIGHEST, bar.bgcolor)
        self.assertEqual("Тональность не определена", bar.tooltip)
        self.assertEqual("—", bar.content.value)

    def test_missing_camelot_code_uses_neutral_placeholder(self) -> None:
        analysis = AudioAnalysis(musical_key="", scale="", camelot="")
        bar = self.controller.track_tonality_bar(_track(analysis=analysis))

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_HIGHEST, bar.bgcolor)
        self.assertEqual("—", bar.content.value)

    def test_known_camelot_code_uses_wheel_color_and_descriptive_tooltip(self) -> None:
        analysis = AudioAnalysis(musical_key="A", scale="minor", camelot="8A")
        bar = self.controller.track_tonality_bar(_track(analysis=analysis))

        self.assertEqual(camelot_to_color("8A"), bar.bgcolor)
        self.assertEqual("A minor · 8A", bar.tooltip)

    def test_camelot_code_is_shown_as_readable_text_in_the_bar(self) -> None:
        analysis = AudioAnalysis(musical_key="G", scale="major", camelot="9B")
        bar = self.controller.track_tonality_bar(_track(analysis=analysis))

        self.assertEqual("9B", bar.content.value)
        self.assertIn(bar.content.color, {ft.Colors.BLACK, ft.Colors.WHITE})

    def test_bar_is_wide_enough_for_camelot_text_and_full_row_height(self) -> None:
        bar = self.controller.track_tonality_bar(_track(analysis=None))
        self.assertEqual(TONALITY_BAR_WIDTH, bar.width)
        self.assertGreaterEqual(bar.width, 24)
        self.assertGreater(bar.height, 0)


class ReadableTextColorTests(unittest.TestCase):
    def test_dark_background_gets_white_text(self) -> None:
        self.assertEqual(ft.Colors.WHITE, _readable_text_color("#101010"))

    def test_bright_background_gets_black_text(self) -> None:
        self.assertEqual(ft.Colors.BLACK, _readable_text_color("#F5F5F5"))


if __name__ == "__main__":
    unittest.main()
