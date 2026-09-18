"""Регрессия глобального редактора темы и пользовательских override-цветов."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from djmaker.settings import AppSettings, SettingsStore, is_valid_theme_color


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
THEME_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "theme.py"
THEME_SETTINGS_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "theme_settings_controls.py"
)


class ThemeEditorTests(unittest.TestCase):
    def test_theme_overrides_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            settings = AppSettings(
                theme_mode="dark",
                theme_palette="clean_graphene",
                theme_light_overrides={"primary": "#123456"},
                theme_dark_overrides={
                    "primary": "#00F0FF",
                    "surface": "#101113",
                },
            )
            store = SettingsStore(path)

            store.save(settings)

            self.assertEqual(settings, store.load())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("#00F0FF", payload["theme_dark_overrides"]["primary"])

    def test_invalid_and_unknown_theme_overrides_are_ignored(self) -> None:
        settings = AppSettings.from_mapping(
            {
                "theme_light_overrides": {
                    "primary": "#aabbcc",
                    "surface": "not-a-color",
                    "unknown_role": "#112233",
                },
                "theme_dark_overrides": "broken",
            }
        )

        self.assertEqual({"primary": "#AABBCC"}, settings.theme_light_overrides)
        self.assertEqual({}, settings.theme_dark_overrides)

    def test_hex_validation_requires_six_digits(self) -> None:
        self.assertTrue(is_valid_theme_color("#00F0FF"))
        self.assertTrue(is_valid_theme_color("#abcdef"))
        self.assertFalse(is_valid_theme_color("00F0FF"))
        self.assertFalse(is_valid_theme_color("#FFF"))
        self.assertFalse(is_valid_theme_color("#00F0FF00"))

    def test_theme_builder_applies_light_and_dark_overrides(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")

        self.assertIn("overrides=settings.theme_light_overrides", source)
        self.assertIn("overrides=settings.theme_dark_overrides", source)
        self.assertIn("ft.ColorScheme(**color_overrides)", source)
        self.assertIn("dataclass_replace(scheme, **color_overrides)", source)

    def test_settings_contains_live_preview_editor(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        theme_settings_source = THEME_SETTINGS_SOURCE.read_text(encoding="utf-8")

        self.assertIn('"Глобальный редактор темы"', theme_settings_source)
        self.assertIn("_apply_theme_editor_preview", source)
        self.assertIn("is_valid_theme_color", theme_settings_source)
        self.assertIn('content="Вернуть сохранённое"', theme_settings_source)
        self.assertIn('content="Сбросить текущую схему"', theme_settings_source)


if __name__ == "__main__":
    unittest.main()
