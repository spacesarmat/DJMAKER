from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo
from djmaker.infrastructure.database import LibraryDatabase


class LibraryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = LibraryDatabase(Path(self.temp.name) / "library.sqlite3")
        self.db.initialize()
        self.root = Path(self.temp.name) / "music"
        self.root.mkdir()

    def _insert(self, name: str, file_hash: str) -> None:
        path = self.root / name
        path.write_bytes(b"audio")
        stat = path.stat()
        self.db.upsert_track(
            path=path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=path.suffix,
            file_hash=file_hash,
            metadata=AudioMetadata(title=name, artist="Artist"),
            technical=AudioTechnicalInfo(duration=123.0),
            scan_token="token",
        )

    def test_exact_duplicates_group_by_full_hash(self) -> None:
        self._insert("one.mp3", "abc")
        self._insert("two.mp3", "abc")
        self._insert("three.mp3", "def")

        groups = self.db.find_exact_duplicates()

        self.assertEqual(1, len(groups))
        self.assertEqual("abc", groups[0].file_hash)
        self.assertEqual(2, len(groups[0].tracks))

    def test_search_finds_artist(self) -> None:
        self._insert("one.mp3", "abc")
        tracks = self.db.list_tracks(search="Artist")
        self.assertEqual(1, len(tracks))


if __name__ == "__main__":
    unittest.main()
