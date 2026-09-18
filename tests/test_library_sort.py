"""Проверки сортировки SQLite, настроек и обработчиков UI."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.domain.library_sort import camelot_order, release_year
from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.services.audio_analysis import camelot_code
from djmaker.services.library import LibraryService
from djmaker.settings import AppSettings, SettingsStore
from djmaker.ui.app import DJMakerUI
from djmaker.ui.library_search_controls import LibrarySearchController
from djmaker.ui.theme_settings_controls import ThemeSettingsController


class LibrarySortDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = LibraryDatabase(self.root / "library.sqlite3")
        self.db.initialize()
        rows = [
            ("a", "Бета", "Я", 100, "2024-01-01", 200, "10A"),
            ("b", "альфа", "Б", 90, "1999", 100, "2B"),
            ("c", "Альфа", "а", 120, "2005", 300, "2A"),
            ("d", "", "", None, "неизвестно", None, ""),
        ]
        for name, artist, title, bpm, year, duration, key in rows:
            self.db.upsert_track(
                path=self.root / f"{name}.mp3",
                root=self.root,
                size=1,
                mtime_ns=1,
                extension=".mp3",
                file_hash=name,
                metadata=AudioMetadata(
                    artist=artist,
                    title=title,
                    bpm=bpm,
                    year=year,
                    musical_key=key,
                    genre="test",
                ),
                technical=AudioTechnicalInfo(duration=duration),
                scan_token="scan",
            )
        with self.db.connection() as conn:
            conn.execute("UPDATE tracks SET added_at='2024-01-01' WHERE file_hash='c'")
            conn.execute("UPDATE tracks SET added_at='2024-01-02' WHERE file_hash='b'")
            conn.execute("UPDATE tracks SET added_at='2024-01-03' WHERE file_hash='a'")
            conn.execute("UPDATE tracks SET added_at='' WHERE file_hash='d'")
            conn.commit()

    def order(self, key: str, descending: bool = False, **kwargs) -> list[str]:
        return [
            t.file_hash
            for t in self.db.list_tracks(sort_by=key, descending=descending, **kwargs)
        ]

    def test_all_fields_both_directions_and_missing_last(self) -> None:
        expected = {
            "artist": ["c", "b", "a", "d"],
            "title": ["c", "b", "a", "d"],
            "bpm": ["b", "a", "c", "d"],
            "camelot": ["c", "b", "a", "d"],
            "added": ["c", "b", "a", "d"],
            "year": ["b", "c", "a", "d"],
            "duration": ["b", "a", "c", "d"],
        }
        for key, order in expected.items():
            with self.subTest(key=key):
                self.assertEqual(self.order(key), order)
                self.assertEqual(
                    self.order(key, True), list(reversed(order[:-1])) + ["d"]
                )

    def test_search_and_limit_are_applied_after_global_order(self) -> None:
        self.assertEqual(self.order("bpm", True, search="test", limit=1), ["c"])
        self.assertEqual(self.order("year", search="Бета", limit=1), ["a"])

    def test_analysis_values_take_precedence_over_tags(self) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE tracks SET analysis_bpm=80, analysis_camelot='1B' WHERE file_hash='a'"
            )
            conn.commit()
        self.assertEqual(self.order("bpm"), ["a", "b", "c", "d"])
        self.assertEqual(self.order("camelot"), ["a", "c", "b", "d"])

    def test_invalid_analysis_falls_back_to_tags(self) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE tracks SET analysis_bpm=0, analysis_camelot='?' WHERE file_hash='a'"
            )
            conn.commit()
        self.assertEqual(self.order("bpm"), ["b", "a", "c", "d"])
        self.assertEqual(self.order("camelot"), ["c", "b", "a", "d"])

    def test_unknown_sort_is_safe_and_ties_are_deterministic(self) -> None:
        self.assertEqual(self.order("id; DROP TABLE tracks; --"), self.order("artist"))
        with self.db.connection() as conn:
            conn.execute("UPDATE tracks SET artist='Same', title='Same', bpm=100")
            conn.commit()
        self.assertEqual(self.order("bpm"), ["a", "b", "c", "d"])
        self.assertEqual(self.order("bpm", True), ["a", "b", "c", "d"])

    def test_service_forwards_query_and_order(self) -> None:
        service = LibraryService.__new__(LibraryService)
        service.database = self.db
        tracks = service.tracks("test", limit=1, sort_by="bpm", descending=True)
        self.assertEqual([t.file_hash for t in tracks], ["c"])


class LibrarySortSettingsTests(unittest.TestCase):
    def test_old_or_invalid_settings_use_defaults(self) -> None:
        for raw in (
            {},
            {"library_sort": []},
            {"library_sort": "bad", "library_sort_descending": "false"},
        ):
            settings = AppSettings.from_mapping(raw)
            self.assertEqual(settings.library_sort, "artist")
            self.assertFalse(settings.library_sort_descending)

    def test_sort_survives_reload_and_keeps_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp) / "settings.json")
            settings = AppSettings(
                theme_palette="strict",
                library_scale_percent=130,
                library_sort="camelot",
                library_sort_descending=True,
            )
            store.save(settings)
            self.assertEqual(store.load(), settings)

    def test_camelot_numeric_and_musical_keys(self) -> None:
        for pitch in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"):
            for scale in ("major", "minor"):
                with self.subTest(pitch=pitch, scale=scale):
                    self.assertEqual(
                        camelot_order(f"{pitch} {scale}"),
                        camelot_order(camelot_code(pitch, scale)),
                    )
        for key, code in [
            ("Am", "8A"),
            ("C major", "8B"),
            ("F# minor", "11A"),
            ("Db", "3B"),
        ]:
            self.assertEqual(camelot_order(key), camelot_order(code))
        self.assertLess(camelot_order("2B"), camelot_order("10A"))
        for key in ("", "13A", "0B", "unknown"):
            self.assertIsNone(camelot_order(key))
        self.assertEqual(release_year("2024-12-31"), 2024)
        self.assertIsNone(release_year("bad"))


class LibrarySortUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = DJMakerUI.__new__(DJMakerUI)
        self.ui.settings = AppSettings()
        self.ui.settings_store = Mock()
        self.ui.page = Mock()
        self.ui.library_search = LibrarySearchController(self.ui)
        self.ui.theme_settings = ThemeSettingsController(self.ui)
        self.ui.show_library = Mock()
        self.ui._notify = Mock()
        self.ui._search_revision = 10
        self.ui._selected_track_id = 7
        self.ui._library_track_indices = {7: 1}
        self.ui.search = ft.TextField(value="Artist")

    def test_selection_saves_preserves_query_and_recenters_selection(self) -> None:
        ui = self.ui
        control = ft.Dropdown(value="bpm")
        ui._on_library_sort_selected(SimpleNamespace(control=control))
        ui.settings_store.save.assert_called_once_with(ui.settings)
        self.assertEqual(ui.settings.library_sort, "bpm")
        self.assertEqual(ui.search.value, "Artist")
        self.assertEqual(ui._search_revision, 11)
        ui.show_library.assert_called_once_with(local_update=True)
        ui.page.run_task.assert_called_once_with(ui._select_library_track, 7)

    def test_toggle_direction_and_construct_controls(self) -> None:
        ui = self.ui
        ui._toggle_library_sort_direction(None)
        self.assertTrue(ui.settings.library_sort_descending)
        row = ui._library_sort_control()
        self.assertEqual(row.controls[0].value, "artist")
        self.assertEqual(len(row.controls[0].options), 7)
        self.assertEqual(row.controls[1].icon, ft.Icons.ARROW_DOWNWARD)

    def test_save_failure_restores_control_without_changing_order(self) -> None:
        ui = self.ui
        ui.settings_store.save.side_effect = OSError("disk full")
        control = ft.Dropdown(value="bpm")
        with self.assertLogs("djmaker.ui.theme_settings_controls", level="ERROR"):
            ui._on_library_sort_selected(SimpleNamespace(control=control))
        self.assertEqual(ui.settings, AppSettings())
        self.assertEqual(control.value, "artist")
        ui.show_library.assert_not_called()
        ui.page.update.assert_called_once_with(control)
        ui._notify.assert_called_once()

    def test_invalid_selection_is_ignored(self) -> None:
        self.ui._on_library_sort_selected(
            SimpleNamespace(control=SimpleNamespace(value="bad"))
        )
        self.ui.settings_store.save.assert_not_called()
