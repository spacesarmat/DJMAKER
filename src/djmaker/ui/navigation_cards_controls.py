"""Навигационные экраны (Папки/Дубликаты/Плагины/Аудио-модули) и общие карточки.

Выделено из DJMakerUI (god object).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

try:
    import flet_dropzone as ftd
except ImportError:  # pragma: no cover - зависит от desktop extension runtime
    ftd = None

from djmaker.config import DEFAULT_TARGET_LUFS
from djmaker.services.tasks import TaskKind
from djmaker.ui.density import COMPACT_UI
from djmaker.ui.task_progress_controls import _AudioTaskContext
from djmaker.ui.theme import apply_app_theme

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI


class NavigationCardsController:
    """Владеет навигационными экранами и общими карточками; состояние — в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def build(self) -> None:
        """Строит главное окно приложения."""
        app = self.app
        app.page.title = "DJMAKER"
        app.page.padding = 0
        app.page.spacing = 0
        app.page.window.min_width = 980
        app.page.window.min_height = 640
        apply_app_theme(app.page, app.settings)

        workspace = ft.Column(
            controls=[
                ft.Container(
                    content=app.content,
                    expand=True,
                    padding=COMPACT_UI.content_padding,
                ),
                app.player_bar,
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
                            app.status,
                            ft.Container(expand=True),
                            app.task_button,
                            app.task_count_text,
                            app.analysis_progress_text,
                            app.analysis_progress,
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
                app.navigation,
                ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                workspace,
            ],
            spacing=0,
            expand=True,
        )
        root_content = ft.Stack(
            controls=[main_row, app._drop_overlay],
            expand=True,
        )
        if self.native_drop_enabled():
            dropzone = ftd.Dropzone(
                content=root_content,
                expand=True,
                on_dropped=app._on_paths_dropped,
                on_entered=app._on_drop_entered,
                on_exited=app._on_drop_exited,
            )
            app.page.add(dropzone)
        else:
            app.page.add(root_content)
        app.show_library()

    @staticmethod
    def native_drop_enabled() -> bool:
        """Проверяет, запущен ли desktop runtime с собранными extensions."""
        return ftd is not None and bool(os.getenv("FLET_DART_BRIDGE_PORT"))

    def set_navigation_index(self, index: int) -> None:
        app = self.app
        if app.navigation.selected_index == 6 and index != 6:
            self.close_theme_editor_session()
        if app.navigation.selected_index != index:
            app.navigation.selected_index = index

    def close_theme_editor_session(self) -> None:
        app = self.app
        if not app._theme_editor_active:
            return
        app._theme_editor_active = False
        app._theme_editor_fields.clear()
        app._theme_editor_swatches.clear()
        app._theme_editor_status = None
        apply_app_theme(app.page, app.settings)

    def replace_content(
        self,
        title: str,
        subtitle: str,
        *controls: ft.Control,
        local_update: bool = False,
    ) -> None:
        del title, subtitle
        app = self.app
        app.content.controls.clear()
        app.content.controls.extend(controls)
        if local_update:
            app.page.update(app.content)
        else:
            app.page.update()

    @staticmethod
    def surface_card(
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

    def empty_state(self, icon: object, title: str, description: str) -> ft.Control:
        return self.surface_card(
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

    def show_folders(self) -> None:
        """Отображает корневые папки и действия сканирования."""
        app = self.app
        self.set_navigation_index(1)
        try:
            roots = app.service.roots()
        except RuntimeError as exc:
            app._notify(str(exc))
            return

        drop_message = (
            "Перетащите в окно DJMAKER файлы или папки — неподдерживаемое "
            "будет отфильтровано автоматически."
            if self.native_drop_enabled()
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
                        on_click=app._pick_and_scan,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            self.surface_card(
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
                self.empty_state(
                    ft.Icons.FOLDER_OUTLINED,
                    "Нет музыкальных папок",
                    "Выберите корневую папку с музыкой. DJMAKER проиндексирует поддерживаемые файлы.",
                )
            )
        for root in roots:
            controls.append(
                self.surface_card(
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
                                on_click=lambda _, path=root: app._scan_existing(path),
                            ),
                        ]
                    )
                )
            )
        self.replace_content(
            "Папки",
            "Источники медиатеки и повторное сканирование",
            *controls,
        )

    def scan_existing(self, path: Path) -> None:
        app = self.app
        app.page.run_task(app._run_scan, path)

    def show_duplicates(self) -> None:
        """Показывает точные дубликаты по SHA-256."""
        app = self.app
        self.set_navigation_index(2)
        try:
            groups = app.service.exact_duplicates()
        except RuntimeError as exc:
            app._notify(str(exc))
            return

        controls: list[ft.Control] = [
            self.surface_card(
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
                self.empty_state(
                    ft.Icons.CONTENT_COPY_OUTLINED,
                    "Точные дубликаты не найдены",
                    "Второй уровень сравнения по аудио-fingerprint будет добавлен отдельным модулем.",
                )
            )
        for index, group in enumerate(groups, start=1):
            total_size = sum(track.size for track in group.tracks)
            rows: list[ft.Control] = [
                ft.Text(
                    f"Группа {index} · {len(group.tracks)} файлов · {self.format_size(total_size)}",
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(f"SHA-256: {group.file_hash}", size=COMPACT_UI.font_micro),
            ]
            rows.extend(ft.Text(f"• {track.path}", size=COMPACT_UI.font_xs) for track in group.tracks)
            controls.append(self.surface_card(ft.Column(controls=rows, spacing=5)))

        self.replace_content(
            "Дубликаты",
            "Поиск идентичных файлов в медиатеке",
            *controls,
        )

    def show_plugins(self) -> None:
        """Показывает подключённые внешние провайдеры."""
        app = self.app
        self.set_navigation_index(3)
        providers = app.service.plugins.all()
        controls: list[ft.Control] = [
            self.surface_card(
                ft.Text(
                    "Каждый внешний каталог или DJ-пул подключается отдельным плагином. "
                    "Ядро медиатеки от конкретного сервиса не зависит."
                )
            ),
            self.bulk_metadata_search_card(),
            self.metadata_review_card(),
        ]
        for provider in providers:
            controls.append(
                self.surface_card(
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
                self.empty_state(
                    ft.Icons.CLOUD_OFF_OUTLINED,
                    "Нет подключённых провайдеров",
                    "Онлайн-каталоги появятся здесь после подключения плагинов.",
                )
            )
        self.replace_content(
            "Онлайн-метаданные",
            "Плагины музыкальных каталогов и DJ-пулов",
            *controls,
        )

    def bulk_metadata_search_card(self) -> ft.Control:
        app = self.app
        selected_ids = getattr(app, "_selected_library_track_ids", set())

        def start_all(_: object) -> None:
            try:
                tracks = app.service.tracks(limit=10_000)
            except RuntimeError as exc:
                app._notify(str(exc))
                return
            app._start_metadata_bulk_search(tracks)

        def start_selected(_: object) -> None:
            tracks = [
                track
                for track_id in selected_ids
                if (track := app.service.database.get_track(track_id)) is not None
            ]
            app._start_metadata_bulk_search(tracks)

        return self.surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.TRAVEL_EXPLORE, color=ft.Colors.PRIMARY),
                            ft.Text(
                                "Массовый поиск метаданных",
                                size=COMPACT_UI.font_lg,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ]
                    ),
                    ft.Text(
                        "Совпадения выше порога (см. настройки) применяются "
                        "автоматически; остальные попадают в «Требуют внимания» ниже.",
                        size=COMPACT_UI.font_xs,
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Найти для всех",
                                icon=ft.Icons.LIBRARY_MUSIC,
                                on_click=start_all,
                            ),
                            ft.Button(
                                content=f"Найти для выделенных ({len(selected_ids)})",
                                icon=ft.Icons.CHECK_BOX_OUTLINED,
                                disabled=not selected_ids,
                                on_click=start_selected,
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                ],
                spacing=5,
            )
        )

    def metadata_review_card(self) -> ft.Control:
        app = self.app
        try:
            flagged = app.service.database.list_tracks_needing_review()
        except RuntimeError as exc:
            app._notify(str(exc))
            flagged = []

        reason_labels = {
            "not_found": "Не найдено",
            "low_confidence": "Низкое совпадение",
            "search_failed": "Все источники недоступны",
        }

        rows: list[ft.Control] = []
        for track in flagged:
            reason = reason_labels.get(track.metadata_review_reason, track.metadata_review_reason)
            score = track.metadata_review_score
            detail = f"{reason} · {score:.0%}" if score is not None else reason
            rows.append(
                self.surface_card(
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        track.metadata.title or track.path.stem,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Text(
                                        f"{track.metadata.artist or '—'} · {detail}",
                                        size=COMPACT_UI.font_xs,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                            ft.Button(
                                content="Найти",
                                icon=ft.Icons.SEARCH,
                                on_click=lambda _, tid=track.id: app.page.run_task(
                                    app._metadata_search, tid
                                ),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                tooltip="Оставить как есть",
                                on_click=lambda _, tid=track.id: app._dismiss_metadata_review(tid),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=5,
                )
            )

        content: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.ERROR),
                    ft.Text(
                        f"Требуют внимания ({len(flagged)})",
                        size=COMPACT_UI.font_lg,
                        weight=ft.FontWeight.BOLD,
                    ),
                ]
            ),
        ]
        if rows:
            content.extend(rows)
        else:
            content.append(
                ft.Text(
                    "Треков без уверенного совпадения нет.",
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )

        return self.surface_card(ft.Column(controls=content, spacing=5))

    def dismiss_metadata_review(self, track_id: int) -> None:
        app = self.app
        try:
            app.service.database.clear_metadata_review(track_id)
        except RuntimeError as exc:
            app._notify(str(exc))
            return
        if app.navigation.selected_index == 3:
            self.show_plugins()

    def show_audio_modules(self) -> None:
        """Показывает доступные DSP-модули и управление анализом."""
        app = self.app
        self.set_navigation_index(4)
        try:
            total, analyzed = app.service.analysis_counts()
            grid_total, grid_analyzed = app.service.beat_grid_counts()
            waveform_total, waveform_analyzed = app.service.waveform_counts()
        except RuntimeError as exc:
            app._notify(str(exc))
            return

        pending = max(0, total - analyzed)
        grid_pending = max(0, grid_total - grid_analyzed)
        waveform_pending = max(0, waveform_total - waveform_analyzed)
        analysis_card = self.surface_card(
            ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SPEED, size=18, color=ft.Colors.PRIMARY),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        "Полный анализ: BPM / сетка / Key / Camelot",
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
                                f"BPM/Key: {analyzed}/{total} · "
                                f"сетка: {grid_analyzed}/{grid_total} · "
                                f"в очереди: {max(pending, grid_pending)}",
                                size=COMPACT_UI.font_xs,
                            ),
                        ]
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Анализировать новые",
                                icon=ft.Icons.SPEED,
                                on_click=app._start_audio_analysis,
                            ),
                            ft.Button(
                                content="Пересчитать всё",
                                icon=ft.Icons.REFRESH,
                                on_click=lambda event: app._start_audio_analysis(
                                    event,
                                    force=True,
                                ),
                            ),
                        ],
                        spacing=COMPACT_UI.space_sm,
                    ),
                    ft.Text(
                        "Результат хранится отдельно от тегов: точный BPM, все "
                        "обнаруженные доли, первая сильная доля, стабильность темпа, "
                        "тональность, лад, Camelot и confidence Essentia.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=COMPACT_UI.space_sm,
            )
        )

        waveform_card = self.surface_card(
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
                                on_click=app._start_waveform_analysis,
                            ),
                            ft.Button(
                                content="Перестроить всё",
                                icon=ft.Icons.REFRESH,
                                on_click=lambda event: app._start_waveform_analysis(
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
            self.surface_card(
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
        self.replace_content(
            "Аудио-модули",
            "Независимые этапы анализа и обработки аудио",
            *controls,
        )

    def start_audio_analysis(self, _: object, *, force: bool = False) -> None:
        app = self.app
        existing = app.tasks.active_for_kind(TaskKind.AUDIO_ANALYSIS)
        if existing is not None:
            app._notify(
                "Полный аудио-анализ уже выполняется или остановлен. "
                "Откройте «Задачи» для управления."
            )
            return

        task = app.tasks.create(
            kind=TaskKind.AUDIO_ANALYSIS,
            title="Полный анализ BPM / сетка / Key",
            detail="Подготовка FFmpeg и Essentia...",
        )
        app._audio_task_contexts[task.id] = _AudioTaskContext(force=force)
        app._refresh_task_indicator()
        app.page.run_task(app._run_audio_analysis, task.id)

    @staticmethod
    def format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"
