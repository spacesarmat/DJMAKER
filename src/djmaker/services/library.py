"""Высокоуровневые операции медиатеки."""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from djmaker.domain.models import (
    AudioMetadata,
    DuplicateGroup,
    EmbeddedArtwork,
    MetadataCandidate,
    ScanStats,
    TrackRecord,
)
from djmaker.infrastructure.beat_grid import BeatGridRepository
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.infrastructure.playlists import PlaylistRepository
from djmaker.infrastructure.set_timeline import SetTimelineRepository
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.http_utils import urlopen_with_retry
from djmaker.plugins.merge import merge_candidates
from djmaker.plugins.registry import PluginRegistry
from djmaker.services.audio_analysis import ANALYSIS_SAMPLE_RATE, AudioAnalysisError, EssentiaAudioAnalyzer
from djmaker.services.artwork import ArtworkCache, MAX_ARTWORK_BYTES
from djmaker.services.audio_tags import AudioTagError, AudioTagService
from djmaker.services.drop_import import DropImportPlan
from djmaker.services.energy_analysis import compute_energy_features, decode_mono_pcm, energy_score
from djmaker.services.genre_inference import GenreClassifier
from djmaker.services.organizer import FileOrganizer
from djmaker.services.scanner import LibraryScanner
from djmaker.services.tasks import TaskControl
from djmaker.services.waveform import WAVEFORM_BAR_COUNT, WaveformAnalyzer


LOGGER = logging.getLogger(__name__)


class LibraryServiceError(RuntimeError):
    """Ошибка высокоуровневой операции медиатеки."""


