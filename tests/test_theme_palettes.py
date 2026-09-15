"""Регрессионные тесты пользовательских палитр оформления."""

from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.settings import AppSettings, THEME_PALETTES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "theme.py"
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class ThemePaletteTests(unittest.TestCase):
    def test_clean_graphene_and_strict_are_valid_saved_palettes(self) -> None:
        self.assertIn("clean_graphene", THEME_PALETTES)
        self.assertIn("strict", THEME_PALETTES)

        graphene = AppSettings.from_mapping({"theme_palette": "clean_graphene"})
        strict = AppSettings.from_mapping({"theme_palette": "strict"})

        self.assertEqual("clean_graphene", graphene.theme_palette)
        self.assertEqual("strict", strict.theme_palette)

    def test_custom_palettes_define_light_and_dark_color_schemes(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")

        self.assertIn('"clean_graphene": (', source)
        self.assertIn('"strict": (', source)
        self.assertGreaterEqual(source.count("ft.ColorScheme("), 4)
        self.assertIn("page.theme = _build_theme(palette, dark=False)", source)
        self.assertIn("page.dark_theme = _build_theme(palette, dark=True)", source)

    def test_settings_show_palette_description(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("palette_description(self.settings.theme_palette)", source)


if __name__ == "__main__":
    unittest.main()
