"""Тесты централизованного профиля плотности интерфейса."""

from __future__ import annotations

import unittest

from djmaker.ui.density import COMPACT_UI, UI_SCALE


class UIDensityTests(unittest.TestCase):
    """Проверяет ключевые ограничения компактного профиля."""

    def test_scale_is_fifty_percent(self) -> None:
        self.assertEqual(0.5, UI_SCALE)
        self.assertEqual(UI_SCALE, COMPACT_UI.scale)

    def test_table_rows_are_compact_and_valid(self) -> None:
        self.assertLess(COMPACT_UI.table_row_min_height, 32)
        self.assertGreaterEqual(
            COMPACT_UI.table_row_max_height,
            COMPACT_UI.table_row_min_height,
        )
        self.assertLessEqual(COMPACT_UI.table_heading_height, 32)

    def test_text_keeps_desktop_readability_floor(self) -> None:
        self.assertGreaterEqual(COMPACT_UI.font_micro, 7)
        self.assertGreater(COMPACT_UI.font_title, COMPACT_UI.font_md)


if __name__ == "__main__":
    unittest.main()
