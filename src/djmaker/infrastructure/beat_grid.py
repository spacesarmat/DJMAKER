"""SQLite-структура будущих Warp-якорей редактируемой BPM-сетки."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from djmaker.infrastructure.database import LibraryDatabase


class BeatGridRepositoryError(RuntimeError):
    """Ошибка чтения или сохранения опорных точек сетки."""


@dataclass(frozen=True, slots=True)
class BeatGridAnchor:
    track_id: int
    source_ms: int
    beat_number: float


def create_beat_grid_schema(conn: sqlite3.Connection) -> None:
    """Создаёт таблицу опорных точек для будущей плавающей сетки."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS track_beat_grid_anchors (
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            source_ms INTEGER NOT NULL CHECK(source_ms >= 0),
            beat_number REAL NOT NULL,
            PRIMARY KEY(track_id, source_ms),
            UNIQUE(track_id, beat_number)
        );
        """
    )


class BeatGridRepository:
    """Сохраняет ручные Warp-якоря отдельно от автоматического анализа."""

    def __init__(self, database: LibraryDatabase) -> None:
        self.database = database

    def anchors(self, track_id: int) -> list[BeatGridAnchor]:
        try:
            with self.database.connection() as conn:
                rows = conn.execute(
                    "SELECT track_id, source_ms, beat_number "
                    "FROM track_beat_grid_anchors WHERE track_id=? "
                    "ORDER BY source_ms",
                    (track_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise BeatGridRepositoryError(
                f"Не удалось прочитать опорные точки сетки: {exc}"
            ) from exc
        return [BeatGridAnchor(**dict(row)) for row in rows]

    def save_anchor(self, anchor: BeatGridAnchor) -> None:
        if anchor.source_ms < 0:
            raise BeatGridRepositoryError("Позиция опорной точки не может быть отрицательной")
        try:
            with self.database.connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM tracks WHERE id=?", (anchor.track_id,)
                ).fetchone()
                if exists is None:
                    raise BeatGridRepositoryError(f"Трек не найден: {anchor.track_id}")
                conn.execute(
                    "INSERT INTO track_beat_grid_anchors"
                    "(track_id, source_ms, beat_number) VALUES (?,?,?) "
                    "ON CONFLICT(track_id, source_ms) DO UPDATE SET "
                    "beat_number=excluded.beat_number",
                    (anchor.track_id, anchor.source_ms, anchor.beat_number),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise BeatGridRepositoryError(
                f"Не удалось сохранить опорную точку сетки: {exc}"
            ) from exc

    def clear_anchors(self, track_id: int) -> None:
        try:
            with self.database.connection() as conn:
                conn.execute(
                    "DELETE FROM track_beat_grid_anchors WHERE track_id=?",
                    (track_id,),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise BeatGridRepositoryError(
                f"Не удалось удалить опорные точки сетки: {exc}"
            ) from exc
