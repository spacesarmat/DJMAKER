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
from djmaker.infrastructure.set_timeline import SetTimelineRepository
from djmaker.services.tasks import TaskManager, TaskPaused, TaskStatus
from djmaker.settings import AppSettings
from djmaker.ui.app import DJMakerUI
from djmaker.ui.navigation_cards_controls import NavigationCardsController
from djmaker.ui.task_progress_controls import TaskProgressController
from djmaker.ui.track_row_controls import TrackRowController


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
                metadata=AudioMetadata(
                    title=f"Track {index}",
                    bpm=128 + index * 2,
                    musical_key=f"{8 + index}A",
                ),
                technical=AudioTechnicalInfo(duration=60),
                scan_token="test",
            )
        self.ids = [t.id for t in self.db.list_tracks()]
        ui = self.ui = DJMakerUI.__new__(DJMakerUI)
        ui.service = SimpleNamespace(
            playlists=self.repo,
            set_timeline=SetTimelineRepository(self.db),
            tracks=lambda search="", limit=10_000: self.db.list_tracks(
                search=search, limit=limit
            ),
        )
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
        ui._selected_library_track_ids = set()
        ui._library_batch_add_button = None
        ui._library_batch_select_all_button = None
        ui._library_batch_clear_button = None
        for name in (
            "_audio_task_contexts",
            "_scan_task_paths",
            "_drop_task_paths",
            "_artwork_task_contexts",
            "_waveform_task_contexts",
            "_metadata_bulk_task_contexts",
        ):
            setattr(ui, name, {})
        ui.tasks = TaskManager()
        ui.task_progress = TaskProgressController(ui)
        ui.navigation_cards = NavigationCardsController(ui)
        ui.track_row = TrackRowController(ui)
        ui._notify = Mock()
        ui.show_library = Mock()
        ui._refresh_task_indicator = Mock()
        ui.task_progress.refresh_task_indicator = Mock()
        ui.show_tasks = Mock(
            side_effect=lambda: setattr(ui.navigation, "selected_index", 5)
        )
        ui.task_progress.show_tasks = ui.show_tasks

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

    def test_library_multiselect_updates_controls_and_can_be_cleared(self) -> None:
        ui = self.ui
        ui._library_track_indices = {self.ids[1]: 0, self.ids[0]: 1}
        controls = ui._library_batch_controls()
        checkbox = ui._library_selection_checkbox(self.ids[0])

        checkbox.value = True
        checkbox.on_change(SimpleNamespace(control=checkbox))

        self.assertEqual(ui._selected_library_track_ids, {self.ids[0]})
        self.assertEqual(controls.controls[0].content, "В плейлист: 1")
        self.assertFalse(controls.controls[0].disabled)
        self.assertTrue(ui._library_selection_checkbox(self.ids[0]).value)
        ui._clear_library_selection()
        self.assertEqual(ui._selected_library_track_ids, set())
        ui.show_library.assert_called_once_with(local_update=True)

    def test_select_all_selects_every_visible_track(self) -> None:
        ui = self.ui
        ui._library_track_indices = {self.ids[0]: 0, self.ids[1]: 1}
        controls = ui._library_batch_controls()

        select_all_button = controls.controls[1]
        self.assertEqual(select_all_button.tooltip, "Выделить все")
        self.assertFalse(select_all_button.disabled)

        ui._select_all_library_tracks()

        self.assertEqual(ui._selected_library_track_ids, set(self.ids))
        ui.show_library.assert_called_once_with(local_update=True)

    def test_select_all_button_disabled_when_library_is_empty(self) -> None:
        ui = self.ui
        ui._library_track_indices = {}
        controls = ui._library_batch_controls()

        self.assertTrue(controls.controls[1].disabled)

    def test_select_all_does_nothing_when_library_is_empty(self) -> None:
        ui = self.ui
        ui._library_track_indices = {}

        ui._select_all_library_tracks()

        ui.show_library.assert_not_called()

    def test_select_all_preserves_previously_selected_tracks(self) -> None:
        ui = self.ui
        ui._library_track_indices = {self.ids[0]: 0, self.ids[1]: 1}
        ui._selected_library_track_ids = {self.ids[0]}

        ui._select_all_library_tracks()

        self.assertEqual(ui._selected_library_track_ids, set(self.ids))

    def test_batch_add_uses_current_library_order_and_reports_duplicates(self) -> None:
        ui = self.ui
        playlist_id = self.repo.create("Batch")
        self.repo.add(playlist_id, self.ids[1])
        ui._library_track_indices = {self.ids[1]: 0, self.ids[0]: 1}
        ui._selected_library_track_ids = set(self.ids)

        ui._add_selected_to_playlist_dialog()
        dialog = ui.page.show_dialog.call_args.args[0]
        dialog.actions[-1].on_click(None)

        self.assertEqual(
            [track.id for track in self.repo.tracks(playlist_id)],
            [self.ids[1], self.ids[0]],
        )
        self.assertEqual(ui._selected_library_track_ids, set())
        self.assertIn("Добавлено: 1", ui._notify.call_args.args[0])

    def test_batch_selection_can_create_new_playlist_atomically(self) -> None:
        ui = self.ui
        ui._library_track_indices = {self.ids[1]: 0, self.ids[0]: 1}
        ui._selected_library_track_ids = set(self.ids)

        ui._add_selected_to_playlist_dialog()
        dialog = ui.page.show_dialog.call_args.args[0]
        dialog.content.value = "Новый сет"
        dialog.actions[-1].on_click(None)

        playlist = self.repo.list()[0]
        self.assertEqual(
            [track.id for track in self.repo.tracks(playlist.id)],
            [self.ids[1], self.ids[0]],
        )
        self.assertEqual(ui._selected_library_track_ids, set())

    def test_recommendation_dialog_adds_checked_candidate(self) -> None:
        ui = self.ui
        playlist_id = self.repo.create("Smart", track_id=self.ids[0])
        ui._selected_playlist_id = playlist_id

        ui._open_set_recommendations()

        dialog = ui.page.show_dialog.call_args.args[0]
        results = dialog.content.controls[3]
        self.assertEqual(len(results.controls), 1)
        checkbox = results.controls[0].content.controls[0]
        checkbox.value = True
        checkbox.on_change(SimpleNamespace(control=checkbox))
        self.assertFalse(dialog.actions[-1].disabled)
        dialog.actions[-1].on_click(None)
        self.assertEqual(
            [track.id for track in self.repo.tracks(playlist_id)], self.ids
        )
        self.assertIn("Рекомендации добавлены: 1", ui._notify.call_args.args[0])

    def test_transition_editor_snaps_point_and_saves_adjacent_pair(self) -> None:
        playlist_id = self.repo.create_with_tracks("Mix", self.ids)
        self.ui._selected_playlist_id = playlist_id

        self.ui._open_transition_pair_editor()

        toolbar, actions, timeline = self.ui.content.controls
        self.assertEqual(toolbar.controls[1].value, "0")
        outgoing_block = timeline.controls[0].content
        gesture = outgoing_block.controls[1].content
        gesture.on_tap(SimpleNamespace(local_position=SimpleNamespace(x=410.0)))
        actions.controls[0].on_click(None)
        saved = self.ui.service.set_timeline.transition(
            playlist_id, self.ids[0], self.ids[1]
        )
        self.assertIsNotNone(saved)
        self.assertGreater(saved.outgoing_cue_ms, 0)
        self.assertIn("Сохранено", actions.controls[2].value)

    def test_arrangement_timeline_overlaps_lanes_and_drag_saves_position(self) -> None:
        playlist_id = self.repo.create_with_tracks("Timeline", self.ids)
        self.ui._selected_playlist_id = playlist_id

        self.ui._open_transition_editor()

        self.assertEqual(len(self.ui.content.controls), 5)
        selected_bar = self.ui.content.controls[1]
        self.assertEqual(selected_bar.controls[1].content, "Прослушать наложение")
        self.ui.page.run_task.reset_mock()
        selected_bar.controls[1].on_click(None)
        self.ui.page.run_task.assert_called_once()
        timeline_row = self.ui.content.controls[2].content
        self.assertIsInstance(timeline_row, ft.ListView)
        self.assertTrue(timeline_row.horizontal)
        self.assertIsNone(timeline_row.expand)
        self.assertEqual(timeline_row.height, 272.0)
        timeline_stack = timeline_row.controls[0]
        self.assertEqual(timeline_stack.height, timeline_row.height)
        self.assertEqual(timeline_stack.controls[0].bgcolor, "#090C10")
        toolbar = self.ui.content.controls[0]
        self.assertEqual(len(toolbar.controls), 2)
        self.assertEqual(toolbar.controls[1].controls[-1].content, "Вместить")
        draggable = [
            control
            for control in timeline_stack.controls
            if isinstance(control, ft.Container)
            and isinstance(control.content, ft.GestureDetector)
            and control.content.on_horizontal_drag_update is not None
        ]
        self.assertEqual(len(draggable), 1)
        gesture = draggable[0].content
        gesture.on_horizontal_drag_start(None)
        gesture.on_horizontal_drag_update(SimpleNamespace(primary_delta=-30.0))
        gesture.on_horizontal_drag_end(None)
        saved = self.ui.service.set_timeline.transition(
            playlist_id, self.ids[0], self.ids[1]
        )
        self.assertIsNotNone(saved)
        self.assertLess(saved.outgoing_cue_ms, 45_000)

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
