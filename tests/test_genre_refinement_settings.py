"""Регрессия настроек жанрового AST-уточнения (тумблеры refinement/GPU)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.settings import AppSettings
from djmaker.ui.theme_settings_controls import ThemeSettingsController


class GenreRefinementSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(
            energy_genre_refinement_enabled=True, energy_genre_gpu_enabled=True
        )
        self.app.settings_store = Mock()
        self.app.page = Mock()
        self.app.theme_button = ft.IconButton()
        self.app.status = ft.Text()
        self.app.navigation = SimpleNamespace(selected_index=6)
        self.app._theme_editor_active = True
        self.app._theme_editor_fields = {"x": 1}
        self.app._theme_editor_swatches = {"x": 1}
        self.app._theme_editor_status = "editing"
        self.app.show_settings = Mock()
        self.app.show_plugins = Mock()
        self.app._notify = Mock()
        self.app._surface_card = lambda content, **kwargs: ft.Container(content=content)
        self.controller = ThemeSettingsController(self.app)

    def test_disabling_refinement_persists_and_refreshes_settings_screen(self) -> None:
        event = SimpleNamespace(control=SimpleNamespace(value=False))

        self.controller.on_genre_refinement_toggled(event)

        self.assertFalse(self.app.settings.energy_genre_refinement_enabled)
        self.app.settings_store.save.assert_called_once()
        self.app.show_settings.assert_called_once()

    def test_disabling_gpu_keeps_refinement_enabled(self) -> None:
        event = SimpleNamespace(control=SimpleNamespace(value=False))

        self.controller.on_genre_gpu_toggled(event)

        self.assertFalse(self.app.settings.energy_genre_gpu_enabled)
        self.assertTrue(self.app.settings.energy_genre_refinement_enabled)

    def test_card_shows_current_values_and_disables_gpu_checkbox_when_refinement_off(self) -> None:
        self.app.settings = AppSettings(
            energy_genre_refinement_enabled=False, energy_genre_gpu_enabled=True
        )

        card = self.controller.genre_refinement_card()

        checkboxes = [
            control
            for control in card.content.controls
            if isinstance(control, ft.Checkbox)
        ]
        self.assertEqual(2, len(checkboxes))
        refinement_checkbox, gpu_checkbox = checkboxes
        self.assertFalse(refinement_checkbox.value)
        self.assertTrue(gpu_checkbox.disabled)

    def test_save_failure_does_not_change_settings(self) -> None:
        original = self.app.settings
        self.app.settings_store.save.side_effect = OSError("disk full")
        event = SimpleNamespace(control=SimpleNamespace(value=False))

        self.controller.on_genre_refinement_toggled(event)

        self.assertIs(original, self.app.settings)


if __name__ == "__main__":
    unittest.main()
