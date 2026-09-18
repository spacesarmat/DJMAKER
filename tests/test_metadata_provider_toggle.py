"""Регрессия переключения провайдеров метаданных (чекбокс в Настройках и клик по чипу)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.settings import AppSettings
from djmaker.ui.theme_settings_controls import ThemeSettingsController


class MetadataProviderToggleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(metadata_providers=("musicbrainz",))
        self.app.settings_store = Mock()
        self.app.page = Mock()
        self.app.theme_button = ft.IconButton()
        self.app.status = ft.Text()
        self.app.navigation = SimpleNamespace(selected_index=3)
        self.app._theme_editor_active = True
        self.app._theme_editor_fields = {"x": 1}
        self.app._theme_editor_swatches = {"x": 1}
        self.app._theme_editor_status = "editing"
        self.app.show_settings = Mock()
        self.app.show_plugins = Mock()
        self.app._notify = Mock()
        self.controller = ThemeSettingsController(self.app)

    def test_checkbox_checked_adds_provider(self) -> None:
        event = SimpleNamespace(control=SimpleNamespace(value=True))

        self.controller.on_metadata_provider_toggled(event, "deezer")

        self.assertIn("deezer", self.app.settings.metadata_providers)
        self.assertIn("musicbrainz", self.app.settings.metadata_providers)

    def test_checkbox_unchecked_removes_provider(self) -> None:
        event = SimpleNamespace(control=SimpleNamespace(value=False))

        self.controller.on_metadata_provider_toggled(event, "musicbrainz")

        self.assertEqual(self.app.settings.metadata_providers, ())

    def test_toggle_metadata_provider_flips_current_state(self) -> None:
        self.controller.toggle_metadata_provider("musicbrainz")
        self.assertEqual(self.app.settings.metadata_providers, ())

        self.controller.toggle_metadata_provider("musicbrainz")
        self.assertIn("musicbrainz", self.app.settings.metadata_providers)

    def test_save_failure_does_not_change_settings(self) -> None:
        original = self.app.settings
        self.app.settings_store.save.side_effect = OSError("disk full")

        self.controller.toggle_metadata_provider("deezer")

        self.assertIs(self.app.settings, original)

    def test_refreshes_metadata_tab_when_currently_open(self) -> None:
        self.app.navigation.selected_index = 3

        self.controller.toggle_metadata_provider("deezer")

        self.app.show_plugins.assert_called_once()
        self.app.show_settings.assert_not_called()

    def test_refreshes_settings_tab_when_currently_open(self) -> None:
        self.app.navigation.selected_index = 6

        self.controller.toggle_metadata_provider("deezer")

        self.app.show_settings.assert_called_once()
        self.app.show_plugins.assert_not_called()

    def test_only_updates_page_on_unrelated_tab(self) -> None:
        self.app.navigation.selected_index = 0

        self.controller.toggle_metadata_provider("deezer")

        self.app.show_plugins.assert_not_called()
        self.app.show_settings.assert_not_called()
        self.app.page.update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
