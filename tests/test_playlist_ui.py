"""Обработчики Flet с настоящей SQLite и экспортом; desktop-клиент не запускается."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import flet as ft

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.infrastructure.playlists import PlaylistRepository
from djmaker.services.tasks import TaskManager, TaskPaused, TaskStatus
from djmaker.settings import AppSettings
from djmaker.ui.app import DJMakerUI


class PlaylistUIFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = LibraryDatabase(self.root / "library.sqlite3")
        self.db.initialize()
        self.repo = PlaylistRepository(self.db)
        for index in range(2):
            path = self.root / f"{index}.mp3"
            path.write_bytes(b"original")
            self.db.upsert_track(
                path=path,
                root=self.root,
                size=8,
                mtime_ns=1,
                extension=".mp3",
                file_hash=str(index),
                metadata=AudioMetadata(title=f"Track {index}"),
                technical=AudioTechnicalInfo(duration=60),
                scan_token="test",
            )
        self.ids = [t.id for t in self.db.list_tracks()]
        ui = self.ui = DJMakerUI.__new__(DJMakerUI)
        ui.service = SimpleNamespace(playlists=self.repo)
        ui.page = Mock()
        ui.navigation = SimpleNamespace(selected_index=0)
        ui.content = ft.Column()
        ui.settings = AppSettings()
        ui._selected_playlist_id = None
        ui._playlist_export_contexts = {}
        ui._waveform_views = {1: object()}
        ui._track_row_cards = {1: ft.Container()}
        ui._library_track_indices = {1: 0}
        ui._library_list = None
        for name in (
            "_audio_task_contexts",
            "_scan_task_paths",
            "_drop_task_paths",
            "_artwork_task_contexts",
            "_waveform_task_contexts",
        ):
            setattr(ui, name, {})
        ui.tasks = TaskManager()
        ui._notify = Mock()
        ui._refresh_task_indicator = Mock()
        ui.show_tasks = Mock(
            side_effect=lambda: setattr(ui.navigation, "selected_index", 5)
        )

        async def run(func, *args, **kwargs):
            return func(*args, **kwargs)

        ui.workers = SimpleNamespace(run=AsyncMock(side_effect=run))

    def create_through_dialog(self) -> int:
        self.ui._playlist_name_dialog(track_id=self.ids[0])
        dialog = self.ui.page.show_dialog.call_args.args[0]
        dialog.content.value = "Вечер"
        dialog.actions[-1].on_click(None)
        return self.repo.list()[0].id

    def test_create_add_reorder_remove_and_render(self) -> None:
        ui = self.ui
        ident = self.create_through_dialog()
        ui._add_to_playlist_dialog(self.ids[1])
        dialog = ui.page.show_dialog.call_args.args[0]
        dialog.actions[-1].on_click(None)
        ui.show_playlists()
        self.assertEqual(ui.navigation.selected_index, 7)
        self.assertEqual(ui._waveform_views, {})
        self.assertEqual(ui._track_row_cards, {})
        self.assertEqual(len(ui.content.controls[-1].controls), 2)
        ui._move_playlist_track(ident, self.ids[1], -1)
        self.assertEqual([t.id for t in self.repo.tracks(ident)], self.ids[::-1])
        ui._remove_playlist_track(ident, self.ids[0])
        self.assertEqual([t.id for t in self.repo.tracks(ident)], [self.ids[1]])
        ui.page.update.assert_called_with(ui.content)

    def test_delete_requires_confirmation_and_preserves_files(self) -> None:
        self.create_through_dialog()
        playlist = self.repo.list()[0]
        self.ui._delete_playlist_dialog(playlist)
        self.assertEqual(len(self.repo.list()), 1)
        dialog = self.ui.page.show_dialog.call_args.args[0]
        dialog.actions[-1].on_click(None)
        self.assertEqual(self.repo.list(), [])
        self.assertTrue((self.root / "0.mp3").exists())

    def test_menu_opens_create_when_no_playlists(self) -> None:
        menu = self.ui._playlist_track_menu(self.ids[0])
        menu.items[0].on_click(None)
        dialog = self.ui.page.show_dialog.call_args.args[0]
        self.assertEqual(dialog.title.value, "Создать плейлист")

    def test_invalid_name_keeps_dialog_open(self) -> None:
        self.ui._playlist_name_dialog()
        dialog = self.ui.page.show_dialog.call_args.args[0]
        dialog.content.value = "   "
        dialog.actions[-1].on_click(None)
        self.assertTrue(dialog.content.error_text)
        self.ui.page.pop_dialog.assert_not_called()

    async def test_export_with_files_through_picker_and_task(self) -> None:
        self.create_through_dialog()
        target = self.root / "exports"
        target.mkdir()
        picker = SimpleNamespace(get_directory_path=AsyncMock(return_value=str(target)))
        with patch("djmaker.ui.playlists.ft.FilePicker", return_value=picker):
            await self.ui._start_playlist_export(True)
        snapshot = self.ui.tasks.snapshots()[0]
        self.assertEqual(snapshot.status, TaskStatus.COMPLETED)
        self.assertEqual(snapshot.completed, 1)
        self.assertEqual(self.ui._playlist_export_contexts, {})
        playlist_file = next(target.rglob("*.m3u8"))
        self.assertIn("Music/", playlist_file.read_text(encoding="utf-8"))
        self.assertEqual(next(target.rglob("*.mp3")).read_bytes(), b"original")

    async def test_picker_cancel_creates_no_task(self) -> None:
        self.create_through_dialog()
        picker = SimpleNamespace(get_directory_path=AsyncMock(return_value=None))
        with patch("djmaker.ui.playlists.ft.FilePicker", return_value=picker):
            await self.ui._start_playlist_export(True)
        self.assertEqual(self.ui.tasks.snapshots(), [])

    async def test_export_failure_is_visible_and_context_released(self) -> None:
        self.create_through_dialog()
        (self.root / "0.mp3").unlink()
        picker = SimpleNamespace(
            get_directory_path=AsyncMock(return_value=str(self.root))
        )
        with (
            patch("djmaker.ui.playlists.ft.FilePicker", return_value=picker),
            self.assertLogs("djmaker.ui.playlists", level="ERROR"),
        ):
            await self.ui._start_playlist_export(True)
        self.assertEqual(self.ui.tasks.snapshots()[0].status, TaskStatus.FAILED)
        self.assertEqual(self.ui._playlist_export_contexts, {})
        self.assertIn("Экспорт не завершён", self.ui._notify.call_args.args[0])

    async def test_paused_export_resumes_via_task_manager(self) -> None:
        self.create_through_dialog()
        picker = SimpleNamespace(
            get_directory_path=AsyncMock(return_value=str(self.root))
        )
        self.ui.workers.run.side_effect = TaskPaused()
        with patch("djmaker.ui.playlists.ft.FilePicker", return_value=picker):
            await self.ui._start_playlist_export(True)
        task = self.ui.tasks.snapshots()[0]
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertIn(task.id, self.ui._playlist_export_contexts)

        async def run(func, *args, **kwargs):
            return func(*args, **kwargs)

        self.ui.workers.run.side_effect = run
        self.ui._resume_task(task.id)
        callback, task_id = self.ui.page.run_task.call_args.args
        await callback(task_id)
        self.assertEqual(
            self.ui.tasks.get(task.id).snapshot().status, TaskStatus.COMPLETED
        )
        self.assertEqual(self.ui._playlist_export_contexts, {})
