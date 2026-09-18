from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TRACK_ROW_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"
LIBRARY_SEARCH_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "library_search_controls.py"
)


class LibraryScaleUITests(unittest.TestCase):
    def test_library_has_persistent_zoom_control(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        library_search_source = LIBRARY_SEARCH_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _library_scale_control", source)
        self.assertIn("ft.Icons.ZOOM_OUT", library_search_source)
        self.assertIn("ft.Icons.ZOOM_IN", library_search_source)
        self.assertIn("library_scale_percent=target", library_search_source)
        self.assertIn("app.settings_store.save(settings)", library_search_source)

    def test_track_geometry_uses_library_scale(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")
        library_search_source = LIBRARY_SEARCH_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _library_size", source)
        self.assertIn("self.app.settings.library_scale_percent", track_row_source)
        self.assertIn(
            "self.library_size(COMPACT_UI.track_icon_box)", track_row_source
        )
        self.assertIn(
            "self.library_size(COMPACT_UI.waveform_height)", track_row_source
        )
        self.assertIn("self.waveform_width()", track_row_source)
        self.assertIn(
            "item_extent=app._track_item_extent()", library_search_source
        )

    def test_scale_change_recenters_selected_track(self) -> None:
        library_search_source = LIBRARY_SEARCH_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            "selected_track_id = app._selected_track_id", library_search_source
        )
        self.assertIn("app._select_library_track,", library_search_source)


if __name__ == "__main__":
    unittest.main()
