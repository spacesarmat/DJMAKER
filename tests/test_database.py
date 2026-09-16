from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    WaveformAnalysis,
)
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

    def test_search_finds_genre_format_and_camelot(self) -> None:
        path = self.root / "club.flac"
        path.write_bytes(b"audio")
        stat = path.stat()
        self.db.upsert_track(
            path=path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=path.suffix,
            file_hash="club-hash",
            metadata=AudioMetadata(
                title="Night Drive",
                artist="DJ Test",
                album_artist="Various",
                genre="Tech House",
                musical_key="A minor",
            ),
            technical=AudioTechnicalInfo(duration=123.0),
            scan_token="token",
        )
        track = self.db.list_tracks()[0]
        self.db.save_audio_analysis(
            track.id,
            AudioAnalysis(
                bpm=128.0,
                musical_key="A",
                scale="minor",
                camelot="8A",
            ),
        )

        self.assertEqual(1, len(self.db.list_tracks(search="Tech House")))
        self.assertEqual(1, len(self.db.list_tracks(search="flac")))
        self.assertEqual(1, len(self.db.list_tracks(search="8A")))
        self.assertEqual(1, len(self.db.list_tracks(search="Various")))

    def test_audio_analysis_is_saved_separately_from_tags(self) -> None:
        self._insert("one.mp3", "abc")
        track = self.db.list_tracks()[0]

        self.db.save_audio_analysis(
            track.id,
            AudioAnalysis(
                bpm=128.0,
                bpm_confidence=4.0,
                musical_key="A",
                scale="minor",
                key_strength=0.8,
                camelot="8A",
            ),
        )

        updated = self.db.get_track(track.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIsNone(updated.metadata.bpm)
        self.assertIsNotNone(updated.analysis)
        assert updated.analysis is not None
        self.assertEqual(128.0, updated.analysis.bpm)
        self.assertEqual("8A", updated.analysis.camelot)
        self.assertTrue(updated.analysis.analyzed_at)

    def test_analysis_queue_excludes_completed_tracks(self) -> None:
        self._insert("one.mp3", "abc")
        self._insert("two.mp3", "def")
        first = self.db.list_tracks()[0]
        self.db.save_audio_analysis(
            first.id,
            AudioAnalysis(bpm=120.0, musical_key="C", scale="major", camelot="8B"),
        )

        pending = self.db.list_tracks_for_analysis()
        self.assertEqual(1, len(pending))
        self.assertNotEqual(first.id, pending[0].id)
        self.assertEqual(2, len(self.db.list_tracks_for_analysis(force=True)))

    def test_analysis_counts(self) -> None:
        self._insert("one.mp3", "abc")
        track = self.db.list_tracks()[0]
        self.assertEqual((1, 0), self.db.analysis_counts())
        self.db.save_audio_analysis(track.id, AudioAnalysis(bpm=120.0))
        self.assertEqual((1, 1), self.db.analysis_counts())

    def test_changed_file_hash_invalidates_previous_analysis(self) -> None:
        self._insert("one.mp3", "abc")
        track = self.db.list_tracks()[0]
        self.db.save_audio_analysis(
            track.id,
            AudioAnalysis(bpm=120.0, musical_key="C", scale="major", camelot="8B"),
        )
        stat = track.path.stat()

        self.db.upsert_track(
            path=track.path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=track.path.suffix,
            file_hash="changed",
            metadata=track.metadata,
            technical=track.technical,
            scan_token="next",
        )

        updated = self.db.get_track(track.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIsNone(updated.analysis)


    def test_waveform_is_saved_and_loaded_separately(self) -> None:
        self._insert("wave.mp3", "wave-hash")
        track = self.db.list_tracks()[0]

        self.db.save_waveform_analysis(
            track.id,
            WaveformAnalysis(peaks=(0.0, 0.25, 0.5, 1.0)),
        )

        updated = self.db.get_track(track.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIsNotNone(updated.waveform)
        assert updated.waveform is not None
        self.assertEqual((0.0, 0.25, 0.5, 1.0), updated.waveform.peaks)
        self.assertTrue(updated.waveform.analyzed_at)
        self.assertEqual((1, 1), self.db.waveform_counts())

    def test_changed_file_hash_invalidates_previous_waveform(self) -> None:
        self._insert("wave.mp3", "wave-hash")
        track = self.db.list_tracks()[0]
        self.db.save_waveform_analysis(
            track.id,
            WaveformAnalysis(peaks=(0.2, 0.8)),
        )
        stat = track.path.stat()

        self.db.upsert_track(
            path=track.path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=track.path.suffix,
            file_hash="wave-changed",
            metadata=track.metadata,
            technical=track.technical,
            scan_token="next",
        )

        updated = self.db.get_track(track.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIsNone(updated.waveform)

    def test_reset_clears_library_and_keeps_schema_ready(self) -> None:
        self._insert("one.mp3", "abc")
        track = self.db.list_tracks()[0]
        self.db.save_audio_analysis(track.id, AudioAnalysis(bpm=128.0))
        self.db.save_waveform_analysis(
            track.id,
            WaveformAnalysis(peaks=(0.25, 0.75)),
        )
        self.db.add_root(self.root)

        self.db.reset()

        self.assertEqual([], self.db.list_tracks())
        self.assertEqual([], self.db.list_roots())
        self.assertEqual((0, 0), self.db.analysis_counts())
        self.assertEqual((0, 0), self.db.waveform_counts())
        with self.db.connection() as conn:
            self.assertEqual(
                5,
                conn.execute("PRAGMA user_version").fetchone()[0],
            )

        self._insert("after-reset.mp3", "new-hash")
        recreated = self.db.list_tracks()[0]
        self.assertEqual(1, recreated.id)

    def test_embedded_artwork_path_is_stored_separately_from_online_url(self) -> None:
        path = self.root / "cover.mp3"
        path.write_bytes(b"audio")
        cover = Path(self.temp.name) / "artwork" / "cover.jpg"
        stat = path.stat()
        self.db.upsert_track(
            path=path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=path.suffix,
            file_hash="cover-hash",
            metadata=AudioMetadata(title="Cover"),
            technical=AudioTechnicalInfo(duration=1.0),
            scan_token="token",
            embedded_artwork_path=cover,
            embedded_artwork_checked=True,
        )
        track = self.db.list_tracks()[0]
        self.db.set_artwork_url(track.id, "https://example.test/cover.jpg")

        updated = self.db.get_track(track.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(cover, updated.embedded_artwork_path)
        self.assertTrue(updated.embedded_artwork_checked)
        self.assertEqual("https://example.test/cover.jpg", updated.artwork_url)


class LibraryDatabaseMigrationTests(unittest.TestCase):
    def test_schema_v1_is_migrated_to_audio_analysis_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "library.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")
                conn.execute("PRAGMA user_version=1")
                conn.commit()

            database = LibraryDatabase(path)
            database.initialize()

            with database.connection() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
                }

            self.assertEqual(5, version)
            self.assertIn("analysis_bpm", columns)
            self.assertIn("analysis_key", columns)
            self.assertIn("analysis_camelot", columns)
            self.assertIn("analyzed_at", columns)
            self.assertIn("embedded_artwork_path", columns)
            self.assertIn("embedded_artwork_checked", columns)
            self.assertIn("waveform_peaks", columns)
            self.assertIn("waveform_analyzed_at", columns)


    def test_schema_v2_is_migrated_to_embedded_artwork_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "library.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")
                conn.execute("PRAGMA user_version=2")
                conn.commit()

            database = LibraryDatabase(path)
            database.initialize()

            with database.connection() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
                }

            self.assertEqual(5, version)
            self.assertIn("embedded_artwork_path", columns)
            self.assertIn("embedded_artwork_checked", columns)
            self.assertIn("waveform_peaks", columns)
            self.assertIn("waveform_analyzed_at", columns)

    def test_schema_v3_is_migrated_to_waveform_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "library.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")
                conn.execute("PRAGMA user_version=3")
                conn.commit()

            database = LibraryDatabase(path)
            database.initialize()

            with database.connection() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
                }

            self.assertEqual(5, version)
            self.assertIn("waveform_peaks", columns)
            self.assertIn("waveform_analyzed_at", columns)


if __name__ == "__main__":
    unittest.main()
