"""Поиск/сортировка/масштаб медиатеки, плеер-бар, диалоги тегов и организации.

Выделено из DJMakerUI (god object).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft
import flet_audio as fta

from djmaker.config import DEFAULT_ORGANIZE_TEMPLATE
from djmaker.domain.library_sort import LIBRARY_SORT_LABELS
from djmaker.domain.models import AudioMetadata, EmbeddedArtwork, MetadataCandidate
from djmaker.plugins.base import MetadataProviderError
from djmaker.settings import LIBRARY_SCALE_MAX, LIBRARY_SCALE_MIN, LIBRARY_SCALE_STEP
from djmaker.ui.density import COMPACT_UI

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)

_LIBRARY_SEARCH_DEBOUNCE_SECONDS = 0.22


@dataclass(slots=True)
class _TagEditorArtworkState:
    changed: bool = False
    artwork: EmbeddedArtwork | None = None


class LibrarySearchController:
    """Владеет поиском/сортировкой/масштабом медиатеки и диалогами тегов/организации."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def build_drop_overlay(self) -> ft.Container:
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

    def build_player_bar(self) -> ft.Container:
        """Создаёт компактный постоянный плеер прослушивания."""
        app = self.app
        return ft.Container(
            visible=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            padding=ft.Padding.symmetric(
                horizontal=COMPACT_UI.status_horizontal_padding,
                vertical=COMPACT_UI.space_sm,
            ),
            content=ft.Row(
                controls=[
                    app.player_play_button,
                    app.player_stop_button,
                    ft.Icon(
                        ft.Icons.HEADPHONES,
                        size=COMPACT_UI.action_icon_size,
                        color=ft.Colors.PRIMARY,
                    ),
                    ft.Column(
                        controls=[app.player_title, app.player_position],
                        spacing=0,
                        width=270,
                    ),
                    app.player_progress,
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

    async def toggle_player(self, _: object) -> None:
        app = self.app
        if app.audio is None:
            return
        try:
            if app._player_state is fta.AudioState.PLAYING:
                await app.audio.pause()
            elif app._player_state is fta.AudioState.COMPLETED:
                await app.audio.play()
            else:
                await app.audio.resume()
        except Exception as exc:
            LOGGER.exception("Ошибка управления плеером")
            app._notify(f"Ошибка плеера: {exc}")

    def set_busy(self, value: bool, message: str = "") -> None:
        app = self.app
        app.busy.visible = value
        if message:
            app.status.value = message
        app.page.update()

    def notify(self, message: str) -> None:
        app = self.app
        app.status.value = message
        app.page.show_dialog(ft.SnackBar(content=ft.Text(message)))
        app.page.update()

    async def on_search(self, _: object) -> None:
        """Немедленно применяет поисковый запрос по Enter."""
        app = self.app
        app._search_revision += 1
        self.show_library(local_update=True)

    async def debounced_library_search(self, revision: int) -> None:
        app = self.app
        await asyncio.sleep(_LIBRARY_SEARCH_DEBOUNCE_SECONDS)
        if revision != app._search_revision or app.navigation.selected_index != 0:
            return
        self.show_library(local_update=True)

    def clear_search(self, _: object) -> None:
        app = self.app
        app.search.value = ""
        app.search_clear_button.visible = False
        app._search_revision += 1
        self.show_library(local_update=True)

    def build_library_search_block(self, result_count: int) -> ft.Container:
        """Возвращает тематический поисковый блок медиатеки."""
        app = self.app
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
                    app.search,
                    self.library_sort_control(),
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

    def library_sort_control(self) -> ft.Row:
        """Выбор порядка не зависит от масштаба строк медиатеки."""
        app = self.app
        descending = app.settings.library_sort_descending
        return ft.Row(
            controls=[
                ft.Dropdown(
                    label="Сортировка",
                    width=210,
                    dense=True,
                    text_size=COMPACT_UI.font_sm,
                    value=app.settings.library_sort,
                    options=[
                        ft.DropdownOption(key=key, text=label)
                        for key, label in LIBRARY_SORT_LABELS.items()
                    ],
                    on_select=app._on_library_sort_selected,
                ),
                ft.IconButton(
                    icon=(
                        ft.Icons.ARROW_DOWNWARD
                        if descending else ft.Icons.ARROW_UPWARD
                    ),
                    icon_size=COMPACT_UI.action_icon_size,
                    tooltip=(
                        "По убыванию. Переключить на возрастание"
                        if descending else
                        "По возрастанию. Переключить на убывание"
                    ),
                    on_click=app._toggle_library_sort_direction,
                ),
            ],
            spacing=0,
            tight=True,
        )

    def library_scale_control(self) -> ft.Control:
        """Строит компактное управление масштабом строк медиатеки."""
        app = self.app
        percent = app.settings.library_scale_percent
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
                        on_click=lambda _: self.change_library_scale(
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
                        on_click=lambda _: self.change_library_scale(
                            LIBRARY_SCALE_STEP
                        ),
                    ),
                ],
                spacing=0,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def change_library_scale(self, delta: int) -> None:
        """Сохраняет новый масштаб и немедленно перестраивает медиатеку."""
        app = self.app
        current = app.settings.library_scale_percent
        target = min(
            LIBRARY_SCALE_MAX,
            max(LIBRARY_SCALE_MIN, current + delta),
        )
        if target == current:
            return

        settings = replace(app.settings, library_scale_percent=target)
        try:
            app.settings_store.save(settings)
        except OSError as exc:
            LOGGER.exception("Не удалось сохранить масштаб медиатеки")
            app._notify(f"Не удалось сохранить масштаб: {exc}")
            return

        app.settings = settings
        selected_track_id = app._selected_track_id
        self.show_library()
        if (
            selected_track_id is not None
            and selected_track_id in app._library_track_indices
        ):
            app.page.run_task(
                app._select_library_track,
                selected_track_id,
            )

    def show_library(self, *, local_update: bool = False) -> None:
        """Отображает локальную медиатеку."""
        app = self.app
        app._set_navigation_index(0)
        try:
            tracks = app.service.tracks(
                app.search.value or "", limit=1000,
                sort_by=app.settings.library_sort,
                descending=app.settings.library_sort_descending,
            )
        except RuntimeError as exc:
            app._notify(str(exc))
            return

        actions = ft.Row(
            controls=[
                ft.Text(
                    f"Показано треков: {len(tracks)}",
                    size=COMPACT_UI.font_sm,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Container(expand=True),
                app._library_batch_controls(),
                app.busy,
                ft.Button(
                    content="Полный анализ",
                    icon=ft.Icons.SPEED,
                    on_click=app._start_audio_analysis,
                ),
                ft.Button(
                    content="Обновить",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _: self.show_library(),
                ),
                self.library_scale_control(),
                app.theme_button,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=COMPACT_UI.space_sm,
        )
        app._waveform_views.clear()
        app._track_row_cards.clear()
        app._library_track_indices = {
            track.id: index for index, track in enumerate(tracks)
        }
        app._library_viewport_extent = 0.0
        app._library_max_scroll_extent = 0.0
        items: list[ft.Control] = []
        if not tracks:
            items.append(
                app._empty_state(
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
            items.extend(app._track_row(track) for track in tracks)
            listing = ft.ListView(
                controls=items,
                expand=True,
                spacing=0,
                item_extent=app._track_item_extent(),
                on_scroll=app._on_library_scroll,
                scroll_interval=50,
            )
        app._library_list = listing
        app.search_clear_button.visible = bool(app.search.value)
        app._replace_content(
            "Медиатека",
            "Поиск, теги и организация локальной музыкальной коллекции",
            actions,
            self.build_library_search_block(len(tracks)),
            listing,
            local_update=local_update,
        )

    @staticmethod
    def on_accent_track_tag_hover(event: ft.Event[ft.Container]) -> None:
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
    def on_neutral_track_tag_hover(event: ft.Event[ft.Container]) -> None:
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

    def open_database_reset_dialog(self, _: object) -> None:
        """Запрашивает подтверждение полного сброса SQLite-медиатеки."""
        app = self.app
        if app.tasks.active_count() > 0:
            app._notify(
                "Сначала остановите или отмените активные задачи перед обнулением БД"
            )
            return

        async def execute(_: object) -> None:
            if app.tasks.active_count() > 0:
                app._notify(
                    "Обнуление отменено: появились активные фоновые задачи"
                )
                return

            app.page.pop_dialog()
            app._set_busy(True, "Обнуление базы данных...")
            try:
                if app.audio is not None:
                    try:
                        await app.audio.pause()
                        await app.audio.seek(ft.Duration(milliseconds=0))
                    except Exception:
                        LOGGER.debug(
                            "Не удалось остановить плеер перед сбросом БД",
                            exc_info=True,
                        )

                await app.workers.run(app.service.reset_library)
            except Exception as exc:
                LOGGER.exception("Ошибка обнуления БД")
                app._notify(f"Не удалось обнулить БД: {exc}")
            else:
                app._clear_library_runtime_state()
                app._notify("База данных обнулена. Музыкальные файлы не изменялись")
                app.show_library()
            finally:
                app._set_busy(False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Обнулить базу данных?"),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Будут удалены все записи медиатеки: музыкальные папки, "
                        "индекс треков, плейлисты, результаты BPM/Key, waveform, ссылки на "
                        "обложки и журнал ошибок сканирования."
                    ),
                    ft.Text(
                        "Музыкальные файлы, их теги, настройки приложения, лог и "
                        "файлы кэша обложек удаляться не будут.",
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        str(app.paths.database),
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
                    on_click=lambda _: app.page.pop_dialog(),
                ),
                ft.Button(
                    content="Обнулить БД",
                    icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                    on_click=execute,
                ),
            ],
        )
        app.page.show_dialog(dialog)

    def clear_library_runtime_state(self) -> None:
        """Сбрасывает UI-состояние, связанное с удалёнными записями БД."""
        app = self.app
        app._selected_playlist_id = None
        app._playlist_export_contexts.clear()
        app._player_request_revision += 1
        app._selected_track_id = None
        app._player_track_id = None
        app._player_track_path = None
        app._player_position_ms = 0
        app._player_duration_ms = 0
        app._player_state = fta.AudioState.STOPPED
        app._player_switching = False
        app._player_load_event = None
        app.player_title.value = ""
        app.player_position.value = "00:00 / 00:00"
        app.player_progress.value = 0.0
        app.player_play_button.icon = ft.Icons.PLAY_ARROW
        app.player_bar.visible = False

        app.search.value = ""
        app.search_clear_button.visible = False
        app._selected_library_track_ids.clear()
        app._library_batch_add_button = None
        app._library_batch_clear_button = None
        app._search_revision += 1
        app._waveform_views.clear()
        app._track_row_cards.clear()
        app._library_track_indices.clear()
        app._library_list = None
        app._library_viewport_extent = 0.0
        app._library_max_scroll_extent = 0.0

    def open_tag_editor(self, track_id: int) -> None:
        app = self.app
        track = app.service.database.get_track(track_id)
        if track is None:
            app._notify("Трек не найден")
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
                app._notify(f"Не удалось выбрать обложку: {exc}")
                return
            if not selected:
                return

            picked = selected[0]
            data = picked.bytes
            if data is None and picked.path:
                try:
                    data = await app.workers.run(Path(picked.path).read_bytes)
                except OSError as exc:
                    app._notify(f"Не удалось прочитать обложку: {exc}")
                    return
            if not data:
                app._notify("Не удалось получить данные обложки")
                return

            try:
                prepared = app.service.prepare_artwork(data)
            except RuntimeError as exc:
                app._notify(str(exc))
                return

            artwork_state.changed = True
            artwork_state.artwork = prepared
            set_artwork_preview(prepared.data)
            artwork_status.value = f"Будет записана: {picked.name}"
            remove_artwork_button.disabled = False
            app.page.update(
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
            app.page.update(
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
                app._format_duration(track.technical.duration),
                *app._track_detail_tags(track),
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
                    track_number=self.parse_int(track_no.value),
                    disc_number=self.parse_int(disc_no.value),
                    bpm=self.parse_float(bpm.value),
                    musical_key=musical_key.value or "",
                )
            except ValueError as exc:
                app._notify(str(exc))
                return

            app._set_busy(True, "Сохранение тегов...")
            try:
                await app.workers.run(
                    app.service.update_tags,
                    track_id,
                    new_metadata,
                    replace_artwork=artwork_state.changed,
                    artwork=artwork_state.artwork,
                )
            except Exception as exc:
                LOGGER.exception("Ошибка сохранения тегов")
                app._notify(f"Не удалось сохранить теги: {exc}")
            else:
                app.page.pop_dialog()
                app._notify("Теги сохранены")
                app._selected_track_id = track_id
                app.show_library()
                app.page.run_task(app._select_library_track, track_id)
            finally:
                app._set_busy(False)

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
                    on_click=lambda _: app.page.pop_dialog(),
                ),
                ft.Button(
                    content="Сохранить",
                    icon=ft.Icons.SAVE_OUTLINED,
                    on_click=save,
                ),
            ],
        )
        app.page.show_dialog(dialog)

    def open_organizer(self, track_id: int) -> None:
        app = self.app
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
                app._notify(f"Не удалось выбрать папку: {exc}")
                return
            if selected:
                destination.value = selected
                destination.update()

        async def execute(_: object) -> None:
            if not destination.value:
                app._notify("Выберите папку назначения")
                return
            app._set_busy(True, "Перенос файла...")
            try:
                updated = await app.workers.run(
                    app.service.organize_track,
                    track_id,
                    Path(destination.value),
                    template.value or DEFAULT_ORGANIZE_TEMPLATE,
                )
            except Exception as exc:
                LOGGER.exception("Ошибка организации файла")
                app._notify(f"Не удалось организовать файл: {exc}")
            else:
                app.page.pop_dialog()
                app._notify(f"Файл перемещён: {updated.path}")
                app.show_library()
            finally:
                app._set_busy(False)

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
                ft.Button(content="Отмена", on_click=lambda _: app.page.pop_dialog()),
                ft.Button(content="Переместить", icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED, on_click=execute),
            ],
        )
        app.page.show_dialog(dialog)

    async def metadata_search(self, track_id: int) -> None:
        app = self.app
        app._set_busy(True, "Поиск в MusicBrainz...")
        try:
            candidates = await app.workers.run(app.service.search_metadata, track_id, "musicbrainz", 8)
        except (MetadataProviderError, RuntimeError, OSError) as exc:
            LOGGER.exception("Ошибка онлайн-поиска")
            app._notify(f"Ошибка поиска метаданных: {exc}")
        else:
            self.open_candidates(track_id, candidates)
        finally:
            app._set_busy(False)

    def open_candidates(self, track_id: int, candidates: list[MetadataCandidate]) -> None:
        app = self.app
        if not candidates:
            app._notify("MusicBrainz не нашёл подходящих вариантов")
            return

        rows: list[ft.Control] = []
        dialog: ft.AlertDialog

        for candidate in candidates:
            async def apply(_: object, value: MetadataCandidate = candidate) -> None:
                app._set_busy(True, "Применение метаданных...")
                try:
                    await app.workers.run(app.service.apply_candidate, track_id, value)
                except Exception as exc:
                    LOGGER.exception("Ошибка применения онлайн-метаданных")
                    app._notify(f"Не удалось применить метаданные: {exc}")
                else:
                    app.page.pop_dialog()
                    app._notify("Метаданные применены")
                    app.show_library()
                finally:
                    app._set_busy(False)

            rows.append(
                app._surface_card(
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
            actions=[ft.Button(content="Закрыть", on_click=lambda _: app.page.pop_dialog())],
        )
        app.page.show_dialog(dialog)

    @staticmethod
    def parse_int(value: str | None) -> int | None:
        if value is None or not value.strip():
            return None
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValueError(f"Ожидалось целое число: {value}") from exc

    @staticmethod
    def parse_float(value: str | None) -> float | None:
        if value is None or not value.strip():
            return None
        try:
            return float(value.strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"Некорректный BPM: {value}") from exc
