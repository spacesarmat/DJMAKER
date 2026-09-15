"""Source-level regression для компактного отображения строки медиатеки."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class TrackRowUITests(unittest.TestCase):
    def test_first_line_uses_title_key_and_bpm_tags(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_title_row", source)
        self.assertIn('f"{artist} - {title}"', source)
        self.assertIn("self._track_key_label(track), accent=True", source)
        self.assertIn("self._track_bpm_label(track), accent=True", source)

    def test_second_line_uses_duration_technical_tags_and_album(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_details_row", source)
        self.assertIn("self._format_duration(track.technical.duration)", source)
        self.assertIn("self._track_detail_tags(track)", source)
        self.assertIn('album = track.metadata.album.strip() or "Альбом —"', source)
        self.assertIn(
            'tags.append((track.extension.lstrip(".") or "audio").upper())',
            source,
        )
        self.assertIn('tags.append(f"{round(technical.bitrate / 1000)} kbps")', source)
        self.assertIn('tags.append(f"{technical.sample_rate / 1000:g} kHz")', source)

    def test_third_line_double_click_reveals_file(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _track_path_link", source)
        self.assertIn("ft.Icons.FOLDER_OPEN_OUTLINED", source)
        self.assertIn("on_double_tap=", source)
        self.assertIn("self._reveal_track_file(current)", source)
        self.assertIn("reveal_file(track.path)", source)


if __name__ == "__main__":
    unittest.main()
