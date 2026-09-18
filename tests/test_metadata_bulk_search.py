"""Регрессия массового поиска метаданных: guard-условия и логика batch-задачи.

С переходом на общий пул (трек, провайдер)-пар (run_many_unordered) поиск
реально выполняется параллельно в потоках — поэтому здесь используется
настоящий BackgroundWorkers, а не синхронная заглушка.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.services.tasks import TaskKind, TaskManager
from djmaker.services.workers import BackgroundWorkers
from djmaker.settings import AppSettings
from djmaker.ui.task_progress_controls import TaskProgressController


def _track(track_id: int, title: str = "Song", artist: str = "Artist") -> TrackRecord:
    return TrackRecord(
        id=track_id,
        path=Path(f"/music/{title}.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash=str(track_id),
        metadata=AudioMetadata(title=title, artist=artist),
        technical=AudioTechnicalInfo(),
    )


class MetadataBulkSearchTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.tasks = TaskManager()
        self.app.settings = AppSettings(
            metadata_providers=("musicbrainz",), metadata_auto_apply_threshold=90
        )
        self.app.service = Mock()
        self.app.service.database = Mock()
        self.app.player = Mock()
        self.app.player.release_if_current = AsyncMock()
        self.app._notify = Mock()
        self.app.navigation = SimpleNamespace(selected_index=3)
        self.app.page = Mock()
        self.app.show_library = Mock()
        self.app.show_plugins = Mock()
        self.app.task_count_text = Mock()
        self.app.busy = Mock()
        for name in (
            "_playlist_export_contexts",
            "_audio_task_contexts",
            "_scan_task_paths",
            "_drop_task_paths",
            "_artwork_task_contexts",
            "_waveform_task_contexts",
            "_metadata_bulk_task_contexts",
        ):
            setattr(self.app, name, {})

        # Настоящий пул: run_many_unordered должен реально разбирать очередь
        # пар (трек, провайдер) несколькими воркерами, а не притворяться.
        self.app.workers = BackgroundWorkers(max_workers=4)
        self.addCleanup(self.app.workers.close)
        self.controller = TaskProgressController(self.app)


class StartMetadataBulkSearchTests(MetadataBulkSearchTestCase):
    def test_guards_against_empty_tracks(self) -> None:
        self.controller.start_metadata_bulk_search([])

        self.app._notify.assert_called_once()
        self.assertIn("Нет треков", self.app._notify.call_args[0][0])
        self.assertEqual(self.app.tasks.active_count(), 0)

    def test_guards_against_no_providers(self) -> None:
        self.app.settings = AppSettings(metadata_providers=())

        self.controller.start_metadata_bulk_search([_track(1)])

        self.app._notify.assert_called_once()
        self.assertIn("источник", self.app._notify.call_args[0][0])
        self.assertEqual(self.app.tasks.active_count(), 0)

    def test_guards_against_concurrent_task(self) -> None:
        self.app.tasks.create(kind=TaskKind.METADATA_BULK_SEARCH, title="Уже идёт")

        self.controller.start_metadata_bulk_search([_track(1)])

        self.app._notify.assert_called_once()
        self.assertIn("уже выполняется", self.app._notify.call_args[0][0])

    def test_creates_task_and_schedules_run(self) -> None:
        self.controller.start_metadata_bulk_search([_track(1), _track(2)])

        self.assertEqual(self.app.tasks.active_count(), 1)
        task_id = next(iter(self.app._metadata_bulk_task_contexts))
        self.assertEqual(
            {1, 2}, self.app._metadata_bulk_task_contexts[task_id].pending_ids
        )
        self.app.page.run_task.assert_called_once_with(
            self.controller.run_metadata_bulk_search, task_id
        )


class RunMetadataBulkSearchTests(MetadataBulkSearchTestCase):
    def _start(self, tracks: list[TrackRecord]) -> str:
        self.app.service.database.get_track = Mock(
            side_effect=lambda tid: next((t for t in tracks if t.id == tid), None)
        )
        self.controller.start_metadata_bulk_search(tracks)
        return next(iter(self.app._metadata_bulk_task_contexts))

    async def test_high_confidence_match_is_auto_applied(self) -> None:
        track = _track(1, title="Shatter and Spin", artist="DataFunk")
        candidate = MetadataCandidate(
            provider_id="musicbrainz",
            external_id="x",
            title="Shatter and Spin",
            artist="DataFunk",
        )
        self.app.service.search_single_provider = Mock(return_value=[candidate])
        self.app.service.apply_candidate = Mock()
        task_id = self._start([track])

        await self.controller.run_metadata_bulk_search(task_id)

        self.app.player.release_if_current.assert_awaited_once_with(1)
        self.app.service.apply_candidate.assert_called_once_with(1, candidate)
        self.app.service.database.set_metadata_review.assert_not_called()
        self.assertEqual(self.app.tasks.get(task_id).snapshot().status.value, "completed")
        self.assertNotIn(task_id, self.app._metadata_bulk_task_contexts)

    async def test_low_confidence_match_is_flagged_not_applied(self) -> None:
        track = _track(1, title="Shatter and Spin", artist="DataFunk")
        candidate = MetadataCandidate(
            provider_id="musicbrainz",
            external_id="x",
            title="Totally Unrelated",
            artist="Someone Else",
        )
        self.app.service.search_single_provider = Mock(return_value=[candidate])
        self.app.service.apply_candidate = Mock()
        task_id = self._start([track])

        await self.controller.run_metadata_bulk_search(task_id)

        self.app.service.apply_candidate.assert_not_called()
        self.app.service.database.set_metadata_review.assert_called_once()
        kwargs = self.app.service.database.set_metadata_review.call_args
        self.assertEqual(kwargs[0][0], 1)
        self.assertEqual(kwargs[1]["reason"], "low_confidence")
        self.assertIsNotNone(kwargs[1]["score"])

    async def test_no_candidates_is_flagged_as_not_found(self) -> None:
        track = _track(1)
        self.app.service.search_single_provider = Mock(return_value=[])
        task_id = self._start([track])

        await self.controller.run_metadata_bulk_search(task_id)

        self.app.service.database.set_metadata_review.assert_called_once_with(
            1, reason="not_found", score=None
        )

    async def test_provider_failing_for_one_track_flags_search_failed(self) -> None:
        tracks = [_track(1), _track(2, title="Other", artist="Other")]
        good_candidate = MetadataCandidate(
            provider_id="musicbrainz", external_id="x", title="Other", artist="Other"
        )

        def search(track_id, provider_id, limit):
            if track_id == 1:
                raise MetadataProviderError("boom")
            return [good_candidate]

        self.app.service.search_single_provider = Mock(side_effect=search)
        self.app.service.apply_candidate = Mock()
        task_id = self._start(tracks)

        await self.controller.run_metadata_bulk_search(task_id)

        self.app.service.apply_candidate.assert_called_once_with(2, good_candidate)
        self.app.service.database.set_metadata_review.assert_called_once_with(
            1, reason="search_failed", score=None
        )
        snapshot = self.app.tasks.get(task_id).snapshot()
        self.assertEqual(snapshot.status.value, "completed")
        self.assertEqual(snapshot.failed, 1)
        self.assertEqual(snapshot.succeeded, 1)

    async def test_multiple_providers_are_merged_with_settings_priority(self) -> None:
        self.app.settings = AppSettings(
            metadata_providers=("spotify", "musicbrainz"), metadata_auto_apply_threshold=90
        )
        track = _track(1, title="Song", artist="Artist")

        def search(track_id, provider_id, limit):
            if provider_id == "spotify":
                return [
                    MetadataCandidate(
                        provider_id="spotify",
                        external_id="1",
                        title="Song",
                        artist="Artist",
                        album="Spotify Album",
                    )
                ]
            return [
                MetadataCandidate(
                    provider_id="musicbrainz",
                    external_id="2",
                    title="Song",
                    artist="Artist",
                    album="MB Album",
                    genre="house",
                )
            ]

        self.app.service.search_single_provider = Mock(side_effect=search)
        self.app.service.apply_candidate = Mock()
        task_id = self._start([track])

        await self.controller.run_metadata_bulk_search(task_id)

        applied = self.app.service.apply_candidate.call_args[0][1]
        self.assertEqual(applied.album, "Spotify Album")
        self.assertEqual(applied.genre, "house")

    async def test_one_provider_failing_for_all_others_succeeding_still_merges(self) -> None:
        self.app.settings = AppSettings(
            metadata_providers=("spotify", "musicbrainz"), metadata_auto_apply_threshold=90
        )
        track = _track(1, title="Song", artist="Artist")

        def search(track_id, provider_id, limit):
            if provider_id == "spotify":
                raise MetadataProviderError("down")
            return [
                MetadataCandidate(
                    provider_id="musicbrainz", external_id="2", title="Song", artist="Artist"
                )
            ]

        self.app.service.search_single_provider = Mock(side_effect=search)
        self.app.service.apply_candidate = Mock()
        task_id = self._start([track])

        await self.controller.run_metadata_bulk_search(task_id)

        self.app.service.apply_candidate.assert_called_once()
        self.app.service.database.set_metadata_review.assert_not_called()

    async def test_cancel_requested_before_start_ends_task_cancelled(self) -> None:
        task_id = self._start([_track(1), _track(2)])
        self.app.service.search_single_provider = Mock(return_value=[])
        self.app.tasks.get(task_id).request_cancel()

        await self.controller.run_metadata_bulk_search(task_id)

        self.assertEqual(self.app.tasks.get(task_id).snapshot().status.value, "cancelled")
        self.assertNotIn(task_id, self.app._metadata_bulk_task_contexts)

    async def test_pause_requested_before_start_keeps_context_for_resume(self) -> None:
        task_id = self._start([_track(1)])
        self.app.service.search_single_provider = Mock(return_value=[])
        self.app.tasks.get(task_id).request_pause()

        await self.controller.run_metadata_bulk_search(task_id)

        self.assertEqual(self.app.tasks.get(task_id).snapshot().status.value, "paused")
        self.assertIn(task_id, self.app._metadata_bulk_task_contexts)

    async def test_providers_for_different_tracks_run_concurrently(self) -> None:
        import time

        self.app.settings = AppSettings(
            metadata_providers=("musicbrainz",), metadata_auto_apply_threshold=90
        )
        tracks = [_track(i, title=f"Song {i}", artist="Artist") for i in range(1, 5)]

        def slow_search(track_id, provider_id, limit):
            time.sleep(0.1)
            return []

        self.app.service.search_single_provider = Mock(side_effect=slow_search)
        task_id = self._start(tracks)

        started = time.monotonic()
        await self.controller.run_metadata_bulk_search(task_id)
        elapsed = time.monotonic() - started

        # Последовательно 4 трека по 0.1с заняли бы ~0.4с; с пулом воркеров —
        # заметно быстрее.
        self.assertLess(elapsed, 0.3)


if __name__ == "__main__":
    unittest.main()
