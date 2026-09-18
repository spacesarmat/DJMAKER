"""Регрессионные тесты отображения обложек в медиатеке."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TRACK_ROW_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"


class ArtworkUITests(unittest.TestCase):
    """Защищает приоритет embedded cover и online fallback."""

    def test_track_row_uses_artwork_helper(self) -> None:
        source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self.track_artwork(track)", source)
        self.assertIn("track.embedded_artwork_path", source)
        self.assertIn('artwork_url = (track.artwork_url or "").strip()', source)

    def test_embedded_artwork_has_online_and_icon_fallbacks(self) -> None:
        source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn("image(str(embedded), remote)", source)
        self.assertIn("fit=ft.BoxFit.COVER", source)
        self.assertIn("error_content=error_content", source)
        self.assertIn("ft.Icons.MUSIC_NOTE", source)
        self.assertIn("return ft.GestureDetector(", source)


if __name__ == "__main__":
    unittest.main()
