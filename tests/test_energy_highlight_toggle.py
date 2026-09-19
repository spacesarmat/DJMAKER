"""Регрессия тумблера подсветки строк медиатеки по энергии (AIR)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from djmaker.settings import AppSettings
from djmaker.ui.theme_settings_controls import ThemeSettingsController


class EnergyHighlightToggleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(energy_highlight_enabled=True)
        self.app.settings_store = Mock()
        self.app.show_library = Mock()
        self.app._notify = Mock()
        self.controller = ThemeSettingsController(self.app)

    def test_toggle_flips_current_state(self) -> None:
        self.controller.toggle_energy_highlight(None)
        self.assertFalse(self.app.settings.energy_highlight_enabled)

        self.controller.toggle_energy_highlight(None)
        self.assertTrue(self.app.settings.energy_highlight_enabled)

    def test_toggle_persists_and_refreshes_library(self) -> None:
        self.controller.toggle_energy_highlight(None)

        self.app.settings_store.save.assert_called_once()
        self.app.show_library.assert_called_once_with(local_update=True)

    def test_save_failure_does_not_change_settings_or_refresh(self) -> None:
        original = self.app.settings
        self.app.settings_store.save.side_effect = OSError("disk full")

        self.controller.toggle_energy_highlight(None)

        self.assertIs(original, self.app.settings)
        self.app.show_library.assert_not_called()


if __name__ == "__main__":
    unittest.main()
