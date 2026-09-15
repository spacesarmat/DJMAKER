from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "app.py"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class WaveformUITests(unittest.TestCase):
    def test_player_uses_flet_audio_and_local_seek(self) -> None:
        ui = UI_SOURCE.read_text(encoding="utf-8")
        project = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn('"flet-audio==0.86.5"', project)
        self.assertNotIn('"flet-audio==0.1.0"', project)
        self.assertIn("import flet_audio as fta", ui)
        self.assertIn("on_loaded=self._on_player_loaded", ui)
        self.assertIn("await self.audio.play(position=ft.Duration", ui)
        self.assertIn("event.local_position.x / self._waveform_width()", ui)

    def test_track_row_has_clickable_cover_and_vertical_waveform_bars(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_waveform", source)
        self.assertIn("on_tap=lambda _, track_id=track.id", source)
        self.assertIn("on_tap_down=lambda event, current=track", source)
        self.assertIn("vertical_alignment=ft.CrossAxisAlignment.END", source)
        self.assertIn("waveform_bar_gap", source)
        self.assertIn("ft.Colors.PRIMARY", source)

    def test_track_metadata_is_rendered_in_three_requested_lines(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_primary_line", source)
        self.assertIn("def _track_technical_line", source)
        self.assertIn("str(track.path)", source)
        self.assertIn("height=COMPACT_UI.track_icon_box", source)

    def test_waveform_background_task_starts_with_application(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        ui = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("page.run_task(ui.ensure_waveforms)", source)
        self.assertIn("TaskKind.WAVEFORM_ANALYSIS", ui)
        self.assertIn("self._run_waveform_analysis", ui)
        self.assertIn("self.page.run_task(self.ensure_waveforms)", ui)


if __name__ == "__main__":
    unittest.main()
