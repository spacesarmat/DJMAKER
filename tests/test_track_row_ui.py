"""Source-level regression для компактного отображения строки медиатеки."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TRACK_ROW_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"


class TrackRowUITests(unittest.TestCase):
    def test_first_line_uses_title_key_and_bpm_tags(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_title_row", source)
        self.assertIn('f"{artist} - {title}"', track_row_source)
        self.assertIn("self.track_key_label(track), accent=True", track_row_source)
        self.assertIn("self.track_bpm_label(track), accent=True", track_row_source)
        self.assertIn(
            'f"{artist} - {title}",\n'
            "                    expand=True,\n"
            "                    expand_loose=True,",
            track_row_source,
        )

    def test_second_line_uses_duration_technical_tags_and_album(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_details_row", source)
        self.assertIn(
            "app._format_duration(track.technical.duration)", track_row_source
        )
        self.assertIn("self.track_detail_tags(track)", track_row_source)
        self.assertIn(
            'album = track.metadata.album.strip() or "Альбом —"', track_row_source
        )
        self.assertIn(
            'tags.append((track.extension.lstrip(".") or "audio").upper())',
            track_row_source,
        )
        self.assertIn(
            'tags.append(f"{round(technical.bitrate / 1000)} kbps")',
            track_row_source,
        )
        self.assertIn(
            'tags.append(f"{technical.sample_rate / 1000:g} kHz")', track_row_source
        )

    def test_track_tags_highlight_on_hover(self) -> None:
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")
        library_search_source = (
            PROJECT_ROOT / "src" / "djmaker" / "ui" / "library_search_controls.py"
        ).read_text(encoding="utf-8")

        self.assertIn("on_hover=(", track_row_source)
        self.assertIn("app._on_accent_track_tag_hover", track_row_source)
        self.assertIn("app._on_neutral_track_tag_hover", track_row_source)
        self.assertIn("ft.Colors.PRIMARY if hovered", library_search_source)
        self.assertIn("ft.Colors.PRIMARY_CONTAINER", library_search_source)
        self.assertIn("event.control.update()", library_search_source)
        self.assertIn("duration=120", track_row_source)
        self.assertIn("ft.AnimationCurve.EASE_OUT_CUBIC", track_row_source)

    def test_third_line_double_click_reveals_file(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_path_link", source)
        self.assertIn("ft.Icons.FOLDER_OPEN_OUTLINED", track_row_source)
        self.assertIn("on_double_tap=", track_row_source)
        self.assertIn("self.reveal_track_file(current)", track_row_source)
        self.assertIn("reveal_file(track.path)", track_row_source)


if __name__ == "__main__":
    unittest.main()
