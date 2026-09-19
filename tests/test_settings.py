from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from djmaker.settings import (
    AppSettings,
    LIBRARY_SCALE_MAX,
    LIBRARY_SCALE_MIN,
    SettingsStore,
)


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

    def test_energy_genre_toggles_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(
                energy_genre_refinement_enabled=False, energy_genre_gpu_enabled=False
            )

            store.save(expected)

            self.assertEqual(expected, store.load())

    def test_energy_genre_toggles_default_to_true_and_reject_non_bool(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "energy_genre_refinement_enabled": "yes",
                        "energy_genre_gpu_enabled": 1,
                    }
                ),
                encoding="utf-8",
            )

            settings = SettingsStore(path).load()

            self.assertTrue(settings.energy_genre_refinement_enabled)
            self.assertTrue(settings.energy_genre_gpu_enabled)

    def test_library_scale_round_trips_and_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(library_scale_percent=130)

            store.save(expected)

            self.assertEqual(130, store.load().library_scale_percent)
            self.assertEqual(
                LIBRARY_SCALE_MIN,
                AppSettings.from_mapping(
                    {"library_scale_percent": 10}
                ).library_scale_percent,
            )
            self.assertEqual(
                LIBRARY_SCALE_MAX,
                AppSettings.from_mapping(
                    {"library_scale_percent": 999}
                ).library_scale_percent,
            )
            self.assertEqual(
                130,
                AppSettings.from_mapping(
                    {"library_scale_percent": 126}
                ).library_scale_percent,
            )

    def test_invalid_library_scale_uses_default(self) -> None:
        settings = AppSettings.from_mapping({"library_scale_percent": "broken"})

        self.assertEqual(100, settings.library_scale_percent)

    def test_broken_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("{broken", encoding="utf-8")

            self.assertEqual(AppSettings(), SettingsStore(path).load())


if __name__ == "__main__":
    unittest.main()
