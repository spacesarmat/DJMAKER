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
        self.assertIn("await audio.play(", ui)
        self.assertIn("position=ft.Duration(milliseconds=target_position_ms)", ui)
        self.assertIn("event.local_position.x / self._waveform_width()", ui)

    def test_track_row_has_clickable_cover_and_vertical_waveform_bars(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_waveform", source)
        self.assertIn("on_tap=lambda _, track_id=track.id", source)
        self.assertIn("on_tap=lambda event, current=track", source)
        self.assertNotIn("on_tap_down=lambda event, current=track", source)
        self.assertIn("def _waveform_svg", source)
        self.assertIn("ft.Stack(", source)
        self.assertIn("waveform_bar_gap", source)
        self.assertIn("ft.Colors.PRIMARY", source)
        self.assertNotIn("_waveform_bar_controls", source)

    def test_waveform_is_wider_and_player_updates_are_partial(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        density = (PROJECT_ROOT / "src" / "djmaker" / "ui" / "density.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("waveform_bar_width: int = 5", density)
        self.assertIn("self.page.update(*controls)", source)
        self.assertIn("dict[int, _WaveformView]", source)
        self.assertIn("played == view.played_bars", source)
        self.assertIn("concurrency = min(1,", source)


    def test_track_switch_waits_for_loaded_source_and_ignores_stale_events(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self._player_switch_lock = asyncio.Lock()", source)
        self.assertIn("self._player_load_event.wait()", source)
        self.assertIn("def _commit_player_track", source)
        self.assertIn("if self._player_switching:\n            return", source)
        self.assertIn("self._player_request_revision += 1", source)
        self.assertNotIn("_player_pending_position_ms", source)

    def test_track_metadata_is_rendered_in_three_requested_lines(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_title_row", source)
        self.assertIn("self._track_key_label(track)", source)
        self.assertIn("self._track_bpm_label(track)", source)
        self.assertIn("def _track_details_row", source)
        self.assertIn("self._track_detail_tags(track)", source)
        self.assertIn("def _track_path_link", source)
        self.assertIn("on_double_tap=", source)
        self.assertIn("height=self._library_size(COMPACT_UI.track_icon_box)", source)

    def test_waveform_background_task_starts_with_application(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        ui = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("page.run_task(ui.ensure_waveforms)", source)
        self.assertIn("TaskKind.WAVEFORM_ANALYSIS", ui)
        self.assertIn("self._run_waveform_analysis", ui)
        self.assertIn("self.page.run_task(self.ensure_waveforms)", ui)


if __name__ == "__main__":
    unittest.main()
