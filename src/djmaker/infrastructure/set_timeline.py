"""SQLite-хранилище быстрых точек и настроек переходов плейлиста."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from djmaker.infrastructure.database import LibraryDatabase


class SetTimelineRepositoryError(RuntimeError):
    """Понятная пользователю ошибка сохранения монтажной сетки."""


@dataclass(frozen=True, slots=True)
class CuePoint:
    track_id: int
    slot: int
    position_ms: int


@dataclass(frozen=True, slots=True)
class SavedTransition:
    playlist_id: int
    outgoing_track_id: int
    incoming_track_id: int
    outgoing_cue_ms: int
    incoming_cue_ms: int
    bars_per_square: int
    square_count: int


def create_set_timeline_schema(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы внутри текущей транзакции миграции."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS track_cue_points (
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 4),
            position_ms INTEGER NOT NULL CHECK(position_ms >= 0),
            PRIMARY KEY(track_id, slot)
        );

        CREATE TABLE IF NOT EXISTS playlist_transitions (
            playlist_id INTEGER NOT NULL
                REFERENCES playlists(id) ON DELETE CASCADE,
            outgoing_track_id INTEGER NOT NULL
                REFERENCES tracks(id) ON DELETE CASCADE,
            incoming_track_id INTEGER NOT NULL
                REFERENCES tracks(id) ON DELETE CASCADE,
            outgoing_cue_ms INTEGER NOT NULL CHECK(outgoing_cue_ms >= 0),
            incoming_cue_ms INTEGER NOT NULL CHECK(incoming_cue_ms >= 0),
            bars_per_square INTEGER NOT NULL DEFAULT 8
                CHECK(bars_per_square IN (4, 8, 16)),
            square_count INTEGER NOT NULL DEFAULT 1
                CHECK(square_count IN (1, 2, 4)),
            PRIMARY KEY(playlist_id, outgoing_track_id, incoming_track_id)
        );
        """
    )


class SetTimelineRepository:
    """Сохраняет монтаж отдельно от тегов и оригинальных аудиофайлов."""

    def __init__(self, database: LibraryDatabase) -> None:
        self.database = database

    def cue_points(self, track_id: int) -> list[CuePoint]:
        try:
            with self.database.connection() as conn:
                rows = conn.execute(
                    "SELECT track_id, slot, position_ms FROM track_cue_points "
                    "WHERE track_id=? ORDER BY slot",
                    (track_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SetTimelineRepositoryError(
                f"Не удалось прочитать быстрые точки: {exc}"
            ) from exc
        return [CuePoint(**dict(row)) for row in rows]

    def save_cue_point(self, track_id: int, slot: int, position_ms: int) -> None:
        if slot not in range(1, 5):
            raise SetTimelineRepositoryError("Доступны быстрые точки 1–4")
        if position_ms < 0:
            raise SetTimelineRepositoryError("Позиция точки не может быть отрицательной")
        try:
            with self.database.connection() as conn:
                if conn.execute(
                    "SELECT 1 FROM tracks WHERE id=?", (track_id,)
                ).fetchone() is None:
                    raise SetTimelineRepositoryError("Трек больше не существует")
                conn.execute(
                    "INSERT INTO track_cue_points(track_id, slot, position_ms) "
                    "VALUES (?, ?, ?) ON CONFLICT(track_id, slot) DO UPDATE SET "
                    "position_ms=excluded.position_ms",
                    (track_id, slot, position_ms),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise SetTimelineRepositoryError(
                f"Не удалось сохранить быструю точку: {exc}"
            ) from exc

    def transition(
        self, playlist_id: int, outgoing_track_id: int, incoming_track_id: int
    ) -> SavedTransition | None:
        try:
            with self.database.connection() as conn:
                row = conn.execute(
                    "SELECT playlist_id, outgoing_track_id, incoming_track_id, "
                    "outgoing_cue_ms, incoming_cue_ms, bars_per_square, square_count "
                    "FROM playlist_transitions WHERE playlist_id=? "
                    "AND outgoing_track_id=? AND incoming_track_id=?",
                    (playlist_id, outgoing_track_id, incoming_track_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SetTimelineRepositoryError(
                f"Не удалось прочитать переход: {exc}"
            ) from exc
        return SavedTransition(**dict(row)) if row else None

    def save_transition(self, transition: SavedTransition) -> None:
        if transition.outgoing_track_id == transition.incoming_track_id:
            raise SetTimelineRepositoryError("Для перехода нужны два разных трека")
        if transition.bars_per_square not in {4, 8, 16}:
            raise SetTimelineRepositoryError("Некорректный размер квадрата")
        if transition.square_count not in {1, 2, 4}:
            raise SetTimelineRepositoryError("Некорректное число квадратов")
        if transition.outgoing_cue_ms < 0 or transition.incoming_cue_ms < 0:
            raise SetTimelineRepositoryError("Позиция точки не может быть отрицательной")
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute(
                    "SELECT 1 FROM playlists WHERE id=?", (transition.playlist_id,)
                ).fetchone() is None:
                    raise SetTimelineRepositoryError("Плейлист больше не существует")
                members = conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id=? "
                    "AND track_id IN (?, ?)",
                    (
                        transition.playlist_id,
                        transition.outgoing_track_id,
                        transition.incoming_track_id,
                    ),
                ).fetchall()
                if len(members) != 2:
                    raise SetTimelineRepositoryError(
                        "Оба трека должны находиться в выбранном плейлисте"
                    )
                conn.execute(
                    "INSERT INTO playlist_transitions(playlist_id, "
                    "outgoing_track_id, incoming_track_id, outgoing_cue_ms, "
                    "incoming_cue_ms, bars_per_square, square_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(playlist_id, "
                    "outgoing_track_id, incoming_track_id) DO UPDATE SET "
                    "outgoing_cue_ms=excluded.outgoing_cue_ms, "
                    "incoming_cue_ms=excluded.incoming_cue_ms, "
                    "bars_per_square=excluded.bars_per_square, "
                    "square_count=excluded.square_count",
                    (
                        transition.playlist_id,
                        transition.outgoing_track_id,
                        transition.incoming_track_id,
                        transition.outgoing_cue_ms,
                        transition.incoming_cue_ms,
                        transition.bars_per_square,
                        transition.square_count,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise SetTimelineRepositoryError(
                f"Не удалось сохранить переход: {exc}"
            ) from exc
