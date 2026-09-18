"""Основной Flet-интерфейс DJMAKER."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import flet as ft
import flet_audio as fta

try:
    import flet_dropzone as ftd
except ImportError:  # pragma: no cover - зависит от desktop extension runtime
    ftd = None

from djmaker.config import DEFAULT_TARGET_LUFS, AppPaths
from djmaker.domain.models import (
    MetadataCandidate,
    TrackRecord,
)
from djmaker.runtime.dependencies import (
    DependencyStatus,
    RuntimeDependencies,
    RuntimeReport,
)
from djmaker.services.library import LibraryService
from djmaker.services.playlist_export import PlaylistExportRequest
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
)
from djmaker.ui.density import COMPACT_UI
from djmaker.ui.beat_grid_editor import BeatGridEditorUI
from djmaker.ui.drop_import_controls import DropImportController
from djmaker.ui.library_search_controls import LibrarySearchController
from djmaker.ui.navigation_cards_controls import NavigationCardsController
from djmaker.ui.player_controls import PlayerControlsController
from djmaker.ui.playlists import PlaylistUI
from djmaker.ui.scrolling import centered_scroll_offset
from djmaker.ui.task_progress_controls import (
    TaskProgressController,
    _AudioTaskContext,
    _BatchTaskContext,
    _WaveformTaskContext,
)
from djmaker.ui.theme_settings_controls import ThemeSettingsController
from djmaker.ui.track_row_controls import TrackRowController, _WaveformView
from djmaker.ui.track_selection_controls import TrackSelectionController
from djmaker.ui.theme import theme_mode_icon


LOGGER = logging.getLogger(__name__)

_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS = 15.0


class DJMakerUI(BeatGridEditorUI, PlaylistUI):
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
        self.theme_settings = ThemeSettingsController(self)
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
        self._selected_library_track_ids: set[int] = set()
        self._library_batch_add_button: ft.Button | None = None
        self._library_batch_clear_button: ft.IconButton | None = None
        self._library_viewport_extent = 0.0
        self._library_max_scroll_extent = 0.0
        self._selected_playlist_id: int | None = None
        self._playlist_export_contexts: dict[str, PlaylistExportRequest] = {}
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
        self.library_search = LibrarySearchController(self)
        self.player_bar = self._build_player_bar()
        self.player = PlayerControlsController(self)
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
        self.drop_import = DropImportController(self)
        self.track_selection = TrackSelectionController(self)
        self.task_progress = TaskProgressController(self)
        self.navigation_cards = NavigationCardsController(self)
        self.track_row = TrackRowController(self)

    def build(self) -> None:
        """Строит главное окно приложения."""
        self.navigation_cards.build()

    @staticmethod
    def _native_drop_enabled() -> bool:
        """Проверяет, запущен ли desktop runtime с собранными extensions."""
        return NavigationCardsController.native_drop_enabled()

    def _build_drop_overlay(self) -> ft.Container:
        """Создаёт полнооконный индикатор активного Drag&Drop."""
        return self.library_search.build_drop_overlay()

    def _on_drop_entered(self, _: object) -> None:
        self._drop_overlay.visible = True
        self._drop_overlay.update()

    def _on_drop_exited(self, _: object) -> None:
        self._drop_overlay.visible = False
        self._drop_overlay.update()

    def _on_paths_dropped(self, event: object) -> None:
        """Передаёт реальные desktop paths в управляемую задачу импорта."""
        self.drop_import.on_paths_dropped(event)

    def _build_player_bar(self) -> ft.Container:
        """Создаёт компактный постоянный плеер прослушивания."""
        return self.library_search.build_player_bar()

    def _ensure_audio_service(
        self,
        track: TrackRecord,
    ) -> tuple[fta.Audio, bool, bool]:
        """Лениво создаёт Audio service и сообщает о смене source."""
        return self.track_selection.ensure_audio_service(track)

    def _ensure_audio_path(self, path: Path) -> tuple[fta.Audio, bool, bool]:
        """Подключает обычный трек или созданный локальный preview."""
        return self.track_selection.ensure_audio_path(path)

    async def _play_external_audio(self, source: Path, title: str) -> None:
        """Воспроизводит локальный preview без добавления его в медиатеку."""
        await self.player.play_external_audio(source, title)

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
        return self.player.commit_player_track(track, position_ms)

    async def _play_track(self, track_id: int, position_ms: int = 0) -> None:
        """Выбирает трек и плавно запускает его с нужной позиции."""
        await self.track_selection.play_track(track_id, position_ms)

    async def _toggle_player(self, _: object) -> None:
        await self.library_search.toggle_player(_)

    async def _stop_player(self, _: object) -> None:
        await self.player.stop_player(_)

    def _on_player_duration_change(self, event: fta.AudioDurationChangeEvent) -> None:
        self.player.on_player_duration_change(event)

    def _on_player_position_change(self, event: fta.AudioPositionChangeEvent) -> None:
        self.player.on_player_position_change(event)

    def _on_player_state_change(self, event: fta.AudioStateChangeEvent) -> None:
        self.player.on_player_state_change(event)

    def _refresh_player_controls(self) -> None:
        self.player.refresh_player_controls()

    @staticmethod
    def _base_waveform_width() -> int:
        return TrackRowController.base_waveform_width()

    def _waveform_width(self) -> float:
        return self.track_row.waveform_width()

    @staticmethod
    def _waveform_svg(
        peaks: tuple[float, ...],
        played_bars: int | None = None,
    ) -> str:
        """Рисует waveform одним SVG вместо десятков Flet-контролов."""
        return TrackRowController.waveform_svg(peaks, played_bars)

    def _play_from_waveform(self, event: ft.TapEvent, track: TrackRecord) -> None:
        self.track_row.play_from_waveform(event, track)

    def _refresh_waveform_progress(self) -> ft.Control | None:
        return self.player.refresh_waveform_progress()

    def _paint_waveform_progress(
        self,
        track_id: int,
        fraction: float,
    ) -> ft.Control | None:
        return self.player.paint_waveform_progress(track_id, fraction)

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
                ft.NavigationRailDestination(
                    icon=ft.Icons.QUEUE_MUSIC,
                    label="Плейлисты",
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
            self.show_playlists,
        )
        if isinstance(index, int) and 0 <= index < len(handlers):
            handlers[index]()

    def _set_navigation_index(self, index: int) -> None:
        self.navigation_cards.set_navigation_index(index)

    def _initial_theme_editor_mode(self) -> str:
        """Выбирает редактируемую схему, соответствующую текущему интерфейсу."""
        return self.theme_settings.initial_theme_editor_mode()

    def _begin_theme_editor_session(self) -> None:
        self.theme_settings.begin_theme_editor_session()

    def _close_theme_editor_session(self) -> None:
        self.navigation_cards.close_theme_editor_session()

    def _theme_draft_for_mode(self, mode: str | None = None) -> dict[str, str]:
        return self.theme_settings.theme_draft_for_mode(mode)

    def _theme_preview_settings(self) -> AppSettings:
        return self.theme_settings.theme_preview_settings()

    def _theme_editor_changed_count(self) -> int:
        return self.theme_settings.theme_editor_changed_count()

    def _apply_theme_editor_preview(self) -> None:
        self.theme_settings.apply_theme_editor_preview()

    def _set_busy(self, value: bool, message: str = "") -> None:
        self.library_search.set_busy(value, message)

    def _set_status(self, message: str) -> None:
        self.status.value = message
        self.page.update()

    def _notify(self, message: str) -> None:
        self.library_search.notify(message)

    def _refresh_task_indicator(self) -> None:
        """Синхронизирует компактный индикатор фоновых задач."""
        self.task_progress.refresh_task_indicator()

    async def monitor_tasks(self) -> None:
        """Обновляет экран задач, пока долгие worker-операции меняют состояние."""
        await self.task_progress.monitor_tasks()

    def show_tasks(self) -> None:
        """Показывает текущие и завершённые задачи текущего сеанса."""
        self.task_progress.show_tasks()

    def _task_card(self, snapshot: TaskSnapshot) -> ft.Control:
        return self.task_progress.task_card(snapshot)

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
        self.task_progress.stop_task(task_id)

    def _cancel_task(self, task_id: str) -> None:
        self.task_progress.cancel_task(task_id)

    def _resume_task(self, task_id: str) -> None:
        self.task_progress.resume_task(task_id)

    def _forget_task_context(self, task_id: str) -> None:
        self.task_progress.forget_task_context(task_id)

    def _replace_content(
        self,
        title: str,
        subtitle: str,
        *controls: ft.Control,
        local_update: bool = False,
    ) -> None:
        self.navigation_cards.replace_content(
            title, subtitle, *controls, local_update=local_update
        )

    @staticmethod
    def _surface_card(
        content: ft.Control,
        *,
        padding: float = COMPACT_UI.card_padding,
    ) -> ft.Container:
        """Возвращает стандартную карточку для экранов приложения."""
        return NavigationCardsController.surface_card(content, padding=padding)

    def _empty_state(self, icon: object, title: str, description: str) -> ft.Control:
        return self.navigation_cards.empty_state(icon, title, description)

    async def _on_search(self, _: object) -> None:
        """Немедленно применяет поисковый запрос по Enter."""
        await self.library_search.on_search(_)

    def _on_search_change(self, _: object) -> None:
        """Дебаунсит живой поиск, чтобы не перестраивать 1000 строк на каждый символ."""
        self._search_revision += 1
        revision = self._search_revision
        self.search_clear_button.visible = bool(self.search.value)
        self.page.update(self.search_clear_button)
        self.page.run_task(self._debounced_library_search, revision)

    async def _debounced_library_search(self, revision: int) -> None:
        await self.library_search.debounced_library_search(revision)

    def _clear_search(self, _: object) -> None:
        self.library_search.clear_search(_)

    def _build_library_search_block(self, result_count: int) -> ft.Container:
        """Возвращает тематический поисковый блок медиатеки."""
        return self.library_search.build_library_search_block(result_count)

    def _library_sort_control(self) -> ft.Row:
        """Выбор порядка не зависит от масштаба строк медиатеки."""
        return self.library_search.library_sort_control()

    def _on_library_sort_selected(self, event: object) -> None:
        self.theme_settings.on_library_sort_selected(event)

    def _toggle_library_sort_direction(self, _: object) -> None:
        self.theme_settings.toggle_library_sort_direction(_)

    def _apply_library_sort(self, settings: AppSettings) -> bool:
        return self.theme_settings.apply_library_sort(settings)

    def _library_size(
        self,
        value: float,
        *,
        minimum: float = 1.0,
    ) -> float:
        """Возвращает размер элемента строки с учётом масштаба медиатеки."""
        return self.track_row.library_size(value, minimum=minimum)

    def _library_scale_control(self) -> ft.Control:
        """Строит компактное управление масштабом строк медиатеки."""
        return self.library_search.library_scale_control()

    def _change_library_scale(self, delta: int) -> None:
        """Сохраняет новый масштаб и немедленно перестраивает медиатеку."""
        self.library_search.change_library_scale(delta)

    def show_library(self, *, local_update: bool = False) -> None:
        """Отображает локальную медиатеку."""
        self.library_search.show_library(local_update=local_update)

    def _track_item_extent(self) -> float:
        """Высота строки медиатеки с учётом пользовательского масштаба."""
        return self.track_row.track_item_extent()

    def _estimated_library_viewport_extent(self) -> float:
        """Оценивает viewport до первого scroll-event от Flutter-клиента."""
        return self.track_selection.estimated_library_viewport_extent()

    def _on_library_scroll(self, event: ft.OnScrollEvent) -> None:
        """Запоминает реальные метрики ListView для точной центровки."""
        self._library_viewport_extent = max(0.0, event.viewport_dimension)
        self._library_max_scroll_extent = max(0.0, event.max_scroll_extent)

    async def _select_library_track(self, track_id: int) -> None:
        """Выделяет трек и плавно размещает его по центру списка."""
        await self.track_selection.select_library_track(track_id)

    def _track_row(self, track: TrackRecord) -> ft.Control:
        return self.track_row.track_row(track)

    def _track_title_row(self, track: TrackRecord) -> ft.Control:
        return self.track_row.track_title_row(track)

    def _track_details_row(self, track: TrackRecord) -> ft.Control:
        return self.track_row.track_details_row(track)

    def _track_path_link(self, track: TrackRecord) -> ft.Control:
        return self.track_row.track_path_link(track)

    def _track_tag(self, label: str, *, accent: bool = False) -> ft.Control:
        return self.track_row.track_tag(label, accent=accent)

    @staticmethod
    def _on_accent_track_tag_hover(event: ft.Event[ft.Container]) -> None:
        """Усиливает акцентный тег при наведении без обновления строки."""
        LibrarySearchController.on_accent_track_tag_hover(event)

    @staticmethod
    def _on_neutral_track_tag_hover(event: ft.Event[ft.Container]) -> None:
        """Подсвечивает технический тег цветами активной темы."""
        LibrarySearchController.on_neutral_track_tag_hover(event)

    @staticmethod
    def _track_bpm_label(track: TrackRecord) -> str:
        return TrackRowController.track_bpm_label(track)

    @staticmethod
    def _track_key_label(track: TrackRecord) -> str:
        return TrackRowController.track_key_label(track)

    @staticmethod
    def _track_detail_tags(track: TrackRecord) -> tuple[str, ...]:
        return TrackRowController.track_detail_tags(track)

    def _reveal_track_file(self, track: TrackRecord) -> None:
        self.track_row.reveal_track_file(track)

    def _track_artwork(self, track: TrackRecord) -> ft.Control:
        """Показывает кликабельную обложку с embedded/online fallback."""
        return self.track_row.track_artwork(track)

    def _track_waveform(self, track: TrackRecord) -> ft.Control:
        return self.track_row.track_waveform(track)

    def show_folders(self) -> None:
        """Отображает корневые папки и действия сканирования."""
        self.navigation_cards.show_folders()

    async def _pick_and_scan(self, _: object) -> None:
        await self.drop_import.pick_and_scan(_)

    def _scan_existing(self, path: Path) -> None:
        self.navigation_cards.scan_existing(path)

    async def _run_scan(self, path: Path, task_id: str | None = None) -> None:
        await self.drop_import.run_scan(path, task_id)

    async def _run_drop_import(
        self,
        paths: tuple[Path, ...],
        task_id: str | None = None,
    ) -> None:
        """Импортирует dropped-файлы/папки через общий scanner/task pipeline."""
        await self.drop_import.run_drop_import(paths, task_id)

    def show_duplicates(self) -> None:
        """Показывает точные дубликаты по SHA-256."""
        self.navigation_cards.show_duplicates()

    def show_plugins(self) -> None:
        """Показывает подключённые внешние провайдеры."""
        self.navigation_cards.show_plugins()

    def show_audio_modules(self) -> None:
        """Показывает доступные DSP-модули и управление анализом."""
        self.navigation_cards.show_audio_modules()

    def _start_audio_analysis(self, _: object, *, force: bool = False) -> None:
        self.navigation_cards.start_audio_analysis(_, force=force)

    async def _run_audio_analysis(self, task_id: str) -> None:
        await self.task_progress.run_audio_analysis(task_id)

    def _start_waveform_analysis(self, _: object, *, force: bool = False) -> None:
        self.task_progress.start_waveform_analysis(_, force=force)

    async def ensure_waveforms(self) -> None:
        """Автоматически строит отсутствующие waveform после запуска приложения."""
        await self.task_progress.ensure_waveforms()

    async def _run_waveform_analysis(self, task_id: str) -> None:
        await self.task_progress.run_waveform_analysis(task_id)

    @staticmethod
    def _analysis_label(track: TrackRecord) -> str:
        return TrackRowController.analysis_label(track)

    async def ensure_embedded_artwork(self) -> None:
        """Индексирует встроенные обложки как управляемую фоновую задачу."""
        await self.task_progress.ensure_embedded_artwork()

    async def _run_embedded_artwork(self, task_id: str) -> None:
        await self.task_progress.run_embedded_artwork(task_id)

    async def ensure_runtime_dependencies(self) -> None:
        """Фоново проверяет и устанавливает FFmpeg/Essentia при запуске."""
        await self.theme_settings.ensure_runtime_dependencies()

    def _retry_runtime_dependencies(self, _: object) -> None:
        """Повторно запускает проверку/установку из экрана настроек."""
        self.theme_settings.retry_runtime_dependencies(_)

    def _runtime_card(self, report: RuntimeReport) -> ft.Control:
        """Создаёт карточку состояния FFmpeg и Essentia."""
        return self.theme_settings.runtime_card(report)

    @staticmethod
    def _runtime_status_row(status: DependencyStatus) -> ft.Control:
        return ThemeSettingsController.runtime_status_row(status)

    def _build_theme_editor(self) -> ft.Container:
        """Строит глобальный редактор цветовых ролей с живым предпросмотром."""
        return self.theme_settings.build_theme_editor()

    def _on_theme_editor_mode_selected(self, event: object) -> None:
        self.theme_settings.on_theme_editor_mode_selected(event)

    def _on_theme_color_changed(self, role: str, event: object) -> None:
        self.theme_settings.on_theme_color_changed(role, event)

    def _save_theme_editor(self, _: object) -> None:
        self.theme_settings.save_theme_editor(_)

    def _revert_theme_editor(self, _: object) -> None:
        self.theme_settings.revert_theme_editor(_)

    def _reset_theme_editor_mode(self, _: object) -> None:
        self.theme_settings.reset_theme_editor_mode(_)

    def show_settings(self) -> None:
        """Показывает настройки оформления и локальные пути приложения."""
        self.theme_settings.show_settings()

    def _open_database_reset_dialog(self, _: object) -> None:
        """Запрашивает подтверждение полного сброса SQLite-медиатеки."""
        self.library_search.open_database_reset_dialog(_)

    def _clear_library_runtime_state(self) -> None:
        """Сбрасывает UI-состояние, связанное с удалёнными записями БД."""
        self.library_search.clear_library_runtime_state()

    @staticmethod
    def _path_setting(label: str, path: Path) -> ft.Control:
        return DropImportController.path_setting(label, path)

    def _on_theme_mode_selected(self, event: object) -> None:
        self.theme_settings.on_theme_mode_selected(event)

    def _on_theme_palette_selected(self, event: object) -> None:
        self.theme_settings.on_theme_palette_selected(event)

    def _cycle_theme_mode(self, _: object) -> None:
        self.theme_settings.cycle_theme_mode(_)

    def _save_and_apply_settings(self, settings: AppSettings) -> None:
        self.theme_settings.save_and_apply_settings(settings)

    def _theme_tooltip(self) -> str:
        return self.theme_settings.theme_tooltip()

    def _open_tag_editor(self, track_id: int) -> None:
        self.library_search.open_tag_editor(track_id)

    def _open_organizer(self, track_id: int) -> None:
        self.library_search.open_organizer(track_id)

    def _start_metadata_search(self, track_id: int) -> None:
        self.track_row.start_metadata_search(track_id)

    async def _metadata_search(self, track_id: int) -> None:
        await self.library_search.metadata_search(track_id)

    def _open_candidates(self, track_id: int, candidates: list[MetadataCandidate]) -> None:
        self.library_search.open_candidates(track_id, candidates)

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        return LibrarySearchController.parse_int(value)

    @staticmethod
    def _parse_float(value: str | None) -> float | None:
        return LibrarySearchController.parse_float(value)

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        return PlayerControlsController.format_duration(seconds)

    @staticmethod
    def _format_size(size: int) -> str:
        return NavigationCardsController.format_size(size)
