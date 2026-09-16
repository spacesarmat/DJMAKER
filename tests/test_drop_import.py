from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, InspectedAudio
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.services.artwork import ArtworkCache
from djmaker.services.drop_import import plan_drop_import
from djmaker.services.scanner import LibraryScanner


class _FakeTags:
    def inspect(self, path: Path) -> InspectedAudio:
        return InspectedAudio(
            path=path,
            metadata=AudioMetadata(title=path.stem),
            technical=AudioTechnicalInfo(duration=1.0),
        )


class DropImportPlanTests(unittest.TestCase):
    def test_plan_deduplicates_nested_items_and_filters_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Music"
            nested = root / "Nested"
            nested.mkdir(parents=True)
            track = nested / "track.FLAC"
            track.write_bytes(b"audio")
            ignored = Path(temp) / "notes.txt"
            ignored.write_text("not audio", encoding="utf-8")

            plan = plan_drop_import([root, nested, track, ignored, root])

            self.assertEqual((root.resolve(),), plan.directories)
            self.assertEqual((), plan.files)
            self.assertEqual((ignored.resolve(),), plan.ignored)

    def test_supported_single_file_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            track = Path(temp) / "track.MP3"
            track.write_bytes(b"audio")

            plan = plan_drop_import([track])

            self.assertEqual((), plan.directories)
            self.assertEqual((track.resolve(),), plan.files)
            self.assertEqual(0, plan.ignored_count)


class DropImportScannerTests(unittest.TestCase):
    def _scanner(self, temp: str) -> tuple[LibraryScanner, LibraryDatabase]:
        base = Path(temp)
        database = LibraryDatabase(base / "library.sqlite3")
        database.initialize()
        scanner = LibraryScanner(
            database,
            _FakeTags(),  # type: ignore[arg-type]
            ArtworkCache(base / "artwork"),
        )
        return scanner, database

    def test_folder_scan_counts_unsupported_files_as_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scanner, _ = self._scanner(temp)
            root = Path(temp) / "Music"
            root.mkdir()
            (root / "track.mp3").write_bytes(b"audio")
            (root / "cover.jpg").write_bytes(b"image")

            stats = scanner.scan(root)

            self.assertEqual(1, stats.discovered)
            self.assertEqual(1, stats.updated)
            self.assertEqual(1, stats.ignored)

    def test_single_file_import_does_not_register_parent_as_library_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scanner, database = self._scanner(temp)
            source = Path(temp) / "Loose"
            source.mkdir()
            track = source / "single.flac"
            track.write_bytes(b"audio")

            stats = scanner.scan_paths([track])

            self.assertEqual(1, stats.discovered)
            self.assertEqual(1, stats.updated)
            self.assertEqual([], database.list_roots())
            tracks = database.list_tracks()
            self.assertEqual(1, len(tracks))
            self.assertEqual(track.resolve(), tracks[0].path)

    def test_single_file_import_preserves_existing_library_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scanner, database = self._scanner(temp)
            root = Path(temp) / "Music"
            album = root / "Album"
            album.mkdir(parents=True)
            track = album / "track.wav"
            track.write_bytes(b"first")
            scanner.scan(root)

            track.write_bytes(b"changed")
            scanner.scan_paths([track])

            stored = database.list_tracks()[0]
            self.assertEqual(root.resolve(), stored.root_path)


if __name__ == "__main__":
    unittest.main()
