from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class LibraryScaleUITests(unittest.TestCase):
    def test_library_has_persistent_zoom_control(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _library_scale_control", source)
        self.assertIn("ft.Icons.ZOOM_OUT", source)
        self.assertIn("ft.Icons.ZOOM_IN", source)
        self.assertIn("library_scale_percent=target", source)
        self.assertIn("self.settings_store.save(settings)", source)

    def test_track_geometry_uses_library_scale(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _library_size", source)
        self.assertIn("self.settings.library_scale_percent", source)
        self.assertIn("self._library_size(COMPACT_UI.track_icon_box)", source)
        self.assertIn("self._library_size(COMPACT_UI.waveform_height)", source)
        self.assertIn("self._waveform_width()", source)
        self.assertIn("item_extent=self._track_item_extent()", source)

    def test_scale_change_recenters_selected_track(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("selected_track_id = self._selected_track_id", source)
        self.assertIn("self._select_library_track,", source)


if __name__ == "__main__":
    unittest.main()
