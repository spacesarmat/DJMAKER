"""Основной Flet-интерфейс DJMAKER."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import flet as ft
import flet_audio as fta

try:
    import flet_dropzone as ftd
except ImportError:  # pragma: no cover - зависит от desktop extension runtime
    ftd = None

from djmaker.config import DEFAULT_ORGANIZE_TEMPLATE, DEFAULT_TARGET_LUFS, AppPaths
from djmaker.domain.models import (
    AudioMetadata,
    EmbeddedArtwork,
    MetadataCandidate,
    TrackRecord,
)
from djmaker.plugins.base import MetadataProviderError
from djmaker.runtime.dependencies import (
    DependencyStatus,
    RuntimeDependencies,
    RuntimeReport,
)
from djmaker.services.audio_analysis import recommended_analysis_concurrency
from djmaker.services.file_browser import reveal_file
from djmaker.services.library import LibraryService
from djmaker.services.tasks import (
    ManagedTask,
    TaskCancelled,
    TaskKind,
    TaskManager,
    TaskPaused,
    TaskSnapshot,
    TaskStatus,
)
from djmaker.services.workers import BackgroundWorkers
from djmaker.settings import (
    AppSettings,
    SettingsStore,
    LIBRARY_SCALE_MAX,
    LIBRARY_SCALE_MIN,
    LIBRARY_SCALE_STEP,
    THEME_COLOR_ROLES,
    THEME_MODES,
    is_valid_theme_color,
)
from djmaker.ui.density import COMPACT_UI, scaled_library_size
from djmaker.ui.scrolling import centered_scroll_offset
from djmaker.ui.theme import (
    THEME_MODE_LABELS,
    THEME_PALETTES,
    apply_app_theme,
    palette_description,
    palette_title,
    theme_mode_icon,
)


LOGGER = logging.getLogger(__name__)

_LIBRARY_SEARCH_DEBOUNCE_SECONDS = 0.22
_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS = 15.0
_THEME_EDITOR_MODE_LABELS = {
    "light": "Светлая",
    "dark": "Тёмная",
}
_THEME_ROLE_LABELS = {
    "primary": "Акцент",
    "on_primary": "Текст на акценте",
    "primary_container": "Акцентный контейнер",
    "on_primary_container": "Текст акцентного контейнера",
    "surface": "Основной фон",
    "surface_container_low": "Карточки",
    "surface_container": "Панели",
    "surface_container_high": "Выделение",
    "surface_container_highest": "Активная поверхность",
    "on_surface": "Основной текст",
    "on_surface_variant": "Вторичный текст",
    "outline": "Контур",
    "outline_variant": "Мягкий контур",
    "error": "Ошибка",
}


@dataclass(slots=True)
class _AudioTaskContext:
    force: bool
    pending_ids: set[int] | None = None
    labels: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class _BatchTaskContext:
    pending_ids: set[int]
    labels: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class _WaveformTaskContext:
    force: bool
    pending_ids: set[int] | None = None
    labels: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class _WaveformView:
    peaks: tuple[float, ...]
    progress_image: ft.Image
    played_bars: int


@dataclass(slots=True)
class _TagEditorArtworkState:
    changed: bool = False
    artwork: EmbeddedArtwork | None = None


class DJMakerUI:
    """Связывает Flet-контролы с сервисным слоем приложения."""

    def __init__(
        self,
        page: ft.Page,
        service: LibraryService,
        workers: BackgroundWorkers,
        paths: AppPaths,
        settings_store: SettingsStore,
        settings: AppSettings,
        runtime: RuntimeDependencies,
        tasks: TaskManager,
    ) -> None:
        self.page = page
        self.service = service
        self.workers = workers
        self.paths = paths
        self.settings_store = settings_store
        self.settings = settings
        self.runtime = runtime
        self.tasks = tasks
        self.runtime_report = runtime.probe()
        self._audio_task_contexts: dict[str, _AudioTaskContext] = {}
        self._scan_task_paths: dict[str, Path] = {}
        self._drop_task_paths: dict[str, tuple[Path, ...]] = {}
        self._artwork_task_contexts: dict[str, _BatchTaskContext] = {}
        self._waveform_task_contexts: dict[str, _WaveformTaskContext] = {}
        self._waveform_views: dict[int, _WaveformView] = {}
        self._library_list: ft.ListView | None = None
        self._library_track_indices: dict[int, int] = {}
        self._track_row_cards: dict[int, ft.Container] = {}
        self._selected_track_id: int | None = None
        self._library_viewport_extent = 0.0
        self._library_max_scroll_extent = 0.0
        self._search_revision = 0
        self._theme_editor_active = False
        self._theme_editor_mode = self._initial_theme_editor_mode()
        self._theme_draft_light: dict[str, str] = {}
        self._theme_draft_dark: dict[str, str] = {}
        self._theme_editor_fields: dict[str, ft.TextField] = {}
        self._theme_editor_swatches: dict[str, ft.Container] = {}
        self._theme_editor_status: ft.Text | None = None
        self.audio: fta.Audio | None = None
        self._player_track_id: int | None = None
        self._player_track_path: Path | None = None
        self._player_state = fta.AudioState.STOPPED
        self._player_position_ms = 0
        self._player_duration_ms = 0
        self._player_switch_lock = asyncio.Lock()
        self._player_load_event: asyncio.Event | None = None
        self._player_switching = False
        self._player_request_revision = 0

        self.search_clear_button = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip="Очистить поиск",
            visible=False,
            on_click=self._clear_search,
        )
        self.search = ft.TextField(
            hint_text="Исполнитель, название, альбом, жанр, формат или путь",
            expand=True,
            dense=True,
            border=ft.InputBorder.NONE,
            text_size=COMPACT_UI.font_sm,
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            suffix=self.search_clear_button,
            on_change=self._on_search_change,
            on_submit=self._on_search,
        )
        self.busy = ft.ProgressRing(width=16, height=16, visible=False)
        self.status = ft.Text(
            "Готово",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.analysis_progress = ft.ProgressBar(
            width=150,
            value=0,
            visible=False,
        )
        self.analysis_progress_text = ft.Text(
            "",
            size=COMPACT_UI.font_micro,
            color=ft.Colors.ON_SURFACE_VARIANT,
            visible=False,
        )
        self.task_count_text = ft.Text(
            "Задачи: 0",
            size=COMPACT_UI.font_micro,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.task_button = ft.IconButton(
            icon=ft.Icons.PENDING_ACTIONS,
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip="Текущие задачи",
            on_click=lambda _: self.show_tasks(),
        )
        self.player_title = ft.Text(
            "",
            size=COMPACT_UI.font_sm,
            weight=ft.FontWeight.BOLD,
        )
        self.player_position = ft.Text(
            "00:00 / 00:00",
            size=COMPACT_UI.font_micro,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.player_progress = ft.ProgressBar(width=190, value=0.0)
        self.player_play_button = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip="Воспроизвести / пауза",
            on_click=self._toggle_player,
        )
        self.player_stop_button = ft.IconButton(
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip="Остановить",
            on_click=self._stop_player,
        )
        self.player_bar = self._build_player_bar()
        self.content = ft.Column(expand=True, spacing=COMPACT_UI.space_md)
        self.theme_button = ft.IconButton(
            icon=theme_mode_icon(self.settings.theme_mode),
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip=self._theme_tooltip(),
            on_click=self._cycle_theme_mode,
        )
        self.navigation = self._build_navigation()
        self._drop_overlay = self._build_drop_overlay()

    def build(self) -> None:
        """Строит главное окно приложения."""
        self.page.title = "DJMAKER"
        self.page.padding = 0
        self.page.spacing = 0
        self.page.window.min_width = 980
        self.page.window.min_height = 640
        apply_app_theme(self.page, self.settings)

        workspace = ft.Column(
            controls=[
                ft.Container(
                    content=self.content,
                    expand=True,
                    padding=COMPACT_UI.content_padding,
                ),
                self.player_bar,
                ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                ft.Container(
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                    padding=ft.Padding.symmetric(
                        horizontal=COMPACT_UI.status_horizontal_padding,
                        vertical=COMPACT_UI.status_vertical_padding,
                    ),
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=12),
                            self.status,
                            ft.Container(expand=True),
                            self.task_button,
                            self.task_count_text,
                            self.analysis_progress_text,
                            self.analysis_progress,
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )

        main_row = ft.Row(
            controls=[
                self.navigation,
                ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                workspace,
            ],
            spacing=0,
            expand=True,
        )
        root_content = ft.Stack(
            controls=[main_row, self._drop_overlay],
            expand=True,
        )
        if self._native_drop_enabled():
            dropzone = ftd.Dropzone(
                content=root_content,
                expand=True,
                on_dropped=self._on_paths_dropped,
                on_entered=self._on_drop_entered,
                on_exited=self._on_drop_exited,
            )
            self.page.add(dropzone)
        else:
            self.page.add(root_content)
        self.show_library()

    @staticmethod
    def _native_drop_enabled() -> bool:
        """Проверяет, запущен ли desktop runtime с собранными extensions."""
        return ftd is not None and bool(os.getenv("FLET_DART_BRIDGE_PORT"))

    def _build_drop_overlay(self) -> ft.Container:
        """Создаёт полнооконный индикатор активного Drag&Drop."""
        return ft.Container(
            visible=False,
            left=0,
            right=0,
            top=0,
            bottom=0,
            opacity=0.96,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    ft.Icon(
                        ft.Icons.DRIVE_FOLDER_UPLOAD,
                        size=52,
                        color=ft.Colors.PRIMARY,
                    ),
                    ft.Text(
                        "Отпустите файлы или папки",
                        size=COMPACT_UI.font_lg,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        "Поддерживаемое аудио будет добавлено, остальные файлы пропущены",
                        size=COMPACT_UI.font_sm,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
        )

    def _on_drop_entered(self, _: object) -> None:
        self._drop_overlay.visible = True
        self._drop_overlay.update()

    def _on_drop_exited(self, _: object) -> None:
        self._drop_overlay.visible = False
        self._drop_overlay.update()

    def _on_paths_dropped(self, event: object) -> None:
        """Передаёт реальные desktop paths в управляемую задачу импорта."""
        self._drop_overlay.visible = False
        self._drop_overlay.update()

        files = getattr(event, "files", ()) or ()
        paths = tuple(
            Path(path)
            for item in files
            if (path := str(getattr(item, "path", "") or "").strip())
        )
        if not paths:
            self._notify("Drag&Drop: не получено локальных файлов или папок")
            return
        self.page.run_task(self._run_drop_import, paths)

    def _build_player_bar(self) -> ft.Container:
        """Создаёт компактный постоянный плеер прослушивания."""
        return ft.Container(
            visible=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            padding=ft.Padding.symmetric(
                horizontal=COMPACT_UI.status_horizontal_padding,
                vertical=COMPACT_UI.space_sm,
            ),
            content=ft.Row(
                controls=[
                    self.player_play_button,
                    self.player_stop_button,
                    ft.Icon(
                        ft.Icons.HEADPHONES,
                        size=COMPACT_UI.action_icon_size,
                        color=ft.Colors.PRIMARY,
                    ),
                    ft.Column(
                        controls=[self.player_title, self.player_position],
                        spacing=0,
                        width=270,
                    ),
                    self.player_progress,
                    ft.Container(expand=True),
                    ft.Text(
                        "Клик по waveform — переход к позиции",
                        size=COMPACT_UI.font_micro,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _ensure_audio_service(
        self,
        track: TrackRecord,
    ) -> tuple[fta.Audio, bool, bool]:
        """Лениво создаёт Audio service и сообщает о смене source."""
        source = track.path.expanduser().resolve()
        created = self.audio is None
        source_changed = created or self._player_track_path != source
        if self.audio is None:
            self.audio = fta.Audio(
                src=str(source),
                autoplay=False,
                release_mode=fta.ReleaseMode.STOP,
                on_loaded=self._on_player_loaded,
                on_duration_change=self._on_player_duration_change,
                on_position_change=self._on_player_position_change,
                on_state_change=self._on_player_state_change,
            )
            self.page.services.append(self.audio)
            self._player_track_path = source
        elif source_changed:
            self.audio.src = str(source)
            self._player_track_path = source
        return self.audio, source_changed, created

    async def _on_player_loaded(self, _: object) -> None:
        """Подтверждает готовность нового native audio source."""
        load_event = self._player_load_event
        if load_event is not None:
            load_event.set()

    def _commit_player_track(
        self,
        track: TrackRecord,
        position_ms: int,
    ) -> list[ft.Control]:
        """Атомарно переключает UI плеера на уже загруженный трек."""
        previous_track_id = self._player_track_id
        waveform_updates: list[ft.Control] = []
        if previous_track_id is not None and previous_track_id != track.id:
            previous_waveform = self._paint_waveform_progress(previous_track_id, 0.0)
            if previous_waveform is not None:
                waveform_updates.append(previous_waveform)

        self._player_track_id = track.id
        self._player_position_ms = max(0, position_ms)
        self._player_duration_ms = max(
            0, int((track.technical.duration or 0.0) * 1000)
        )
        self._player_state = fta.AudioState.STOPPED
        artist = track.metadata.artist or "Unknown Artist"
        title = track.metadata.title or track.path.stem
        self.player_title.value = f"{artist} - {title}"
        self.player_bar.visible = True
        self._refresh_player_controls()
        current_waveform = self._refresh_waveform_progress()
        if current_waveform is not None:
            waveform_updates.append(current_waveform)
        return waveform_updates

    async def _play_track(self, track_id: int, position_ms: int = 0) -> None:
        """Выбирает трек и плавно запускает его с нужной позиции."""
        self._player_request_revision += 1
        request_revision = self._player_request_revision
        await self._select_library_track(track_id)
        if request_revision != self._player_request_revision:
            return

        started_switch = False
        try:
            track = await self.workers.run(self.service.track, track_id)
            if request_revision != self._player_request_revision:
                return
            if not track.path.is_file():
                raise RuntimeError(f"Файл не найден: {track.path}")

            async with self._player_switch_lock:
                if request_revision != self._player_request_revision:
                    return

                source = track.path.expanduser().resolve()
                source_changed = self.audio is None or self._player_track_path != source
                target_position_ms = max(0, position_ms)

                if source_changed:
                    started_switch = True
                    self._player_switching = True
                    if self.audio is not None:
                        try:
                            await self.audio.pause()
                        except Exception:
                            LOGGER.debug(
                                "Не удалось приостановить предыдущий source",
                                exc_info=True,
                            )
                    if request_revision != self._player_request_revision:
                        return

                    self._player_load_event = asyncio.Event()
                    audio, _, audio_created = self._ensure_audio_service(track)
                    if audio_created:
                        # Первый Audio service нужно смонтировать в дерево страницы.
                        self.page.update()
                    else:
                        audio.update()

                    await asyncio.wait_for(
                        self._player_load_event.wait(),
                        timeout=_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS,
                    )
                    if request_revision != self._player_request_revision:
                        return
                else:
                    audio, _, _ = self._ensure_audio_service(track)

                waveform_updates = self._commit_player_track(
                    track,
                    target_position_ms,
                )
                self.page.update(
                    self.player_bar,
                    self.player_title,
                    self.player_play_button,
                    self.player_position,
                    self.player_progress,
                    *waveform_updates,
                )
                if started_switch:
                    self._player_switching = False
                    self._player_load_event = None
                    started_switch = False
                await audio.play(
                    position=ft.Duration(milliseconds=target_position_ms)
                )
        except TimeoutError:
            LOGGER.error("Audio source не загрузился вовремя: track_id=%s", track_id)
            self._notify("Плеер не успел загрузить выбранный файл")
        except Exception as exc:
            LOGGER.exception("Не удалось воспроизвести трек %s", track_id)
            self._notify(f"Не удалось воспроизвести файл: {exc}")
        finally:
            if started_switch:
                self._player_switching = False
                self._player_load_event = None

    async def _toggle_player(self, _: object) -> None:
        if self.audio is None:
            return
        try:
            if self._player_state is fta.AudioState.PLAYING:
                await self.audio.pause()
            elif self._player_state is fta.AudioState.COMPLETED:
                await self.audio.play()
            else:
                await self.audio.resume()
        except Exception as exc:
            LOGGER.exception("Ошибка управления плеером")
            self._notify(f"Ошибка плеера: {exc}")

    async def _stop_player(self, _: object) -> None:
        if self.audio is None:
            return
        try:
            await self.audio.pause()
            await self.audio.seek(ft.Duration(milliseconds=0))
            self._player_position_ms = 0
            self._player_state = fta.AudioState.STOPPED
            self._refresh_player_controls()
            controls: list[ft.Control] = [
                self.player_play_button,
                self.player_position,
                self.player_progress,
            ]
            waveform = self._refresh_waveform_progress()
            if waveform is not None:
                controls.append(waveform)
            self.page.update(*controls)
        except Exception as exc:
            LOGGER.exception("Ошибка остановки плеера")
            self._notify(f"Ошибка плеера: {exc}")

    def _on_player_duration_change(self, event: fta.AudioDurationChangeEvent) -> None:
        if self._player_switching:
            return
        self._player_duration_ms = max(0, event.duration.in_milliseconds)
        self._refresh_player_controls()
        controls: list[ft.Control] = [self.player_position, self.player_progress]
        waveform = self._refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        self.page.update(*controls)

    def _on_player_position_change(self, event: fta.AudioPositionChangeEvent) -> None:
        if self._player_switching:
            return
        self._player_position_ms = max(0, int(event.position))
        self._refresh_player_controls()
        controls: list[ft.Control] = [self.player_position, self.player_progress]
        waveform = self._refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        self.page.update(*controls)

    def _on_player_state_change(self, event: fta.AudioStateChangeEvent) -> None:
        if self._player_switching:
            return
        self._player_state = event.state
        if event.state is fta.AudioState.COMPLETED:
            self._player_position_ms = self._player_duration_ms
        self._refresh_player_controls()
        controls: list[ft.Control] = [
            self.player_play_button,
            self.player_position,
            self.player_progress,
        ]
        waveform = self._refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        self.page.update(*controls)

    def _refresh_player_controls(self) -> None:
        duration = self._player_duration_ms
        position = (
            min(self._player_position_ms, duration)
            if duration
            else self._player_position_ms
        )
        self.player_play_button.icon = (
            ft.Icons.PAUSE
            if self._player_state is fta.AudioState.PLAYING
            else ft.Icons.PLAY_ARROW
        )
        self.player_position.value = (
            f"{self._format_duration(position / 1000)} / "
            f"{self._format_duration(duration / 1000)}"
        )
        self.player_progress.value = (position / duration) if duration > 0 else 0.0

    @staticmethod
    def _base_waveform_width() -> int:
        return (
            COMPACT_UI.waveform_bar_count * COMPACT_UI.waveform_bar_width
            + (COMPACT_UI.waveform_bar_count - 1) * COMPACT_UI.waveform_bar_gap
        )

    def _waveform_width(self) -> float:
        return self._library_size(self._base_waveform_width())

    @staticmethod
    def _waveform_svg(
        peaks: tuple[float, ...],
        played_bars: int | None = None,
    ) -> str:
        """Рисует waveform одним SVG вместо десятков Flet-контролов."""
        width = DJMakerUI._base_waveform_width()
        height = COMPACT_UI.waveform_height
        limit = len(peaks) if played_bars is None else max(0, played_bars)
        rectangles: list[str] = []
        for index, peak in enumerate(peaks[:limit]):
            normalized = min(1.0, max(0.0, float(peak)))
            bar_height = max(
                COMPACT_UI.waveform_min_bar_height,
                round(height * normalized),
            )
            x = index * (COMPACT_UI.waveform_bar_width + COMPACT_UI.waveform_bar_gap)
            y = height - bar_height
            rectangles.append(
                f'<rect x="{x}" y="{y}" width="{COMPACT_UI.waveform_bar_width}" '
                f'height="{bar_height}" rx="1" fill="#000"/>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'{"".join(rectangles)}</svg>'
        )

    def _play_from_waveform(self, event: ft.TapEvent, track: TrackRecord) -> None:
        if event.local_position is None:
            return
        duration = track.technical.duration or 0.0
        if duration <= 0:
            self._notify("Для seek недоступна продолжительность трека")
            return
        fraction = min(
            1.0,
            max(0.0, event.local_position.x / self._waveform_width()),
        )
        position_ms = int(duration * fraction * 1000)
        self.page.run_task(self._play_track, track.id, position_ms)

    def _refresh_waveform_progress(self) -> ft.Control | None:
        track_id = self._player_track_id
        if track_id is None:
            return None
        duration = self._player_duration_ms
        fraction = (self._player_position_ms / duration) if duration > 0 else 0.0
        return self._paint_waveform_progress(track_id, fraction)

    def _paint_waveform_progress(
        self,
        track_id: int,
        fraction: float,
    ) -> ft.Control | None:
        view = self._waveform_views.get(track_id)
        if view is None:
            return None
        played = min(
            len(view.peaks),
            max(0, round(len(view.peaks) * min(1.0, max(0.0, fraction)))),
        )
        if played == view.played_bars:
            return None
        view.played_bars = played
        view.progress_image.src = self._waveform_svg(view.peaks, played)
        return view.progress_image

    def _build_navigation(self) -> ft.NavigationRail:
        """Создаёт постоянную боковую навигацию приложения."""
        return ft.NavigationRail(
            selected_index=0,
            extended=True,
            label_type=ft.NavigationRailLabelType.NONE,
            min_width=COMPACT_UI.nav_min_width,
            min_extended_width=COMPACT_UI.nav_extended_width,
            use_indicator=True,
            group_alignment=-0.88,
            pin_trailing_to_bottom=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            selected_label_text_style=ft.TextStyle(
                size=COMPACT_UI.font_sm,
                color=ft.Colors.PRIMARY,
                weight=ft.FontWeight.BOLD,
            ),
            unselected_label_text_style=ft.TextStyle(
                size=COMPACT_UI.font_sm,
                color=ft.Colors.ON_SURFACE,
            ),
            leading=ft.Container(
                padding=ft.Padding.only(left=9, right=6, top=9, bottom=6),
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.HEADPHONES, size=20, color=ft.Colors.PRIMARY),
                        ft.Column(
                            controls=[
                                ft.Text(
                                    "DJMAKER",
                                    size=COMPACT_UI.font_lg,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(
                                    "Music Library",
                                    size=COMPACT_UI.font_micro,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=0,
                        ),
                    ],
                    spacing=COMPACT_UI.space_sm,
                ),
            ),
            trailing=ft.Container(
                padding=ft.Padding.only(left=6, right=6, bottom=8),
                content=ft.Text(
                    "Windows · macOS",
                    size=COMPACT_UI.font_micro,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ),
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.LIBRARY_MUSIC_OUTLINED,
                    selected_icon=ft.Icons.LIBRARY_MUSIC,
                    label="Медиатека",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FOLDER_OUTLINED,
                    selected_icon=ft.Icons.FOLDER,
                    label="Папки",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.CONTENT_COPY_OUTLINED,
                    selected_icon=ft.Icons.CONTENT_COPY,
                    label="Дубликаты",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.CLOUD_OUTLINED,
                    selected_icon=ft.Icons.CLOUD,
                    label="Метаданные",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.GRAPHIC_EQ,
                    selected_icon=ft.Icons.EQUALIZER,
                    label="Аудио-модули",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.PENDING_ACTIONS,
                    selected_icon=ft.Icons.TASK_ALT,
                    label="Задачи",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label="Настройки",
                ),
            ],
            on_change=self._on_navigation_change,
        )

    def _on_navigation_change(self, event: object) -> None:
        control = getattr(event, "control", None)
        index = getattr(control, "selected_index", None)
        handlers = (
            self.show_library,
            self.show_folders,
            self.show_duplicates,
            self.show_plugins,
            self.show_audio_modules,
            self.show_tasks,
            self.show_settings,
        )
        if isinstance(index, int) and 0 <= index < len(handlers):
            handlers[index]()

    def _set_navigation_index(self, index: int) -> None:
        if self.navigation.selected_index == 6 and index != 6:
            self._close_theme_editor_session()
        if self.navigation.selected_index != index:
            self.navigation.selected_index = index

    def _initial_theme_editor_mode(self) -> str:
        """Выбирает редактируемую схему, соответствующую текущему интерфейсу."""
        if self.settings.theme_mode in _THEME_EDITOR_MODE_LABELS:
            return self.settings.theme_mode
        brightness = getattr(self.page, "platform_brightness", None)
        value = str(getattr(brightness, "value", brightness or "")).lower()
        return "dark" if "dark" in value else "light"

    def _begin_theme_editor_session(self) -> None:
        if self._theme_editor_active:
            return
        self._theme_editor_active = True
        self._theme_editor_mode = self._initial_theme_editor_mode()
        self._theme_draft_light = dict(self.settings.theme_light_overrides)
        self._theme_draft_dark = dict(self.settings.theme_dark_overrides)

    def _close_theme_editor_session(self) -> None:
        if not self._theme_editor_active:
            return
        self._theme_editor_active = False
        self._theme_editor_fields.clear()
        self._theme_editor_swatches.clear()
        self._theme_editor_status = None
        apply_app_theme(self.page, self.settings)

    def _theme_draft_for_mode(self, mode: str | None = None) -> dict[str, str]:
        target = mode or self._theme_editor_mode
        return self._theme_draft_dark if target == "dark" else self._theme_draft_light

    def _theme_preview_settings(self) -> AppSettings:
        return replace(
            self.settings,
            theme_light_overrides=dict(self._theme_draft_light),
            theme_dark_overrides=dict(self._theme_draft_dark),
        )

    def _theme_editor_changed_count(self) -> int:
        saved_light = self.settings.theme_light_overrides
        saved_dark = self.settings.theme_dark_overrides
        roles = set(THEME_COLOR_ROLES)
        return sum(
            self._theme_draft_light.get(role) != saved_light.get(role)
            for role in roles
        ) + sum(
            self._theme_draft_dark.get(role) != saved_dark.get(role)
            for role in roles
        )

    def _apply_theme_editor_preview(self) -> None:
        preview = self._theme_preview_settings()
        apply_app_theme(self.page, preview)
        self.page.theme_mode = (
            ft.ThemeMode.DARK
            if self._theme_editor_mode == "dark"
            else ft.ThemeMode.LIGHT
        )
        changed = self._theme_editor_changed_count()
        if self._theme_editor_status is not None:
            if changed:
                self._theme_editor_status.value = (
                    f"Предпросмотр · несохранённых изменений: {changed}"
                )
                self._theme_editor_status.color = ft.Colors.PRIMARY
            else:
                self._theme_editor_status.value = "Предпросмотр совпадает с сохранённой темой"
                self._theme_editor_status.color = ft.Colors.ON_SURFACE_VARIANT
        self.page.update()

    def _set_busy(self, value: bool, message: str = "") -> None:
        self.busy.visible = value
        if message:
            self.status.value = message
        self.page.update()

    def _set_status(self, message: str) -> None:
        self.status.value = message
        self.page.update()

    def _notify(self, message: str) -> None:
        self.status.value = message
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message)))
        self.page.update()

    def _refresh_task_indicator(self) -> None:
        """Синхронизирует компактный индикатор фоновых задач."""
        active = self.tasks.active_count()
        self.task_count_text.value = f"Задачи: {active}"
        self.busy.visible = active > 0

    async def monitor_tasks(self) -> None:
        """Обновляет экран задач, пока долгие worker-операции меняют состояние."""
        previous: tuple[object, ...] | None = None
        while True:
            await asyncio.sleep(0.4)
            snapshots = self.tasks.snapshots()
            signature = tuple(
                (
                    item.id,
                    item.status,
                    item.completed,
                    item.succeeded,
                    item.failed,
                    item.detail,
                )
                for item in snapshots
            )
            if signature == previous:
                continue
            previous = signature
            self._refresh_task_indicator()
            if self.navigation.selected_index == 5:
                self.show_tasks()
            else:
                self.page.update()

    def show_tasks(self) -> None:
        """Показывает текущие и завершённые задачи текущего сеанса."""
        self._set_navigation_index(5)
        snapshots = self.tasks.snapshots()
        active = [item for item in snapshots if item.active]
        history = [item for item in snapshots if not item.active]

        summary = self._surface_card(
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.PENDING_ACTIONS, color=ft.Colors.PRIMARY),
                    ft.Column(
                        controls=[
                            ft.Text(
                                f"Текущих задач: {len(active)}",
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                "Остановка сохраняет очередь для продолжения; "
                                "отмена завершает задачу окончательно.",
                                size=COMPACT_UI.font_xs,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                ]
            )
        )

        items: list[ft.Control] = []
        if not snapshots:
            items.append(
                self._empty_state(
                    ft.Icons.TASK_ALT,
                    "Фоновых задач пока нет",
                    "Сканирование, Drag&Drop, обложки, waveform и BPM / Key появятся здесь.",
                )
            )
        else:
            if active:
                items.append(ft.Text("Текущие", weight=ft.FontWeight.BOLD))
                items.extend(self._task_card(item) for item in active)
            if history:
                items.append(ft.Text("История сеанса", weight=ft.FontWeight.BOLD))
                items.extend(self._task_card(item) for item in history)

        listing = ft.ListView(
            controls=items,
            expand=True,
            spacing=COMPACT_UI.space_sm,
        )
        self._replace_content(
            "Задачи",
            "Фоновые операции DJMAKER",
            summary,
            listing,
        )

    def _task_card(self, snapshot: TaskSnapshot) -> ft.Control:
        state_label, state_icon, state_color = self._task_state_view(snapshot.status)
        if snapshot.total is not None:
            counters = (
                f"{snapshot.completed}/{snapshot.total} · "
                f"успешно: {snapshot.succeeded} · ошибок: {snapshot.failed}"
            )
        else:
            counters = (
                f"обработано: {snapshot.completed} · "
                f"успешно: {snapshot.succeeded} · ошибок: {snapshot.failed}"
            )

        actions: list[ft.Control] = []
        if snapshot.can_stop:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.PAUSE_CIRCLE_OUTLINE,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Остановить с возможностью продолжения",
                    on_click=lambda _, task_id=snapshot.id: self._stop_task(task_id),
                )
            )
        if snapshot.can_resume:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.PLAY_ARROW,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Возобновить",
                    on_click=lambda _, task_id=snapshot.id: self._resume_task(task_id),
                )
            )
        if snapshot.can_cancel:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.CANCEL_OUTLINED,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Отменить окончательно",
                    on_click=lambda _, task_id=snapshot.id: self._cancel_task(task_id),
                )
            )

        progress_value = snapshot.progress
        if progress_value is None and snapshot.status is not TaskStatus.RUNNING:
            progress_value = 0.0

        details: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Icon(state_icon, size=16, color=state_color),
                    ft.Text(
                        snapshot.title,
                        weight=ft.FontWeight.BOLD,
                        size=COMPACT_UI.font_sm,
                    ),
                    ft.Text(
                        state_label,
                        size=COMPACT_UI.font_xs,
                        color=state_color,
                    ),
                    ft.Container(expand=True),
                    *actions,
                ],
                spacing=COMPACT_UI.space_xs,
            ),
            ft.Text(
                snapshot.detail or "—",
                size=COMPACT_UI.font_xs,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.ProgressBar(value=progress_value),
            ft.Text(
                counters,
                size=COMPACT_UI.font_micro,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if snapshot.error:
            details.append(
                ft.Text(
                    snapshot.error,
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ERROR,
                )
            )
        return self._surface_card(ft.Column(controls=details, spacing=3))

    @staticmethod
    def _task_state_view(status: TaskStatus) -> tuple[str, object, object]:
        mapping = {
            TaskStatus.RUNNING: ("Выполняется", ft.Icons.PLAY_ARROW, ft.Colors.PRIMARY),
            TaskStatus.STOPPING: ("Останавливается", ft.Icons.PAUSE, ft.Colors.TERTIARY),
            TaskStatus.PAUSED: ("Остановлено", ft.Icons.PAUSE_CIRCLE_OUTLINE, ft.Colors.TERTIARY),
            TaskStatus.CANCELLING: ("Отменяется", ft.Icons.CANCEL, ft.Colors.ERROR),
            TaskStatus.CANCELLED: ("Отменено", ft.Icons.CANCEL_OUTLINED, ft.Colors.ERROR),
            TaskStatus.COMPLETED: ("Завершено", ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Colors.PRIMARY),
            TaskStatus.FAILED: ("Ошибка", ft.Icons.ERROR_OUTLINE, ft.Colors.ERROR),
        }
        return mapping[status]

    def _stop_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or not task.request_pause():
            return
        self._refresh_task_indicator()
        self.show_tasks()

    def _cancel_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or not task.request_cancel():
            return
        if task.snapshot().status is TaskStatus.CANCELLED:
            self._forget_task_context(task_id)
        self._refresh_task_indicator()
        self.show_tasks()

    def _resume_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        snapshot = task.snapshot()
        if not task.resume():
            return

        if snapshot.kind is TaskKind.AUDIO_ANALYSIS and task_id in self._audio_task_contexts:
            self.page.run_task(self._run_audio_analysis, task_id)
        elif snapshot.kind is TaskKind.LIBRARY_SCAN and task_id in self._scan_task_paths:
            self.page.run_task(self._run_scan, self._scan_task_paths[task_id], task_id)
        elif (
            snapshot.kind is TaskKind.LIBRARY_IMPORT
            and task_id in self._drop_task_paths
        ):
            self.page.run_task(
                self._run_drop_import,
                self._drop_task_paths[task_id],
                task_id,
            )
        elif snapshot.kind is TaskKind.ARTWORK_INDEX and task_id in self._artwork_task_contexts:
            self.page.run_task(self._run_embedded_artwork, task_id)
        elif (
            snapshot.kind is TaskKind.WAVEFORM_ANALYSIS
            and task_id in self._waveform_task_contexts
        ):
            self.page.run_task(self._run_waveform_analysis, task_id)
        else:
            task.mark_failed("Контекст задачи больше недоступен")

        self._refresh_task_indicator()
        self.show_tasks()

    def _forget_task_context(self, task_id: str) -> None:
        self._audio_task_contexts.pop(task_id, None)
        self._scan_task_paths.pop(task_id, None)
        self._drop_task_paths.pop(task_id, None)
        self._artwork_task_contexts.pop(task_id, None)
        self._waveform_task_contexts.pop(task_id, None)

    def _replace_content(
        self,
        title: str,
        subtitle: str,
        *controls: ft.Control,
    ) -> None:
        del title, subtitle
        self.content.controls.clear()
        self.content.controls.extend(controls)
        self.page.update()

    @staticmethod
    def _surface_card(
        content: ft.Control,
        *,
        padding: float = COMPACT_UI.card_padding,
    ) -> ft.Container:
        """Возвращает стандартную карточку для экранов приложения."""
        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border_radius=COMPACT_UI.radius,
            padding=padding,
            content=content,
        )

    def _empty_state(self, icon: object, title: str, description: str) -> ft.Control:
        return self._surface_card(
            ft.Column(
                controls=[
                    ft.Icon(icon, size=24, color=ft.Colors.PRIMARY),
                    ft.Text(title, size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        description,
                        size=COMPACT_UI.font_sm,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=COMPACT_UI.space_sm,
            ),
            padding=14,
        )

    async def _on_search(self, _: object) -> None:
        """Немедленно применяет поисковый запрос по Enter."""
        self._search_revision += 1
        self.show_library()

    def _on_search_change(self, _: object) -> None:
        """Дебаунсит живой поиск, чтобы не перестраивать 1000 строк на каждый символ."""
        self._search_revision += 1
        revision = self._search_revision
        self.search_clear_button.visible = bool((self.search.value or "").strip())
        self.page.update(self.search_clear_button)
        self.page.run_task(self._debounced_library_search, revision)

    async def _debounced_library_search(self, revision: int) -> None:
        await asyncio.sleep(_LIBRARY_SEARCH_DEBOUNCE_SECONDS)
        if revision != self._search_revision or self.navigation.selected_index != 0:
            return
        self.show_library()

    def _clear_search(self, _: object) -> None:
        self.search.value = ""
        self.search_clear_button.visible = False
        self._search_revision += 1
        self.show_library()

    def _build_library_search_block(self, result_count: int) -> ft.Container:
        """Возвращает тематический поисковый блок медиатеки."""
        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=COMPACT_UI.radius,
            padding=ft.Padding.symmetric(
                horizontal=COMPACT_UI.card_padding,
                vertical=COMPACT_UI.space_sm,
            ),
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.SEARCH,
                        size=COMPACT_UI.action_icon_size,
                        color=ft.Colors.PRIMARY,
                    ),
                    self.search,
                    ft.Container(
                        width=1,
                        height=22,
                        bgcolor=ft.Colors.OUTLINE_VARIANT,
                    ),
                    ft.Text(
                        f"Найдено: {result_count}",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _library_size(
        self,
        value: float,
        *,
        minimum: float = 1.0,
    ) -> float:
        """Возвращает размер элемента строки с учётом масштаба медиатеки."""
        return scaled_library_size(
            value,
            self.settings.library_scale_percent,
            minimum=minimum,
        )

    def _library_scale_control(self) -> ft.Control:
        """Строит компактное управление масштабом строк медиатеки."""
        percent = self.settings.library_scale_percent
        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=COMPACT_UI.radius,
            padding=ft.Padding.symmetric(horizontal=COMPACT_UI.space_xs),
            content=ft.Row(
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.ZOOM_OUT,
                        icon_size=COMPACT_UI.action_icon_size,
                        padding=COMPACT_UI.space_xs,
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Уменьшить строки",
                        disabled=percent <= LIBRARY_SCALE_MIN,
                        on_click=lambda _: self._change_library_scale(
                            -LIBRARY_SCALE_STEP
                        ),
                    ),
                    ft.Text(
                        f"{percent}%",
                        width=34,
                        text_align=ft.TextAlign.CENTER,
                        size=COMPACT_UI.font_xs,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ZOOM_IN,
                        icon_size=COMPACT_UI.action_icon_size,
                        padding=COMPACT_UI.space_xs,
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Увеличить строки",
                        disabled=percent >= LIBRARY_SCALE_MAX,
                        on_click=lambda _: self._change_library_scale(
                            LIBRARY_SCALE_STEP
                        ),
                    ),
                ],
                spacing=0,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _change_library_scale(self, delta: int) -> None:
        """Сохраняет новый масштаб и немедленно перестраивает медиатеку."""
        current = self.settings.library_scale_percent
        target = min(
            LIBRARY_SCALE_MAX,
            max(LIBRARY_SCALE_MIN, current + delta),
        )
        if target == current:
            return

        settings = replace(self.settings, library_scale_percent=target)
        try:
            self.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить масштаб медиатеки")
            self._notify(f"Не удалось сохранить масштаб: {exc}")
            return

        self.settings = settings
        selected_track_id = self._selected_track_id
        self.show_library()
        if (
            selected_track_id is not None
            and selected_track_id in self._library_track_indices
        ):
            self.page.run_task(
                self._select_library_track,
                selected_track_id,
            )

    def show_library(self) -> None:
        """Отображает локальную медиатеку."""
        self._set_navigation_index(0)
        try:
            tracks = self.service.tracks(self.search.value or "", limit=1000)
        except RuntimeError as exc:
            self._notify(str(exc))
            return

        actions = ft.Row(
            controls=[
                ft.Text(
                    f"Показано треков: {len(tracks)}",
                    size=COMPACT_UI.font_sm,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Container(expand=True),
                self.busy,
                ft.Button(
                    content="BPM / Key",
                    icon=ft.Icons.SPEED,
                    on_click=self._start_audio_analysis,
                ),
                ft.Button(
                    content="Обновить",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _: self.show_library(),
                ),
                self._library_scale_control(),
                self.theme_button,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=COMPACT_UI.space_sm,
        )
        self._waveform_views.clear()
        self._track_row_cards.clear()
        self._library_track_indices = {
            track.id: index for index, track in enumerate(tracks)
        }
        self._library_viewport_extent = 0.0
        self._library_max_scroll_extent = 0.0
        items: list[ft.Control] = []
        if not tracks:
            items.append(
                self._empty_state(
                    ft.Icons.LIBRARY_MUSIC_OUTLINED,
                    "Медиатека пока пуста",
                    "Добавьте музыкальную папку в разделе «Папки» и запустите сканирование.",
                )
            )
            listing = ft.ListView(
                controls=items,
                expand=True,
                spacing=COMPACT_UI.space_sm,
            )
        else:
            items.extend(self._track_row(track) for track in tracks)
            listing = ft.ListView(
                controls=items,
                expand=True,
                spacing=0,
                item_extent=self._track_item_extent(),
                on_scroll=self._on_library_scroll,
                scroll_interval=50,
            )
        self._library_list = listing
        self.search_clear_button.visible = bool((self.search.value or "").strip())
        self._replace_content(
            "Медиатека",
            "Поиск, теги и организация локальной музыкальной коллекции",
            actions,
            self._build_library_search_block(len(tracks)),
            listing,
        )

    def _track_item_extent(self) -> float:
        """Высота строки медиатеки с учётом пользовательского масштаба."""
        return (
            self._library_size(COMPACT_UI.track_icon_box)
            + self._library_size(COMPACT_UI.card_padding) * 2
            + self._library_size(COMPACT_UI.space_sm)
        )

    def _estimated_library_viewport_extent(self) -> float:
        """Оценивает viewport до первого scroll-event от Flutter-клиента."""
        item_extent = self._track_item_extent()
        page_height = float(self.page.height or self.page.window.height or 640)
        reserved = 150 if self.player_bar.visible else 115
        return max(item_extent * 3, page_height - reserved)

    def _on_library_scroll(self, event: ft.OnScrollEvent) -> None:
        """Запоминает реальные метрики ListView для точной центровки."""
        self._library_viewport_extent = max(0.0, event.viewport_dimension)
        self._library_max_scroll_extent = max(0.0, event.max_scroll_extent)

    async def _select_library_track(self, track_id: int) -> None:
        """Выделяет трек и плавно размещает его по центру списка."""
        previous_id = self._selected_track_id
        self._selected_track_id = track_id

        listing = self._library_list
        index = self._library_track_indices.get(track_id)
        if listing is None or index is None or self.navigation.selected_index != 0:
            return

        updates: list[ft.Control] = []
        if previous_id is not None and previous_id != track_id:
            previous_card = self._track_row_cards.get(previous_id)
            if previous_card is not None:
                previous_card.bgcolor = ft.Colors.SURFACE_CONTAINER_LOW
                updates.append(previous_card)

        current_card = self._track_row_cards.get(track_id)
        if current_card is not None:
            current_card.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGH
            updates.append(current_card)
        if updates:
            self.page.update(*updates)

        item_extent = self._track_item_extent()
        viewport = (
            self._library_viewport_extent
            if self._library_viewport_extent > 0
            else self._estimated_library_viewport_extent()
        )
        estimated_max = max(
            0.0,
            len(self._library_track_indices) * item_extent - viewport,
        )
        max_scroll = max(self._library_max_scroll_extent, estimated_max)
        offset = centered_scroll_offset(
            index=index,
            item_extent=item_extent,
            viewport_extent=viewport,
            max_scroll_extent=max_scroll,
        )
        await listing.scroll_to(
            offset=offset,
            duration=COMPACT_UI.track_center_scroll_ms,
            curve=ft.AnimationCurve.EASE_IN_OUT_CUBIC,
        )

    def _track_row(self, track: TrackRecord) -> ft.Control:
        metadata_block = ft.Column(
            controls=[
                self._track_title_row(track),
                self._track_details_row(track),
                self._track_path_link(track),
            ],
            expand=True,
            height=self._library_size(COMPACT_UI.track_icon_box),
            spacing=0,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        card = self._surface_card(
            ft.Row(
                controls=[
                    self._track_artwork(track),
                    metadata_block,
                    self._track_waveform(track),
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=self._library_size(COMPACT_UI.action_icon_size),
                        padding=self._library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Редактировать теги",
                        on_click=lambda _, track_id=track.id: self._open_tag_editor(
                            track_id
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
                        icon_size=self._library_size(COMPACT_UI.action_icon_size),
                        padding=self._library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Организовать файл",
                        on_click=lambda _, track_id=track.id: self._open_organizer(
                            track_id
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SEARCH,
                        icon_size=self._library_size(COMPACT_UI.action_icon_size),
                        padding=self._library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Найти метаданные",
                        on_click=lambda _, track_id=track.id: (
                            self._start_metadata_search(track_id)
                        ),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=self._library_size(COMPACT_UI.space_sm),
            ),
            padding=self._library_size(COMPACT_UI.card_padding),
        )
        card.bgcolor = (
            ft.Colors.SURFACE_CONTAINER_HIGH
            if self._selected_track_id == track.id
            else ft.Colors.SURFACE_CONTAINER_LOW
        )
        self._track_row_cards[track.id] = card
        return ft.Container(
            height=self._track_item_extent(),
            padding=ft.Padding.only(
                bottom=self._library_size(COMPACT_UI.space_sm)
            ),
            content=ft.GestureDetector(
                content=card,
                on_tap=lambda _, track_id=track.id: self.page.run_task(
                    self._select_library_track, track_id
                ),
                on_double_tap=lambda _, track_id=track.id: self._open_tag_editor(
                    track_id
                ),
                mouse_cursor=ft.MouseCursor.CLICK,
            ),
        )

    def _track_title_row(self, track: TrackRecord) -> ft.Control:
        artist = track.metadata.artist.strip() or "Unknown Artist"
        title = track.metadata.title.strip() or track.path.stem
        return ft.Row(
            controls=[
                ft.Text(
                    f"{artist} - {title}",
                    expand=True,
                    expand_loose=True,
                    weight=ft.FontWeight.BOLD,
                    size=self._library_size(COMPACT_UI.font_sm, minimum=6.0),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                self._track_tag(self._track_key_label(track), accent=True),
                self._track_tag(self._track_bpm_label(track), accent=True),
            ],
            spacing=self._library_size(COMPACT_UI.space_xs),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _track_details_row(self, track: TrackRecord) -> ft.Control:
        album = track.metadata.album.strip() or "Альбом —"
        controls: list[ft.Control] = [
            ft.Text(
                self._format_duration(track.technical.duration),
                size=self._library_size(COMPACT_UI.font_xs, minimum=5.5),
                color=ft.Colors.ON_SURFACE,
            )
        ]
        controls.extend(
            self._track_tag(label) for label in self._track_detail_tags(track)
        )
        controls.append(
            ft.Text(
                album,
                expand=True,
                size=self._library_size(COMPACT_UI.font_xs, minimum=5.5),
                color=ft.Colors.ON_SURFACE_VARIANT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
        return ft.Row(
            controls=controls,
            spacing=self._library_size(COMPACT_UI.space_xs),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _track_path_link(self, track: TrackRecord) -> ft.Control:
        return ft.GestureDetector(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.FOLDER_OPEN_OUTLINED,
                        size=self._library_size(COMPACT_UI.font_xs, minimum=5.5),
                        color=ft.Colors.PRIMARY,
                    ),
                    ft.Text(
                        str(track.path),
                        expand=True,
                        size=self._library_size(COMPACT_UI.font_micro, minimum=5.0),
                        color=ft.Colors.PRIMARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=self._library_size(COMPACT_UI.space_xs),
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_double_tap=lambda _, current=track: self._reveal_track_file(current),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _track_tag(self, label: str, *, accent: bool = False) -> ft.Control:
        return ft.Container(
            height=self._library_size(12, minimum=8.0),
            padding=ft.Padding.symmetric(horizontal=self._library_size(4)),
            border_radius=self._library_size(4),
            bgcolor=(
                ft.Colors.PRIMARY_CONTAINER
                if accent
                else ft.Colors.SURFACE_CONTAINER_HIGHEST
            ),
            alignment=ft.Alignment.CENTER,
            animate=ft.Animation(
                duration=120,
                curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            ),
            on_hover=(
                DJMakerUI._on_accent_track_tag_hover
                if accent
                else DJMakerUI._on_neutral_track_tag_hover
            ),
            content=ft.Text(
                label,
                size=self._library_size(COMPACT_UI.font_micro, minimum=5.0),
                color=(
                    ft.Colors.ON_PRIMARY_CONTAINER
                    if accent
                    else ft.Colors.ON_SURFACE_VARIANT
                ),
                max_lines=1,
            ),
        )

    @staticmethod
    def _on_accent_track_tag_hover(event: ft.Event[ft.Container]) -> None:
        """Усиливает акцентный тег при наведении без обновления строки."""
        hovered = bool(event.data)
        event.control.bgcolor = (
            ft.Colors.PRIMARY if hovered else ft.Colors.PRIMARY_CONTAINER
        )
        if isinstance(event.control.content, ft.Text):
            event.control.content.color = (
                ft.Colors.ON_PRIMARY
                if hovered
                else ft.Colors.ON_PRIMARY_CONTAINER
            )
        event.control.update()

    @staticmethod
    def _on_neutral_track_tag_hover(event: ft.Event[ft.Container]) -> None:
        """Подсвечивает технический тег цветами активной темы."""
        hovered = bool(event.data)
        event.control.bgcolor = (
            ft.Colors.PRIMARY_CONTAINER
            if hovered
            else ft.Colors.SURFACE_CONTAINER_HIGHEST
        )
        if isinstance(event.control.content, ft.Text):
            event.control.content.color = (
                ft.Colors.ON_PRIMARY_CONTAINER
                if hovered
                else ft.Colors.ON_SURFACE_VARIANT
            )
        event.control.update()

    @staticmethod
    def _track_bpm_label(track: TrackRecord) -> str:
        bpm = track.analysis.bpm if track.analysis is not None else None
        if bpm is None:
            bpm = track.metadata.bpm
        return f"{bpm:.1f} BPM" if bpm is not None else "BPM —"

    @staticmethod
    def _track_key_label(track: TrackRecord) -> str:
        analysis = track.analysis
        if analysis is not None:
            key = " ".join(
                part for part in (analysis.musical_key, analysis.scale) if part
            )
            parts = [part for part in (key, analysis.camelot) if part]
            if parts:
                return " · ".join(parts)
        return track.metadata.musical_key.strip() or "Key —"

    @staticmethod
    def _track_detail_tags(track: TrackRecord) -> tuple[str, ...]:
        tags: list[str] = []
        year = track.metadata.year.strip()
        if year:
            tags.append(year)

        tags.append((track.extension.lstrip(".") or "audio").upper())

        technical = track.technical
        if technical.bitrate:
            tags.append(f"{round(technical.bitrate / 1000)} kbps")
        if technical.sample_rate:
            tags.append(f"{technical.sample_rate / 1000:g} kHz")
        if technical.channels:
            channel_label = (
                "Mono"
                if technical.channels == 1
                else "Stereo"
                if technical.channels == 2
                else f"{technical.channels} ch"
            )
            tags.append(channel_label)
        return tuple(tags)

    def _reveal_track_file(self, track: TrackRecord) -> None:
        try:
            reveal_file(track.path)
        except OSError as exc:
            LOGGER.warning(
                "Не удалось открыть расположение файла %s: %s",
                track.path,
                exc,
            )
            self._notify("Не удалось открыть папку с файлом")

    def _track_artwork(self, track: TrackRecord) -> ft.Control:
        """Показывает кликабельную обложку с embedded/online fallback."""
        fallback = ft.Container(
            width=self._library_size(COMPACT_UI.track_icon_box),
            height=self._library_size(COMPACT_UI.track_icon_box),
            border_radius=self._library_size(6),
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(
                ft.Icons.MUSIC_NOTE,
                size=self._library_size(COMPACT_UI.track_icon_size),
                color=ft.Colors.ON_PRIMARY_CONTAINER,
            ),
        )

        def image(source: str, error_content: ft.Control) -> ft.Image:
            return ft.Image(
                src=source,
                width=self._library_size(COMPACT_UI.track_icon_box),
                height=self._library_size(COMPACT_UI.track_icon_box),
                fit=ft.BoxFit.COVER,
                border_radius=self._library_size(6),
                error_content=error_content,
                cache_width=128,
                cache_height=128,
                semantics_label="Обложка альбома",
            )

        artwork_url = (track.artwork_url or "").strip()
        remote = image(artwork_url, fallback) if artwork_url else fallback
        embedded = track.embedded_artwork_path
        artwork: ft.Control = (
            image(str(embedded), remote)
            if embedded is not None and embedded.is_file()
            else remote
        )
        return ft.GestureDetector(
            content=artwork,
            on_tap=lambda _, track_id=track.id: self.page.run_task(
                self._play_track, track_id, 0
            ),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _track_waveform(self, track: TrackRecord) -> ft.Control:
        peaks = track.waveform.peaks if track.waveform is not None else ()
        values = list(peaks[: COMPACT_UI.waveform_bar_count])
        if len(values) < COMPACT_UI.waveform_bar_count:
            values.extend([0.0] * (COMPACT_UI.waveform_bar_count - len(values)))
        normalized_peaks = tuple(
            min(1.0, max(0.0, float(peak))) for peak in values
        )

        played = 0
        if self._player_track_id == track.id:
            duration = self._player_duration_ms
            fraction = (self._player_position_ms / duration) if duration > 0 else 0.0
            played = min(
                len(normalized_peaks),
                max(0, round(len(normalized_peaks) * fraction)),
            )

        base_image = ft.Image(
            src=self._waveform_svg(normalized_peaks),
            width=self._waveform_width(),
            height=self._library_size(COMPACT_UI.waveform_height),
            fit=ft.BoxFit.FILL,
            color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            exclude_from_semantics=True,
        )
        progress_image = ft.Image(
            src=self._waveform_svg(normalized_peaks, played),
            width=self._waveform_width(),
            height=self._library_size(COMPACT_UI.waveform_height),
            fit=ft.BoxFit.FILL,
            color=ft.Colors.PRIMARY,
            exclude_from_semantics=True,
        )
        self._waveform_views[track.id] = _WaveformView(
            peaks=normalized_peaks,
            progress_image=progress_image,
            played_bars=played,
        )

        waveform = ft.Container(
            width=self._waveform_width(),
            height=self._library_size(COMPACT_UI.track_icon_box),
            alignment=ft.Alignment.CENTER,
            content=ft.Stack(
                controls=[base_image, progress_image],
                width=self._waveform_width(),
                height=self._library_size(COMPACT_UI.waveform_height),
            ),
        )
        return ft.GestureDetector(
            content=waveform,
            on_tap=lambda event, current=track: self._play_from_waveform(
                event, current
            ),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def show_folders(self) -> None:
        """Отображает корневые папки и действия сканирования."""
        self._set_navigation_index(1)
        try:
            roots = self.service.roots()
        except RuntimeError as exc:
            self._notify(str(exc))
            return

        drop_message = (
            "Перетащите в окно DJMAKER файлы или папки — неподдерживаемое "
            "будет отфильтровано автоматически."
            if self._native_drop_enabled()
            else (
                "Native Drag&Drop доступен в desktop debug/build. "
                "Обычный python -m djmaker продолжает работать без extension."
            )
        )
        controls: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Text(f"Добавлено папок: {len(roots)}", weight=ft.FontWeight.BOLD),
                    ft.Button(
                        content="Добавить и сканировать",
                        icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
                        on_click=self._pick_and_scan,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            self._surface_card(
                ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.DRIVE_FOLDER_UPLOAD,
                            color=ft.Colors.PRIMARY,
                            size=COMPACT_UI.action_icon_size,
                        ),
                        ft.Text(
                            drop_message,
                            size=COMPACT_UI.font_xs,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            expand=True,
                        ),
                    ],
                    spacing=COMPACT_UI.space_sm,
                )
            ),
        ]
        if not roots:
            controls.append(
                self._empty_state(
                    ft.Icons.FOLDER_OUTLINED,
                    "Нет музыкальных папок",
                    "Выберите корневую папку с музыкой. DJMAKER проиндексирует поддерживаемые файлы.",
                )
            )
        for root in roots:
            controls.append(
                self._surface_card(
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.FOLDER_OUTLINED, color=ft.Colors.PRIMARY),
                            ft.Column(
                                controls=[
                                    ft.Text(root.name or str(root), weight=ft.FontWeight.BOLD),
                                    ft.Text(str(root), size=COMPACT_UI.font_xs),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.Button(
                                content="Сканировать",
                                icon=ft.Icons.REFRESH,
                                on_click=lambda _, path=root: self._scan_existing(path),
                            ),
                        ]
                    )
                )
            )
        self._replace_content(
            "Папки",
            "Источники медиатеки и повторное сканирование",
            *controls,
        )

    async def _pick_and_scan(self, _: object) -> None:
        try:
            selected = await ft.FilePicker().get_directory_path(
                dialog_title="Выберите папку с музыкой"
            )
        except Exception as exc:
            LOGGER.exception("Ошибка FilePicker")
            self._notify(f"Не удалось открыть выбор папки: {exc}")
            return
        if not selected:
            return
        await self._run_scan(Path(selected))

    def _scan_existing(self, path: Path) -> None:
        self.page.run_task(self._run_scan, path)

    async def _run_scan(self, path: Path, task_id: str | None = None) -> None:
        root = path.expanduser().resolve()
        task: ManagedTask
        if task_id is None:
            task = self.tasks.create(
                kind=TaskKind.LIBRARY_SCAN,
                title=f"Сканирование: {root.name or root}",
                detail=str(root),
            )
            task_id = task.id
            self._scan_task_paths[task_id] = root
        else:
            task = self.tasks.get(task_id)  # type: ignore[assignment]
            if task is None:
                return

        self._refresh_task_indicator()
        self.status.value = f"Сканирование: {root}"
        self.page.update()

        def update_progress(stats: object, current_path: Path) -> None:
            discovered = int(getattr(stats, "discovered", 0))
            updated = int(getattr(stats, "updated", 0))
            unchanged = int(getattr(stats, "unchanged", 0))
            errors = int(getattr(stats, "errors", 0))
            task.set_progress(
                completed=updated + unchanged + errors,
                succeeded=updated + unchanged,
                failed=errors,
                detail=f"{current_path.name} · найдено: {discovered}",
            )

        try:
            stats = await self.workers.run(
                self.service.scan_folder,
                root,
                task=task,
                progress=update_progress,
            )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            self._notify(f"Сканирование остановлено: {root}")
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
            self._notify(f"Сканирование отменено: {root}")
        except Exception as exc:
            LOGGER.exception("Ошибка сканирования")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
            self._notify(f"Ошибка сканирования: {exc}")
        else:
            task.set_progress(
                completed=stats.updated + stats.unchanged + stats.errors,
                succeeded=stats.updated + stats.unchanged,
                failed=stats.errors,
                detail=f"Завершено · найдено: {stats.discovered}",
            )
            task.mark_completed()
            self._forget_task_context(task_id)
            self._notify(
                "Сканирование завершено: "
                f"найдено {stats.discovered}, обновлено {stats.updated}, "
                f"без изменений {stats.unchanged}, удалено {stats.removed}, "
                f"пропущено {stats.ignored}, ошибок {stats.errors}"
            )
            if self.navigation.selected_index == 1:
                self.show_folders()
            if self.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS) is None:
                self.page.run_task(self.ensure_waveforms)
        finally:
            self._refresh_task_indicator()
            self.page.update()

    async def _run_drop_import(
        self,
        paths: tuple[Path, ...],
        task_id: str | None = None,
    ) -> None:
        """Импортирует dropped-файлы/папки через общий scanner/task pipeline."""
        task: ManagedTask
        if task_id is None:
            if (
                self.tasks.active_for_kind(TaskKind.LIBRARY_SCAN) is not None
                or self.tasks.active_for_kind(TaskKind.LIBRARY_IMPORT) is not None
            ):
                self._notify(
                    "Drag&Drop: дождитесь завершения текущего сканирования "
                    "или остановите его в «Задачах»"
                )
                return

            plan = self.service.plan_import_paths(list(paths))
            if plan.accepted_count == 0:
                self._notify(
                    "Drag&Drop: поддерживаемых аудиофайлов или папок не найдено "
                    f"· пропущено: {plan.ignored_count}"
                )
                return

            task = self.tasks.create(
                kind=TaskKind.LIBRARY_IMPORT,
                title="Drag&Drop импорт",
                detail=(
                    f"папок: {len(plan.directories)} · файлов: {len(plan.files)} "
                    f"· сразу пропущено: {plan.ignored_count}"
                ),
            )
            task_id = task.id
            self._drop_task_paths[task_id] = tuple(paths)
        else:
            task = self.tasks.get(task_id)  # type: ignore[assignment]
            if task is None:
                return

        self._refresh_task_indicator()
        self.status.value = "Drag&Drop: импорт файлов и папок"
        self.page.update()

        def update_progress(stats: object, current_path: Path) -> None:
            discovered = int(getattr(stats, "discovered", 0))
            updated = int(getattr(stats, "updated", 0))
            unchanged = int(getattr(stats, "unchanged", 0))
            errors = int(getattr(stats, "errors", 0))
            ignored = int(getattr(stats, "ignored", 0))
            task.set_progress(
                completed=updated + unchanged + errors + ignored,
                succeeded=updated + unchanged,
                failed=errors,
                detail=(
                    f"{current_path.name} · аудио: {discovered} "
                    f"· пропущено: {ignored}"
                ),
            )

        try:
            stats = await self.workers.run(
                self.service.import_paths,
                list(paths),
                task=task,
                progress=update_progress,
            )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            self._notify("Drag&Drop импорт остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
            self._notify("Drag&Drop импорт отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка Drag&Drop импорта")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
            self._notify(f"Ошибка Drag&Drop импорта: {exc}")
        else:
            task.set_progress(
                completed=(
                    stats.updated
                    + stats.unchanged
                    + stats.errors
                    + stats.ignored
                ),
                succeeded=stats.updated + stats.unchanged,
                failed=stats.errors,
                detail=(
                    f"Завершено · аудио: {stats.discovered} "
                    f"· пропущено: {stats.ignored}"
                ),
            )
            task.mark_completed()
            self._forget_task_context(task_id)
            self._notify(
                "Drag&Drop завершён: "
                f"аудио {stats.discovered}, обновлено {stats.updated}, "
                f"без изменений {stats.unchanged}, пропущено {stats.ignored}, "
                f"ошибок {stats.errors}"
            )
            if self.navigation.selected_index == 0:
                self.show_library()
            elif self.navigation.selected_index == 1:
                self.show_folders()
            if self.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS) is None:
                self.page.run_task(self.ensure_waveforms)
        finally:
            self._refresh_task_indicator()
            self.page.update()

    def show_duplicates(self) -> None:
        """Показывает точные дубликаты по SHA-256."""
        self._set_navigation_index(2)
        try:
            groups = self.service.exact_duplicates()
        except RuntimeError as exc:
            self._notify(str(exc))
            return

        controls: list[ft.Control] = [
            self._surface_card(
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.FINGERPRINT, color=ft.Colors.PRIMARY),
                        ft.Text(
                            "Текущий режим находит только файлы с полностью идентичными байтами (SHA-256).",
                            expand=True,
                        ),
                    ]
                )
            )
        ]
        if not groups:
            controls.append(
                self._empty_state(
                    ft.Icons.CONTENT_COPY_OUTLINED,
                    "Точные дубликаты не найдены",
                    "Второй уровень сравнения по аудио-fingerprint будет добавлен отдельным модулем.",
                )
            )
        for index, group in enumerate(groups, start=1):
            total_size = sum(track.size for track in group.tracks)
            rows: list[ft.Control] = [
                ft.Text(
                    f"Группа {index} · {len(group.tracks)} файлов · {self._format_size(total_size)}",
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(f"SHA-256: {group.file_hash}", size=COMPACT_UI.font_micro),
            ]
            rows.extend(ft.Text(f"• {track.path}", size=COMPACT_UI.font_xs) for track in group.tracks)
            controls.append(self._surface_card(ft.Column(controls=rows, spacing=5)))

        self._replace_content(
            "Дубликаты",
            "Поиск идентичных файлов в медиатеке",
            *controls,
        )

    def show_plugins(self) -> None:
        """Показывает подключённые внешние провайдеры."""
        self._set_navigation_index(3)
        providers = self.service.plugins.all()
        controls: list[ft.Control] = [
            self._surface_card(
                ft.Text(
                    "Каждый внешний каталог или DJ-пул подключается отдельным плагином. "
                    "Ядро медиатеки от конкретного сервиса не зависит."
                )
            )
        ]
        for provider in providers:
            controls.append(
                self._surface_card(
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.PRIMARY),
                            ft.Column(
                                controls=[
                                    ft.Text(provider.display_name, weight=ft.FontWeight.BOLD),
                                    ft.Text(provider.provider_id, size=COMPACT_UI.font_xs),
                                ],
                                expand=True,
                                spacing=1,
                            ),
                            ft.Text("Подключён", size=COMPACT_UI.font_xs),
                        ]
                    )
                )
            )
        if not providers:
            controls.append(
                self._empty_state(
                    ft.Icons.CLOUD_OFF_OUTLINED,
                    "Нет подключённых провайдеров",
                    "Онлайн-каталоги появятся здесь после подключения плагинов.",
                )
            )
        self._replace_content(
            "Онлайн-метаданные",
            "Плагины музыкальных каталогов и DJ-пулов",
            *controls,
        )

    def show_audio_modules(self) -> None:
        """Показывает доступные DSP-модули и управление анализом."""
        self._set_navigation_index(4)
        try:
            total, analyzed = self.service.analysis_counts()
            waveform_total, waveform_analyzed = self.service.waveform_counts()
        except RuntimeError as exc:
            self._notify(str(exc))
            return

        pending = max(0, total - analyzed)
        waveform_pending = max(0, waveform_total - waveform_analyzed)
        analysis_card = self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SPEED, size=18, color=ft.Colors.PRIMARY),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        "BPM / Key / Camelot",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Text(
                                        "FFmpeg → mono 44.1 kHz float32 → DJMAKER Essentia "
                                        "· параллельный worker-пул",
                                        size=COMPACT_UI.font_xs,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.Text(
                                f"Готово: {analyzed}/{total} · в очереди: {pending}",
                                size=COMPACT_UI.font_xs,
                            ),
                        ]
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Анализировать новые",
                                icon=ft.Icons.SPEED,
                                on_click=self._start_audio_analysis,
                            ),
                            ft.Button(
                                content="Пересчитать всё",
                                icon=ft.Icons.REFRESH,
                                on_click=lambda event: self._start_audio_analysis(
                                    event,
                                    force=True,
                                ),
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                    ft.Text(
                        "Результат хранится отдельно от тегов файла: BPM, обычная "
                        "тональность, лад, Camelot и confidence Essentia.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

        waveform_card = self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.GRAPHIC_EQ,
                                size=18,
                                color=ft.Colors.PRIMARY,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text("Waveform", weight=ft.FontWeight.BOLD),
                                    ft.Text(
                                        "FFmpeg → mono PCM → 60 нормализованных вертикальных полос",
                                        size=COMPACT_UI.font_xs,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.Text(
                                f"Готово: {waveform_analyzed}/{waveform_total} · "
                                f"в очереди: {waveform_pending}",
                                size=COMPACT_UI.font_xs,
                            ),
                        ]
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Построить новые",
                                icon=ft.Icons.GRAPHIC_EQ,
                                on_click=self._start_waveform_analysis,
                            ),
                            ft.Button(
                                content="Перестроить всё",
                                icon=ft.Icons.REFRESH,
                                on_click=lambda event: self._start_waveform_analysis(
                                    event,
                                    force=True,
                                ),
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

        planned = (
            (
                ft.Icons.VOLUME_UP_OUTLINED,
                "Нормализация",
                f"Отдельный этап · основной target {DEFAULT_TARGET_LUFS} LUFS",
            ),
            (
                ft.Icons.FINGERPRINT,
                "Audio fingerprint",
                "Второй уровень поиска музыкальных дубликатов",
            ),
        )
        controls: list[ft.Control] = [analysis_card, waveform_card]
        controls.extend(
            self._surface_card(
                ft.Row(
                    controls=[
                        ft.Icon(icon, size=18, color=ft.Colors.PRIMARY),
                        ft.Column(
                            controls=[
                                ft.Text(title, weight=ft.FontWeight.BOLD),
                                ft.Text(description, size=COMPACT_UI.font_xs),
                            ],
                            expand=True,
                            spacing=2,
                        ),
                        ft.Text("Запланировано", size=COMPACT_UI.font_xs),
                    ]
                )
            )
            for icon, title, description in planned
        )
        self._replace_content(
            "Аудио-модули",
            "Независимые этапы анализа и обработки аудио",
            *controls,
        )

    def _start_audio_analysis(self, _: object, *, force: bool = False) -> None:
        existing = self.tasks.active_for_kind(TaskKind.AUDIO_ANALYSIS)
        if existing is not None:
            self._notify(
                "BPM / Key уже выполняется или остановлен. "
                "Откройте «Задачи» для управления."
            )
            return

        task = self.tasks.create(
            kind=TaskKind.AUDIO_ANALYSIS,
            title="BPM / Key анализ",
            detail="Подготовка FFmpeg и Essentia...",
        )
        self._audio_task_contexts[task.id] = _AudioTaskContext(force=force)
        self._refresh_task_indicator()
        self.page.run_task(self._run_audio_analysis, task.id)

    async def _run_audio_analysis(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        context = self._audio_task_contexts.get(task_id)
        if task is None or context is None:
            return

        self.analysis_progress.visible = True
        self.analysis_progress.value = task.snapshot().progress or 0
        self.analysis_progress_text.visible = True
        self._set_status("Подготовка FFmpeg и Essentia...")

        try:
            task.checkpoint()
            report = await self.workers.run(self.runtime.ensure_all)
            self.runtime_report = report
            task.checkpoint()
            if not report.ffmpeg.available:
                raise RuntimeError(f"FFmpeg недоступен: {report.ffmpeg.detail}")
            if self.runtime.essentia_analyzer_path() is None:
                raise RuntimeError(
                    "Собственный DJMAKER Essentia runtime недоступен. "
                    "Откройте Настройки → Аудио-компоненты."
                )

            if context.pending_ids is None:
                tracks = await self.workers.run(
                    self.service.tracks_for_analysis,
                    force=context.force,
                )
                context.pending_ids = {track.id for track in tracks}
                context.labels = {track.id: str(track.path) for track in tracks}
                task.set_progress(
                    total=len(tracks),
                    detail="Очередь BPM / Key подготовлена",
                )

            pending_ids = context.pending_ids
            if not pending_ids:
                task.mark_completed("Все треки уже проанализированы")
                self._forget_task_context(task_id)
                self._notify("Все треки уже проанализированы")
                return

            total = task.snapshot().total or len(pending_ids)
            parallelism = min(
                recommended_analysis_concurrency(),
                self.workers.max_workers,
                len(pending_ids),
            )
            task.set_progress(
                detail=f"BPM / Key · потоков: {parallelism}"
            )
            self.analysis_progress_text.value = (
                f"{task.snapshot().completed}/{total} · потоков: {parallelism}"
            )
            self.page.update()

            def analyze_one(track_id: int) -> TrackRecord:
                task.checkpoint()
                return self.service.analyze_track(track_id, task=task)

            async for outcome in self.workers.run_many_unordered(
                analyze_one,
                list(pending_ids),
                max_concurrency=parallelism,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(outcome.item, f"track_id={outcome.item}")
                pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось проанализировать %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    task.advance(success=True, detail=label)

                snapshot = task.snapshot()
                self.status.value = (
                    f"BPM / Key: {snapshot.completed}/{total} · "
                    f"потоков: {parallelism}"
                )
                self.analysis_progress.value = snapshot.progress or 0
                self.analysis_progress_text.value = (
                    f"{snapshot.completed}/{total} · потоков: {parallelism}"
                )
                self.page.update()

            if task.cancel_requested:
                task.mark_cancelled()
                self._forget_task_context(task_id)
                self._notify("BPM / Key анализ отменён")
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
                self._notify("BPM / Key анализ остановлен")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} успешно, "
                    f"{snapshot.failed} ошибок"
                )
                self._forget_task_context(task_id)
                self._notify(
                    "Аудио-анализ завершён: "
                    f"{snapshot.succeeded} успешно, {snapshot.failed} ошибок"
                )
                if self.navigation.selected_index == 0:
                    self.show_library()
                elif self.navigation.selected_index == 4:
                    self.show_audio_modules()
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            self._notify("BPM / Key анализ остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
            self._notify("BPM / Key анализ отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка пакетного аудио-анализа")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
            self._notify(f"Не удалось выполнить BPM / Key анализ: {exc}")
        finally:
            snapshot = task.snapshot()
            running = snapshot.status in {
                TaskStatus.RUNNING,
                TaskStatus.STOPPING,
                TaskStatus.CANCELLING,
            }
            self.analysis_progress.visible = running
            self.analysis_progress_text.visible = running
            self._refresh_task_indicator()
            self.page.update()

    def _start_waveform_analysis(self, _: object, *, force: bool = False) -> None:
        existing = self.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS)
        if existing is not None:
            self._notify(
                "Waveform уже строится или остановлен. "
                "Откройте «Задачи» для управления."
            )
            return

        task = self.tasks.create(
            kind=TaskKind.WAVEFORM_ANALYSIS,
            title="Анализ Waveform",
            detail="Подготовка FFmpeg...",
        )
        self._waveform_task_contexts[task.id] = _WaveformTaskContext(force=force)
        self._refresh_task_indicator()
        self.page.run_task(self._run_waveform_analysis, task.id)

    async def ensure_waveforms(self) -> None:
        """Автоматически строит отсутствующие waveform после запуска приложения."""
        existing = self.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS)
        if existing is not None:
            return
        try:
            tracks = await self.workers.run(
                self.service.tracks_for_waveform_analysis,
                force=False,
            )
        except Exception:
            LOGGER.exception("Не удалось получить очередь waveform")
            return
        if not tracks:
            return

        task = self.tasks.create(
            kind=TaskKind.WAVEFORM_ANALYSIS,
            title="Анализ Waveform",
            detail="Подготовка FFmpeg...",
            total=len(tracks),
        )
        self._waveform_task_contexts[task.id] = _WaveformTaskContext(
            force=False,
            pending_ids={track.id for track in tracks},
            labels={track.id: str(track.path) for track in tracks},
        )
        self._refresh_task_indicator()
        await self._run_waveform_analysis(task.id)

    async def _run_waveform_analysis(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        context = self._waveform_task_contexts.get(task_id)
        if task is None or context is None:
            return

        changed = False
        try:
            task.checkpoint()
            report = await self.workers.run(self.runtime.ensure_all)
            self.runtime_report = report
            task.checkpoint()
            if not report.ffmpeg.available:
                raise RuntimeError(f"FFmpeg недоступен: {report.ffmpeg.detail}")

            if context.pending_ids is None:
                tracks = await self.workers.run(
                    self.service.tracks_for_waveform_analysis,
                    force=context.force,
                )
                context.pending_ids = {track.id for track in tracks}
                context.labels = {track.id: str(track.path) for track in tracks}
                task.set_progress(
                    total=len(tracks),
                    detail="Очередь waveform подготовлена",
                )

            pending_ids = context.pending_ids
            if not pending_ids:
                task.mark_completed("Все waveform уже построены")
                self._forget_task_context(task_id)
                return

            concurrency = min(1, self.workers.max_workers, len(pending_ids))
            task.set_progress(detail=f"Waveform · потоков: {concurrency}")

            def analyze_one(track_id: int) -> TrackRecord:
                task.checkpoint()
                return self.service.analyze_waveform(track_id, task=task)

            async for outcome in self.workers.run_many_unordered(
                analyze_one,
                list(pending_ids),
                max_concurrency=concurrency,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(outcome.item, f"track_id={outcome.item}")
                pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось построить waveform %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    changed = True
                    task.advance(success=True, detail=label)

            if task.cancel_requested:
                task.mark_cancelled()
                self._forget_task_context(task_id)
                self._notify("Анализ waveform отменён")
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
                self._notify("Анализ waveform остановлен")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} waveform, "
                    f"{snapshot.failed} ошибок"
                )
                self._forget_task_context(task_id)
                self._notify(
                    "Waveform-анализ завершён: "
                    f"{snapshot.succeeded} успешно, {snapshot.failed} ошибок"
                )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            self._notify("Анализ waveform остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
            self._notify("Анализ waveform отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка пакетного waveform-анализа")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
            self._notify(f"Не удалось выполнить waveform-анализ: {exc}")
        finally:
            self._refresh_task_indicator()
            if changed and self.navigation.selected_index == 0:
                self.show_library()
            elif self.navigation.selected_index == 4:
                self.show_audio_modules()
            else:
                self.page.update()

    @staticmethod
    def _analysis_label(track: TrackRecord) -> str:
        analysis = track.analysis
        if analysis is None:
            return "BPM / Key: —"
        bpm = f"{analysis.bpm:.1f} BPM" if analysis.bpm is not None else "— BPM"
        key = " ".join(part for part in (analysis.musical_key, analysis.scale) if part)
        parts = [bpm, key or "—"]
        if analysis.camelot:
            parts.append(analysis.camelot)
        return " · ".join(parts)

    async def ensure_embedded_artwork(self) -> None:
        """Индексирует встроенные обложки как управляемую фоновую задачу."""
        existing = self.tasks.active_for_kind(TaskKind.ARTWORK_INDEX)
        if existing is not None:
            return
        try:
            tracks = await self.workers.run(self.service.tracks_for_artwork_refresh)
        except Exception:
            LOGGER.exception("Не удалось получить очередь встроенных обложек")
            return
        if not tracks:
            return

        task = self.tasks.create(
            kind=TaskKind.ARTWORK_INDEX,
            title="Индексирование встроенных обложек",
            detail="Подготовка очереди...",
            total=len(tracks),
        )
        self._artwork_task_contexts[task.id] = _BatchTaskContext(
            pending_ids={track.id for track in tracks},
            labels={track.id: str(track.path) for track in tracks},
        )
        self._refresh_task_indicator()
        await self._run_embedded_artwork(task.id)

    async def _run_embedded_artwork(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        context = self._artwork_task_contexts.get(task_id)
        if task is None or context is None:
            return

        changed = False
        concurrency = min(2, self.workers.max_workers)

        def refresh_one(track_id: int) -> TrackRecord:
            task.checkpoint()
            return self.service.refresh_embedded_artwork(track_id, task=task)

        try:
            async for outcome in self.workers.run_many_unordered(
                refresh_one,
                list(context.pending_ids),
                max_concurrency=concurrency,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(
                    outcome.item, f"track_id={outcome.item}"
                )
                context.pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось прочитать встроенную обложку %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    changed = True
                    task.advance(success=True, detail=label)

            if task.cancel_requested:
                task.mark_cancelled()
                self._forget_task_context(task_id)
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} обложек, "
                    f"{snapshot.failed} ошибок"
                )
                self._forget_task_context(task_id)
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
        except Exception as exc:
            LOGGER.exception("Ошибка индексирования встроенных обложек")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
        finally:
            self._refresh_task_indicator()
            if changed and self.navigation.selected_index == 0:
                self.show_library()
            else:
                self.page.update()

    async def ensure_runtime_dependencies(self) -> None:
        """Фоново проверяет и устанавливает FFmpeg/Essentia при запуске."""
        self._set_status("Проверка FFmpeg и Essentia...")
        try:
            report = await self.workers.run(self.runtime.ensure_all)
        except Exception as exc:
            LOGGER.exception("Ошибка проверки runtime-зависимостей")
            self._notify(f"Ошибка проверки аудио-компонентов: {exc}")
            return

        self.runtime_report = report
        problems = [
            status.name
            for status in (report.ffmpeg, report.essentia)
            if not status.available
        ]
        if problems:
            self._set_status(
                "Аудио-компоненты требуют внимания: " + ", ".join(problems)
            )
        else:
            self._set_status(
                f"FFmpeg {report.ffmpeg.version} · Essentia {report.essentia.version}"
            )

        if self.navigation.selected_index == 6:
            self.show_settings()

    def _retry_runtime_dependencies(self, _: object) -> None:
        """Повторно запускает проверку/установку из экрана настроек."""
        self.page.run_task(self.ensure_runtime_dependencies)

    def _runtime_card(self, report: RuntimeReport) -> ft.Control:
        """Создаёт карточку состояния FFmpeg и Essentia."""
        return self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.CONSTRUCTION, color=ft.Colors.PRIMARY),
                            ft.Text(
                                "Аудио-компоненты",
                                size=COMPACT_UI.font_lg,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Container(expand=True),
                            ft.Button(
                                content="Проверить / установить",
                                icon=ft.Icons.DOWNLOAD,
                                on_click=self._retry_runtime_dependencies,
                            ),
                        ]
                    ),
                    self._runtime_status_row(report.ffmpeg),
                    self._runtime_status_row(report.essentia),
                    ft.Text(
                        "FFmpeg декодирует аудио. Essentia используется как собственная "
                        "lightweight-сборка DJMAKER с KISS FFT и загружается из Releases "
                        "этого репозитория после проверки SHA-256.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

    @staticmethod
    def _runtime_status_row(status: DependencyStatus) -> ft.Control:
        icon = ft.Icons.CHECK_CIRCLE if status.available else ft.Icons.ERROR_OUTLINE
        color = ft.Colors.PRIMARY if status.available else ft.Colors.ERROR
        state = "Готов" if status.available else "Недоступен"
        version = f" · {status.version}" if status.version else ""
        backend = f" · {status.backend}" if status.backend else ""
        detail = status.detail or status.path
        return ft.Row(
            controls=[
                ft.Icon(icon, size=COMPACT_UI.action_icon_size, color=color),
                ft.Text(
                    f"{status.name}: {state}{version}{backend}",
                    weight=ft.FontWeight.BOLD,
                    size=COMPACT_UI.font_sm,
                ),
                ft.Text(
                    detail,
                    expand=True,
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=COMPACT_UI.space_sm,
        )

    def _build_theme_editor(self) -> ft.Container:
        """Строит глобальный редактор цветовых ролей с живым предпросмотром."""
        self._begin_theme_editor_session()
        draft = self._theme_draft_for_mode()
        self._theme_editor_fields.clear()
        self._theme_editor_swatches.clear()

        editor_mode = ft.Dropdown(
            label="Редактируемая схема",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=self._theme_editor_mode,
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in _THEME_EDITOR_MODE_LABELS.items()
            ],
            on_select=self._on_theme_editor_mode_selected,
        )

        def build_role(role: str) -> ft.Control:
            value = draft.get(role, "")
            swatch = ft.Container(
                width=18,
                height=18,
                border_radius=4,
                bgcolor=value or ft.Colors.OUTLINE_VARIANT,
            )
            field = ft.TextField(
                label=_THEME_ROLE_LABELS[role],
                hint_text="#RRGGBB · пусто = наследовать",
                value=value,
                dense=True,
                text_size=COMPACT_UI.font_xs,
                expand=True,
                on_change=lambda event, color_role=role: self._on_theme_color_changed(
                    color_role, event
                ),
            )
            self._theme_editor_fields[role] = field
            self._theme_editor_swatches[role] = swatch
            return ft.Row(
                controls=[swatch, field],
                spacing=COMPACT_UI.space_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

        split = (len(THEME_COLOR_ROLES) + 1) // 2
        left = ft.Column(
            controls=[build_role(role) for role in THEME_COLOR_ROLES[:split]],
            spacing=COMPACT_UI.space_sm,
            expand=True,
        )
        right = ft.Column(
            controls=[build_role(role) for role in THEME_COLOR_ROLES[split:]],
            spacing=COMPACT_UI.space_sm,
            expand=True,
        )

        changed = self._theme_editor_changed_count()
        self._theme_editor_status = ft.Text(
            (
                f"Предпросмотр · несохранённых изменений: {changed}"
                if changed
                else "Предпросмотр совпадает с сохранённой темой"
            ),
            size=COMPACT_UI.font_xs,
            color=ft.Colors.PRIMARY if changed else ft.Colors.ON_SURFACE_VARIANT,
        )

        return self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.TUNE,
                                color=ft.Colors.PRIMARY,
                                size=COMPACT_UI.action_icon_size,
                            ),
                            ft.Text(
                                "Глобальный редактор темы",
                                size=COMPACT_UI.font_lg,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Container(expand=True),
                            editor_mode,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Цвета накладываются поверх выбранной базовой темы. "
                        "Корректный #RRGGBB применяется сразу; пустое поле "
                        "возвращает значение базовой темы.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Row(
                        controls=[left, right],
                        spacing=COMPACT_UI.space_md,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Сохранить",
                                icon=ft.Icons.SAVE_OUTLINED,
                                on_click=self._save_theme_editor,
                            ),
                            ft.Button(
                                content="Вернуть сохранённое",
                                icon=ft.Icons.UNDO,
                                on_click=self._revert_theme_editor,
                            ),
                            ft.Button(
                                content="Сбросить текущую схему",
                                icon=ft.Icons.RESTART_ALT,
                                on_click=self._reset_theme_editor_mode,
                            ),
                            ft.Container(expand=True),
                            self._theme_editor_status,
                        ],
                        spacing=COMPACT_UI.space_sm,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

    def _on_theme_editor_mode_selected(self, event: object) -> None:
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in _THEME_EDITOR_MODE_LABELS:
            return
        self._theme_editor_mode = value
        self.show_settings()
        self._apply_theme_editor_preview()

    def _on_theme_color_changed(self, role: str, event: object) -> None:
        control = getattr(event, "control", None)
        if not isinstance(control, ft.TextField) or role not in THEME_COLOR_ROLES:
            return

        raw = (control.value or "").strip()
        swatch = self._theme_editor_swatches.get(role)
        status = self._theme_editor_status
        if raw and not is_valid_theme_color(raw):
            control.border_color = ft.Colors.ERROR
            if swatch is not None:
                swatch.bgcolor = ft.Colors.ERROR_CONTAINER
            if status is not None:
                status.value = f"{_THEME_ROLE_LABELS[role]}: ожидается #RRGGBB"
                status.color = ft.Colors.ERROR
            updates = [item for item in (control, swatch, status) if item is not None]
            self.page.update(*updates)
            return

        control.border_color = None
        draft = self._theme_draft_for_mode()
        if raw:
            normalized = raw.upper()
            draft[role] = normalized
            if swatch is not None:
                swatch.bgcolor = normalized
        else:
            draft.pop(role, None)
            if swatch is not None:
                swatch.bgcolor = ft.Colors.OUTLINE_VARIANT
        self._apply_theme_editor_preview()

    def _save_theme_editor(self, _: object) -> None:
        invalid = [
            field
            for field in self._theme_editor_fields.values()
            if (field.value or "").strip()
            and not is_valid_theme_color((field.value or "").strip())
        ]
        if invalid:
            self._notify("Исправьте некорректные HEX-цвета перед сохранением")
            return
        self._save_and_apply_settings(self._theme_preview_settings())

    def _revert_theme_editor(self, _: object) -> None:
        self._theme_draft_light = dict(self.settings.theme_light_overrides)
        self._theme_draft_dark = dict(self.settings.theme_dark_overrides)
        self.show_settings()
        self._apply_theme_editor_preview()

    def _reset_theme_editor_mode(self, _: object) -> None:
        self._theme_draft_for_mode().clear()
        self.show_settings()
        self._apply_theme_editor_preview()

    def show_settings(self) -> None:
        """Показывает настройки оформления и локальные пути приложения."""
        self._set_navigation_index(6)
        mode_dropdown = ft.Dropdown(
            label="Режим интерфейса",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=self.settings.theme_mode,
            options=[
                ft.DropdownOption(key=mode, text=THEME_MODE_LABELS[mode])
                for mode in THEME_MODES
            ],
            on_select=self._on_theme_mode_selected,
        )
        palette_dropdown = ft.Dropdown(
            label="Цветовая схема",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=self.settings.theme_palette,
            options=[
                ft.DropdownOption(key=palette.key, text=palette.title)
                for palette in THEME_PALETTES
            ],
            on_select=self._on_theme_palette_selected,
        )

        appearance = self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.PALETTE_OUTLINED, color=ft.Colors.PRIMARY),
                            ft.Text("Оформление", size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                        ]
                    ),
                    ft.Text(
                        "Системный режим автоматически следует настройке Windows или macOS. "
                        "Базовая палитра общая, а пользовательские цвета можно "
                        "настроить отдельно для светлой и тёмной схемы.",
                        size=COMPACT_UI.font_xs,
                    ),
                    mode_dropdown,
                    palette_dropdown,
                    ft.Text(
                        palette_description(self.settings.theme_palette),
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=5,
            )
        )

        theme_editor = self._build_theme_editor()

        storage = self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.STORAGE_OUTLINED, color=ft.Colors.PRIMARY),
                            ft.Text("Локальные данные", size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                        ]
                    ),
                    self._path_setting("Каталог данных", self.paths.data_dir),
                    self._path_setting("База данных", self.paths.database),
                    self._path_setting("Настройки", self.paths.settings_file),
                    self._path_setting("Лог", self.paths.log_file),
                    ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.WARNING_AMBER,
                                color=ft.Colors.ERROR,
                                size=COMPACT_UI.action_icon_size,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        "Обнуление медиатеки",
                                        size=COMPACT_UI.font_sm,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Text(
                                        "Удаляет треки, папки, ошибки сканирования и "
                                        "результаты анализа только из SQLite.",
                                        size=COMPACT_UI.font_micro,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                            ft.Button(
                                content="Обнулить БД",
                                icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                                on_click=self._open_database_reset_dialog,
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
            )
        )

        organizer = self._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.DRIVE_FILE_MOVE_OUTLINED, color=ft.Colors.PRIMARY),
                            ft.Text("Организация файлов", size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                        ]
                    ),
                    ft.Text("Шаблон по умолчанию", size=COMPACT_UI.font_xs),
                    ft.Text(DEFAULT_ORGANIZE_TEMPLATE),
                ],
                spacing=3,
            )
        )

        settings_list = ft.ListView(
            controls=[
                self._runtime_card(self.runtime_report),
                appearance,
                theme_editor,
                storage,
                organizer,
            ],
            expand=True,
            spacing=COMPACT_UI.space_md,
            padding=0,
        )
        self._replace_content(
            "Настройки",
            f"Тема: {THEME_MODE_LABELS[self.settings.theme_mode]} · {palette_title(self.settings.theme_palette)}",
            settings_list,
        )

    def _open_database_reset_dialog(self, _: object) -> None:
        """Запрашивает подтверждение полного сброса SQLite-медиатеки."""
        if self.tasks.active_count() > 0:
            self._notify(
                "Сначала остановите или отмените активные задачи перед обнулением БД"
            )
            return

        async def execute(_: object) -> None:
            if self.tasks.active_count() > 0:
                self._notify(
                    "Обнуление отменено: появились активные фоновые задачи"
                )
                return

            self.page.pop_dialog()
            self._set_busy(True, "Обнуление базы данных...")
            try:
                if self.audio is not None:
                    try:
                        await self.audio.pause()
                        await self.audio.seek(ft.Duration(milliseconds=0))
                    except Exception:
                        LOGGER.debug(
                            "Не удалось остановить плеер перед сбросом БД",
                            exc_info=True,
                        )

                await self.workers.run(self.service.reset_library)
            except Exception as exc:
                LOGGER.exception("Ошибка обнуления БД")
                self._notify(f"Не удалось обнулить БД: {exc}")
            else:
                self._clear_library_runtime_state()
                self._notify("База данных обнулена. Музыкальные файлы не изменялись")
                self.show_library()
            finally:
                self._set_busy(False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Обнулить базу данных?"),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Будут удалены все записи медиатеки: музыкальные папки, "
                        "индекс треков, результаты BPM/Key, waveform, ссылки на "
                        "обложки и журнал ошибок сканирования."
                    ),
                    ft.Text(
                        "Музыкальные файлы, их теги, настройки приложения, лог и "
                        "файлы кэша обложек удаляться не будут.",
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        str(self.paths.database),
                        selectable=True,
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ERROR,
                    ),
                ],
                tight=True,
                spacing=COMPACT_UI.space_sm,
            ),
            actions=[
                ft.Button(
                    content="Отмена",
                    on_click=lambda _: self.page.pop_dialog(),
                ),
                ft.Button(
                    content="Обнулить БД",
                    icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                    on_click=execute,
                ),
            ],
        )
        self.page.show_dialog(dialog)

    def _clear_library_runtime_state(self) -> None:
        """Сбрасывает UI-состояние, связанное с удалёнными записями БД."""
        self._player_request_revision += 1
        self._selected_track_id = None
        self._player_track_id = None
        self._player_track_path = None
        self._player_position_ms = 0
        self._player_duration_ms = 0
        self._player_state = fta.AudioState.STOPPED
        self._player_switching = False
        self._player_load_event = None
        self.player_title.value = ""
        self.player_position.value = "00:00 / 00:00"
        self.player_progress.value = 0.0
        self.player_play_button.icon = ft.Icons.PLAY_ARROW
        self.player_bar.visible = False

        self.search.value = ""
        self.search_clear_button.visible = False
        self._search_revision += 1
        self._waveform_views.clear()
        self._track_row_cards.clear()
        self._library_track_indices.clear()
        self._library_list = None
        self._library_viewport_extent = 0.0
        self._library_max_scroll_extent = 0.0

    @staticmethod
    def _path_setting(label: str, path: Path) -> ft.Control:
        return ft.Column(
            controls=[
                ft.Text(label, size=COMPACT_UI.font_micro),
                ft.Text(str(path), selectable=True, size=COMPACT_UI.font_xs),
            ],
            spacing=0,
        )

    def _on_theme_mode_selected(self, event: object) -> None:
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in THEME_MODES:
            return
        self._save_and_apply_settings(replace(self.settings, theme_mode=value))

    def _on_theme_palette_selected(self, event: object) -> None:
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        palette_keys = {palette.key for palette in THEME_PALETTES}
        if not isinstance(value, str) or value not in palette_keys:
            return
        self._save_and_apply_settings(replace(self.settings, theme_palette=value))

    def _cycle_theme_mode(self, _: object) -> None:
        order = ("system", "light", "dark")
        try:
            index = order.index(self.settings.theme_mode)
        except ValueError:
            index = 0
        next_mode = order[(index + 1) % len(order)]
        self._save_and_apply_settings(replace(self.settings, theme_mode=next_mode))

    def _save_and_apply_settings(self, settings: AppSettings) -> None:
        try:
            self.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить настройки")
            self._notify(f"Не удалось сохранить настройки: {exc}")
            return

        self.settings = settings
        self._theme_editor_active = False
        self._theme_editor_fields.clear()
        self._theme_editor_swatches.clear()
        self._theme_editor_status = None
        apply_app_theme(self.page, self.settings)
        self.theme_button.icon = theme_mode_icon(self.settings.theme_mode)
        self.theme_button.tooltip = self._theme_tooltip()
        self.status.value = (
            f"Тема: {THEME_MODE_LABELS[self.settings.theme_mode]} · "
            f"{palette_title(self.settings.theme_palette)}"
        )

        if self.navigation.selected_index == 6:
            self.show_settings()
        else:
            self.page.update()

    def _theme_tooltip(self) -> str:
        return f"Тема: {THEME_MODE_LABELS[self.settings.theme_mode]}. Нажмите для переключения."

    def _open_tag_editor(self, track_id: int) -> None:
        track = self.service.database.get_track(track_id)
        if track is None:
            self._notify("Трек не найден")
            return

        metadata = track.metadata
        title = ft.TextField(
            label="Название",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.title,
            expand=True,
        )
        artist = ft.TextField(
            label="Исполнитель",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.artist,
            expand=True,
        )
        album = ft.TextField(
            label="Альбом",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.album,
            expand=True,
        )
        album_artist = ft.TextField(
            label="Исполнитель альбома",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.album_artist,
            expand=True,
        )
        genre = ft.TextField(
            label="Жанр",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.genre,
            expand=True,
        )
        year = ft.TextField(
            label="Год",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.year,
            width=110,
        )
        track_no = ft.TextField(
            label="Трек",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=str(metadata.track_number or ""),
            width=90,
        )
        disc_no = ft.TextField(
            label="Диск",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=str(metadata.disc_number or ""),
            width=90,
        )
        bpm = ft.TextField(
            label="BPM (тег)",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=str(metadata.bpm or ""),
            width=120,
        )
        musical_key = ft.TextField(
            label="Key (тег)",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=metadata.musical_key,
            width=150,
        )

        artwork_state = _TagEditorArtworkState()
        artwork_supported = track.path.suffix.lower() in {
            ".mp3",
            ".flac",
            ".m4a",
            ".m4b",
            ".mp4",
        }
        artwork_box = ft.Container(
            width=116,
            height=116,
            border_radius=COMPACT_UI.radius,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            alignment=ft.Alignment.CENTER,
        )
        artwork_status = ft.Text(
            "Встроенная обложка",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
            max_lines=2,
        )
        remove_artwork_button = ft.Button(
            content="Удалить",
            icon=ft.Icons.DELETE_OUTLINE,
            disabled=(
                not artwork_supported
                or track.embedded_artwork_path is None
                or not track.embedded_artwork_path.is_file()
            ),
        )

        def set_artwork_preview(source: str | bytes | None) -> None:
            if source is None:
                artwork_box.content = ft.Icon(
                    ft.Icons.IMAGE_OUTLINED,
                    size=32,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
                return
            artwork_box.content = ft.Image(
                src=source,
                width=116,
                height=116,
                fit=ft.BoxFit.COVER,
                border_radius=COMPACT_UI.radius,
                cache_width=232,
                cache_height=232,
                semantics_label="Встроенная обложка",
            )

        embedded = track.embedded_artwork_path
        if embedded is not None and embedded.is_file():
            set_artwork_preview(str(embedded))
        else:
            set_artwork_preview(None)
            artwork_status.value = "Встроенной обложки нет"

        async def choose_artwork(_: object) -> None:
            try:
                selected = await ft.FilePicker().pick_files(
                    dialog_title="Выберите обложку",
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["jpg", "jpeg", "png"],
                    allow_multiple=False,
                    with_data=True,
                )
            except Exception as exc:
                LOGGER.exception("Ошибка выбора обложки")
                self._notify(f"Не удалось выбрать обложку: {exc}")
                return
            if not selected:
                return

            picked = selected[0]
            data = picked.bytes
            if data is None and picked.path:
                try:
                    data = await self.workers.run(Path(picked.path).read_bytes)
                except OSError as exc:
                    self._notify(f"Не удалось прочитать обложку: {exc}")
                    return
            if not data:
                self._notify("Не удалось получить данные обложки")
                return

            try:
                prepared = self.service.prepare_artwork(data)
            except RuntimeError as exc:
                self._notify(str(exc))
                return

            artwork_state.changed = True
            artwork_state.artwork = prepared
            set_artwork_preview(prepared.data)
            artwork_status.value = f"Будет записана: {picked.name}"
            remove_artwork_button.disabled = False
            self.page.update(
                artwork_box,
                artwork_status,
                remove_artwork_button,
            )

        def remove_artwork(_: object) -> None:
            artwork_state.changed = True
            artwork_state.artwork = None
            set_artwork_preview(None)
            artwork_status.value = "Встроенная обложка будет удалена"
            remove_artwork_button.disabled = True
            self.page.update(
                artwork_box,
                artwork_status,
                remove_artwork_button,
            )

        remove_artwork_button.on_click = remove_artwork
        choose_artwork_button = ft.Button(
            content="Заменить",
            icon=ft.Icons.PHOTO_LIBRARY_OUTLINED,
            disabled=not artwork_supported,
            on_click=choose_artwork,
        )

        analysis_parts: list[str] = []
        if track.analysis is not None:
            if track.analysis.bpm is not None:
                analysis_parts.append(f"{track.analysis.bpm:.1f} BPM")
            key = " ".join(
                part
                for part in (
                    track.analysis.musical_key,
                    track.analysis.scale,
                )
                if part
            )
            if key:
                analysis_parts.append(key)
            if track.analysis.camelot:
                analysis_parts.append(track.analysis.camelot)
        analysis_label = " · ".join(analysis_parts) or "нет"
        technical_label = " · ".join(
            (
                self._format_duration(track.technical.duration),
                *self._track_detail_tags(track),
            )
        )

        async def save(_: object) -> None:
            try:
                new_metadata = AudioMetadata(
                    title=title.value or "",
                    artist=artist.value or "",
                    album=album.value or "",
                    album_artist=album_artist.value or "",
                    genre=genre.value or "",
                    year=year.value or "",
                    track_number=self._parse_int(track_no.value),
                    disc_number=self._parse_int(disc_no.value),
                    bpm=self._parse_float(bpm.value),
                    musical_key=musical_key.value or "",
                )
            except ValueError as exc:
                self._notify(str(exc))
                return

            self._set_busy(True, "Сохранение тегов...")
            try:
                await self.workers.run(
                    self.service.update_tags,
                    track_id,
                    new_metadata,
                    replace_artwork=artwork_state.changed,
                    artwork=artwork_state.artwork,
                )
            except Exception as exc:
                LOGGER.exception("Ошибка сохранения тегов")
                self._notify(f"Не удалось сохранить теги: {exc}")
            else:
                self.page.pop_dialog()
                self._notify("Теги сохранены")
                self._selected_track_id = track_id
                self.show_library()
                self.page.run_task(self._select_library_track, track_id)
            finally:
                self._set_busy(False)

        artwork_controls: list[ft.Control] = [
            ft.Text(
                "Встроенная обложка",
                weight=ft.FontWeight.BOLD,
                size=COMPACT_UI.font_sm,
            ),
            artwork_box,
            artwork_status,
            choose_artwork_button,
            remove_artwork_button,
        ]
        if not artwork_supported:
            artwork_controls.append(
                ft.Text(
                    "Запись обложки доступна для MP3, FLAC и M4A/MP4.",
                    size=COMPACT_UI.font_micro,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )

        fields = ft.Column(
            controls=[
                ft.Row(controls=[artist, title], spacing=COMPACT_UI.space_sm),
                ft.Row(
                    controls=[album_artist, album],
                    spacing=COMPACT_UI.space_sm,
                ),
                ft.Row(
                    controls=[genre, year, track_no, disc_no],
                    spacing=COMPACT_UI.space_sm,
                ),
                ft.Row(
                    controls=[bpm, musical_key],
                    spacing=COMPACT_UI.space_sm,
                ),
                ft.Text(
                    f"DSP-анализ: {analysis_label}",
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    technical_label,
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    str(track.path),
                    size=COMPACT_UI.font_micro,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            expand=True,
            spacing=COMPACT_UI.space_sm,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(track.path.name),
            content=ft.Container(
                width=760,
                content=ft.Row(
                    controls=[
                        ft.Column(
                            controls=artwork_controls,
                            width=150,
                            spacing=COMPACT_UI.space_sm,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        fields,
                    ],
                    spacing=COMPACT_UI.space_md,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ),
            actions=[
                ft.Button(
                    content="Отмена",
                    on_click=lambda _: self.page.pop_dialog(),
                ),
                ft.Button(
                    content="Сохранить",
                    icon=ft.Icons.SAVE_OUTLINED,
                    on_click=save,
                ),
            ],
        )
        self.page.show_dialog(dialog)

    def _open_organizer(self, track_id: int) -> None:
        destination = ft.TextField(
            label="Корневая папка назначения",
            expand=True,
            dense=True,
            text_size=COMPACT_UI.font_sm,
        )
        template = ft.TextField(
            label="Шаблон",
            value=DEFAULT_ORGANIZE_TEMPLATE,
            dense=True,
            text_size=COMPACT_UI.font_sm,
        )

        async def choose(_: object) -> None:
            try:
                selected = await ft.FilePicker().get_directory_path(dialog_title="Папка назначения")
            except Exception as exc:
                LOGGER.exception("Ошибка выбора папки назначения")
                self._notify(f"Не удалось выбрать папку: {exc}")
                return
            if selected:
                destination.value = selected
                destination.update()

        async def execute(_: object) -> None:
            if not destination.value:
                self._notify("Выберите папку назначения")
                return
            self._set_busy(True, "Перенос файла...")
            try:
                updated = await self.workers.run(
                    self.service.organize_track,
                    track_id,
                    Path(destination.value),
                    template.value or DEFAULT_ORGANIZE_TEMPLATE,
                )
            except Exception as exc:
                LOGGER.exception("Ошибка организации файла")
                self._notify(f"Не удалось организовать файл: {exc}")
            else:
                self.page.pop_dialog()
                self._notify(f"Файл перемещён: {updated.path}")
                self.show_library()
            finally:
                self._set_busy(False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Организация файла"),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            destination,
                            ft.Button(content="Выбрать", icon=ft.Icons.FOLDER_OPEN, on_click=choose),
                        ]
                    ),
                    template,
                    ft.Text("Доступно: artist, album, album_artist, title, year, track, disc, ext"),
                ],
                tight=True,
            ),
            actions=[
                ft.Button(content="Отмена", on_click=lambda _: self.page.pop_dialog()),
                ft.Button(content="Переместить", icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED, on_click=execute),
            ],
        )
        self.page.show_dialog(dialog)

    def _start_metadata_search(self, track_id: int) -> None:
        self.page.run_task(self._metadata_search, track_id)

    async def _metadata_search(self, track_id: int) -> None:
        self._set_busy(True, "Поиск в MusicBrainz...")
        try:
            candidates = await self.workers.run(self.service.search_metadata, track_id, "musicbrainz", 8)
        except (MetadataProviderError, RuntimeError, OSError) as exc:
            LOGGER.exception("Ошибка онлайн-поиска")
            self._notify(f"Ошибка поиска метаданных: {exc}")
        else:
            self._open_candidates(track_id, candidates)
        finally:
            self._set_busy(False)

    def _open_candidates(self, track_id: int, candidates: list[MetadataCandidate]) -> None:
        if not candidates:
            self._notify("MusicBrainz не нашёл подходящих вариантов")
            return

        rows: list[ft.Control] = []
        dialog: ft.AlertDialog

        for candidate in candidates:
            async def apply(_: object, value: MetadataCandidate = candidate) -> None:
                self._set_busy(True, "Применение метаданных...")
                try:
                    await self.workers.run(self.service.apply_candidate, track_id, value)
                except Exception as exc:
                    LOGGER.exception("Ошибка применения онлайн-метаданных")
                    self._notify(f"Не удалось применить метаданные: {exc}")
                else:
                    self.page.pop_dialog()
                    self._notify("Метаданные применены")
                    self.show_library()
                finally:
                    self._set_busy(False)

            rows.append(
                self._surface_card(
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(candidate.title, weight=ft.FontWeight.BOLD),
                                    ft.Text(f"{candidate.artist} · {candidate.album} · {candidate.year}"),
                                ],
                                expand=True,
                            ),
                            ft.Button(content="Применить", on_click=apply),
                        ]
                    ),
                    padding=5,
                )
            )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Результаты MusicBrainz"),
            content=ft.Column(controls=rows, scroll=ft.ScrollMode.AUTO, height=500, width=760),
            actions=[ft.Button(content="Закрыть", on_click=lambda _: self.page.pop_dialog())],
        )
        self.page.show_dialog(dialog)

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None or not value.strip():
            return None
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValueError(f"Ожидалось целое число: {value}") from exc

    @staticmethod
    def _parse_float(value: str | None) -> float | None:
        if value is None or not value.strip():
            return None
        try:
            return float(value.strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"Некорректный BPM: {value}") from exc

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "--:--"
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"
