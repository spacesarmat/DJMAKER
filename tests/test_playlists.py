"""Плейлисты: миграция, порядок, каскады и сохранность музыкальных файлов."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.infrastructure.playlists import PlaylistError, PlaylistRepository


class PlaylistRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = LibraryDatabase(self.root / "library.sqlite3")
        self.db.initialize()
        self.repo = PlaylistRepository(self.db)
        self.ids = []
        for index in range(3):
            path = self.root / f"{index}.mp3"
            path.write_bytes(f"original {index}".encode())
            self.db.upsert_track(
                path=path,
                root=self.root,
                size=10,
                mtime_ns=1,
                extension=".mp3",
                file_hash=str(index),
                metadata=AudioMetadata(title=str(index)),
                technical=AudioTechnicalInfo(duration=60 if index < 2 else None),
                scan_token="scan",
            )
            self.ids.append(self.db.list_tracks(search=str(path))[0].id)

    def test_create_rename_unicode_duplicates_and_validation(self) -> None:
        ident = self.repo.create("  Вечер  ")
        self.assertEqual(self.repo.list()[0].name, "Вечер")
        with self.assertRaises(PlaylistError):
            self.repo.create("вечер")
        self.repo.rename(ident, "Ночь")
        self.assertEqual(self.repo.list()[0].name, "Ночь")
        for value in ("", "   ", "x" * 121, "a\nb"):
            with self.assertRaises(PlaylistError):
                self.repo.create(value)

    def test_append_deduplicate_and_summary(self) -> None:
        ident = self.repo.create("Set")
        for track_id in self.ids:
            self.assertTrue(self.repo.add(ident, track_id))
        self.assertFalse(self.repo.add(ident, self.ids[0]))
        self.assertEqual([t.id for t in self.repo.tracks(ident)], self.ids)
        playlist = self.repo.list()[0]
        self.assertEqual(
            (playlist.track_count, playlist.duration, playlist.unknown_duration_count),
            (3, 120, 1),
        )

    def test_batch_add_preserves_order_deduplicates_and_reports_skipped(self) -> None:
        ident = self.repo.create("Set")
        self.repo.add(ident, self.ids[1])

        added, skipped = self.repo.add_many(
            ident, [self.ids[2], self.ids[0], self.ids[2], self.ids[1]]
        )

        self.assertEqual((added, skipped), (2, 1))
        self.assertEqual(
            [track.id for track in self.repo.tracks(ident)],
            [self.ids[1], self.ids[2], self.ids[0]],
        )

    def test_batch_add_rolls_back_when_one_track_is_missing(self) -> None:
        ident = self.repo.create("Set")
        with self.assertRaises(PlaylistError):
            self.repo.add_many(ident, [self.ids[0], 999999, self.ids[1]])
        self.assertEqual(self.repo.tracks(ident), [])

    def test_create_with_tracks_is_atomic_and_preserves_selection_order(self) -> None:
        ident = self.repo.create_with_tracks(
            "Batch", [self.ids[2], self.ids[0], self.ids[2], self.ids[1]]
        )
        self.assertEqual(
            [track.id for track in self.repo.tracks(ident)],
            [self.ids[2], self.ids[0], self.ids[1]],
        )
        with self.assertRaises(PlaylistError):
            self.repo.create_with_tracks("Broken", [self.ids[0], 999999])
        self.assertEqual([item.name for item in self.repo.list()], ["Batch"])

    def test_manual_order_survives_reopen_and_removal_gaps(self) -> None:
        ident = self.repo.create("Set")
        for track_id in self.ids:
            self.repo.add(ident, track_id)
        self.repo.remove(ident, self.ids[1])
        self.repo.add(ident, self.ids[1])
        self.repo.move(ident, self.ids[1], -1)
        self.repo.move(ident, self.ids[0], -1)
        self.repo.move(ident, self.ids[2], 1)
        reopened = LibraryDatabase(self.db.path)
        reopened.initialize()
        self.assertEqual(
            [t.id for t in PlaylistRepository(reopened).tracks(ident)], self.ids
        )

    def test_delete_and_remove_preserve_library_and_originals(self) -> None:
        ident = self.repo.create("Set")
        self.repo.add(ident, self.ids[0])
        self.repo.remove(ident, self.ids[0])
        self.repo.add(ident, self.ids[1])
        self.repo.delete(ident)
        self.assertEqual(self.repo.list(), [])
        self.assertEqual(len(self.db.list_tracks()), 3)
        for index in range(3):
            self.assertEqual(
                (self.root / f"{index}.mp3").read_bytes(), f"original {index}".encode()
            )
        with self.db.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0], 0
            )

    def test_track_deletion_cascades_and_reset_clears_playlists(self) -> None:
        ident = self.repo.create("Set")
        self.repo.add(ident, self.ids[0])
        with self.db.connection() as conn:
            conn.execute("DELETE FROM tracks WHERE id=?", (self.ids[0],))
            conn.commit()
        self.assertEqual(self.repo.tracks(ident), [])
        self.db.reset()
        self.assertEqual(self.repo.list(), [])
        self.assertEqual(self.repo.create("After reset"), 1)
        self.assertTrue((self.root / "0.mp3").exists())

    def test_invalid_membership_does_not_mutate(self) -> None:
        ident = self.repo.create("Set")
        for args in ((ident, 999999), (999999, self.ids[0])):
            with self.assertRaises(PlaylistError):
                self.repo.add(*args)
        with self.assertRaises(PlaylistError):
            self.repo.move(ident, self.ids[0], 2)
        with self.assertRaises(PlaylistError):
            self.repo.tracks(999999)
        self.assertEqual(self.repo.tracks(ident), [])

    def test_track_path_and_tags_follow_library_updates(self) -> None:
        ident = self.repo.create("Set")
        self.repo.add(ident, self.ids[0])
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE tracks SET path=?, title='Новое название' WHERE id=?",
                (str(self.root / "moved.mp3"), self.ids[0]),
            )
            conn.commit()
        track = self.repo.tracks(ident)[0]
        self.assertEqual(track.path.name, "moved.mp3")
        self.assertEqual(track.metadata.title, "Новое название")

    def test_v4_migration_keeps_tracks_and_is_repeatable(self) -> None:
        old = LibraryDatabase(self.root / "old.sqlite3")
        with closing(sqlite3.connect(old.path)) as conn:
            old._create_schema(conn)
            conn.execute("PRAGMA user_version=4")
            conn.commit()
        old.upsert_track(
            path=self.root / "0.mp3",
            root=self.root,
            size=10,
            mtime_ns=1,
            extension=".mp3",
            file_hash="original",
            metadata=AudioMetadata(title="Сохранить"),
            technical=AudioTechnicalInfo(duration=120),
            scan_token="old",
        )
        old.initialize()
        old.initialize()
        self.assertEqual(old.list_tracks()[0].metadata.title, "Сохранить")
        repo = PlaylistRepository(old)
        ident = repo.create("После миграции")
        repo.add(ident, old.list_tracks()[0].id)
        self.assertEqual(len(repo.tracks(ident)), 1)
        with old.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_v4_migration_recovers_from_partially_created_playlist_schema(self) -> None:
        old = LibraryDatabase(self.root / "partial.sqlite3")
        with closing(sqlite3.connect(old.path)) as conn:
            old._create_schema(conn)
            conn.execute(
                "CREATE TABLE playlists ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE)"
            )
            conn.execute("PRAGMA user_version=4")
            conn.commit()

        old.initialize()

        repo = PlaylistRepository(old)
        ident = repo.create("Восстановлено")
        self.assertEqual(repo.tracks(ident), [])
        with old.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 9)

    def test_create_with_track_is_atomic(self) -> None:
        with self.assertRaises(PlaylistError):
            self.repo.create("Missing", track_id=999999)
        self.assertEqual(self.repo.list(), [])
        ident = self.repo.create("Set", track_id=self.ids[0])
        self.assertEqual([t.id for t in self.repo.tracks(ident)], [self.ids[0]])
