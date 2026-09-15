"""Основной Flet-интерфейс DJMAKER."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import flet as ft

from djmaker.config import DEFAULT_ORGANIZE_TEMPLATE, DEFAULT_TARGET_LUFS, AppPaths
from djmaker.domain.models import AudioMetadata, MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.services.library import LibraryService
from djmaker.services.workers import BackgroundWorkers
from djmaker.settings import AppSettings, SettingsStore, THEME_MODES
from djmaker.ui.density import COMPACT_UI
from djmaker.ui.theme import (
    THEME_MODE_LABELS,
    THEME_PALETTES,
    apply_app_theme,
    palette_title,
    theme_mode_icon,
)


LOGGER = logging.getLogger(__name__)


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
    ) -> None:
        self.page = page
        self.service = service
        self.workers = workers
        self.paths = paths
        self.settings_store = settings_store
        self.settings = settings

        self.search = ft.TextField(
            label="Поиск в медиатеке",
            hint_text="Название, артист, альбом или путь",
            expand=True,
            dense=True,
            text_size=COMPACT_UI.font_sm,
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            on_submit=self._on_search,
        )
        self.busy = ft.ProgressRing(width=16, height=16, visible=False)
        self.status = ft.Text(
            "Готово",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.content = ft.Column(expand=True, spacing=COMPACT_UI.space_md)
        self.header_title = ft.Text(
            "Медиатека",
            size=COMPACT_UI.font_title,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )
        self.header_subtitle = ft.Text(
            "Локальная музыкальная библиотека",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.theme_button = ft.IconButton(
            icon=theme_mode_icon(self.settings.theme_mode),
            icon_size=COMPACT_UI.action_icon_size,
            padding=COMPACT_UI.space_xs,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip=self._theme_tooltip(),
            on_click=self._cycle_theme_mode,
        )
        self.navigation = self._build_navigation()

    def build(self) -> None:
        """Строит главное окно приложения."""
        self.page.title = "DJMAKER"
        self.page.padding = 0
        self.page.spacing = 0
        self.page.window.min_width = 980
        self.page.window.min_height = 640
        apply_app_theme(self.page, self.settings)

        header = ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            padding=ft.Padding.symmetric(
                horizontal=COMPACT_UI.header_horizontal_padding,
                vertical=COMPACT_UI.header_vertical_padding,
            ),
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[self.header_title, self.header_subtitle],
                        spacing=1,
                        width=200,
                    ),
                    self.search,
                    self.busy,
                    self.theme_button,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=COMPACT_UI.space_md,
            ),
        )

        workspace = ft.Column(
            controls=[
                header,
                ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                ft.Container(
                    content=self.content,
                    expand=True,
                    padding=COMPACT_UI.content_padding,
                ),
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
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )

        self.page.add(
            ft.Row(
                controls=[
                    self.navigation,
                    ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                    workspace,
                ],
                spacing=0,
                expand=True,
            )
        )
        self.show_library()

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
            self.show_settings,
        )
        if isinstance(index, int) and 0 <= index < len(handlers):
            handlers[index]()

    def _set_navigation_index(self, index: int) -> None:
        if self.navigation.selected_index != index:
            self.navigation.selected_index = index

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

    def _replace_content(
        self,
        title: str,
        subtitle: str,
        *controls: ft.Control,
    ) -> None:
        self.header_title.value = title
        self.header_subtitle.value = subtitle
        self.content.controls.clear()
        self.content.controls.extend(controls)
        self.page.update()

    @staticmethod
    def _surface_card(
        content: ft.Control,
        *,
        padding: int = COMPACT_UI.card_padding,
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
        self.show_library()

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
                ft.Button(
                    content="Обновить",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _: self.show_library(),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        items: list[ft.Control] = []
        if not tracks:
            items.append(
                self._empty_state(
                    ft.Icons.LIBRARY_MUSIC_OUTLINED,
                    "Медиатека пока пуста",
                    "Добавьте музыкальную папку в разделе «Папки» и запустите сканирование.",
                )
            )
        else:
            items.extend(self._track_row(track) for track in tracks)

        listing = ft.ListView(
            controls=items,
            expand=True,
            spacing=COMPACT_UI.space_sm,
        )
        self._replace_content(
            "Медиатека",
            "Поиск, теги и организация локальной музыкальной коллекции",
            actions,
            listing,
        )

    def _track_row(self, track: TrackRecord) -> ft.Control:
        duration = self._format_duration(track.technical.duration)
        bitrate = f"{round(track.technical.bitrate / 1000)} kbps" if track.technical.bitrate else "—"
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist or "Unknown Artist"
        album = track.metadata.album or "Unknown Album"
        return self._surface_card(
            ft.Row(
                controls=[
                    ft.Container(
                        width=COMPACT_UI.track_icon_box,
                        height=COMPACT_UI.track_icon_box,
                        border_radius=6,
                        bgcolor=ft.Colors.PRIMARY_CONTAINER,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Icon(
                            ft.Icons.MUSIC_NOTE,
                            size=COMPACT_UI.track_icon_size,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                        ),
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                title,
                                weight=ft.FontWeight.BOLD,
                                size=COMPACT_UI.font_md,
                            ),
                            ft.Text(
                                f"{artist} · {album}",
                                size=COMPACT_UI.font_xs,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.Text(
                                str(track.path),
                                size=COMPACT_UI.font_micro,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                    ft.Text(
                        f"{duration}  ·  {bitrate}",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=COMPACT_UI.action_icon_size,
                        padding=COMPACT_UI.space_xs,
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Редактировать теги",
                        on_click=lambda _, track_id=track.id: self._open_tag_editor(track_id),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
                        icon_size=COMPACT_UI.action_icon_size,
                        padding=COMPACT_UI.space_xs,
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Организовать файл",
                        on_click=lambda _, track_id=track.id: self._open_organizer(track_id),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SEARCH,
                        icon_size=COMPACT_UI.action_icon_size,
                        padding=COMPACT_UI.space_xs,
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Найти метаданные",
                        on_click=lambda _, track_id=track.id: self._start_metadata_search(track_id),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=COMPACT_UI.space_sm,
            )
        )

    def show_folders(self) -> None:
        """Отображает корневые папки и действия сканирования."""
        self._set_navigation_index(1)
        try:
            roots = self.service.roots()
        except RuntimeError as exc:
            self._notify(str(exc))
            return

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
            )
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
            selected = await ft.FilePicker().get_directory_path(dialog_title="Выберите папку с музыкой")
        except Exception as exc:
            LOGGER.exception("Ошибка FilePicker")
            self._notify(f"Не удалось открыть выбор папки: {exc}")
            return
        if not selected:
            return
        await self._run_scan(Path(selected))

    def _scan_existing(self, path: Path) -> None:
        self.page.run_task(self._run_scan, path)

    async def _run_scan(self, path: Path) -> None:
        self._set_busy(True, f"Сканирование: {path}")
        try:
            stats = await self.workers.run(self.service.scan_folder, path)
        except Exception as exc:
            LOGGER.exception("Ошибка сканирования")
            self._notify(f"Ошибка сканирования: {exc}")
        else:
            self._notify(
                "Сканирование завершено: "
                f"найдено {stats.discovered}, обновлено {stats.updated}, "
                f"без изменений {stats.unchanged}, удалено {stats.removed}, ошибок {stats.errors}"
            )
            self.show_folders()
        finally:
            self._set_busy(False)

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
        """Показывает состояние отдельных DSP-модулей."""
        self._set_navigation_index(4)
        modules = (
            (ft.Icons.VOLUME_UP_OUTLINED, "Нормализация", f"Отдельный этап · основной target {DEFAULT_TARGET_LUFS} LUFS"),
            (ft.Icons.SPEED, "BPM", "Интерфейс подготовлен · алгоритм будет выбран отдельно"),
            (ft.Icons.MUSIC_NOTE_OUTLINED, "Key / Camelot", "Планируется обычная нотация и Camelot"),
            (ft.Icons.FINGERPRINT, "Audio fingerprint", "Второй уровень поиска музыкальных дубликатов"),
        )
        controls = [
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
            for icon, title, description in modules
        ]
        self._replace_content(
            "Аудио-модули",
            "Независимые этапы анализа и обработки аудио",
            *controls,
        )

    def show_settings(self) -> None:
        """Показывает настройки оформления и локальные пути приложения."""
        self._set_navigation_index(5)
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
                        "Цветовая схема применяется одновременно к светлой и тёмной теме.",
                        size=COMPACT_UI.font_xs,
                    ),
                    mode_dropdown,
                    palette_dropdown,
                ],
                spacing=5,
            )
        )

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

        self._replace_content(
            "Настройки",
            f"Тема: {THEME_MODE_LABELS[self.settings.theme_mode]} · {palette_title(self.settings.theme_palette)}",
            appearance,
            storage,
            organizer,
        )

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
        apply_app_theme(self.page, self.settings)
        self.theme_button.icon = theme_mode_icon(self.settings.theme_mode)
        self.theme_button.tooltip = self._theme_tooltip()
        self.status.value = (
            f"Тема: {THEME_MODE_LABELS[self.settings.theme_mode]} · "
            f"{palette_title(self.settings.theme_palette)}"
        )

        if self.navigation.selected_index == 5:
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
        title = ft.TextField(label="Title", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.title)
        artist = ft.TextField(label="Artist", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.artist)
        album = ft.TextField(label="Album", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.album)
        album_artist = ft.TextField(label="Album Artist", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.album_artist)
        genre = ft.TextField(label="Genre", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.genre)
        year = ft.TextField(label="Year", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.year)
        track_no = ft.TextField(label="Track", dense=True, text_size=COMPACT_UI.font_sm, value=str(metadata.track_number or ""))
        disc_no = ft.TextField(label="Disc", dense=True, text_size=COMPACT_UI.font_sm, value=str(metadata.disc_number or ""))
        bpm = ft.TextField(label="BPM", dense=True, text_size=COMPACT_UI.font_sm, value=str(metadata.bpm or ""))
        musical_key = ft.TextField(label="Key", dense=True, text_size=COMPACT_UI.font_sm, value=metadata.musical_key)

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
                await self.workers.run(self.service.update_tags, track_id, new_metadata)
            except Exception as exc:
                LOGGER.exception("Ошибка сохранения тегов")
                self._notify(f"Не удалось сохранить теги: {exc}")
            else:
                self.page.pop_dialog()
                self._notify("Теги сохранены")
                self.show_library()
            finally:
                self._set_busy(False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(track.path.name),
            content=ft.Column(
                controls=[
                    title,
                    artist,
                    album,
                    album_artist,
                    genre,
                    year,
                    ft.Row(controls=[track_no, disc_no]),
                    ft.Row(controls=[bpm, musical_key]),
                ],
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.Button(content="Отмена", on_click=lambda _: self.page.pop_dialog()),
                ft.Button(content="Сохранить", icon=ft.Icons.SAVE_OUTLINED, on_click=save),
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
