"""SQLite-хранилище медиатеки DJMAKER."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from djmaker.domain.beat_grid import BeatGridError, validate_beat_grid
from djmaker.domain.library_sort import (
    DEFAULT_LIBRARY_SORT,
    LIBRARY_SORT_LABELS,
    camelot_order,
    positive_number,
    release_year,
)
from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    BeatGridAnalysis,
    DuplicateGroup,
    TrackRecord,
    WaveformAnalysis,
)
from djmaker.infrastructure.beat_grid import beat_grid_payload, create_beat_grid_schema
from djmaker.infrastructure.playlists import create_playlist_schema
from djmaker.infrastructure.set_timeline import create_set_timeline_schema


SCHEMA_VERSION = 7


class DatabaseError(RuntimeError):
    """Ошибка работы с базой данных DJMAKER."""


class LibraryDatabase:
    """Потокобезопасная обёртка над SQLite с соединением на операцию."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        """Создаёт схему БД и применяет простую версионную миграцию."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.connection() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise DatabaseError(
                        f"База данных новее приложения: {version} > {SCHEMA_VERSION}"
                    )
                if version == 0:
                    self._create_schema(conn)
                    create_playlist_schema(conn)
                    create_set_timeline_schema(conn)
                    create_beat_grid_schema(conn)
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    conn.commit()
                else:
                    if version == 1:
                        self._migrate_v1_to_v2(conn)
                        version = 2
                    if version == 2:
                        self._migrate_v2_to_v3(conn)
                        version = 3
                    if version == 3:
                        self._migrate_v3_to_v4(conn)
                        version = 4
                    if version == 4:
                        if not conn.in_transaction:
                            conn.execute("BEGIN IMMEDIATE")
                        create_playlist_schema(conn)
                        version = 5
                    if version == 5:
                        if not conn.in_transaction:
                            conn.execute("BEGIN IMMEDIATE")
                        create_set_timeline_schema(conn)
                        version = 6
                    if version == 6:
                        self._migrate_v6_to_v7(conn)
                        version = 7
                    conn.execute(f"PRAGMA user_version={version}")
                    conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось инициализировать БД: {exc}") from exc

    def reset(self) -> None:
        """Удаляет данные медиатеки, сохраняя актуальную схему SQLite."""
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM scan_errors")
                conn.execute("DELETE FROM playlists")
                conn.execute("DELETE FROM tracks")
                conn.execute("DELETE FROM roots")
                conn.execute(
                    "DELETE FROM sqlite_sequence "
                    "WHERE name IN ('scan_errors', 'tracks', 'roots', 'playlists')"
                )
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось обнулить БД: {exc}") from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Открывает настроенное SQLite-соединение и гарантирует его закрытие."""
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS roots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_scan_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                root_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                extension TEXT NOT NULL,
                file_hash TEXT,
                duration REAL,
                bitrate INTEGER,
                sample_rate INTEGER,
                channels INTEGER,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER,
                disc_number INTEGER,
                bpm REAL,
                musical_key TEXT NOT NULL DEFAULT '',
                analysis_bpm REAL,
                analysis_bpm_confidence REAL,
                analysis_key TEXT NOT NULL DEFAULT '',
                analysis_scale TEXT NOT NULL DEFAULT '',
                analysis_key_strength REAL,
                analysis_camelot TEXT NOT NULL DEFAULT '',
                analyzed_at TEXT,
                artwork_url TEXT,
                embedded_artwork_path TEXT,
                embedded_artwork_checked INTEGER NOT NULL DEFAULT 0,
                waveform_peaks TEXT,
                waveform_analyzed_at TEXT,
                beat_grid_json TEXT,
                beat_grid_analyzed_at TEXT,
                scan_token TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_scanned_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL,
                path TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_hash ON tracks(file_hash);
            CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
            CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);
            CREATE INDEX IF NOT EXISTS idx_tracks_root ON tracks(root_path);
            CREATE INDEX IF NOT EXISTS idx_tracks_scan_token ON tracks(scan_token);
            """
        )

    @staticmethod
    def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
        """Добавляет отдельные поля DSP-анализа, не смешивая их с тегами файла."""
        conn.executescript(
            """
            ALTER TABLE tracks ADD COLUMN analysis_bpm REAL;
            ALTER TABLE tracks ADD COLUMN analysis_bpm_confidence REAL;
            ALTER TABLE tracks ADD COLUMN analysis_key TEXT NOT NULL DEFAULT '';
            ALTER TABLE tracks ADD COLUMN analysis_scale TEXT NOT NULL DEFAULT '';
            ALTER TABLE tracks ADD COLUMN analysis_key_strength REAL;
            ALTER TABLE tracks ADD COLUMN analysis_camelot TEXT NOT NULL DEFAULT '';
            ALTER TABLE tracks ADD COLUMN analyzed_at TEXT;
            """
        )

    @staticmethod
    def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
        """Добавляет локальный кэш встроенных обложек и флаг индексации."""
        conn.executescript(
            """
            ALTER TABLE tracks ADD COLUMN embedded_artwork_path TEXT;
            ALTER TABLE tracks ADD COLUMN embedded_artwork_checked
                INTEGER NOT NULL DEFAULT 0;
            """
        )

    @staticmethod
    def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
        """Добавляет компактную форму волны для UI и seek."""
        conn.executescript(
            """
            ALTER TABLE tracks ADD COLUMN waveform_peaks TEXT;
            ALTER TABLE tracks ADD COLUMN waveform_analyzed_at TEXT;
            """
        )

    @staticmethod
    def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
        """Добавляет полную BPM-сетку и будущие Warp-якоря."""
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        if "beat_grid_json" not in columns:
            conn.execute("ALTER TABLE tracks ADD COLUMN beat_grid_json TEXT")
        if "beat_grid_analyzed_at" not in columns:
            conn.execute("ALTER TABLE tracks ADD COLUMN beat_grid_analyzed_at TEXT")
        create_beat_grid_schema(conn)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def add_root(self, root: Path) -> None:
        """Добавляет корневую музыкальную папку в медиатеку."""
        path = str(root.resolve())
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO roots(path) VALUES (?) ON CONFLICT(path) DO UPDATE SET enabled=1",
                    (path,),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось сохранить папку {path}: {exc}") from exc

    def list_roots(self) -> list[Path]:
        """Возвращает активные корневые папки медиатеки."""
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT path FROM roots WHERE enabled=1 ORDER BY path"
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить список папок: {exc}") from exc
        return [Path(row["path"]) for row in rows]

    def set_root_scanned(self, root: Path) -> None:
        """Обновляет время последнего успешного сканирования папки."""
        try:
            with self.connection() as conn:
                conn.execute(
                    "UPDATE roots SET last_scan_at=? WHERE path=?",
                    (self._now(), str(root.resolve())),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось обновить статус папки: {exc}") from exc

    def get_signature(self, path: Path) -> tuple[int, int] | None:
        """Возвращает сохранённые размер и mtime файла, если он уже индексирован."""
        try:
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT size, mtime_ns FROM tracks WHERE path=?",
                    (str(path),),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось проверить файл {path}: {exc}") from exc
        if row is None:
            return None
        return int(row["size"]), int(row["mtime_ns"])

    def touch_track(self, path: Path, scan_token: str) -> None:
        """Помечает неизменившийся трек как увиденный текущим сканированием."""
        now = self._now()
        try:
            with self.connection() as conn:
                conn.execute(
                    "UPDATE tracks SET scan_token=?, last_scanned_at=? WHERE path=?",
                    (scan_token, now, str(path)),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось обновить трек {path}: {exc}") from exc

    def upsert_track(
        self,
        *,
        path: Path,
        root: Path,
        size: int,
        mtime_ns: int,
        extension: str,
        file_hash: str,
        metadata: AudioMetadata,
        technical: AudioTechnicalInfo,
        scan_token: str,
        embedded_artwork_path: Path | None = None,
        embedded_artwork_checked: bool = False,
    ) -> None:
        """Добавляет новый трек или обновляет существующий."""
        now = self._now()
        values = (
            str(path),
            str(root),
            size,
            mtime_ns,
            extension,
            file_hash,
            technical.duration,
            technical.bitrate,
            technical.sample_rate,
            technical.channels,
            metadata.title,
            metadata.artist,
            metadata.album,
            metadata.album_artist,
            metadata.genre,
            metadata.year,
            metadata.track_number,
            metadata.disc_number,
            metadata.bpm,
            metadata.musical_key,
            (
                str(embedded_artwork_path)
                if embedded_artwork_path is not None
                else None
            ),
            int(embedded_artwork_checked),
            scan_token,
            now,
            now,
            now,
        )
        try:
            with self.connection() as conn:
                previous = conn.execute(
                    "SELECT id, file_hash FROM tracks WHERE path=?",
                    (str(path),),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO tracks(
                        path, root_path, size, mtime_ns, extension, file_hash,
                        duration, bitrate, sample_rate, channels,
                        title, artist, album, album_artist, genre, year,
                        track_number, disc_number, bpm, musical_key,
                        embedded_artwork_path, embedded_artwork_checked,
                        scan_token, added_at, updated_at, last_scanned_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET
                        root_path=excluded.root_path,
                        size=excluded.size,
                        mtime_ns=excluded.mtime_ns,
                        extension=excluded.extension,
                        file_hash=excluded.file_hash,
                        duration=excluded.duration,
                        bitrate=excluded.bitrate,
                        sample_rate=excluded.sample_rate,
                        channels=excluded.channels,
                        title=excluded.title,
                        artist=excluded.artist,
                        album=excluded.album,
                        album_artist=excluded.album_artist,
                        genre=excluded.genre,
                        year=excluded.year,
                        track_number=excluded.track_number,
                        disc_number=excluded.disc_number,
                        bpm=excluded.bpm,
                        musical_key=excluded.musical_key,
                        embedded_artwork_path=excluded.embedded_artwork_path,
                        embedded_artwork_checked=excluded.embedded_artwork_checked,
                        analysis_bpm=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.analysis_bpm
                            ELSE NULL END,
                        analysis_bpm_confidence=CASE
                            WHEN tracks.file_hash=excluded.file_hash
                            THEN tracks.analysis_bpm_confidence
                            ELSE NULL END,
                        analysis_key=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.analysis_key
                            ELSE '' END,
                        analysis_scale=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.analysis_scale
                            ELSE '' END,
                        analysis_key_strength=CASE
                            WHEN tracks.file_hash=excluded.file_hash
                            THEN tracks.analysis_key_strength
                            ELSE NULL END,
                        analysis_camelot=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.analysis_camelot
                            ELSE '' END,
                        analyzed_at=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.analyzed_at
                            ELSE NULL END,
                        waveform_peaks=CASE
                            WHEN tracks.file_hash=excluded.file_hash THEN tracks.waveform_peaks
                            ELSE NULL END,
                        waveform_analyzed_at=CASE
                            WHEN tracks.file_hash=excluded.file_hash
                            THEN tracks.waveform_analyzed_at
                            ELSE NULL END,
                        beat_grid_json=CASE
                            WHEN tracks.file_hash=excluded.file_hash
                            THEN tracks.beat_grid_json
                            ELSE NULL END,
                        beat_grid_analyzed_at=CASE
                            WHEN tracks.file_hash=excluded.file_hash
                            THEN tracks.beat_grid_analyzed_at
                            ELSE NULL END,
                        scan_token=excluded.scan_token,
                        updated_at=excluded.updated_at,
                        last_scanned_at=excluded.last_scanned_at
                    """,
                    values,
                )
                if previous is not None and previous["file_hash"] != file_hash:
                    conn.execute(
                        "DELETE FROM track_beat_grid_anchors WHERE track_id=?",
                        (int(previous["id"]),),
                    )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось сохранить трек {path}: {exc}") from exc

    def remove_stale(self, root: Path, scan_token: str) -> int:
        """Удаляет записи файлов, исчезнувших из успешно просканированной папки."""
        try:
            with self.connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM tracks WHERE root_path=? AND scan_token<>?",
                    (str(root), scan_token),
                )
                conn.commit()
                return int(cursor.rowcount)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось удалить устаревшие записи: {exc}") from exc

    def record_scan_error(self, root: Path, path: Path, error: str) -> None:
        """Сохраняет ошибку анализа файла без остановки полного сканирования."""
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO scan_errors(root_path, path, error, created_at) VALUES (?,?,?,?)",
                    (str(root), str(path), error[:4000], self._now()),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось записать ошибку сканирования: {exc}") from exc

    def list_tracks(
        self,
        search: str = "",
        limit: int = 1000,
        *,
        sort_by: str = DEFAULT_LIBRARY_SORT,
        descending: bool = False,
    ) -> list[TrackRecord]:
        """Возвращает треки с необязательным поиском по основным полям."""
        limit = max(1, min(limit, 10_000))
        search = search.strip()
        if not isinstance(sort_by, str) or sort_by not in LIBRARY_SORT_LABELS:
            sort_by = DEFAULT_LIBRARY_SORT
        direction = "DESC" if descending else "ASC"
        expressions = {
            "artist": "NULLIF(TRIM(artist), '') COLLATE DJMAKER_TEXT",
            "title": "NULLIF(TRIM(title), '') COLLATE DJMAKER_TEXT",
            "bpm": "COALESCE(positive_number(analysis_bpm), positive_number(bpm))",
            "camelot": (
                "COALESCE(camelot_order(analysis_camelot), "
                "camelot_order(NULLIF(TRIM(analysis_key || ' ' || analysis_scale), '')), "
                "camelot_order(musical_key))"
            ),
            "added": "NULLIF(added_at, '')",
            "year": "release_year(year)",
            "duration": "positive_number(duration)",
        }
        expression = expressions[sort_by]
        # SQL-фрагменты выбираются только из фиксированного списка.
        order_by = f"({expression}) IS NULL, {expression} {direction}"
        if sort_by == "artist":
            order_by += (
                ", NULLIF(TRIM(title), '') IS NULL, "
                f"title COLLATE DJMAKER_TEXT {direction}"
            )
        order_by += ", artist COLLATE DJMAKER_TEXT, title COLLATE DJMAKER_TEXT, id"
        params: tuple[object, ...]
        if search:
            pattern = f"%{search}%"
            sql = f"""
                SELECT * FROM tracks
                WHERE title LIKE ?
                   OR artist LIKE ?
                   OR album LIKE ?
                   OR album_artist LIKE ?
                   OR genre LIKE ?
                   OR extension LIKE ?
                   OR musical_key LIKE ?
                   OR analysis_key LIKE ?
                   OR analysis_camelot LIKE ?
                   OR path LIKE ?
                ORDER BY {order_by}
                LIMIT ?
            """
            params = (
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                limit,
            )
        else:
            sql = f"""
                SELECT * FROM tracks
                ORDER BY {order_by}
                LIMIT ?
            """
            params = (limit,)

        try:
            with self.connection() as conn:
                conn.create_collation(
                    "DJMAKER_TEXT",
                    lambda left, right: (left.casefold() > right.casefold())
                    - (left.casefold() < right.casefold()),
                )
                conn.create_function(
                    "positive_number", 1, positive_number, deterministic=True
                )
                conn.create_function(
                    "release_year", 1, release_year, deterministic=True
                )
                conn.create_function(
                    "camelot_order", 1, camelot_order, deterministic=True
                )
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить медиатеку: {exc}") from exc
        return [self._row_to_track(row) for row in rows]

    def get_track(self, track_id: int) -> TrackRecord | None:
        """Возвращает трек по внутреннему идентификатору."""
        try:
            with self.connection() as conn:
                row = conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить трек {track_id}: {exc}") from exc
        return self._row_to_track(row) if row is not None else None

    def count_tracks(self) -> int:
        """Возвращает число треков в медиатеке."""
        try:
            with self.connection() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось посчитать треки: {exc}") from exc

    def analysis_counts(self) -> tuple[int, int]:
        """Возвращает количество всех и уже DSP-проанализированных треков."""
        try:
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, COUNT(analyzed_at) AS analyzed FROM tracks"
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить статистику аудио-анализа: {exc}") from exc
        return int(row["total"]), int(row["analyzed"])

    def beat_grid_counts(self) -> tuple[int, int]:
        """Возвращает число треков и число завершённых анализов сетки."""
        try:
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN analyzed_at IS NOT NULL AND "
                    "(analysis_bpm IS NULL OR beat_grid_analyzed_at IS NOT NULL) "
                    "THEN 1 ELSE 0 END) AS analyzed FROM tracks"
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить статистику BPM-сетки: {exc}") from exc
        return int(row["total"]), int(row["analyzed"] or 0)

    def find_exact_duplicates(self) -> list[DuplicateGroup]:
        """Возвращает группы точных дубликатов по полному SHA-256."""
        try:
            with self.connection() as conn:
                hashes = conn.execute(
                    """
                    SELECT file_hash
                    FROM tracks
                    WHERE file_hash IS NOT NULL AND file_hash<>''
                    GROUP BY file_hash
                    HAVING COUNT(*) > 1
                    ORDER BY COUNT(*) DESC
                    """
                ).fetchall()
                groups: list[DuplicateGroup] = []
                for hash_row in hashes:
                    file_hash = str(hash_row["file_hash"])
                    rows = conn.execute(
                        "SELECT * FROM tracks WHERE file_hash=? ORDER BY path",
                        (file_hash,),
                    ).fetchall()
                    groups.append(
                        DuplicateGroup(
                            file_hash=file_hash,
                            tracks=[self._row_to_track(row) for row in rows],
                        )
                    )
                return groups
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось найти дубликаты: {exc}") from exc

    def update_after_file_change(
        self,
        track_id: int,
        *,
        new_path: Path,
        root: Path,
        size: int,
        mtime_ns: int,
        file_hash: str,
        metadata: AudioMetadata,
        technical: AudioTechnicalInfo,
    ) -> None:
        """Обновляет запись после изменения тегов, переименования или переноса файла."""
        try:
            with self.connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET
                        path=?, root_path=?, size=?, mtime_ns=?, extension=?, file_hash=?,
                        duration=?, bitrate=?, sample_rate=?, channels=?,
                        title=?, artist=?, album=?, album_artist=?, genre=?, year=?,
                        track_number=?, disc_number=?, bpm=?, musical_key=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        str(new_path),
                        str(root),
                        size,
                        mtime_ns,
                        new_path.suffix.lower(),
                        file_hash,
                        technical.duration,
                        technical.bitrate,
                        technical.sample_rate,
                        technical.channels,
                        metadata.title,
                        metadata.artist,
                        metadata.album,
                        metadata.album_artist,
                        metadata.genre,
                        metadata.year,
                        metadata.track_number,
                        metadata.disc_number,
                        metadata.bpm,
                        metadata.musical_key,
                        self._now(),
                        track_id,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось обновить изменённый трек: {exc}") from exc

    def list_tracks_for_analysis(self, *, force: bool = False) -> list[TrackRecord]:
        """Возвращает треки, которым нужен DSP-анализ, либо всю медиатеку."""
        where = (
            ""
            if force
            else "WHERE analyzed_at IS NULL OR "
            "(analysis_bpm IS NOT NULL AND beat_grid_analyzed_at IS NULL)"
        )
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    f"SELECT * FROM tracks {where} ORDER BY id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось получить очередь аудио-анализа: {exc}") from exc
        return [self._row_to_track(row) for row in rows]

    def save_audio_analysis(self, track_id: int, analysis: AudioAnalysis) -> None:
        """Атомарно сохраняет BPM, Key и полную музыкальную сетку."""
        analyzed_at = analysis.analyzed_at or self._now()
        grid_payload: str | None = None
        grid_analyzed_at: str | None = None
        if analysis.beat_grid is not None:
            grid = analysis.beat_grid
            try:
                validate_beat_grid(grid)
            except BeatGridError as exc:
                raise DatabaseError(f"Некорректная BPM-сетка: {exc}") from exc
            grid_payload = beat_grid_payload(grid)
            grid_analyzed_at = grid.analyzed_at or analyzed_at
        try:
            with self.connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE tracks SET
                        analysis_bpm=?, analysis_bpm_confidence=?,
                        analysis_key=?, analysis_scale=?, analysis_key_strength=?,
                        analysis_camelot=?, analyzed_at=?,
                        beat_grid_json=?, beat_grid_analyzed_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        analysis.bpm,
                        analysis.bpm_confidence,
                        analysis.musical_key,
                        analysis.scale,
                        analysis.key_strength,
                        analysis.camelot,
                        analyzed_at,
                        grid_payload,
                        grid_analyzed_at,
                        self._now(),
                        track_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DatabaseError(f"Трек не найден: {track_id}")
                conn.execute(
                    "DELETE FROM track_beat_grid_anchors WHERE track_id=?",
                    (track_id,),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось сохранить аудио-анализ: {exc}") from exc

    def waveform_counts(self, *, minimum_points: int = 0) -> tuple[int, int]:
        """Возвращает количество всех и уже построенных waveform."""
        try:
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(waveform_analyzed_at) AS analyzed FROM tracks"
                ).fetchone()
                payloads = (
                    conn.execute(
                        "SELECT waveform_peaks FROM tracks "
                        "WHERE waveform_analyzed_at IS NOT NULL"
                    ).fetchall()
                    if minimum_points > 0
                    else ()
                )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Не удалось получить статистику waveform: {exc}"
            ) from exc
        total = int(row["total"])
        if minimum_points <= 0:
            return total, int(row["analyzed"])
        detailed = 0
        for payload_row in payloads:
            try:
                peak_count = len(
                    json.loads(payload_row["waveform_peaks"] or "[]")
                )
                if peak_count >= minimum_points:
                    detailed += 1
            except (TypeError, json.JSONDecodeError):
                continue
        return total, detailed

    def list_tracks_for_waveform_analysis(
        self, *, force: bool = False, minimum_points: int = 0
    ) -> list[TrackRecord]:
        """Возвращает треки без waveform/детализации либо всю медиатеку."""
        where = (
            ""
            if force or minimum_points > 0
            else "WHERE waveform_analyzed_at IS NULL"
        )
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    f"SELECT * FROM tracks {where} ORDER BY id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Не удалось получить очередь waveform: {exc}"
            ) from exc
        tracks = [self._row_to_track(row) for row in rows]
        if force or minimum_points <= 0:
            return tracks
        return [
            track
            for track in tracks
            if track.waveform is None or len(track.waveform.peaks) < minimum_points
        ]

    def save_waveform_analysis(
        self, track_id: int, waveform: WaveformAnalysis
    ) -> None:
        """Сохраняет компактный массив пиков формы волны."""
        analyzed_at = waveform.analyzed_at or self._now()
        payload = json.dumps(
            [round(float(value), 4) for value in waveform.peaks],
            separators=(",", ":"),
        )
        try:
            with self.connection() as conn:
                cursor = conn.execute(
                    "UPDATE tracks SET waveform_peaks=?, waveform_analyzed_at=?, "
                    "updated_at=? WHERE id=?",
                    (payload, analyzed_at, self._now(), track_id),
                )
                if cursor.rowcount != 1:
                    raise DatabaseError(f"Трек не найден: {track_id}")
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Не удалось сохранить waveform: {exc}"
            ) from exc

    def list_tracks_for_artwork_refresh(self) -> list[TrackRecord]:
        """Возвращает старые записи, где встроенная обложка ещё не проверялась."""
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM tracks "
                    "WHERE embedded_artwork_checked=0 ORDER BY id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Не удалось получить очередь встроенных обложек: {exc}"
            ) from exc
        return [self._row_to_track(row) for row in rows]

    def set_embedded_artwork(
        self,
        track_id: int,
        artwork_path: Path | None,
    ) -> None:
        """Сохраняет локальный путь встроенной обложки и отмечает проверку."""
        try:
            with self.connection() as conn:
                cursor = conn.execute(
                    "UPDATE tracks SET embedded_artwork_path=?, "
                    "embedded_artwork_checked=1, updated_at=? WHERE id=?",
                    (
                        str(artwork_path) if artwork_path is not None else None,
                        self._now(),
                        track_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DatabaseError(f"Трек не найден: {track_id}")
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Не удалось сохранить встроенную обложку: {exc}"
            ) from exc

    def set_artwork_url(self, track_id: int, artwork_url: str | None) -> None:
        """Сохраняет найденный URL обложки для трека."""
        try:
            with self.connection() as conn:
                conn.execute(
                    "UPDATE tracks SET artwork_url=?, updated_at=? WHERE id=?",
                    (artwork_url, self._now(), track_id),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Не удалось сохранить URL обложки: {exc}") from exc

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> TrackRecord:
        return TrackRecord(
            id=int(row["id"]),
            path=Path(row["path"]),
            root_path=Path(row["root_path"]),
            size=int(row["size"]),
            mtime_ns=int(row["mtime_ns"]),
            extension=str(row["extension"]),
            file_hash=row["file_hash"],
            metadata=AudioMetadata(
                title=str(row["title"] or ""),
                artist=str(row["artist"] or ""),
                album=str(row["album"] or ""),
                album_artist=str(row["album_artist"] or ""),
                genre=str(row["genre"] or ""),
                year=str(row["year"] or ""),
                track_number=row["track_number"],
                disc_number=row["disc_number"],
                bpm=row["bpm"],
                musical_key=str(row["musical_key"] or ""),
            ),
            technical=AudioTechnicalInfo(
                duration=row["duration"],
                bitrate=row["bitrate"],
                sample_rate=row["sample_rate"],
                channels=row["channels"],
            ),
            artwork_url=row["artwork_url"],
            embedded_artwork_path=(
                Path(row["embedded_artwork_path"])
                if row["embedded_artwork_path"]
                else None
            ),
            embedded_artwork_checked=bool(row["embedded_artwork_checked"]),
            analysis=(
                AudioAnalysis(
                    bpm=row["analysis_bpm"],
                    bpm_confidence=row["analysis_bpm_confidence"],
                    musical_key=str(row["analysis_key"] or ""),
                    scale=str(row["analysis_scale"] or ""),
                    key_strength=row["analysis_key_strength"],
                    camelot=str(row["analysis_camelot"] or ""),
                    analyzed_at=str(row["analyzed_at"] or ""),
                    beat_grid=_beat_grid_from_row(row),
                )
                if row["analyzed_at"] is not None
                else None
            ),
            waveform=(
                WaveformAnalysis(
                    peaks=tuple(
                        float(value)
                        for value in json.loads(row["waveform_peaks"] or "[]")
                    ),
                    analyzed_at=str(row["waveform_analyzed_at"] or ""),
                )
                if row["waveform_analyzed_at"] is not None
                else None
            ),
        )


def _beat_grid_from_row(row: sqlite3.Row) -> BeatGridAnalysis | None:
    """Восстанавливает сетку из SQLite; повреждённая сетка требует переанализа."""
    payload = row["beat_grid_json"]
    analyzed_at = row["beat_grid_analyzed_at"]
    if not payload or not analyzed_at:
        return None
    try:
        data = json.loads(payload)
        ticks = tuple(int(value) for value in data.get("beat_ticks_ms", ()))
        grid = BeatGridAnalysis(
            bpm=float(data["bpm"]),
            first_beat_ms=int(data["first_beat_ms"]),
            downbeat_ms=int(data["downbeat_ms"]),
            beats_per_bar=int(data.get("beats_per_bar", 4)),
            beat_ticks_ms=ticks,
            tempo_stability=(
                float(data["tempo_stability"])
                if data.get("tempo_stability") is not None
                else None
            ),
            downbeat_confidence=(
                float(data["downbeat_confidence"])
                if data.get("downbeat_confidence") is not None
                else None
            ),
            source=str(data.get("source") or "auto"),
            analyzed_at=str(analyzed_at),
        )
        return validate_beat_grid(grid)
    except (BeatGridError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
