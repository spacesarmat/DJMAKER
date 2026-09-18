"""Регрессия диалога кандидатов метаданных: возврат на текущий экран после применения."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, MetadataCandidate, TrackRecord
from djmaker.ui.library_search_controls import LibrarySearchController
from djmaker.ui.navigation_cards_controls import NavigationCardsController


def _track(track_id: int = 1) -> TrackRecord:
    return TrackRecord(
        id=track_id,
        path=Path("/music/a.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash="h",
        metadata=AudioMetadata(title="Old", artist="Old"),
        technical=AudioTechnicalInfo(),
    )


def _find_buttons(control: ft.Control) -> list[ft.Button]:
    found: list[ft.Button] = []
    stack = [control]
    while stack:
        current = stack.pop()
        if isinstance(current, ft.Button):
            found.append(current)
        content = getattr(current, "content", None)
        if isinstance(content, ft.Control):
            stack.append(content)
        for child in getattr(current, "controls", None) or []:
            stack.append(child)
    return found


class OpenCandidatesApplyNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.service = Mock()
        self.app.service.database = Mock()
        self.app.service.database.get_track.return_value = _track()
        self.app.service.apply_candidate = Mock()
        self.app.player = Mock()
        self.app.player.release_if_current = AsyncMock()
        self.app.workers = SimpleNamespace(run=AsyncMock(side_effect=lambda f, *a: f(*a)))
        self.app._notify = Mock()
        self.app._set_busy = Mock()
        self.app.page = Mock()
        self.app.show_library = Mock()
        self.app.show_plugins = Mock()
        self.app._surface_card = NavigationCardsController.surface_card
        self.controller = LibrarySearchController(self.app)

    def _open_and_get_apply(self, track_id: int = 1):
        candidate = MetadataCandidate(
            provider_id="deezer", external_id="1", title="New", artist="New"
        )
        self.controller.open_candidates(track_id, [candidate], "Deezer")
        dialog = self.app.page.show_dialog.call_args[0][0]
        button = next(b for b in _find_buttons(dialog) if str(b.content) == "Применить")
        return button.on_click

    async def test_stays_on_metadata_tab_when_applied_from_there(self) -> None:
        self.app.navigation = SimpleNamespace(selected_index=3)
        apply = self._open_and_get_apply()

        await apply(None)

        self.app.show_plugins.assert_called_once()
        self.app.show_library.assert_not_called()

    async def test_returns_to_library_when_applied_from_library(self) -> None:
        self.app.navigation = SimpleNamespace(selected_index=0)
        apply = self._open_and_get_apply()

        await apply(None)

        self.app.show_library.assert_called_once()
        self.app.show_plugins.assert_not_called()


if __name__ == "__main__":
    unittest.main()
