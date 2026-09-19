"""Тема оформления, глобальный редактор цветов, настройки и runtime-зависимости.

Выделено из DJMakerUI (god object).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import flet as ft

from djmaker.config import DEFAULT_ORGANIZE_TEMPLATE
from djmaker.domain.library_sort import LIBRARY_SORT_LABELS
from djmaker.runtime.dependencies import DependencyStatus, RuntimeReport
from djmaker.settings import (
    AppSettings,
    METADATA_AUTO_APPLY_THRESHOLD_MAX,
    METADATA_AUTO_APPLY_THRESHOLD_MIN,
    THEME_COLOR_ROLES,
    THEME_MODES,
    is_valid_theme_color,
)
from djmaker.ui.density import COMPACT_UI
from djmaker.ui.theme import (
    THEME_MODE_LABELS,
    THEME_PALETTES,
    apply_app_theme,
    palette_description,
    palette_title,
    theme_mode_icon,
)

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)

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


class ThemeSettingsController:
    """Владеет темой, редактором цветов, настройками и runtime-зависимостями."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def initial_theme_editor_mode(self) -> str:
        """Выбирает редактируемую схему, соответствующую текущему интерфейсу."""
        app = self.app
        if app.settings.theme_mode in _THEME_EDITOR_MODE_LABELS:
            return app.settings.theme_mode
        brightness = getattr(app.page, "platform_brightness", None)
        value = str(getattr(brightness, "value", brightness or "")).lower()
        return "dark" if "dark" in value else "light"

    def begin_theme_editor_session(self) -> None:
        app = self.app
        if app._theme_editor_active:
            return
        app._theme_editor_active = True
        app._theme_editor_mode = self.initial_theme_editor_mode()
        app._theme_draft_light = dict(app.settings.theme_light_overrides)
        app._theme_draft_dark = dict(app.settings.theme_dark_overrides)

    def theme_draft_for_mode(self, mode: str | None = None) -> dict[str, str]:
        app = self.app
        target = mode or app._theme_editor_mode
        return app._theme_draft_dark if target == "dark" else app._theme_draft_light

    def theme_preview_settings(self) -> AppSettings:
        app = self.app
        return replace(
            app.settings,
            theme_light_overrides=dict(app._theme_draft_light),
            theme_dark_overrides=dict(app._theme_draft_dark),
        )

    def theme_editor_changed_count(self) -> int:
        app = self.app
        saved_light = app.settings.theme_light_overrides
        saved_dark = app.settings.theme_dark_overrides
        roles = set(THEME_COLOR_ROLES)
        return sum(
            app._theme_draft_light.get(role) != saved_light.get(role)
            for role in roles
        ) + sum(
            app._theme_draft_dark.get(role) != saved_dark.get(role)
            for role in roles
        )

    def apply_theme_editor_preview(self) -> None:
        app = self.app
        preview = self.theme_preview_settings()
        apply_app_theme(app.page, preview)
        app.page.theme_mode = (
            ft.ThemeMode.DARK
            if app._theme_editor_mode == "dark"
            else ft.ThemeMode.LIGHT
        )
        changed = self.theme_editor_changed_count()
        if app._theme_editor_status is not None:
            if changed:
                app._theme_editor_status.value = (
                    f"Предпросмотр · несохранённых изменений: {changed}"
                )
                app._theme_editor_status.color = ft.Colors.PRIMARY
            else:
                app._theme_editor_status.value = "Предпросмотр совпадает с сохранённой темой"
                app._theme_editor_status.color = ft.Colors.ON_SURFACE_VARIANT
        app.page.update()

    def on_library_sort_selected(self, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in LIBRARY_SORT_LABELS:
            return
        settings = replace(app.settings, library_sort=value)
        if not self.apply_library_sort(settings):
            control.value = app.settings.library_sort
            app.page.update(control)

    def toggle_library_sort_direction(self, _: object) -> None:
        app = self.app
        self.apply_library_sort(replace(
            app.settings,
            library_sort_descending=not app.settings.library_sort_descending,
        ))

    def toggle_energy_highlight(self, _: object) -> None:
        """Вкл/выкл подсветку строк медиатеки по цвету энергии (AIR)."""
        app = self.app
        settings = replace(
            app.settings,
            energy_highlight_enabled=not app.settings.energy_highlight_enabled,
        )
        try:
            app.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить настройку подсветки энергии")
            app._notify(f"Не удалось сохранить настройку: {exc}")
            return
        app.settings = settings
        app.show_library(local_update=True)

    def apply_library_sort(self, settings: AppSettings) -> bool:
        app = self.app
        if settings == app.settings:
            return True
        try:
            app.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить сортировку")
            app._notify(f"Не удалось сохранить сортировку: {exc}")
            return False
        app.settings = settings
        app._search_revision += 1
        selected_track_id = app._selected_track_id
        app.show_library(local_update=True)
        if (
            selected_track_id is not None
            and selected_track_id in app._library_track_indices
        ):
            app.page.run_task(app._select_library_track, selected_track_id)
        return True

    async def ensure_runtime_dependencies(self) -> None:
        """Фоново проверяет и устанавливает FFmpeg/Essentia при запуске."""
        app = self.app
        app._set_status("Проверка FFmpeg и Essentia...")
        try:
            report = await app.workers.run(app.runtime.ensure_all)
        except Exception as exc:
            LOGGER.exception("Ошибка проверки runtime-зависимостей")
            app._notify(f"Ошибка проверки аудио-компонентов: {exc}")
            return

        app.runtime_report = report
        problems = [
            status.name
            for status in (report.ffmpeg, report.essentia)
            if not status.available
        ]
        if problems:
            app._set_status(
                "Аудио-компоненты требуют внимания: " + ", ".join(problems)
            )
        else:
            app._set_status(
                f"FFmpeg {report.ffmpeg.version} · Essentia {report.essentia.version}"
            )

        if app.navigation.selected_index == 6:
            app.show_settings()

    def genre_refinement_card(self) -> ft.Control:
        """Тумблеры жанрового AST-уточнения цвета энергии и GPU-ускорения."""
        app = self.app
        return app._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PRIMARY),
                            ft.Text(
                                "Жанровое уточнение цвета энергии",
                                size=COMPACT_UI.font_lg,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ]
                    ),
                    ft.Text(
                        "AST (AudioSet) уточняет цвет подсветки для тяжёлых/мрачных и "
                        "эмбиент-треков, которые чистая энергия не различает. Модель "
                        "(~170МБ) загружается по требованию, не при запуске приложения.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Checkbox(
                        label="Включить жанровое уточнение (медленнее полного анализа)",
                        value=app.settings.energy_genre_refinement_enabled,
                        on_change=self.on_genre_refinement_toggled,
                    ),
                    ft.Checkbox(
                        label="Использовать GPU (DirectML/CoreML, при наличии)",
                        value=app.settings.energy_genre_gpu_enabled,
                        disabled=not app.settings.energy_genre_refinement_enabled,
                        on_change=self.on_genre_gpu_toggled,
                    ),
                ],
                spacing=5,
            )
        )

    def on_genre_refinement_toggled(self, event: object) -> None:
        control = getattr(event, "control", None)
        checked = bool(getattr(control, "value", False))
        self.save_and_apply_settings(
            replace(self.app.settings, energy_genre_refinement_enabled=checked)
        )

    def on_genre_gpu_toggled(self, event: object) -> None:
        control = getattr(event, "control", None)
        checked = bool(getattr(control, "value", False))
        self.save_and_apply_settings(
            replace(self.app.settings, energy_genre_gpu_enabled=checked)
        )

    def retry_runtime_dependencies(self, _: object) -> None:
        """Повторно запускает проверку/установку из экрана настроек."""
        app = self.app
        app.page.run_task(app.ensure_runtime_dependencies)

    def runtime_card(self, report: RuntimeReport) -> ft.Control:
        """Создаёт карточку состояния FFmpeg и Essentia."""
        app = self.app
        return app._surface_card(
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
                                on_click=app._retry_runtime_dependencies,
                            ),
                        ]
                    ),
                    self.runtime_status_row(report.ffmpeg),
                    self.runtime_status_row(report.essentia),
                    self.runtime_status_row(report.ast),
                    ft.Text(
                        "FFmpeg декодирует аудио. Essentia используется как собственная "
                        "lightweight-сборка DJMAKER с KISS FFT и загружается из Releases "
                        "этого репозитория после проверки SHA-256. AST — опциональная модель "
                        "жанрового уточнения цвета энергии, загружается по требованию при "
                        "включении соответствующей настройки.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

    @staticmethod
    def runtime_status_row(status: DependencyStatus) -> ft.Control:
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

    def build_theme_editor(self) -> ft.Container:
        """Строит глобальный редактор цветовых ролей с живым предпросмотром."""
        app = self.app
        self.begin_theme_editor_session()
        draft = self.theme_draft_for_mode()
        app._theme_editor_fields.clear()
        app._theme_editor_swatches.clear()

        editor_mode = ft.Dropdown(
            label="Редактируемая схема",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=app._theme_editor_mode,
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in _THEME_EDITOR_MODE_LABELS.items()
            ],
            on_select=app._on_theme_editor_mode_selected,
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
                on_change=lambda event, color_role=role: app._on_theme_color_changed(
                    color_role, event
                ),
            )
            app._theme_editor_fields[role] = field
            app._theme_editor_swatches[role] = swatch
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

        changed = self.theme_editor_changed_count()
        app._theme_editor_status = ft.Text(
            (
                f"Предпросмотр · несохранённых изменений: {changed}"
                if changed
                else "Предпросмотр совпадает с сохранённой темой"
            ),
            size=COMPACT_UI.font_xs,
            color=ft.Colors.PRIMARY if changed else ft.Colors.ON_SURFACE_VARIANT,
        )

        return app._surface_card(
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
                                on_click=app._save_theme_editor,
                            ),
                            ft.Button(
                                content="Вернуть сохранённое",
                                icon=ft.Icons.UNDO,
                                on_click=app._revert_theme_editor,
                            ),
                            ft.Button(
                                content="Сбросить текущую схему",
                                icon=ft.Icons.RESTART_ALT,
                                on_click=app._reset_theme_editor_mode,
                            ),
                            ft.Container(expand=True),
                            app._theme_editor_status,
                        ],
                        spacing=COMPACT_UI.space_sm,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

    def on_theme_editor_mode_selected(self, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in _THEME_EDITOR_MODE_LABELS:
            return
        app._theme_editor_mode = value
        app.show_settings()
        self.apply_theme_editor_preview()

    def on_theme_color_changed(self, role: str, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        if not isinstance(control, ft.TextField) or role not in THEME_COLOR_ROLES:
            return

        raw = (control.value or "").strip()
        swatch = app._theme_editor_swatches.get(role)
        status = app._theme_editor_status
        if raw and not is_valid_theme_color(raw):
            control.border_color = ft.Colors.ERROR
            if swatch is not None:
                swatch.bgcolor = ft.Colors.ERROR_CONTAINER
            if status is not None:
                status.value = f"{_THEME_ROLE_LABELS[role]}: ожидается #RRGGBB"
                status.color = ft.Colors.ERROR
            updates = [item for item in (control, swatch, status) if item is not None]
            app.page.update(*updates)
            return

        control.border_color = None
        draft = self.theme_draft_for_mode()
        if raw:
            normalized = raw.upper()
            draft[role] = normalized
            if swatch is not None:
                swatch.bgcolor = normalized
        else:
            draft.pop(role, None)
            if swatch is not None:
                swatch.bgcolor = ft.Colors.OUTLINE_VARIANT
        self.apply_theme_editor_preview()

    def save_theme_editor(self, _: object) -> None:
        app = self.app
        invalid = [
            field
            for field in app._theme_editor_fields.values()
            if (field.value or "").strip()
            and not is_valid_theme_color((field.value or "").strip())
        ]
        if invalid:
            app._notify("Исправьте некорректные HEX-цвета перед сохранением")
            return
        self.save_and_apply_settings(self.theme_preview_settings())

    def revert_theme_editor(self, _: object) -> None:
        app = self.app
        app._theme_draft_light = dict(app.settings.theme_light_overrides)
        app._theme_draft_dark = dict(app.settings.theme_dark_overrides)
        app.show_settings()
        self.apply_theme_editor_preview()

    def reset_theme_editor_mode(self, _: object) -> None:
        app = self.app
        self.theme_draft_for_mode().clear()
        app.show_settings()
        self.apply_theme_editor_preview()

    def show_settings(self) -> None:
        """Показывает настройки оформления и локальные пути приложения."""
        app = self.app
        app._set_navigation_index(6)
        mode_dropdown = ft.Dropdown(
            label="Режим интерфейса",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=app.settings.theme_mode,
            options=[
                ft.DropdownOption(key=mode, text=THEME_MODE_LABELS[mode])
                for mode in THEME_MODES
            ],
            on_select=app._on_theme_mode_selected,
        )
        palette_dropdown = ft.Dropdown(
            label="Цветовая схема",
            dense=True,
            text_size=COMPACT_UI.font_sm,
            value=app.settings.theme_palette,
            options=[
                ft.DropdownOption(key=palette.key, text=palette.title)
                for palette in THEME_PALETTES
            ],
            on_select=app._on_theme_palette_selected,
        )

        appearance = app._surface_card(
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
                        palette_description(app.settings.theme_palette),
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=5,
            )
        )

        theme_editor = self.build_theme_editor()

        storage = app._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.STORAGE_OUTLINED, color=ft.Colors.PRIMARY),
                            ft.Text("Локальные данные", size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                        ]
                    ),
                    app._path_setting("Каталог данных", app.paths.data_dir),
                    app._path_setting("База данных", app.paths.database),
                    app._path_setting("Настройки", app.paths.settings_file),
                    app._path_setting("Лог", app.paths.log_file),
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
                                on_click=app._open_database_reset_dialog,
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
            )
        )

        organizer = app._surface_card(
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

        metadata_sources = self.metadata_sources_card()

        settings_list = ft.ListView(
            controls=[
                self.runtime_card(app.runtime_report),
                self.genre_refinement_card(),
                appearance,
                theme_editor,
                metadata_sources,
                storage,
                organizer,
            ],
            expand=True,
            spacing=COMPACT_UI.space_md,
            padding=0,
        )
        app._replace_content(
            "Настройки",
            f"Тема: {THEME_MODE_LABELS[app.settings.theme_mode]} · {palette_title(app.settings.theme_palette)}",
            settings_list,
        )

    def metadata_sources_card(self) -> ft.Control:
        app = self.app
        selected = set(app.settings.metadata_providers)

        rows: list[ft.Control] = []
        for provider in app.service.plugins.all():
            row_controls: list[ft.Control] = [
                ft.Checkbox(
                    label=provider.display_name,
                    value=provider.provider_id in selected,
                    on_change=lambda e, pid=provider.provider_id: self.on_metadata_provider_toggled(e, pid),
                )
            ]
            is_configured = getattr(provider, "is_configured", None)
            if callable(is_configured):
                if not is_configured():
                    row_controls.append(
                        ft.Text(
                            "не настроен",
                            size=COMPACT_UI.font_micro,
                            color=ft.Colors.ERROR,
                        )
                    )
                row_controls.append(ft.Container(expand=True))
                row_controls.append(
                    ft.Button(
                        content="Настроить",
                        icon=ft.Icons.SETTINGS_OUTLINED,
                        on_click=lambda _, pid=provider.provider_id: self.open_provider_configuration_dialog(pid),
                    )
                )
            rows.append(ft.Row(controls=row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        return app._surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.TRAVEL_EXPLORE, color=ft.Colors.PRIMARY),
                            ft.Text("Источники метаданных", size=COMPACT_UI.font_lg, weight=ft.FontWeight.BOLD),
                        ]
                    ),
                    ft.Text(
                        "Выбранные источники опрашиваются при поиске онлайн-метаданных; "
                        "результаты объединяются, недостающие поля у одного заполняются "
                        "данными другого.",
                        size=COMPACT_UI.font_xs,
                    ),
                    *rows,
                    ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Text(
                        "Порог автоприменения при массовом поиске: "
                        f"{app.settings.metadata_auto_apply_threshold}%",
                        size=COMPACT_UI.font_xs,
                    ),
                    ft.Slider(
                        min=METADATA_AUTO_APPLY_THRESHOLD_MIN,
                        max=METADATA_AUTO_APPLY_THRESHOLD_MAX,
                        divisions=(METADATA_AUTO_APPLY_THRESHOLD_MAX - METADATA_AUTO_APPLY_THRESHOLD_MIN)
                        // 5,
                        value=app.settings.metadata_auto_apply_threshold,
                        label="{value}%",
                        on_change_end=self.on_metadata_threshold_changed,
                    ),
                    ft.Text(
                        "Совпадения выше порога применяются автоматически; ниже — "
                        "трек помечается «требует проверки».",
                        size=COMPACT_UI.font_micro,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=5,
            )
        )

    def on_metadata_threshold_changed(self, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        try:
            threshold = int(round(float(value)))
        except (TypeError, ValueError):
            return
        threshold = min(
            METADATA_AUTO_APPLY_THRESHOLD_MAX,
            max(METADATA_AUTO_APPLY_THRESHOLD_MIN, threshold),
        )
        self.save_and_apply_settings(
            replace(app.settings, metadata_auto_apply_threshold=threshold)
        )

    def on_metadata_provider_toggled(self, event: object, provider_id: str) -> None:
        control = getattr(event, "control", None)
        checked = bool(getattr(control, "value", False))
        self.set_metadata_provider_enabled(provider_id, checked)

    def toggle_metadata_provider(self, provider_id: str) -> None:
        """Переключает провайдер по клику на чипе (без чекбокса под рукой)."""
        enabled = provider_id not in self.app.settings.metadata_providers
        self.set_metadata_provider_enabled(provider_id, enabled)

    def set_metadata_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        app = self.app
        current = list(app.settings.metadata_providers)
        if enabled and provider_id not in current:
            current.append(provider_id)
        elif not enabled and provider_id in current:
            current.remove(provider_id)
        self.save_and_apply_settings(replace(app.settings, metadata_providers=tuple(current)))

    def open_provider_configuration_dialog(self, provider_id: str) -> None:
        if provider_id == "spotify":
            self.open_spotify_configuration_dialog()

    def open_spotify_configuration_dialog(self) -> None:
        app = self.app
        client_id_field = ft.TextField(
            label="Client ID",
            value=app.settings.spotify_client_id,
            dense=True,
        )
        client_secret_field = ft.TextField(
            label="Client Secret",
            value=app.settings.spotify_client_secret,
            dense=True,
            password=True,
            can_reveal_password=True,
        )

        def save(_: object) -> None:
            client_id = (client_id_field.value or "").strip()
            client_secret = (client_secret_field.value or "").strip()
            self.save_and_apply_settings(
                replace(
                    app.settings,
                    spotify_client_id=client_id,
                    spotify_client_secret=client_secret,
                )
            )
            provider = app.service.plugins.get("spotify")
            configure = getattr(provider, "configure", None)
            if callable(configure):
                configure(client_id, client_secret)
            app.page.pop_dialog()
            app._notify(
                "Spotify настроен" if client_id and client_secret else "Spotify credentials очищены"
            )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Настройка Spotify"),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Бесплатные Client ID и Client Secret нужны для поиска метаданных "
                        "и обложек в Spotify:",
                        size=COMPACT_UI.font_xs,
                    ),
                    ft.Text(
                        "1. Откройте developer.spotify.com/dashboard и войдите со своим "
                        "аккаунтом Spotify.\n"
                        "2. Create app — укажите любое имя и Redirect URI, например "
                        "http://127.0.0.1:9090 (Spotify требует его в форме, даже если вход "
                        "пользователя здесь не используется).\n"
                        "3. Откройте Settings созданного приложения и скопируйте Client ID "
                        "и Client Secret.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    client_id_field,
                    client_secret_field,
                    ft.Text(
                        "Хранится локально в settings.json на этом устройстве.",
                        size=COMPACT_UI.font_micro,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                tight=True,
                spacing=8,
                width=420,
            ),
            actions=[
                ft.Button(content="Отмена", on_click=lambda _: app.page.pop_dialog()),
                ft.Button(content="Сохранить", icon=ft.Icons.SAVE_OUTLINED, on_click=save),
            ],
        )
        app.page.show_dialog(dialog)

    def on_theme_mode_selected(self, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        if not isinstance(value, str) or value not in THEME_MODES:
            return
        self.save_and_apply_settings(replace(app.settings, theme_mode=value))

    def on_theme_palette_selected(self, event: object) -> None:
        app = self.app
        control = getattr(event, "control", None)
        value = getattr(control, "value", None)
        palette_keys = {palette.key for palette in THEME_PALETTES}
        if not isinstance(value, str) or value not in palette_keys:
            return
        self.save_and_apply_settings(replace(app.settings, theme_palette=value))

    def cycle_theme_mode(self, _: object) -> None:
        app = self.app
        order = ("system", "light", "dark")
        try:
            index = order.index(app.settings.theme_mode)
        except ValueError:
            index = 0
        next_mode = order[(index + 1) % len(order)]
        self.save_and_apply_settings(replace(app.settings, theme_mode=next_mode))

    def save_and_apply_settings(self, settings: AppSettings) -> None:
        app = self.app
        try:
            app.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить настройки")
            app._notify(f"Не удалось сохранить настройки: {exc}")
            return

        app.settings = settings
        app._theme_editor_active = False
        app._theme_editor_fields.clear()
        app._theme_editor_swatches.clear()
        app._theme_editor_status = None
        apply_app_theme(app.page, app.settings)
        app.theme_button.icon = theme_mode_icon(app.settings.theme_mode)
        app.theme_button.tooltip = self.theme_tooltip()
        app.status.value = (
            f"Тема: {THEME_MODE_LABELS[app.settings.theme_mode]} · "
            f"{palette_title(app.settings.theme_palette)}"
        )

        if app.navigation.selected_index == 6:
            app.show_settings()
        elif app.navigation.selected_index == 3:
            app.show_plugins()
        else:
            app.page.update()

    def theme_tooltip(self) -> str:
        app = self.app
        return f"Тема: {THEME_MODE_LABELS[app.settings.theme_mode]}. Нажмите для переключения."
