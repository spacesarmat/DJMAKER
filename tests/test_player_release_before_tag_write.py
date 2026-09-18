"""Регрессия: плеер отпускает файловый хендл перед записью тегов (Windows PermissionError)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

import flet_audio as fta

from djmaker.ui.library_search_controls import LibrarySearchController
from djmaker.ui.player_controls import PlayerControlsController


def _run(coro):
    return asyncio.run(coro)


class ReleaseIfCurrentTests(unittest.TestCase):
    def _app(self) -> Mock:
        app = Mock()
        app.audio = Mock()
        app.audio.pause = AsyncMock()
        app.audio.release = AsyncMock()
        app._player_duration_ms = 0
        app._player_position_ms = 0
        return app

    def test_releases_audio_when_track_is_currently_loaded(self) -> None:
        app = self._app()
        app._player_track_id = 7
        controller = PlayerControlsController(app)

        _run(controller.release_if_current(7))

        app.audio.pause.assert_awaited_once()
        app.audio.release.assert_awaited_once()
        self.assertEqual(app._player_state, fta.AudioState.STOPPED)

    def test_does_nothing_for_a_different_track(self) -> None:
        app = self._app()
        app._player_track_id = 1
        controller = PlayerControlsController(app)

        _run(controller.release_if_current(7))

        app.audio.pause.assert_not_called()
        app.audio.release.assert_not_called()

    def test_does_nothing_when_no_audio_loaded(self) -> None:
        app = self._app()
        app.audio = None
        app._player_track_id = 7
        controller = PlayerControlsController(app)

        _run(controller.release_if_current(7))  # must not raise

    def test_release_failure_is_swallowed(self) -> None:
        app = self._app()
        app._player_track_id = 7
        app.audio.release.side_effect = RuntimeError("backend unavailable")
        controller = PlayerControlsController(app)

        _run(controller.release_if_current(7))  # must not raise


class TagWriteErrorMessageTests(unittest.TestCase):
    def test_permission_error_gets_actionable_hint(self) -> None:
        exc = RuntimeError(
            "Не удалось записать теги C:\\Music\\a.flac: [Errno 13] Permission denied: 'a.flac'"
        )
        message = LibrarySearchController.tag_write_error_message(exc)
        self.assertIn("занят другой программой", message)
        self.assertIn("только чтение", message)

    def test_other_errors_keep_generic_message(self) -> None:
        exc = RuntimeError("disk full")
        message = LibrarySearchController.tag_write_error_message(exc)
        self.assertEqual(message, "Не удалось сохранить: disk full")


if __name__ == "__main__":
    unittest.main()
