"""Подсветка строки медиатеки по цвету энергии (AIR)."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from djmaker.domain.models import AudioAnalysis, AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.settings import AppSettings
from djmaker.ui.track_row_controls import ENERGY_HIGHLIGHT_OPACITY, TrackRowController


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


class TrackRowBackgroundColorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(energy_highlight_enabled=True)
        self.app._selected_track_id = None
        self.controller = TrackRowController(self.app)

    def test_selected_row_always_uses_selection_color_even_with_energy_data(self) -> None:
        self.app._selected_track_id = 1
        track = _track(1, analysis=AudioAnalysis(energy=95.0, scale="major", noisiness=0.05))

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_HIGH, self.controller.track_row_bgcolor(track))

    def test_no_analysis_uses_default_surface_color(self) -> None:
        track = _track(1, analysis=None)

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_LOW, self.controller.track_row_bgcolor(track))

    def test_highlight_disabled_ignores_energy_data(self) -> None:
        self.app.settings = AppSettings(energy_highlight_enabled=False)
        track = _track(1, analysis=AudioAnalysis(energy=95.0, scale="major", noisiness=0.05))

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_LOW, self.controller.track_row_bgcolor(track))

    def test_high_energy_major_track_is_tinted_yellow(self) -> None:
        track = _track(1, analysis=AudioAnalysis(energy=90.0, scale="major", noisiness=0.05))

        expected = ft.Colors.with_opacity(ENERGY_HIGHLIGHT_OPACITY, ft.Colors.YELLOW)
        self.assertEqual(expected, self.controller.track_row_bgcolor(track))

    def test_heavy_dark_genre_tag_is_tinted_black_regardless_of_energy(self) -> None:
        track = _track(
            1, analysis=AudioAnalysis(energy=10.0, scale="major", noisiness=0.05, genre_tag="heavy_dark")
        )

        expected = ft.Colors.with_opacity(ENERGY_HIGHLIGHT_OPACITY, ft.Colors.BLUE_GREY_900)
        self.assertEqual(expected, self.controller.track_row_bgcolor(track))

    def test_missing_energy_value_falls_back_to_default_surface(self) -> None:
        track = _track(1, analysis=AudioAnalysis(energy=None, scale="major"))

        self.assertEqual(ft.Colors.SURFACE_CONTAINER_LOW, self.controller.track_row_bgcolor(track))


if __name__ == "__main__":
    unittest.main()