class LibraryService:
    """Фасад для UI: БД, сканирование, теги, организация и онлайн-плагины."""

    def __init__(
        self,
        database: LibraryDatabase,
        scanner: LibraryScanner,
        tags: AudioTagService,
        organizer: FileOrganizer,
        plugins: PluginRegistry,
        analyzer: EssentiaAudioAnalyzer,
        artwork_cache: ArtworkCache,
        waveform_analyzer: WaveformAnalyzer,
    ) -> None:
        self.database = database
        self.scanner = scanner
        self.tags = tags
        self.organizer = organizer
        self.plugins = plugins
        self.analyzer = analyzer
        self.artwork_cache = artwork_cache
        self.waveform_analyzer = waveform_analyzer
        self._genre_classifier: GenreClassifier | None = None

    @property
    def playlists(self) -> PlaylistRepository:
        return PlaylistRepository(self.database)

    @property
    def set_timeline(self) -> SetTimelineRepository:
        return SetTimelineRepository(self.database)

    @property
    def beat_grid(self) -> BeatGridRepository:
        return BeatGridRepository(self.database)

    def scan_folder(
        self,
        root: Path,
        *,
        task: TaskControl | None = None,
        progress: Callable[[ScanStats, Path], None] | None = None,
    ) -> ScanStats:
        """Сканирует папку и возвращает статистику."""
        return self.scanner.scan(root, task=task, progress=progress)

    def plan_import_paths(
        self,
        paths: tuple[Path, ...] | list[Path],
    ) -> DropImportPlan:
        """Фильтрует dropped-пути до создания фоновой задачи."""
        return self.scanner.plan_paths(paths)

    def import_paths(
        self,
        paths: tuple[Path, ...] | list[Path],
        *,
        task: TaskControl | None = None,
        progress: Callable[[ScanStats, Path], None] | None = None,
    ) -> ScanStats:
        """Импортирует dropped-файлы и папки с автоматической фильтрацией."""
        return self.scanner.scan_paths(paths, task=task, progress=progress)

    def tracks(
        self,
        search: str = "",
        limit: int = 1000,
        *,
        sort_by: str = "artist",
        descending: bool = False,
    ) -> list[TrackRecord]:
        """Возвращает треки медиатеки."""
        return self.database.list_tracks(
            search=search, limit=limit, sort_by=sort_by, descending=descending
        )

    def reset_library(self) -> None:
        """Обнуляет SQLite-медиатеку, не затрагивая музыкальные файлы."""
        self.database.reset()

    def track(self, track_id: int) -> TrackRecord:
        """Возвращает один трек или поднимает понятную ошибку."""
        return self._require_track(track_id)

    def roots(self) -> list[Path]:
        """Возвращает корневые музыкальные папки."""
        return self.database.list_roots()

    def prepare_artwork(self, data: bytes) -> EmbeddedArtwork:
        """Проверяет выбранные пользователем bytes встроенной обложки."""
        return self.tags.prepare_artwork(data)

    def exact_duplicates(self) -> list[DuplicateGroup]:
        """Возвращает точные дубликаты по SHA-256."""
        return self.database.find_exact_duplicates()

    def analysis_counts(self) -> tuple[int, int]:
        """Возвращает (всего треков, проанализировано)."""
        return self.database.analysis_counts()

    def beat_grid_counts(self) -> tuple[int, int]:
        """Возвращает (всего треков, полный анализ сетки завершён)."""
        return self.database.beat_grid_counts()

    def tracks_for_analysis(self, *, force: bool = False) -> list[TrackRecord]:
        """Возвращает очередь треков для BPM/Key анализа."""
        return self.database.list_tracks_for_analysis(force=force)

    def waveform_counts(self) -> tuple[int, int]:
        """Возвращает (всего треков, waveform построено)."""
        return self.database.waveform_counts(minimum_points=WAVEFORM_BAR_COUNT)

    def tracks_for_waveform_analysis(
        self, *, force: bool = False
    ) -> list[TrackRecord]:
        """Возвращает очередь треков для построения waveform."""
        return self.database.list_tracks_for_waveform_analysis(
            force=force,
            minimum_points=WAVEFORM_BAR_COUNT,
        )

    def analyze_waveform(
        self,
        track_id: int,
        task: TaskControl | None = None,
    ) -> TrackRecord:
        """Строит waveform одного трека и сохраняет её в SQLite."""
        if task is not None:
            task.checkpoint()
        track = self._require_track(track_id)
        waveform = self.waveform_analyzer.analyze(track.path, task=task)
        self.database.save_waveform_analysis(track_id, waveform)
        return self._require_track(track_id)

    def tracks_for_artwork_refresh(self) -> list[TrackRecord]:
        """Возвращает треки, для которых ещё не проверена встроенная обложка."""
        return self.database.list_tracks_for_artwork_refresh()

    def refresh_embedded_artwork(
        self,
        track_id: int,
        task: TaskControl | None = None,
    ) -> TrackRecord:
        """Читает встроенную обложку, кэширует её и обновляет запись трека."""
        if task is not None:
            task.checkpoint()
        track = self._require_track(track_id)
        artwork = self.tags.embedded_artwork(track.path)
        artwork_path = self.artwork_cache.store(artwork)
        self.database.set_embedded_artwork(track_id, artwork_path)
        return self._require_track(track_id)

    def analyze_track(
        self,
        track_id: int,
        task: TaskControl | None = None,
        *,
        genre_refinement_enabled: bool = True,
        genre_gpu_enabled: bool = True,
    ) -> TrackRecord:
        """Анализирует трек, пишет BPM/Key в файл и синхронизирует SQLite."""
        if task is not None:
            task.checkpoint()
        track = self._require_track(track_id)
        analysis = self.analyzer.analyze(track.path, task=task)

        try:
            samples = decode_mono_pcm(self.analyzer.runtime, track.path)
            features = compute_energy_features(samples)
            analysis.energy = energy_score(features, analysis.bpm)
            analysis.noisiness = features.spectral_flatness
            if genre_refinement_enabled:
                classifier = self._get_genre_classifier(genre_gpu_enabled)
                analysis.genre_tag = classifier.classify(samples, sample_rate=ANALYSIS_SAMPLE_RATE)
        except (AudioAnalysisError, OSError) as exc:
            LOGGER.warning("Не удалось посчитать энергию для %s: %s", track.path, exc)

        # После этой контрольной точки запись тегов и синхронизация БД выполняются
        # как единый участок: пауза не должна оставить уже изменённый файл со
        # старым hash/mtime в медиатеке.
        if task is not None:
            task.checkpoint()
        self.tags.write_analysis(track.path, analysis)
        inspected, file_hash, stat = self.scanner.inspect_and_hash(track.path)

        key_tag = self.tags.analysis_key_tag_value(analysis)
        if analysis.bpm is not None and inspected.metadata.bpm is None:
            inspected.metadata.bpm = analysis.bpm
        if key_tag and not inspected.metadata.musical_key:
            inspected.metadata.musical_key = key_tag

        self.database.update_after_file_change(
            track_id,
            new_path=track.path,
            root=track.root_path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            file_hash=file_hash,
            metadata=inspected.metadata,
            technical=inspected.technical,
        )
        self.database.save_audio_analysis(track_id, analysis)
        return self._require_track(track_id)

    def _get_genre_classifier(self, gpu_enabled: bool) -> GenreClassifier:
        """Переиспользует ONNX-сессию между треками одного запуска анализа."""
        if self._genre_classifier is None or self._genre_classifier.gpu_enabled != gpu_enabled:
            self._genre_classifier = GenreClassifier(self.analyzer.runtime, gpu_enabled=gpu_enabled)
        return self._genre_classifier

    def update_tags(
        self,
        track_id: int,
        metadata: AudioMetadata,
        *,
        replace_artwork: bool = False,
        artwork: EmbeddedArtwork | None = None,
    ) -> TrackRecord:
        """Записывает теги/обложку в файл и синхронизирует SQLite."""
        track = self._require_track(track_id)
        self.tags.write(track.path, metadata)

        if replace_artwork:
            try:
                self.tags.write_artwork(track.path, artwork)
            except Exception:
                # Основные теги уже могли быть успешно записаны. Не оставляем
                # SQLite со старым hash/mtime даже при ошибке смены обложки.
                self._sync_changed_track(track_id, track)
                raise

        return self._sync_changed_track(
            track_id,
            track,
            refresh_artwork=replace_artwork,
        )

    def _sync_changed_track(
        self,
        track_id: int,
        track: TrackRecord,
        *,
        refresh_artwork: bool = False,
    ) -> TrackRecord:
        """Перечитывает изменённый файл и обновляет его техническую запись."""
        inspected, file_hash, stat = self.scanner.inspect_and_hash(track.path)
        self.database.update_after_file_change(
            track_id,
            new_path=track.path,
            root=track.root_path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            file_hash=file_hash,
            metadata=inspected.metadata,
            technical=inspected.technical,
        )

        if refresh_artwork:
            try:
                artwork_path = self.artwork_cache.store(inspected.artwork)
            except RuntimeError:
                self.database.set_embedded_artwork(track_id, None)
                raise
            self.database.set_embedded_artwork(track_id, artwork_path)
        return self._require_track(track_id)

    def organize_track(self, track_id: int, destination: Path, template: str) -> TrackRecord:
        """Переносит/переименовывает трек по шаблону и обновляет медиатеку."""
        track = self._require_track(track_id)
        destination = destination.expanduser().resolve()
        target = self.organizer.build_target(track, destination, template)
        moved = self.organizer.move(track.path, target)
        try:
            inspected, file_hash, stat = self.scanner.inspect_and_hash(moved)
            self.database.update_after_file_change(
                track_id,
                new_path=moved,
                root=destination,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                file_hash=file_hash,
                metadata=inspected.metadata,
                technical=inspected.technical,
            )
            self.database.add_root(destination)
        except Exception as exc:
            # Файл уже был перемещён: возвращаем точный контекст вместо маскировки ошибки.
            raise LibraryServiceError(
                f"Файл перемещён в {moved}, но БД не удалось обновить: {exc}"
            ) from exc
        return self._require_track(track_id)

    def search_metadata(
        self,
        track_id: int,
        provider_ids: Sequence[str] = ("musicbrainz",),
        limit: int = 10,
    ) -> list[MetadataCandidate]:
        """Ищет метаданные трека через один или несколько внешних плагинов.

        Провайдеры опрашиваются параллельно (каждый — независимый HTTP-запрос
        со своим троттлингом), общее время сводится к самому медленному
        источнику, а не к сумме всех. Провайдер, вернувший ошибку, пропускается
        (best-effort): остальные результаты всё равно возвращаются. Исключение
        поднимается, только если не сработал ни один из запрошенных провайдеров.
        """
        track = self._require_track(track_id)
        results: dict[str, list[MetadataCandidate]] = {}
        errors: list[str] = []

        def search_one(provider_id: str) -> tuple[str, list[MetadataCandidate] | None, str | None]:
            try:
                provider = self.plugins.get(provider_id)
                return provider_id, provider.search(track, limit=limit), None
            except (MetadataProviderError, KeyError) as exc:
                return provider_id, None, str(exc)

        with ThreadPoolExecutor(max_workers=max(1, len(provider_ids))) as executor:
            for provider_id, candidates, error in executor.map(search_one, provider_ids):
                if error is not None:
                    LOGGER.warning("Провайдер метаданных %s недоступен: %s", provider_id, error)
                    errors.append(f"{provider_id}: {error}")
                else:
                    assert candidates is not None
                    results[provider_id] = candidates

        if not results and errors:
            raise LibraryServiceError("; ".join(errors))
        # Порядок задаёт приоритет при merge — сохраняем порядок provider_ids,
        # а не порядок завершения потоков.
        per_provider = [results[pid] for pid in provider_ids if pid in results]
        return merge_candidates(per_provider)

    def search_single_provider(
        self, track_id: int, provider_id: str, limit: int = 10
    ) -> list[MetadataCandidate]:
        """Ищет метаданные трека через один конкретный провайдер.

        Используется массовым поиском для тонкой параллелизации по парам
        (трек, провайдер) — вместо того, чтобы каждый трек ждал все свои
        провайдеры последовательно, свободный воркер сразу берёт следующую
        пару, независимо от того, какому треку она принадлежит.
        """
        track = self._require_track(track_id)
        return self.plugins.get(provider_id).search(track, limit=limit)

    def apply_candidate(self, track_id: int, candidate: MetadataCandidate) -> TrackRecord:
        """Применяет найденные метаданные и встраивает обложку в файл.

        Обложка скачивается по artwork_url и пишется как embedded artwork —
        так же, как при ручном редактировании тегов — а не только сохраняется
        ссылкой в БД: иначе обложка видна только внутри DJMAKER (пока доступен
        интернет и жива ссылка), а в самом файле и в других плеерах её нет.
        Если скачать/встроить не удалось (сеть, неподходящий формат) —
        best-effort: теги всё равно применяются, ссылка сохраняется как есть.
        """
        current = self._require_track(track_id)
        metadata = AudioMetadata(
            title=candidate.title or current.metadata.title,
            artist=candidate.artist or current.metadata.artist,
            album=candidate.album or current.metadata.album,
            album_artist=current.metadata.album_artist,
            genre=candidate.genre or current.metadata.genre,
            year=candidate.year or current.metadata.year,
            track_number=current.metadata.track_number,
            disc_number=current.metadata.disc_number,
            bpm=candidate.bpm or current.metadata.bpm,
            musical_key=candidate.musical_key or current.metadata.musical_key,
        )

        artwork: EmbeddedArtwork | None = None
        if candidate.artwork_url:
            try:
                artwork = self._download_artwork(candidate.artwork_url)
            except (MetadataProviderError, AudioTagError) as exc:
                LOGGER.warning(
                    "Не удалось подготовить обложку %s: %s", candidate.artwork_url, exc
                )

        try:
            updated = self.update_tags(
                track_id, metadata, replace_artwork=artwork is not None, artwork=artwork
            )
        except AudioTagError as exc:
            if artwork is None:
                raise
            # Теги, скорее всего, уже записаны — не даём формату файла,
            # который не поддерживает embedded artwork (см. write_artwork),
            # обрушить применение метаданных целиком.
            LOGGER.warning(
                "Не удалось встроить обложку в %s, сохраняю только теги: %s",
                current.path,
                exc,
            )
            artwork = None
            updated = self.update_tags(track_id, metadata)

        if candidate.artwork_url and artwork is None:
            self.database.set_artwork_url(track_id, candidate.artwork_url)
            updated.artwork_url = candidate.artwork_url
        return updated

    @staticmethod
    def _download_artwork(url: str) -> EmbeddedArtwork:
        """Скачивает обложку по URL и проверяет её как embedded artwork."""
        request = urllib.request.Request(
            url, headers={"Accept": "image/*", "User-Agent": "DJMAKER metadata search"}
        )
        payload = urlopen_with_retry(request, provider_label="Обложка")
        if len(payload) > MAX_ARTWORK_BYTES:
            raise AudioTagError(f"Обложка слишком большая: {len(payload)} байт")
        return AudioTagService.prepare_artwork(payload)

    def _require_track(self, track_id: int) -> TrackRecord:
        track = self.database.get_track(track_id)
        if track is None:
            raise LibraryServiceError(f"Трек не найден: {track_id}")
        return track
