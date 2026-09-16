"""Интерфейс плейлистов и управляемого экспорта для DJMakerUI."""

from __future__ import annotations

import logging
from pathlib import Path

import flet as ft

from djmaker.domain.models import TrackRecord
from djmaker.infrastructure.playlists import Playlist, PlaylistError
from djmaker.services.playlist_export import (
    ExportTrack,
    PlaylistExportRequest,
    export_playlist,
)
from djmaker.services.tasks import TaskCancelled, TaskKind, TaskPaused, TaskStatus
from djmaker.ui.density import COMPACT_UI

LOGGER = logging.getLogger(__name__)
PLAYLIST_NAV_INDEX = 7


def duration_label(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class PlaylistUI:
    """Методы экрана плейлистов; использует сервисы основного окна."""

    def show_playlists(self, *, local_update: bool = False) -> None:
        self._set_navigation_index(PLAYLIST_NAV_INDEX)
        self._waveform_views.clear()
        self._track_row_cards.clear()
        self._library_track_indices.clear()
        self._library_list = None
        try:
            playlists = self.service.playlists.list()
            selected = next(
                (p for p in playlists if p.id == self._selected_playlist_id), None
            )
            if selected is None and playlists:
                selected = playlists[0]
            self._selected_playlist_id = selected.id if selected else None
            tracks = self.service.playlists.tracks(selected.id) if selected else []
        except PlaylistError as exc:
            self._notify(str(exc))
            return
        chooser = ft.Dropdown(
            label="Плейлист",
            width=260,
            dense=True,
            value=str(selected.id) if selected else None,
            options=[ft.DropdownOption(key=str(p.id), text=p.name) for p in playlists],
            on_select=self._on_playlist_selected,
        )
        toolbar = ft.Row(
            controls=[
                chooser,
                ft.Button(
                    content="Создать",
                    icon=ft.Icons.ADD,
                    on_click=lambda _: self._playlist_name_dialog(),
                ),
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    tooltip="Переименовать плейлист",
                    disabled=selected is None,
                    on_click=lambda _: self._playlist_name_dialog(selected),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    tooltip="Удалить плейлист",
                    disabled=selected is None,
                    on_click=lambda _: self._delete_playlist_dialog(selected),
                ),
            ],
            wrap=True,
            spacing=COMPACT_UI.space_sm,
        )
        actions = ft.Row(
            controls=[
                ft.Button(
                    content="Экспорт .m3u8",
                    disabled=not tracks,
                    on_click=lambda _: self.page.run_task(
                        self._start_playlist_export, False
                    ),
                ),
                ft.Button(
                    content="Экспорт с файлами",
                    icon=ft.Icons.FOLDER_COPY_OUTLINED,
                    disabled=not tracks,
                    on_click=lambda _: self.page.run_task(
                        self._start_playlist_export, True
                    ),
                ),
            ],
            wrap=True,
        )
        count = len(tracks)
        duration = sum(
            t.technical.duration or 0 for t in tracks if (t.technical.duration or 0) > 0
        )
        unknown = sum(
            1 for t in tracks if not t.technical.duration or t.technical.duration <= 0
        )
        summary = f"Треков: {count} · Длительность: {duration_label(duration)}"
        if unknown:
            summary += f" · Неизвестная длительность: {unknown}"
        listing = ft.ListView(
            controls=[
                self._playlist_track_row(selected.id, track, index, count)
                for index, track in enumerate(tracks)
            ]
            if selected
            else [],
            expand=True,
            spacing=4,
            item_extent=94,
        )
        body = (
            listing
            if tracks
            else self._empty_state(
                ft.Icons.QUEUE_MUSIC,
                "Плейлист пуст" if selected else "Создайте первый плейлист",
                "Добавляйте треки через меню ⋮ в строке медиатеки.",
            )
        )
        self._replace_content(
            "Плейлисты",
            "Подготовка DJ-сетов",
            toolbar,
            ft.Text(summary, size=COMPACT_UI.font_sm),
            actions,
            body,
            local_update=local_update,
        )

    def _on_playlist_selected(self, event: object) -> None:
        value = getattr(getattr(event, "control", None), "value", None)
        try:
            self._selected_playlist_id = int(value)
        except TypeError, ValueError:
            return
        self.show_playlists(local_update=True)

    def _playlist_track_row(
        self, playlist_id: int, track: TrackRecord, index: int, count: int
    ) -> ft.Control:
        title = (
            " - ".join(filter(None, (track.metadata.artist, track.metadata.title)))
            or track.path.stem
        )
        return self._surface_card(
            ft.Row(
                controls=[
                    ft.Text(str(index + 1), width=34, size=COMPACT_UI.font_sm),
                    ft.IconButton(
                        icon=ft.Icons.PLAY_ARROW,
                        tooltip="Прослушать трек",
                        on_click=lambda _: self.page.run_task(
                            self._play_track, track.id
                        ),
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                title, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS
                            ),
                            ft.Text(
                                f"{self._track_bpm_label(track)} · {self._track_key_label(track)}",
                                size=COMPACT_UI.font_xs,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            self._track_path_link(track),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_UPWARD,
                        tooltip="На позицию выше",
                        disabled=index == 0,
                        on_click=lambda _: self._move_playlist_track(
                            playlist_id, track.id, -1
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_DOWNWARD,
                        tooltip="На позицию ниже",
                        disabled=index == count - 1,
                        on_click=lambda _: self._move_playlist_track(
                            playlist_id, track.id, 1
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.REMOVE_CIRCLE_OUTLINE,
                        tooltip="Убрать из плейлиста",
                        on_click=lambda _: self._remove_playlist_track(
                            playlist_id, track.id
                        ),
                    ),
                ],
                spacing=4,
            ),
            padding=8,
        )

    def _move_playlist_track(self, playlist_id: int, track_id: int, delta: int) -> None:
        try:
            self.service.playlists.move(playlist_id, track_id, delta)
        except PlaylistError as exc:
            self._notify(str(exc))
        self.show_playlists(local_update=True)

    def _remove_playlist_track(self, playlist_id: int, track_id: int) -> None:
        try:
            self.service.playlists.remove(playlist_id, track_id)
        except PlaylistError as exc:
            self._notify(str(exc))
        self.show_playlists(local_update=True)

    def _playlist_name_dialog(
        self, playlist: Playlist | None = None, track_id: int | None = None
    ) -> None:
        field = ft.TextField(
            label="Название плейлиста",
            value=playlist.name if playlist else "",
            max_length=120,
            autofocus=True,
        )

        def save(_: object) -> None:
            try:
                if playlist:
                    self.service.playlists.rename(playlist.id, field.value or "")
                    ident = playlist.id
                else:
                    ident = self.service.playlists.create(
                        field.value or "", track_id=track_id
                    )
                self._selected_playlist_id = ident
            except PlaylistError as exc:
                field.error_text = str(exc)
                self.page.update(field)
                return
            self.page.pop_dialog()
            if self.navigation.selected_index == PLAYLIST_NAV_INDEX:
                self.show_playlists(local_update=True)
            else:
                self._notify(
                    "Трек добавлен в новый плейлист"
                    if track_id is not None
                    else "Плейлист создан"
                )

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(
                    "Переименовать плейлист" if playlist else "Создать плейлист"
                ),
                content=field,
                actions=[
                    ft.Button(
                        content="Отмена", on_click=lambda _: self.page.pop_dialog()
                    ),
                    ft.Button(content="Сохранить", on_click=save),
                ],
            )
        )

    def _delete_playlist_dialog(self, playlist: Playlist | None) -> None:
        if playlist is None:
            return

        def delete(_: object) -> None:
            try:
                self.service.playlists.delete(playlist.id)
            except PlaylistError as exc:
                self._notify(str(exc))
                return
            self.page.pop_dialog()
            self._selected_playlist_id = None
            self.show_playlists(local_update=True)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Удалить «{playlist.name}»?"),
                content=ft.Text(
                    "Удалится только плейлист. Треки останутся в медиатеке и на диске."
                ),
                actions=[
                    ft.Button(
                        content="Отмена", on_click=lambda _: self.page.pop_dialog()
                    ),
                    ft.Button(content="Удалить", on_click=delete),
                ],
            )
        )

    def _playlist_track_menu(self, track_id: int) -> ft.PopupMenuButton:
        return ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            tooltip="Меню трека",
            icon_size=self._library_size(COMPACT_UI.action_icon_size),
            padding=self._library_size(COMPACT_UI.space_xs),
            items=[
                ft.PopupMenuItem(
                    content="Добавить в плейлист",
                    icon=ft.Icons.PLAYLIST_ADD,
                    on_click=lambda _: self._add_to_playlist_dialog(track_id),
                )
            ],
        )

    def _add_to_playlist_dialog(self, track_id: int) -> None:
        try:
            playlists = self.service.playlists.list()
        except PlaylistError as exc:
            self._notify(str(exc))
            return
        if not playlists:
            self._playlist_name_dialog(track_id=track_id)
            return
        chooser = ft.Dropdown(
            label="Плейлист",
            width=350,
            value=str(playlists[0].id),
            options=[ft.DropdownOption(key=str(p.id), text=p.name) for p in playlists],
        )

        def add(_: object) -> None:
            try:
                added = self.service.playlists.add(int(chooser.value), track_id)
            except (PlaylistError, ValueError, TypeError) as exc:
                self._notify(str(exc))
                return
            self.page.pop_dialog()
            self._notify("Трек добавлен" if added else "Этот трек уже есть в плейлисте")

        def create(_: object) -> None:
            self.page.pop_dialog()
            self._playlist_name_dialog(track_id=track_id)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Добавить трек в плейлист"),
                content=chooser,
                actions=[
                    ft.Button(
                        content="Отмена", on_click=lambda _: self.page.pop_dialog()
                    ),
                    ft.Button(content="Новый плейлист", on_click=create),
                    ft.Button(content="Добавить", on_click=add),
                ],
            )
        )

    async def _start_playlist_export(self, copy_files: bool) -> None:
        playlist_id = self._selected_playlist_id
        if playlist_id is None:
            return
        try:
            destination = await ft.FilePicker().get_directory_path(
                dialog_title="Выберите папку: внутри будет создана новая папка экспорта",
            )
            if not destination:
                return
            playlist = next(
                (p for p in self.service.playlists.list() if p.id == playlist_id), None
            )
            if playlist is None:
                raise PlaylistError("Плейлист больше не существует")
            tracks = self.service.playlists.tracks(playlist_id)
            if not tracks:
                raise PlaylistError("В плейлисте нет треков")
            request = PlaylistExportRequest(
                playlist.name,
                tuple(ExportTrack.from_track(t) for t in tracks),
                Path(destination),
                copy_files,
            )
            task = self.tasks.create(
                kind=TaskKind.PLAYLIST_EXPORT,
                title=f"Экспорт: {playlist.name}",
                total=len(tracks),
            )
            self._playlist_export_contexts[task.id] = request
        except Exception as exc:
            LOGGER.exception("Не удалось начать экспорт плейлиста")
            self._notify(f"Не удалось начать экспорт: {exc}")
            return
        self.show_tasks()
        await self._run_playlist_export(task.id)

    async def _run_playlist_export(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        request = self._playlist_export_contexts.get(task_id)
        if task is None or request is None:
            return
        task.set_progress(
            completed=0, succeeded=0, failed=0, detail="Подготовка экспорта"
        )

        def progress(completed: int, detail: str) -> None:
            task.set_progress(completed=completed, succeeded=completed, detail=detail)

        try:
            output = await self.workers.run(
                export_playlist, request, task=task, progress=progress
            )
        except TaskPaused:
            task.mark_paused("Остановлено. При продолжении экспорт начнётся заново")
            if task.snapshot().status is TaskStatus.CANCELLED:
                self._forget_task_context(task_id)
        except TaskCancelled:
            task.mark_cancelled()
            self._forget_task_context(task_id)
        except Exception as exc:
            LOGGER.exception("Ошибка экспорта плейлиста")
            task.mark_failed(exc)
            self._forget_task_context(task_id)
            self._notify(f"Экспорт не завершён: {exc}")
        else:
            task.mark_completed(f"Готово: {output}")
            self._forget_task_context(task_id)
            self._notify(f"Плейлист экспортирован: {output}")
        finally:
            self._refresh_task_indicator()
            if self.navigation.selected_index == 5:
                self.show_tasks()
