"""SQLite-структура будущих Warp-якорей редактируемой BPM-сетки."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from djmaker.domain.beat_grid import (
    BeatGridAnchor,
    BeatGridError,
    validate_anchor_mapping,
    validate_beat_grid,
)
from djmaker.domain.models import BeatGridAnalysis

if TYPE_CHECKING:
    from djmaker.infrastructure.database import LibraryDatabase


class BeatGridRepositoryError(RuntimeError):
    """Ошибка чтения или сохранения опорных точек сетки."""


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


def beat_grid_payload(grid: BeatGridAnalysis) -> str:
    """Сериализует проверенную сетку в стабильный компактный JSON."""
    validate_beat_grid(grid)
    return json.dumps(
        {
            "bpm": round(float(grid.bpm), 8),
            "first_beat_ms": grid.first_beat_ms,
            "downbeat_ms": grid.downbeat_ms,
            "beats_per_bar": grid.beats_per_bar,
            "beat_ticks_ms": list(grid.beat_ticks_ms),
            "tempo_stability": grid.tempo_stability,
            "downbeat_confidence": grid.downbeat_confidence,
            "source": grid.source,
        },
        ensure_ascii=False,
        separators=(",", ":"),
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

    def save_editor_state(
        self,
        track_id: int,
        grid: BeatGridAnalysis,
        anchors: tuple[BeatGridAnchor, ...] | list[BeatGridAnchor],
    ) -> None:
        """Атомарно сохраняет ручную сетку и полный набор Warp-якорей."""
        normalized = tuple(
            BeatGridAnchor(
                track_id=track_id,
                source_ms=int(anchor.source_ms),
                beat_number=float(anchor.beat_number),
            )
            for anchor in anchors
        )
        try:
            validate_beat_grid(grid)
            validate_anchor_mapping(grid, normalized)
            payload = beat_grid_payload(grid)
        except BeatGridError as exc:
            raise BeatGridRepositoryError(str(exc)) from exc

        now = datetime.now(UTC).isoformat()
        analyzed_at = grid.analyzed_at or now
        try:
            with self.database.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE tracks SET analysis_bpm=?, beat_grid_json=?, "
                    "beat_grid_analyzed_at=?, updated_at=? "
                    "WHERE id=? AND analyzed_at IS NOT NULL",
                    (grid.bpm, payload, analyzed_at, now, track_id),
                )
                if cursor.rowcount != 1:
                    raise BeatGridRepositoryError(
                        "Трек не найден или полный анализ ещё не выполнен"
                    )
                conn.execute(
                    "DELETE FROM track_beat_grid_anchors WHERE track_id=?",
                    (track_id,),
                )
                conn.executemany(
                    "INSERT INTO track_beat_grid_anchors"
                    "(track_id, source_ms, beat_number) VALUES (?,?,?)",
                    (
                        (track_id, anchor.source_ms, anchor.beat_number)
                        for anchor in normalized
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise BeatGridRepositoryError(
                f"Не удалось сохранить редактор BPM-сетки: {exc}"
            ) from exc
