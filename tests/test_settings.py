from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from djmaker.settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp) / "settings.json")
            self.assertEqual(AppSettings(), store.load())

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(theme_mode="dark", theme_palette="violet")

            store.save(expected)

            self.assertEqual(expected, store.load())
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_values_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text(
                json.dumps({"theme_mode": "unknown", "theme_palette": "neon"}),
                encoding="utf-8",
            )

            settings = SettingsStore(path).load()

            self.assertEqual("system", settings.theme_mode)
            self.assertEqual("djmaker_blue", settings.theme_palette)

    def test_broken_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("{broken", encoding="utf-8")

            self.assertEqual(AppSettings(), SettingsStore(path).load())


if __name__ == "__main__":
    unittest.main()
