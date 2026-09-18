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
from djmaker.domain.library_sort import LIBRARY_SORT_LABELS
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
    THEME_COLOR_ROLES,
    THEME_MODES,
    is_valid_theme_color,
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
from djmaker.ui.track_row_controls import TrackRowController, _WaveformView
from djmaker.ui.track_selection_controls import TrackSelectionController
from djmaker.ui.theme import (
    THEME_MODE_LABELS,
    THEME_PALETTES,
    apply_app_theme,
    palette_description,
    palette_title,
    theme_mode_icon,
)


LOGGER = logging.getLogger(__name__)

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
        self.navigation_cards.close_theme_editor_session()

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
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in LIBRARY_SORT_LABELS:
            return
        settings = replace(self.settings, library_sort=value)
        if not self._apply_library_sort(settings):
            control.value = self.settings.library_sort
            self.page.update(control)

    def _toggle_library_sort_direction(self, _: object) -> None:
        self._apply_library_sort(replace(
            self.settings,
            library_sort_descending=not self.settings.library_sort_descending,
        ))

    def _apply_library_sort(self, settings: AppSettings) -> bool:
        if settings == self.settings:
            return True
        try:
            self.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить сортировку")
            self._notify(f"Не удалось сохранить сортировку: {exc}")
            return False
        self.settings = settings
        self._search_revision += 1
        selected_track_id = self._selected_track_id
        self.show_library(local_update=True)
        if (
            selected_track_id is not None
            and selected_track_id in self._library_track_indices
        ):
            self.page.run_task(self._select_library_track, selected_track_id)
        return True

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
                                        "Удаляет треки, папки, плейлисты, ошибки сканирования и "
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
        self.library_search.open_database_reset_dialog(_)

    def _clear_library_runtime_state(self) -> None:
        """Сбрасывает UI-состояние, связанное с удалёнными записями БД."""
        self.library_search.clear_library_runtime_state()

    @staticmethod
    def _path_setting(label: str, path: Path) -> ft.Control:
        return DropImportController.path_setting(label, path)

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
