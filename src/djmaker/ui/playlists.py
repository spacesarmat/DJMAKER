"""Интерфейс плейлистов и управляемого экспорта для DJMakerUI."""

from __future__ import annotations

import logging
from pathlib import Path

import flet as ft

from djmaker.domain.models import TrackRecord
from djmaker.domain.set_planner import (
    DEFAULT_MATCH_MODE,
    MATCH_MODE_LABELS,
    recommend_tracks,
    track_bpm,
)
from djmaker.domain.set_timeline import (
    ALLOWED_BARS_PER_SQUARE,
    ALLOWED_SQUARE_COUNTS,
    SetTimelineError,
    build_transition_plan,
    move_by_beats,
    snap_to_beat,
)
from djmaker.infrastructure.playlists import Playlist, PlaylistError
from djmaker.infrastructure.set_timeline import (
    SavedTransition,
    SetTimelineRepositoryError,
)
from djmaker.services.playlist_export import (
    ExportTrack,
    PlaylistExportRequest,
    export_playlist,
)
from djmaker.services.transition_preview import (
    TransitionPreviewError,
    render_transition_preview,
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

    def _library_selection_checkbox(self, track_id: int) -> ft.Checkbox:
        selected_track_ids = getattr(self, "_selected_library_track_ids", set())
        return ft.Checkbox(
            value=track_id in selected_track_ids,
            tooltip="Выбрать для добавления в плейлист",
            on_change=lambda event: self._on_library_track_selection(event, track_id),
        )

    def _on_library_track_selection(self, event: object, track_id: int) -> None:
        if not hasattr(self, "_selected_library_track_ids"):
            self._selected_library_track_ids = set()
        selected = bool(getattr(getattr(event, "control", None), "value", False))
        if selected:
            self._selected_library_track_ids.add(track_id)
        else:
            self._selected_library_track_ids.discard(track_id)
        self._refresh_library_batch_controls()

    def _library_batch_controls(self) -> ft.Row:
        count = len(getattr(self, "_selected_library_track_ids", set()))
        self._library_batch_add_button = ft.Button(
            content=f"В плейлист: {count}",
            icon=ft.Icons.PLAYLIST_ADD,
            disabled=count == 0,
            on_click=lambda _: self._add_selected_to_playlist_dialog(),
        )
        self._library_batch_clear_button = ft.IconButton(
            icon=ft.Icons.CLEAR_ALL,
            tooltip="Снять выделение",
            disabled=count == 0,
            on_click=lambda _: self._clear_library_selection(),
        )
        return ft.Row(
            controls=[
                self._library_batch_add_button,
                self._library_batch_clear_button,
            ],
            spacing=0,
            tight=True,
        )

    def _refresh_library_batch_controls(self) -> None:
        count = len(getattr(self, "_selected_library_track_ids", set()))
        controls: list[ft.Control] = []
        if self._library_batch_add_button is not None:
            self._library_batch_add_button.content = f"В плейлист: {count}"
            self._library_batch_add_button.disabled = count == 0
            controls.append(self._library_batch_add_button)
        if self._library_batch_clear_button is not None:
            self._library_batch_clear_button.disabled = count == 0
            controls.append(self._library_batch_clear_button)
        if controls:
            self.page.update(*controls)

    def _clear_library_selection(self) -> None:
        selected_track_ids = getattr(self, "_selected_library_track_ids", set())
        if not selected_track_ids:
            return
        selected_track_ids.clear()
        self.show_library(local_update=True)

    def _ordered_library_selection(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                getattr(self, "_selected_library_track_ids", set()),
                key=lambda track_id: (
                    self._library_track_indices.get(track_id, 1_000_000_000),
                    track_id,
                ),
            )
        )

    def _add_selected_to_playlist_dialog(self) -> None:
        track_ids = self._ordered_library_selection()
        if not track_ids:
            return
        try:
            playlists = self.service.playlists.list()
        except PlaylistError as exc:
            self._notify(str(exc))
            return
        if not playlists:
            self._playlist_name_dialog(track_ids=track_ids)
            return
        chooser = ft.Dropdown(
            label="Плейлист",
            width=350,
            value=str(playlists[0].id),
            options=[ft.DropdownOption(key=str(p.id), text=p.name) for p in playlists],
        )

        def add(_: object) -> None:
            try:
                added, skipped = self.service.playlists.add_many(
                    int(chooser.value), track_ids
                )
            except (PlaylistError, TypeError, ValueError) as exc:
                self._notify(str(exc))
                return
            self.page.pop_dialog()
            self._selected_library_track_ids.clear()
            self.show_library(local_update=True)
            self._notify(f"Добавлено: {added} · уже были в плейлисте: {skipped}")

        def create(_: object) -> None:
            self.page.pop_dialog()
            self._playlist_name_dialog(track_ids=track_ids)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Добавить выбранные треки: {len(track_ids)}"),
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
                ft.Button(
                    content="Подобрать продолжение",
                    icon=ft.Icons.AUTO_AWESOME,
                    disabled=not tracks,
                    on_click=lambda _: self._open_set_recommendations(),
                ),
                ft.Button(
                    content="Монтаж переходов",
                    icon=ft.Icons.MULTILINE_CHART,
                    disabled=len(tracks) < 2,
                    on_click=lambda _: self._open_transition_editor(),
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
        self,
        playlist: Playlist | None = None,
        track_id: int | None = None,
        track_ids: tuple[int, ...] | None = None,
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
                elif track_ids:
                    ident = self.service.playlists.create_with_tracks(
                        field.value or "", track_ids
                    )
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
            if track_ids:
                self._selected_library_track_ids.clear()
            if self.navigation.selected_index == PLAYLIST_NAV_INDEX:
                self.show_playlists(local_update=True)
            else:
                self._notify(
                    f"Создан плейлист · добавлено треков: {len(track_ids)}"
                    if track_ids
                    else (
                        "Трек добавлен в новый плейлист"
                        if track_id is not None
                        else "Плейлист создан"
                    )
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

    def _open_set_recommendations(self) -> None:
        playlist_id = self._selected_playlist_id
        if playlist_id is None:
            return
        try:
            playlist_tracks = self.service.playlists.tracks(playlist_id)
            library_tracks = self.service.tracks("", limit=10_000)
        except (PlaylistError, RuntimeError) as exc:
            self._notify(str(exc))
            return
        if not playlist_tracks:
            self._notify("Сначала добавьте в плейлист опорный трек")
            return

        playlist_by_id = {track.id: track for track in playlist_tracks}
        excluded = set(playlist_by_id)
        chosen: set[int] = set()
        current_recommendations = []
        reference = ft.Dropdown(
            label="Опорный трек",
            width=390,
            value=str(playlist_tracks[-1].id),
            options=[
                ft.DropdownOption(
                    key=str(track.id),
                    text=(
                        " - ".join(
                            filter(
                                None,
                                (track.metadata.artist, track.metadata.title),
                            )
                        )
                        or track.path.stem
                    ),
                )
                for track in playlist_tracks
            ],
        )
        mode = ft.Dropdown(
            label="Строгость",
            width=210,
            value=DEFAULT_MATCH_MODE,
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in MATCH_MODE_LABELS.items()
            ],
        )
        status = ft.Text("", size=COMPACT_UI.font_xs)
        results = ft.ListView(height=390, width=790, spacing=4)
        add_button = ft.Button(
            content="Добавить выбранные: 0",
            icon=ft.Icons.PLAYLIST_ADD,
            disabled=True,
        )

        def update_add_button() -> None:
            add_button.content = f"Добавить выбранные: {len(chosen)}"
            add_button.disabled = not chosen
            self.page.update(add_button)

        def select_candidate(event: object, track_id: int) -> None:
            if bool(getattr(getattr(event, "control", None), "value", False)):
                chosen.add(track_id)
            else:
                chosen.discard(track_id)
            update_add_button()

        def render(_: object | None = None) -> None:
            chosen.clear()
            add_button.content = "Добавить выбранные: 0"
            add_button.disabled = True
            try:
                reference_track = playlist_by_id[int(reference.value)]
            except (KeyError, TypeError, ValueError):
                return
            current_recommendations[:] = recommend_tracks(
                reference_track,
                library_tracks,
                mode=str(mode.value or DEFAULT_MATCH_MODE),
                excluded_track_ids=excluded,
                limit=50,
            )
            controls: list[ft.Control] = []
            for recommendation in current_recommendations:
                track = recommendation.track
                title = (
                    " - ".join(
                        filter(None, (track.metadata.artist, track.metadata.title))
                    )
                    or track.path.stem
                )
                controls.append(
                    self._surface_card(
                        ft.Row(
                            controls=[
                                ft.Checkbox(
                                    value=False,
                                    on_change=lambda event, track_id=track.id: (
                                        select_candidate(event, track_id)
                                    ),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.PLAY_ARROW,
                                    tooltip="Прослушать",
                                    on_click=lambda _, track_id=track.id: (
                                        self.page.run_task(self._play_track, track_id)
                                    ),
                                ),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            title,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                        ft.Text(
                                            recommendation.reason,
                                            size=COMPACT_UI.font_xs,
                                            color=ft.Colors.ON_SURFACE_VARIANT,
                                            max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                    expand=True,
                                    spacing=2,
                                ),
                            ],
                            spacing=4,
                        ),
                        padding=6,
                    )
                )
            results.controls = controls
            status.value = (
                f"Найдено кандидатов: {len(controls)}"
                if controls
                else "Совместимых треков при выбранной строгости не найдено"
            )
            self.page.update(results, status, add_button)

        def add_selected(_: object) -> None:
            ordered_ids = tuple(
                recommendation.track.id
                for recommendation in current_recommendations
                if recommendation.track.id in chosen
            )
            if not ordered_ids:
                return
            try:
                added, skipped = self.service.playlists.add_many(
                    playlist_id, ordered_ids
                )
            except PlaylistError as exc:
                self._notify(str(exc))
                return
            self.page.pop_dialog()
            self.show_playlists(local_update=True)
            self._notify(
                f"Рекомендации добавлены: {added} · уже были: {skipped}"
            )

        reference.on_select = render
        mode.on_select = render
        add_button.on_click = add_selected
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Подобрать продолжение сета"),
            content=ft.Column(
                controls=[
                    ft.Row(controls=[reference, mode], wrap=True),
                    ft.Text(
                        "Строгий: до 3% BPM и близкий Camelot · "
                        "сбалансированный: до 6% · свободный: до 12%",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    status,
                    results,
                ],
                width=810,
                height=500,
                tight=True,
                spacing=COMPACT_UI.space_sm,
            ),
            actions=[
                ft.Button(content="Закрыть", on_click=lambda _: self.page.pop_dialog()),
                add_button,
            ],
        )
        self.page.show_dialog(dialog)
        render()

    @staticmethod
    def _transition_track_title(track: TrackRecord) -> str:
        return (
            " - ".join(filter(None, (track.metadata.artist, track.metadata.title)))
            or track.path.stem
        )

    def _transition_waveform_control(
        self,
        track: TrackRecord,
        cue_ms: int,
        bpm: float,
        bars_per_square: int,
        anchor_ms: int,
        on_tap: object,
    ) -> ft.Control:
        """Рисует waveform, границы квадратов и текущую монтажную точку."""
        width = 820.0
        height = 76.0
        duration_ms = max(1, round((track.technical.duration or 0) * 1000))
        peaks = list(track.waveform.peaks if track.waveform else ())
        if not peaks:
            peaks = [0.08] * COMPACT_UI.waveform_bar_count
        controls: list[ft.Control] = [
            ft.Image(
                src=self._waveform_svg(tuple(peaks)),
                width=width,
                height=height,
                fit=ft.BoxFit.FILL,
                color=ft.Colors.ON_SURFACE_VARIANT,
                exclude_from_semantics=True,
            )
        ]
        square_ms = bars_per_square * 4 * 60_000 / bpm
        first_index = int(-anchor_ms // square_ms) - 1
        last_index = int((duration_ms - anchor_ms) // square_ms) + 1
        for index in range(first_index, last_index + 1):
            position_ms = anchor_ms + index * square_ms
            if 0 <= position_ms <= duration_ms:
                controls.append(
                    ft.Container(
                        left=position_ms / duration_ms * width,
                        width=2,
                        height=height,
                        bgcolor=ft.Colors.PRIMARY_CONTAINER,
                    )
                )
        controls.append(
            ft.Container(
                left=min(width - 3, max(0, cue_ms / duration_ms * width)),
                width=3,
                height=height,
                bgcolor=ft.Colors.PRIMARY,
            )
        )
        return ft.GestureDetector(
            content=ft.Stack(controls=controls, width=width, height=height),
            on_tap=on_tap,
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _open_transition_editor(self, pair_index: int = 0) -> None:
        """Открывает монтаж соседней пары треков на общей музыкальной сетке."""
        playlist_id = self._selected_playlist_id
        if playlist_id is None:
            return
        try:
            tracks = self.service.playlists.tracks(playlist_id)
        except PlaylistError as exc:
            self._notify(str(exc))
            return
        if len(tracks) < 2:
            self._notify("Для монтажа перехода нужны минимум два трека")
            return
        pair_index = min(max(0, pair_index), len(tracks) - 2)
        outgoing = tracks[pair_index]
        incoming = tracks[pair_index + 1]
        outgoing_bpm = track_bpm(outgoing)
        incoming_bpm = track_bpm(incoming)
        if outgoing_bpm is None or incoming_bpm is None:
            self._notify("Для монтажа сначала определите BPM обоих треков")
            return
        try:
            repository = self.service.set_timeline
            saved = repository.transition(playlist_id, outgoing.id, incoming.id)
            outgoing_hot_cues = {
                item.slot: item.position_ms
                for item in repository.cue_points(outgoing.id)
            }
            incoming_hot_cues = {
                item.slot: item.position_ms
                for item in repository.cue_points(incoming.id)
            }
        except SetTimelineRepositoryError as exc:
            self._notify(str(exc))
            return

        bars_per_square = saved.bars_per_square if saved else 8
        square_count = saved.square_count if saved else 1
        provisional_overlap_ms = round(
            bars_per_square * 4 * square_count * 60_000 / outgoing_bpm
        )
        outgoing_duration_ms = round((outgoing.technical.duration or 0) * 1000)
        incoming_duration_ms = round((incoming.technical.duration or 0) * 1000)
        cue_positions = {
            "outgoing": (
                saved.outgoing_cue_ms
                if saved
                else max(0, outgoing_duration_ms - provisional_overlap_ms)
            ),
            "incoming": saved.incoming_cue_ms if saved else 0,
        }

        pair = ft.Dropdown(
            label="Соседний переход",
            width=560,
            value=str(pair_index),
            options=[
                ft.DropdownOption(
                    key=str(index),
                    text=(
                        f"{index + 1}. {self._transition_track_title(first)} → "
                        f"{self._transition_track_title(second)}"
                    ),
                )
                for index, (first, second) in enumerate(zip(tracks, tracks[1:]))
            ],
            on_select=lambda event: self._open_transition_editor(
                int(getattr(event.control, "value", 0))
            ),
        )
        bars = ft.Dropdown(
            label="Тактов в квадрате",
            width=190,
            value=str(bars_per_square),
            options=[
                ft.DropdownOption(key=str(value), text=str(value))
                for value in ALLOWED_BARS_PER_SQUARE
            ],
        )
        squares = ft.Dropdown(
            label="Квадратов наложения",
            width=210,
            value=str(square_count),
            options=[
                ft.DropdownOption(key=str(value), text=str(value))
                for value in ALLOWED_SQUARE_COUNTS
            ],
        )
        status = ft.Text(
            "Клик по waveform ставит точку на ближайшую долю. Cue 1 — якорь сетки.",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        preview_button = ft.Button(
            content="Собрать и прослушать",
            icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
        )

        def anchor(hot_cues: dict[int, int]) -> int:
            return hot_cues.get(1, 0)

        def current_bars() -> int:
            return int(bars.value or 8)

        def waveform(track: TrackRecord, side: str) -> ft.Container:
            bpm = outgoing_bpm if side == "outgoing" else incoming_bpm
            hot_cues = outgoing_hot_cues if side == "outgoing" else incoming_hot_cues
            holder = ft.Container()

            def set_from_tap(event: object) -> None:
                local_position = getattr(event, "local_position", None)
                duration_ms = round((track.technical.duration or 0) * 1000)
                if local_position is None or duration_ms <= 0:
                    return
                raw = round(
                    min(1.0, max(0.0, local_position.x / 820.0)) * duration_ms
                )
                cue_positions[side] = snap_to_beat(
                    raw, bpm, anchor_ms=anchor(hot_cues)
                )
                refresh_timeline()

            holder.content = self._transition_waveform_control(
                track,
                cue_positions[side],
                bpm,
                current_bars(),
                anchor(hot_cues),
                set_from_tap,
            )
            return holder

        outgoing_waveform = waveform(outgoing, "outgoing")
        incoming_waveform = waveform(incoming, "incoming")
        outgoing_position = ft.Text()
        incoming_position = ft.Text()
        outgoing_hot_row = ft.Row(wrap=True, spacing=4)
        incoming_hot_row = ft.Row(wrap=True, spacing=4)

        def save_hot_cue(side: str, slot: int) -> None:
            track = outgoing if side == "outgoing" else incoming
            hot_cues = outgoing_hot_cues if side == "outgoing" else incoming_hot_cues
            try:
                repository.save_cue_point(track.id, slot, cue_positions[side])
            except SetTimelineRepositoryError as exc:
                self._notify(str(exc))
                return
            hot_cues[slot] = cue_positions[side]
            refresh_timeline()
            self._notify(f"Cue {slot} сохранена")

        def use_hot_cue(side: str, slot: int) -> None:
            hot_cues = outgoing_hot_cues if side == "outgoing" else incoming_hot_cues
            if slot in hot_cues:
                cue_positions[side] = hot_cues[slot]
                refresh_timeline()
            else:
                save_hot_cue(side, slot)

        def hot_buttons(side: str, hot_cues: dict[int, int]) -> list[ft.Control]:
            controls: list[ft.Control] = []
            for slot in range(1, 5):
                position = hot_cues.get(slot)
                label = (
                    f"Cue {slot} · {self._format_duration(position / 1000)}"
                    if position is not None
                    else f"Записать Cue {slot}"
                )
                controls.append(
                    ft.Button(
                        content=label,
                        icon=ft.Icons.BOOKMARK if position is not None else ft.Icons.ADD,
                        on_click=lambda _, current=slot: use_hot_cue(side, current),
                    )
                )
                controls.append(
                    ft.IconButton(
                        icon=ft.Icons.SAVE_OUTLINED,
                        tooltip=f"Перезаписать Cue {slot} текущей точкой",
                        on_click=lambda _, current=slot: save_hot_cue(side, current),
                    )
                )
            return controls

        def refresh_timeline(_: object | None = None) -> None:
            outgoing_waveform.content = self._transition_waveform_control(
                outgoing,
                cue_positions["outgoing"],
                outgoing_bpm,
                current_bars(),
                anchor(outgoing_hot_cues),
                outgoing_waveform.content.on_tap,
            )
            incoming_waveform.content = self._transition_waveform_control(
                incoming,
                cue_positions["incoming"],
                incoming_bpm,
                current_bars(),
                anchor(incoming_hot_cues),
                incoming_waveform.content.on_tap,
            )
            outgoing_position.value = (
                "Точка начала наложения: "
                f"{self._format_duration(cue_positions['outgoing'] / 1000)}"
            )
            incoming_position.value = (
                "Точка входа: "
                f"{self._format_duration(cue_positions['incoming'] / 1000)}"
            )
            outgoing_hot_row.controls = hot_buttons("outgoing", outgoing_hot_cues)
            incoming_hot_row.controls = hot_buttons("incoming", incoming_hot_cues)
            self.page.update(
                outgoing_waveform,
                incoming_waveform,
                outgoing_position,
                incoming_position,
                outgoing_hot_row,
                incoming_hot_row,
            )

        def nudge(side: str, beats: int) -> None:
            bpm = outgoing_bpm if side == "outgoing" else incoming_bpm
            cue_positions[side] = move_by_beats(cue_positions[side], beats, bpm)
            refresh_timeline()

        def nudge_row(side: str) -> ft.Row:
            return ft.Row(
                controls=[
                    ft.Button(content="−4 доли", on_click=lambda _: nudge(side, -4)),
                    ft.Button(content="−1", on_click=lambda _: nudge(side, -1)),
                    ft.Button(content="+1", on_click=lambda _: nudge(side, 1)),
                    ft.Button(content="+4 доли", on_click=lambda _: nudge(side, 4)),
                ],
                spacing=4,
            )

        def plan_and_save() -> object:
            plan = build_transition_plan(
                outgoing_cue_ms=cue_positions["outgoing"],
                incoming_cue_ms=cue_positions["incoming"],
                outgoing_bpm=outgoing_bpm,
                incoming_bpm=incoming_bpm,
                outgoing_duration_ms=outgoing_duration_ms,
                incoming_duration_ms=incoming_duration_ms,
                bars_per_square=int(bars.value or 8),
                square_count=int(squares.value or 1),
            )
            repository.save_transition(
                SavedTransition(
                    playlist_id=playlist_id,
                    outgoing_track_id=outgoing.id,
                    incoming_track_id=incoming.id,
                    outgoing_cue_ms=cue_positions["outgoing"],
                    incoming_cue_ms=cue_positions["incoming"],
                    bars_per_square=plan.bars_per_square,
                    square_count=plan.square_count,
                )
            )
            return plan

        def save(_: object) -> None:
            try:
                plan = plan_and_save()
            except (SetTimelineError, SetTimelineRepositoryError) as exc:
                status.value = str(exc)
                status.color = ft.Colors.ERROR
                self.page.update(status)
                return
            status.value = (
                f"Сохранено · наложение {plan.overlap_ms / 1000:.1f} с · "
                f"входящий темп ×{plan.incoming_tempo:.4f}"
            )
            status.color = ft.Colors.PRIMARY
            self.page.update(status)

        async def preview() -> None:
            try:
                plan = plan_and_save()
                ffmpeg = self.runtime.ffmpeg_path()
                if ffmpeg is None:
                    raise TransitionPreviewError("FFmpeg недоступен")
                preview_button.disabled = True
                preview_button.content = "Собирается preview…"
                self.page.update(preview_button)
                output = await self.workers.run(
                    render_transition_preview,
                    ffmpeg,
                    outgoing.path,
                    incoming.path,
                    self.paths.data_dir / "transition_previews",
                    plan,
                )
                await self._play_external_audio(
                    output,
                    f"Переход: {self._transition_track_title(outgoing)} → "
                    f"{self._transition_track_title(incoming)}",
                )
                status.value = "Preview готов и воспроизводится"
                status.color = ft.Colors.PRIMARY
            except (
                SetTimelineError,
                SetTimelineRepositoryError,
                TransitionPreviewError,
            ) as exc:
                status.value = str(exc)
                status.color = ft.Colors.ERROR
            finally:
                preview_button.disabled = False
                preview_button.content = "Собрать и прослушать"
                self.page.update(preview_button, status)

        bars.on_select = refresh_timeline
        preview_button.on_click = lambda _: self.page.run_task(preview)
        outgoing_block = self._surface_card(
            ft.Column(
                controls=[
                    ft.Text(
                        f"A · {self._transition_track_title(outgoing)} · "
                        f"{outgoing_bpm:.2f} BPM",
                        weight=ft.FontWeight.BOLD,
                    ),
                    outgoing_waveform,
                    outgoing_position,
                    nudge_row("outgoing"),
                    outgoing_hot_row,
                ],
                spacing=6,
            ),
            padding=10,
        )
        incoming_block = self._surface_card(
            ft.Column(
                controls=[
                    ft.Text(
                        f"B · {self._transition_track_title(incoming)} · "
                        f"{incoming_bpm:.2f} BPM → {outgoing_bpm:.2f} BPM",
                        weight=ft.FontWeight.BOLD,
                    ),
                    incoming_waveform,
                    incoming_position,
                    nudge_row("incoming"),
                    incoming_hot_row,
                ],
                spacing=6,
            ),
            padding=10,
        )
        toolbar = ft.Row(
            controls=[
                ft.Button(
                    content="К плейлистам",
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda _: self.show_playlists(local_update=True),
                ),
                pair,
                bars,
                squares,
            ],
            wrap=True,
        )
        actions = ft.Row(
            controls=[
                ft.Button(content="Сохранить", icon=ft.Icons.SAVE, on_click=save),
                preview_button,
                status,
            ],
            wrap=True,
        )
        self._replace_content(
            "Монтаж переходов",
            "Соседние треки, быстрые Cue и привязка к музыкальным квадратам",
            toolbar,
            actions,
            ft.ListView(
                controls=[outgoing_block, incoming_block],
                expand=True,
                spacing=COMPACT_UI.space_sm,
            ),
            local_update=True,
        )
        refresh_timeline()

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
