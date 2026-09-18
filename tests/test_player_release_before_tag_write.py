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
        app._player_switch_lock = asyncio.Lock()
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

    def test_waits_for_player_switch_lock_before_touching_audio(self) -> None:
        """Регрессия гонки: release() во время активного play()/смены source
        вешает RPC-вызов play() на стороне Flet (видели TimeoutException 30s).
        release_if_current должен ждать тот же _player_switch_lock, что и
        play_track/play_external_audio, а не трогать audio в обход него.
        """

        async def scenario() -> list[str]:
            app = self._app()
            app._player_track_id = 7
            controller = PlayerControlsController(app)
            order: list[str] = []

            async def holds_lock_like_play_track() -> None:
                async with app._player_switch_lock:
                    order.append("play-acquired")
                    await asyncio.sleep(0.05)
                    order.append("play-released")

            hold_task = asyncio.create_task(holds_lock_like_play_track())
            await asyncio.sleep(0.01)  # дать play_track захватить лок первым
            await controller.release_if_current(7)
            order.append("release_if_current-done")
            await hold_task
            return order

        order = _run(scenario())

        self.assertEqual(
            order, ["play-acquired", "play-released", "release_if_current-done"]
        )

    def test_rechecks_current_track_after_acquiring_lock(self) -> None:
        """Пока release_if_current ждал лок, плеер мог переключиться на другой
        трек — после захвата лока нужно проверить это состояние заново."""

        async def scenario() -> Mock:
            app = self._app()
            app._player_track_id = 7
            controller = PlayerControlsController(app)

            async def switch_track_while_locked() -> None:
                async with app._player_switch_lock:
                    app._player_track_id = 99
                    await asyncio.sleep(0.02)

            hold_task = asyncio.create_task(switch_track_while_locked())
            await asyncio.sleep(0.005)
            await controller.release_if_current(7)
            await hold_task
            return app

        app = _run(scenario())

        app.audio.pause.assert_not_called()
        app.audio.release.assert_not_called()


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
