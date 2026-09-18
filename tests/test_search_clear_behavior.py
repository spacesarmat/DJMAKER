"""Проверки обработчиков поиска без запуска desktop-клиента."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.settings import AppSettings
from djmaker.ui.app import DJMakerUI
from djmaker.ui.library_search_controls import LibrarySearchController
from djmaker.ui.navigation_cards_controls import NavigationCardsController
from djmaker.ui.track_row_controls import TrackRowController


class SearchClearBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.ui = DJMakerUI.__new__(DJMakerUI)
        self.ui.page = Mock()
        self.ui.search = ft.TextField(value="Artist")
        self.ui.search_clear_button = ft.IconButton(visible=True)
        self.ui.navigation = SimpleNamespace(selected_index=0)
        self.ui.navigation_cards = NavigationCardsController(self.ui)
        self.ui.track_row = TrackRowController(self.ui)
        self.ui.library_search = LibrarySearchController(self.ui)
        self.ui._search_revision = 0
        self.ui.show_library = Mock()
        self.ui.library_search.show_library = self.ui.show_library

    async def test_clear_invalidates_search_already_waiting_for_debounce(self) -> None:
        ui = self.ui
        ui._on_search_change(None)
        callback, revision = ui.page.run_task.call_args.args
        pending = asyncio.create_task(callback(revision))
        await asyncio.sleep(0)

        ui._clear_search(None)

        self.assertEqual(ui.search.value, "")
        self.assertFalse(ui.search_clear_button.visible)
        ui.show_library.assert_called_once_with(local_update=True)
        await pending
        ui.show_library.assert_called_once_with(local_update=True)

    async def test_typing_again_after_clear_applies_only_latest_query(self) -> None:
        ui = self.ui
        ui._on_search_change(None)
        old_revision = ui._search_revision
        ui._clear_search(None)
        ui.show_library.reset_mock()
        ui.search.value = "Новый запрос"
        ui._on_search_change(None)

        await asyncio.gather(
            ui._debounced_library_search(old_revision),
            ui._debounced_library_search(ui._search_revision),
        )

        self.assertTrue(ui.search_clear_button.visible)
        self.assertEqual(ui.search.value, "Новый запрос")
        ui.show_library.assert_called_once_with(local_update=True)

    async def test_enter_invalidates_pending_debounce(self) -> None:
        ui = self.ui
        ui._on_search_change(None)
        revision = ui._search_revision
        await ui._on_search(None)
        await ui._debounced_library_search(revision)
        ui.show_library.assert_called_once_with(local_update=True)

    async def test_pending_search_does_not_reopen_library_after_navigation(self) -> None:
        ui = self.ui
        ui._on_search_change(None)
        ui.navigation.selected_index = 1
        await ui._debounced_library_search(ui._search_revision)
        ui.show_library.assert_not_called()

    def test_button_visibility_includes_whitespace_and_updates_only_button(self) -> None:
        ui = self.ui
        cases = [("Artist", True), ("   ", True), ("", False), (None, False)]
        for value, visible in cases:
            with self.subTest(value=value):
                ui.search.value = value
                ui._on_search_change(None)
                self.assertEqual(ui.search_clear_button.visible, visible)
                ui.page.update.assert_called_with(ui.search_clear_button)

    def test_clear_queries_full_library_and_updates_only_content(self) -> None:
        ui = self.ui
        del ui.show_library
        del ui.library_search.show_library
        ui.service = Mock()
        ui.service.tracks.return_value = [
            SimpleNamespace(id=1), SimpleNamespace(id=2)
        ]
        ui.settings = AppSettings()
        ui.busy = ft.ProgressRing()
        ui.theme_button = ft.IconButton()
        ui.content = ft.Column()
        ui._waveform_views = {}
        ui._track_row_cards = {}
        ui._track_row = Mock(side_effect=lambda track: ft.Text(str(track.id)))

        ui._clear_search(None)

        ui.service.tracks.assert_called_once_with(
            "", limit=1000, sort_by="artist", descending=False
        )
        self.assertEqual(len(ui._library_list.controls), 2)
        self.assertEqual(ui._library_track_indices, {1: 0, 2: 1})
        self.assertEqual(ui._library_list.item_extent, ui._track_item_extent())
        ui.page.update.assert_called_once_with(ui.content)
        ui.page.run_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
