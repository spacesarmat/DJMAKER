"""Тесты плавной центровки выбранного трека."""

from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.ui.scrolling import centered_scroll_offset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


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

        self.assertIn("item_extent=self._track_item_extent()", source)
        self.assertIn("on_scroll=self._on_library_scroll", source)
        self.assertIn("await listing.scroll_to(", source)
        self.assertIn("duration=COMPACT_UI.track_center_scroll_ms", source)
        self.assertIn("ft.AnimationCurve.EASE_IN_OUT_CUBIC", source)
        self.assertIn("centered_scroll_offset(", source)

    def test_track_row_can_be_selected_by_tapping_row(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self._selected_track_id", source)
        self.assertIn("self._track_row_cards", source)
        self.assertIn("self._select_library_track", source)
        self.assertIn("ft.Colors.SURFACE_CONTAINER_HIGH", source)


if __name__ == "__main__":
    unittest.main()
