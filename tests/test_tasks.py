from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.services.audio_analysis import EssentiaAudioAnalyzer
from djmaker.services.scanner import LibraryScanner
from djmaker.services.tasks import (
    TaskCancelled,
    TaskKind,
    TaskManager,
    TaskPaused,
    TaskStatus,
)


class TaskManagerTests(unittest.TestCase):
    def test_pause_resume_and_cancel_transitions(self) -> None:
        manager = TaskManager()
        task = manager.create(
            kind=TaskKind.AUDIO_ANALYSIS,
            title="BPM / Key",
            total=10,
        )

        task.advance(success=True, detail="one.mp3")
        self.assertEqual(TaskStatus.RUNNING, task.snapshot().status)
        self.assertEqual(1, task.snapshot().completed)

        self.assertTrue(task.request_pause())
        self.assertEqual(TaskStatus.STOPPING, task.snapshot().status)
        with self.assertRaises(TaskPaused):
            task.checkpoint()

        task.mark_paused()
        self.assertEqual(TaskStatus.PAUSED, task.snapshot().status)
        self.assertTrue(task.resume())
        self.assertEqual(TaskStatus.RUNNING, task.snapshot().status)
        task.checkpoint()

        self.assertTrue(task.request_cancel())
        self.assertEqual(TaskStatus.CANCELLING, task.snapshot().status)
        with self.assertRaises(TaskCancelled):
            task.checkpoint()
        task.mark_cancelled()
        self.assertEqual(TaskStatus.CANCELLED, task.snapshot().status)
        self.assertEqual(0, manager.active_count())

    def test_cancel_paused_task_finishes_immediately(self) -> None:
        task = TaskManager().create(
            kind=TaskKind.LIBRARY_SCAN,
            title="Scan",
        )
        task.request_pause()
        task.mark_paused()

        self.assertTrue(task.request_cancel())
        snapshot = task.snapshot()
        self.assertEqual(TaskStatus.CANCELLED, snapshot.status)
        self.assertIsNotNone(snapshot.finished_at)

    def test_active_for_kind_includes_paused_job(self) -> None:
        manager = TaskManager()
        task = manager.create(
            kind=TaskKind.ARTWORK_INDEX,
            title="Artwork",
        )
        task.request_pause()
        task.mark_paused()

        active = manager.active_for_kind(TaskKind.ARTWORK_INDEX)
        self.assertIsNotNone(active)
        self.assertEqual(task.id, active.id if active else "")


class CooperativeCancellationTests(unittest.TestCase):
    def test_scanner_hash_honors_stop_checkpoint(self) -> None:
        task = TaskManager().create(
            kind=TaskKind.LIBRARY_SCAN,
            title="Scan",
        )
        task.request_pause()

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "track.bin"
            source.write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaises(TaskPaused):
                LibraryScanner.sha256(source, task=task)

    def test_audio_analyzer_checks_task_before_starting_processes(self) -> None:
        class RuntimeShouldNotBeUsed:
            def ffmpeg_path(self) -> Path:
                raise AssertionError("runtime must not be probed after stop")

            def essentia_analyzer_path(self) -> Path:
                raise AssertionError("runtime must not be probed after stop")

        task = TaskManager().create(
            kind=TaskKind.AUDIO_ANALYSIS,
            title="BPM / Key",
        )
        task.request_pause()
        analyzer = EssentiaAudioAnalyzer(RuntimeShouldNotBeUsed())  # type: ignore[arg-type]

        with self.assertRaises(TaskPaused):
            analyzer.analyze(Path("missing.mp3"), task=task)


if __name__ == "__main__":
    unittest.main()
