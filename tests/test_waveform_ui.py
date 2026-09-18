from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "app.py"
PLAYER_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "player_controls.py"
TRACK_SELECTION_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_selection_controls.py"
)
TASK_PROGRESS_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "task_progress_controls.py"
)
TRACK_ROW_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class WaveformUITests(unittest.TestCase):
    def test_player_uses_flet_audio_and_local_seek(self) -> None:
        ui = UI_SOURCE.read_text(encoding="utf-8")
        track_selection_source = TRACK_SELECTION_SOURCE.read_text(encoding="utf-8")
        project = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn('"flet-audio==0.86.5"', project)
        self.assertNotIn('"flet-audio==0.1.0"', project)
        self.assertIn("import flet_audio as fta", ui)
        self.assertIn("on_loaded=app._on_player_loaded", track_selection_source)
        self.assertIn("await audio.play(", track_selection_source)
        self.assertIn(
            "position=ft.Duration(milliseconds=target_position_ms)",
            track_selection_source,
        )
        self.assertIn(
            "event.local_position.x / self.waveform_width()",
            TRACK_ROW_SOURCE.read_text(encoding="utf-8"),
        )

    def test_track_row_has_clickable_cover_and_vertical_waveform_bars(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_waveform", source)
        self.assertIn("on_tap=lambda _, track_id=track.id", track_row_source)
        self.assertIn("on_tap=lambda event, current=track", track_row_source)
        self.assertNotIn("on_tap_down=lambda event, current=track", track_row_source)
        self.assertIn("def _waveform_svg", source)
        self.assertIn("ft.Stack(", track_row_source)
        self.assertIn("waveform_bar_gap", track_row_source)
        self.assertIn("ft.Colors.PRIMARY", track_row_source)
        self.assertNotIn("_waveform_bar_controls", track_row_source)

    def test_waveform_is_wider_and_player_updates_are_partial(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        player_source = PLAYER_SOURCE.read_text(encoding="utf-8")
        density = (PROJECT_ROOT / "src" / "djmaker" / "ui" / "density.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("waveform_bar_width: int = 5", density)
        self.assertIn("app.page.update(*controls)", player_source)
        self.assertIn("dict[int, _WaveformView]", source)
        self.assertIn("played == view.played_bars", player_source)
        self.assertIn(
            "concurrency = min(1,", TASK_PROGRESS_SOURCE.read_text(encoding="utf-8")
        )


    def test_track_switch_waits_for_loaded_source_and_ignores_stale_events(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        player_source = PLAYER_SOURCE.read_text(encoding="utf-8")
        track_selection_source = TRACK_SELECTION_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self._player_switch_lock = asyncio.Lock()", source)
        self.assertIn("app._player_load_event.wait()", player_source)
        self.assertIn("app._player_load_event.wait()", track_selection_source)
        self.assertIn("def _commit_player_track", source)
        self.assertIn("if app._player_switching:\n            return", player_source)
        self.assertIn("self._player_request_revision += 1", source)
        self.assertNotIn("_player_pending_position_ms", source)

    def test_track_metadata_is_rendered_in_three_requested_lines(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_title_row", source)
        self.assertIn("self.track_key_label(track)", track_row_source)
        self.assertIn("self.track_bpm_label(track)", track_row_source)
        self.assertIn("def _track_details_row", source)
        self.assertIn("self.track_detail_tags(track)", track_row_source)
        self.assertIn("def _track_path_link", source)
        self.assertIn("on_double_tap=", track_row_source)
        self.assertIn(
            "height=self.library_size(COMPACT_UI.track_icon_box)", track_row_source
        )

    def test_waveform_background_task_starts_with_application(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        ui = UI_SOURCE.read_text(encoding="utf-8")
        drop_source = (
            PROJECT_ROOT / "src" / "djmaker" / "ui" / "drop_import_controls.py"
        ).read_text(encoding="utf-8")

        self.assertIn("page.run_task(ui.ensure_waveforms)", source)
        self.assertIn(
            "TaskKind.WAVEFORM_ANALYSIS", TASK_PROGRESS_SOURCE.read_text(encoding="utf-8")
        )
        self.assertIn(
            "self.run_waveform_analysis",
            TASK_PROGRESS_SOURCE.read_text(encoding="utf-8"),
        )
        self.assertIn("app.page.run_task(app.ensure_waveforms)", drop_source)


if __name__ == "__main__":
    unittest.main()
