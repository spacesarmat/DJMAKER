"""Регрессионные тесты контраста светлой/тёмной темы."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "theme.py"
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class ThemeContrastTests(unittest.TestCase):
    """Защищает UI от возврата к фиксированному белому тексту."""

    def test_text_theme_uses_semantic_on_surface_color(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")

        self.assertIn("color=ft.Colors.ON_SURFACE", source)
        self.assertNotIn("color=ft.Colors.WHITE", source)
        self.assertNotIn("color=ft.Colors.BLACK", source)

    def test_navigation_uses_theme_aware_text_colors(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("color=ft.Colors.ON_SURFACE", source)
        self.assertIn("color=ft.Colors.ON_SURFACE_VARIANT", source)
        self.assertNotIn("color=ft.Colors.WHITE", source)
        self.assertNotIn("color=ft.Colors.BLACK", source)


if __name__ == "__main__":
    unittest.main()
