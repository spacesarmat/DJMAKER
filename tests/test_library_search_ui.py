"""Регрессия поискового блока медиатеки."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class LibrarySearchUITests(unittest.TestCase):
    def test_library_has_themed_search_block_and_clear_action(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _build_library_search_block", source)
        self.assertIn("bgcolor=ft.Colors.SURFACE_CONTAINER", source)
        self.assertIn('tooltip="Очистить поиск"', source)
        self.assertIn("border=ft.InputBorder.NONE", source)
        self.assertIn("suffix=self.search_clear_button", source)
        self.assertIn('f"Найдено: {result_count}"', source)

    def test_live_search_is_debounced(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("_LIBRARY_SEARCH_DEBOUNCE_SECONDS = 0.22", source)
        self.assertIn("on_change=self._on_search_change", source)
        self.assertIn("await asyncio.sleep(_LIBRARY_SEARCH_DEBOUNCE_SECONDS)", source)
        self.assertIn("revision != self._search_revision", source)


if __name__ == "__main__":
    unittest.main()
