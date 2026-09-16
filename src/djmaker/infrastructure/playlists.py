"""Плейлисты SQLite: порядок хранится отдельно от сортировки медиатеки."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from djmaker.domain.models import TrackRecord

if TYPE_CHECKING:
    from djmaker.infrastructure.database import LibraryDatabase


class PlaylistError(RuntimeError):
    """Понятная пользователю ошибка работы с плейлистом."""


@dataclass(frozen=True, slots=True)
class Playlist:
    id: int
    name: str
    track_count: int
    duration: float
    unknown_duration_count: int


def create_playlist_schema(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы в текущей транзакции миграции."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            PRIMARY KEY (playlist_id, track_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS playlist_order "
        "ON playlist_tracks(playlist_id, position)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS playlist_track_lookup "
        "ON playlist_tracks(track_id)"
    )


class PlaylistRepository:
    def __init__(self, database: LibraryDatabase) -> None:
        self.database = database

    @staticmethod
    def _name(name: str) -> str:
        name = name.strip()
        if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
            raise PlaylistError(
                "Название должно содержать от 1 до 120 символов без переносов строк"
            )
        return name

    @staticmethod
    def _require(conn: sqlite3.Connection, playlist_id: int) -> None:
        if (
            conn.execute(
                "SELECT 1 FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            is None
        ):
            raise PlaylistError("Плейлист больше не существует")

    def list(self) -> list[Playlist]:
        try:
            with self.database.connection() as conn:
                rows = conn.execute("""
                    SELECT p.id, p.name, COUNT(t.id) AS track_count,
                           COALESCE(SUM(CASE WHEN t.duration > 0 THEN t.duration ELSE 0 END), 0) AS duration,
                           SUM(CASE WHEN t.id IS NOT NULL AND
                               (t.duration IS NULL OR t.duration <= 0) THEN 1 ELSE 0 END) AS unknown_count
                    FROM playlists p
                    LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id
                    LEFT JOIN tracks t ON t.id=pt.track_id
                    GROUP BY p.id ORDER BY p.name_key, p.id
                """).fetchall()
            return [
                Playlist(
                    r["id"],
                    r["name"],
                    r["track_count"],
                    r["duration"],
                    r["unknown_count"],
                )
                for r in rows
            ]
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось прочитать плейлисты: {exc}") from exc

    def create(self, name: str, *, track_id: int | None = None) -> int:
        name = self._name(name)
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if (
                    track_id is not None
                    and conn.execute(
                        "SELECT 1 FROM tracks WHERE id=?", (track_id,)
                    ).fetchone()
                    is None
                ):
                    raise PlaylistError("Трек больше не существует в медиатеке")
                cursor = conn.execute(
                    "INSERT INTO playlists(name, name_key) VALUES (?, ?)",
                    (name, name.casefold()),
                )
                ident = int(cursor.lastrowid)
                if track_id is not None:
                    conn.execute(
                        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, 0)",
                        (ident, track_id),
                    )
                conn.commit()
                return ident
        except sqlite3.IntegrityError as exc:
            raise PlaylistError("Плейлист с таким названием уже существует") from exc
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось создать плейлист: {exc}") from exc

    def rename(self, playlist_id: int, name: str) -> None:
        name = self._name(name)
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require(conn, playlist_id)
                conn.execute(
                    "UPDATE playlists SET name=?, name_key=? WHERE id=?",
                    (name, name.casefold(), playlist_id),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise PlaylistError("Плейлист с таким названием уже существует") from exc
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось переименовать плейлист: {exc}") from exc

    def delete(self, playlist_id: int) -> None:
        try:
            with self.database.connection() as conn:
                conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
                conn.commit()
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось удалить плейлист: {exc}") from exc

    def tracks(self, playlist_id: int) -> list[TrackRecord]:
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN")
                self._require(conn, playlist_id)
                rows = conn.execute(
                    """
                    SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id
                    WHERE pt.playlist_id=? ORDER BY pt.position, pt.track_id
                """,
                    (playlist_id,),
                ).fetchall()
            return [self.database._row_to_track(row) for row in rows]
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось прочитать треки плейлиста: {exc}") from exc

    def add(self, playlist_id: int, track_id: int) -> bool:
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require(conn, playlist_id)
                if (
                    conn.execute(
                        "SELECT 1 FROM tracks WHERE id=?", (track_id,)
                    ).fetchone()
                    is None
                ):
                    raise PlaylistError("Трек больше не существует в медиатеке")
                cursor = conn.execute(
                    """
                    INSERT INTO playlist_tracks(playlist_id, track_id, position)
                    SELECT ?, ?, COALESCE(MAX(position), -1) + 1
                    FROM playlist_tracks WHERE playlist_id=?
                    ON CONFLICT(playlist_id, track_id) DO NOTHING
                """,
                    (playlist_id, track_id, playlist_id),
                )
                conn.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось добавить трек: {exc}") from exc

    def remove(self, playlist_id: int, track_id: int) -> None:
        try:
            with self.database.connection() as conn:
                conn.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                    (playlist_id, track_id),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось убрать трек: {exc}") from exc

    def move(self, playlist_id: int, track_id: int, delta: int) -> None:
        if delta not in (-1, 1):
            raise PlaylistError("Допустимо перемещение на одну позицию вверх или вниз")
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require(conn, playlist_id)
                ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position, track_id",
                        (playlist_id,),
                    )
                ]
                if track_id not in ids:
                    raise PlaylistError("Трек больше не находится в плейлисте")
                index = ids.index(track_id)
                target = index + delta
                if 0 <= target < len(ids):
                    ids[index], ids[target] = ids[target], ids[index]
                    conn.executemany(
                        "UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",
                        [
                            (position, playlist_id, ident)
                            for position, ident in enumerate(ids)
                        ],
                    )
                conn.commit()
        except sqlite3.Error as exc:
            raise PlaylistError(f"Не удалось изменить порядок: {exc}") from exc
