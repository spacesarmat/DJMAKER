"""Реальное копирование и M3U8 в временных каталогах без GUI."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from djmaker.services.playlist_export import (
    ExportTrack,
    PlaylistExportError,
    PlaylistExportRequest,
    export_playlist,
)
from djmaker.services.tasks import ManagedTask, TaskCancelled, TaskKind, TaskPaused


class PlaylistExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "exports"
        self.destination.mkdir()
        tracks = []
        for index in range(2):
            folder = self.root / str(index)
            folder.mkdir()
            path = folder / "Одинаковый трек.flac"
            path.write_bytes(bytes([index + 1]) * (1024 * 1024 + 25))
            tracks.append(ExportTrack(path, f"Исполнитель - Трек {index}", 90.5))
        self.request = PlaylistExportRequest(
            "Сет", tuple(tracks), self.destination, True
        )
        self.task = ManagedTask(kind=TaskKind.PLAYLIST_EXPORT, title="Export")

    def test_copy_export_has_relative_paths_order_and_identical_bytes(self) -> None:
        output = export_playlist(self.request, task=self.task)
        text = output.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#EXTM3U\n"))
        lines = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0], lines[1])
        self.assertIn("Исполнитель - Трек 0", text)
        for reference, source in zip(lines, self.request.tracks):
            self.assertTrue(reference.startswith("Music/"))
            self.assertEqual(
                (output.parent / reference).read_bytes(), source.path.read_bytes()
            )
        moved = self.root / "moved"
        shutil.copytree(output.parent, moved)
        for reference in lines:
            self.assertTrue((moved / reference).is_file())

    def test_reference_only_export_uses_absolute_sources(self) -> None:
        output = export_playlist(
            replace(self.request, copy_files=False), task=self.task
        )
        paths = [
            line
            for line in output.read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        ]
        self.assertEqual(paths, [str(t.path.resolve()) for t in self.request.tracks])
        self.assertFalse((output.parent / "Music").exists())

    def test_existing_export_is_never_overwritten(self) -> None:
        first = export_playlist(self.request, task=self.task)
        first.write_text("Do not overwrite", encoding="utf-8")
        second = export_playlist(self.request, task=self.task)
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(first.read_text(encoding="utf-8"), "Do not overwrite")

    def test_missing_source_and_empty_playlist_produce_no_output(self) -> None:
        self.request.tracks[1].path.unlink()
        with self.assertRaisesRegex(PlaylistExportError, "недоступен"):
            export_playlist(self.request, task=self.task)
        with self.assertRaises(PlaylistExportError):
            export_playlist(replace(self.request, tracks=()), task=self.task)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cancel_during_copy_removes_only_export(self) -> None:
        sentinel = self.destination / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        calls = 0

        def checkpoint() -> None:
            nonlocal calls
            calls += 1
            if calls == 6:
                raise TaskCancelled()

        with (
            patch.object(self.task, "checkpoint", side_effect=checkpoint),
            self.assertRaises(TaskCancelled),
        ):
            export_playlist(self.request, task=self.task)
        self.assertEqual(list(self.destination.iterdir()), [sentinel])
        self.assertTrue(all(t.path.exists() for t in self.request.tracks))

    def test_pause_then_restart_creates_complete_export(self) -> None:
        def progress(completed: int, detail: str) -> None:
            if completed == 1:
                self.task.request_pause()

        with self.assertRaises(TaskPaused):
            export_playlist(self.request, task=self.task, progress=progress)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.task.mark_paused()
        self.task.resume()
        self.assertTrue(export_playlist(self.request, task=self.task).is_file())

    def test_write_failure_cleans_partial_export_and_preserves_originals(self) -> None:
        with (
            patch.object(Path, "write_text", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            export_playlist(self.request, task=self.task)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(self.request.tracks[0].path.read_bytes()[:4], b"\x01" * 4)

    def test_unsafe_names_and_newline_titles_are_sanitized(self) -> None:
        request = replace(
            self.request,
            name="../CON: test",
            tracks=(
                replace(self.request.tracks[0], title="a\n#bad\rb", duration=None),
            ),
        )
        output = export_playlist(request, task=self.task)
        self.assertEqual(output.parent.parent, self.destination)
        text = output.read_text(encoding="utf-8")
        self.assertIn("#EXTINF:-1,a #bad b\n", text)
        self.assertNotIn("\n#bad", text)

    def test_source_change_during_copy_fails_and_cleans(self) -> None:
        original_open = Path.open
        source = self.request.tracks[0].path

        class Reader:
            def __enter__(inner):
                inner.handle = original_open(source, "rb")
                return inner

            def read(inner, size):
                chunk = inner.handle.read(size)
                if chunk:
                    with original_open(source, "ab") as writer:
                        writer.write(b"changed")
                    # Изменяем один раз, иначе чтение могло бы бесконечно расти.
                    inner.read = inner.handle.read
                return chunk

            def __exit__(inner, *args):
                inner.handle.close()

        def open_path(path, *args, **kwargs):
            return (
                Reader()
                if path == source and args == ("rb",)
                else original_open(path, *args, **kwargs)
            )

        with (
            patch.object(Path, "open", open_path),
            self.assertRaisesRegex(PlaylistExportError, "изменился"),
        ):
            export_playlist(self.request, task=self.task)
        self.assertEqual(list(self.destination.iterdir()), [])
