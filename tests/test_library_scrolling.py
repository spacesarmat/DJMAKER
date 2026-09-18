"""Тесты плавной центровки выбранного трека."""

from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.ui.scrolling import centered_scroll_offset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TRACK_SELECTION_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_selection_controls.py"
)


class LibraryScrollingTests(unittest.TestCase):
    def test_centered_scroll_offset_centers_middle_item(self) -> None:
        offset = centered_scroll_offset(
            index=10,
            item_extent=58,
            viewport_extent=580,
            max_scroll_extent=5000,
        )

        self.assertEqual(offset, 319)

    def test_centered_scroll_offset_clamps_edges(self) -> None:
        self.assertEqual(
            centered_scroll_offset(
                index=0,
                item_extent=58,
                viewport_extent=580,
                max_scroll_extent=5000,
            ),
            0,
        )
        self.assertEqual(
            centered_scroll_offset(
                index=100,
                item_extent=58,
                viewport_extent=580,
                max_scroll_extent=1200,
            ),
            1200,
        )

    def test_library_uses_fixed_extent_and_smooth_scroll(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_selection_source = TRACK_SELECTION_SOURCE.read_text(encoding="utf-8")
        library_search_source = (
            PROJECT_ROOT / "src" / "djmaker" / "ui" / "library_search_controls.py"
        ).read_text(encoding="utf-8")

        self.assertIn("item_extent=app._track_item_extent()", library_search_source)
        self.assertIn("on_scroll=app._on_library_scroll", library_search_source)
        self.assertIn("await listing.scroll_to(", track_selection_source)
        self.assertIn(
            "duration=COMPACT_UI.track_center_scroll_ms", track_selection_source
        )
        self.assertIn("ft.AnimationCurve.EASE_IN_OUT_CUBIC", track_selection_source)
        self.assertIn("centered_scroll_offset(", track_selection_source)

    def test_track_row_can_be_selected_by_tapping_row(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = (
            PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"
        ).read_text(encoding="utf-8")

        self.assertIn("self._selected_track_id", source)
        self.assertIn("self._track_row_cards", source)
        self.assertIn("app._select_library_track", track_row_source)
        self.assertIn("ft.Colors.SURFACE_CONTAINER_HIGH", track_row_source)


if __name__ == "__main__":
    unittest.main()
