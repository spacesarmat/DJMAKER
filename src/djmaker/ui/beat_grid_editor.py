"""Визуальный редактор BPM-сетки и Warp-якорей."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import flet as ft

from djmaker.domain.beat_grid import (
    ALLOWED_SQUARE_BARS,
    BeatGridAnchor,
    BeatGridError,
    nearest_warped_grid_position,
    validate_anchor_mapping,
    validate_beat_grid,
    warped_grid_markers,
)
from djmaker.domain.models import BeatGridAnalysis, TrackRecord
from djmaker.infrastructure.beat_grid import BeatGridRepositoryError
from djmaker.services.waveform import resample_waveform_peaks
from djmaker.ui.density import COMPACT_UI


LOGGER = logging.getLogger(__name__)
EDITOR_WIDTH = 980.0
EDITOR_WAVEFORM_HEIGHT = 260.0
MIN_VIEW_MS = 4_000


@dataclass(slots=True)
class BeatGridEditorState:
    """Состояние диалога до атомарного сохранения в SQLite."""

    track_id: int
    grid: BeatGridAnalysis
    anchors: list[BeatGridAnchor]
    duration_ms: int
    peaks: tuple[float, ...]
    cursor_ms: int
    view_start_ms: int
    view_duration_ms: int
    square_bars: int = 8

    @classmethod
    def from_track(
        cls,
        track: TrackRecord,
        anchors: list[BeatGridAnchor],
    ) -> BeatGridEditorState:
        analysis = track.analysis
        if analysis is None or analysis.beat_grid is None:
            raise BeatGridError(
                "Сначала выполните полный анализ BPM-сетки"
            )
        grid = analysis.beat_grid
        validate_beat_grid(grid)
        validate_anchor_mapping(grid, anchors)
        duration_ms = max(
            1,
            round((track.technical.duration or 0) * 1000),
            grid.beat_ticks_ms[-1] if grid.beat_ticks_ms else 0,
        )
        initial_view = min(duration_ms, 32_000)
        view_start = max(
            0,
            min(duration_ms - initial_view, grid.downbeat_ms - initial_view // 4),
        )
        return cls(
            track_id=track.id,
            grid=grid,
            anchors=list(anchors),
            duration_ms=duration_ms,
            peaks=track.waveform.peaks if track.waveform else (),
            cursor_ms=grid.downbeat_ms,
            view_start_ms=view_start,
            view_duration_ms=initial_view,
        )

    @property
    def view_end_ms(self) -> int:
        return min(self.duration_ms, self.view_start_ms + self.view_duration_ms)

    def set_cursor_fraction(self, fraction: float) -> None:
        clamped = min(1.0, max(0.0, fraction))
        self.cursor_ms = round(
            self.view_start_ms + clamped * self.view_duration_ms
        )
        self.cursor_ms = min(self.duration_ms, max(0, self.cursor_ms))

    def zoom(self, factor: float) -> None:
        if factor <= 0:
            raise BeatGridError(
                "Масштаб должен быть положительным"
            )
        new_duration = round(self.view_duration_ms * factor)
        new_duration = min(self.duration_ms, max(MIN_VIEW_MS, new_duration))
        center = self.cursor_ms
        self.view_duration_ms = new_duration
        self.view_start_ms = min(
            max(0, center - new_duration // 2),
            max(0, self.duration_ms - new_duration),
        )

    def show_all(self) -> None:
        self.view_start_ms = 0
        self.view_duration_ms = self.duration_ms

    def pan(self, direction: int) -> None:
        delta = round(self.view_duration_ms * 0.75) * direction
        self.view_start_ms = min(
            max(0, self.view_start_ms + delta),
            max(0, self.duration_ms - self.view_duration_ms),
        )
        self.cursor_ms = min(
            self.view_end_ms,
            max(self.view_start_ms, self.cursor_ms),
        )

    def apply_grid(
        self,
        *,
        bpm: float,
        first_beat_ms: int,
        downbeat_ms: int,
        beats_per_bar: int,
    ) -> None:
        candidate = replace(
            self.grid,
            bpm=bpm,
            first_beat_ms=first_beat_ms,
            downbeat_ms=downbeat_ms,
            beats_per_bar=beats_per_bar,
            source="manual",
        )
        validate_beat_grid(candidate)
        validate_anchor_mapping(candidate, self.anchors)
        self.grid = candidate

    def add_warp_at_cursor(self) -> BeatGridAnchor:
        _, beat_number = nearest_warped_grid_position(
            self.grid,
            self.cursor_ms,
            self.anchors,
        )
        candidate = BeatGridAnchor(
            track_id=self.track_id,
            source_ms=self.cursor_ms,
            beat_number=float(beat_number),
        )
        updated = [
            anchor
            for anchor in self.anchors
            if anchor.beat_number != candidate.beat_number
            and anchor.source_ms != candidate.source_ms
        ]
        updated.append(candidate)
        self.anchors = list(validate_anchor_mapping(self.grid, updated))
        return candidate

    def remove_warp(self, anchor: BeatGridAnchor) -> None:
        self.anchors = [item for item in self.anchors if item != anchor]
        validate_anchor_mapping(self.grid, self.anchors)


def format_position(position_ms: int) -> str:
    total_ms = max(0, int(position_ms))
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def beat_grid_editor_svg(state: BeatGridEditorState) -> str:
    """Рисует waveform, detected beats, сетку, Warp и курсор."""
    width = round(EDITOR_WIDTH)
    height = round(EDITOR_WAVEFORM_HEIGHT)
    center = height / 2
    view_duration = max(1, state.view_duration_ms)
    source_count = len(state.peaks)
    if source_count:
        first = max(
            0,
            min(
                source_count - 1,
                state.view_start_ms * source_count // state.duration_ms,
            ),
        )
        last = max(
            first + 1,
            min(
                source_count,
                (state.view_end_ms * source_count + state.duration_ms - 1)
                // state.duration_ms,
            ),
        )
        visible_peaks = resample_waveform_peaks(
            state.peaks[first:last],
            min(490, max(80, last - first)),
        )
    else:
        visible_peaks = (0.05,) * 160

    elements = [
        f'<rect width="{width}" height="{height}" fill="#0F1115"/>',
        f'<line x1="0" y1="{center:.1f}" x2="{width}" y2="{center:.1f}" '
        'stroke="#3C4652" stroke-width="1"/>',
    ]
    step = width / max(1, len(visible_peaks))
    bar_width = max(1.0, step * 0.72)
    for index, peak in enumerate(visible_peaks):
        amplitude = min(1.0, max(0.02, peak)) * (center - 16)
        x = index * step
        elements.append(
            f'<rect x="{x:.1f}" y="{center - amplitude:.1f}" '
            f'width="{bar_width:.1f}" height="{amplitude * 2:.1f}" '
            'rx="1" fill="#657786" fill-opacity="0.78"/>'
        )

    def x_at(position_ms: int) -> float:
        return (position_ms - state.view_start_ms) / view_duration * width

    for tick in state.grid.beat_ticks_ms:
        if state.view_start_ms <= tick <= state.view_end_ms:
            x = x_at(tick)
            elements.append(
                f'<line x1="{x:.1f}" y1="{center - 13:.1f}" '
                f'x2="{x:.1f}" y2="{center + 13:.1f}" '
                'stroke="#FFB300" stroke-width="1" stroke-opacity="0.8"/>'
            )

    markers = warped_grid_markers(
        state.grid,
        state.duration_ms,
        anchors=state.anchors,
        square_bars=state.square_bars,
    )
    for marker in markers:
        if not state.view_start_ms <= marker.position_ms <= state.view_end_ms:
            continue
        x = x_at(marker.position_ms)
        if marker.is_square:
            color, line_width, opacity = "#00F0FF", 2.4, 0.95
        elif marker.is_bar:
            color, line_width, opacity = "#D1D5DB", 1.5, 0.75
        else:
            color, line_width, opacity = "#536170", 1.0, 0.52
        elements.append(
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
            f'stroke="{color}" stroke-width="{line_width}" '
            f'stroke-opacity="{opacity}"/>'
        )
        if marker.is_bar:
            elements.append(
                f'<text x="{x + 3:.1f}" y="14" fill="{color}" '
                f'font-size="10">{marker.bar_number + 1}</text>'
            )

    for anchor in state.anchors:
        if state.view_start_ms <= anchor.source_ms <= state.view_end_ms:
            x = x_at(anchor.source_ms)
            elements.append(
                f'<polygon points="{x - 7:.1f},0 {x + 7:.1f},0 {x:.1f},12" '
                'fill="#FF4D8D"/>'
            )
            elements.append(
                f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
                'stroke="#FF4D8D" stroke-width="2"/>'
            )

    if state.view_start_ms <= state.cursor_ms <= state.view_end_ms:
        cursor_x = x_at(state.cursor_ms)
        elements.append(
            f'<line x1="{cursor_x:.1f}" y1="0" x2="{cursor_x:.1f}" '
            f'y2="{height}" stroke="#FFFFFF" stroke-width="2"/>'
        )
        elements.append(
            f'<polygon points="{cursor_x - 6:.1f},{height} '
            f'{cursor_x + 6:.1f},{height} {cursor_x:.1f},{height - 10}" '
            'fill="#FFFFFF"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'{"".join(elements)}</svg>'
    )


class BeatGridEditorUI:
    """Mixin редактора сетки для основного окна."""

    def _open_beat_grid_editor(self, track_id: int) -> None:
        try:
            track = self.service.track(track_id)
            repository = self.service.beat_grid
            state = BeatGridEditorState.from_track(
                track,
                repository.anchors(track_id),
            )
        except (BeatGridError, BeatGridRepositoryError, RuntimeError) as exc:
            self._notify(str(exc))
            return

        title = (
            " - ".join(
                filter(None, (track.metadata.artist, track.metadata.title))
            )
            or track.path.stem
        )
        bpm = ft.TextField(
            label="BPM сетки",
            value=f"{state.grid.bpm:.4f}",
            width=130,
            dense=True,
        )
        first_beat = ft.TextField(
            label="Первая доля, мс",
            value=str(state.grid.first_beat_ms),
            width=150,
            dense=True,
        )
        downbeat = ft.TextField(
            label="Сильная доля, мс",
            value=str(state.grid.downbeat_ms),
            width=160,
            dense=True,
        )
        beats_per_bar = ft.Dropdown(
            label="Размер такта",
            value=str(state.grid.beats_per_bar),
            width=140,
            options=[
                ft.DropdownOption(key="3", text="3/4"),
                ft.DropdownOption(key="4", text="4/4"),
            ],
        )
        squares = ft.Dropdown(
            label="Квадрат",
            value=str(state.square_bars),
            width=140,
            options=[
                ft.DropdownOption(key=str(value), text=f"{value} тактов")
                for value in ALLOWED_SQUARE_BARS
            ],
        )
        status = ft.Text(
            "Белая линия — курсор · жёлтые риски — detected beats · "
            "розовые — Warp-якоря",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        cursor_label = ft.Text(size=COMPACT_UI.font_sm)
        view_label = ft.Text(
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        anchors_column = ft.Column(spacing=3)
        waveform_holder = ft.Container()

        def show_error(exc: Exception) -> None:
            status.value = str(exc)
            status.color = ft.Colors.ERROR
            self.page.update(status)

        def anchor_controls() -> list[ft.Control]:
            if not state.anchors:
                return [
                    ft.Text(
                        "Warp-якорей нет — действует постоянный BPM.",
                        size=COMPACT_UI.font_xs,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    )
                ]
            controls: list[ft.Control] = []
            for anchor in sorted(state.anchors, key=lambda item: item.source_ms):
                controls.append(
                    ft.Row(
                        controls=[
                            ft.Text(
                                f"Доля {anchor.beat_number:g} → "
                                f"{format_position(anchor.source_ms)}",
                                width=220,
                                size=COMPACT_UI.font_xs,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_size=16,
                                tooltip="Удалить Warp-якорь",
                                on_click=lambda _, current=anchor: remove_anchor(
                                    current
                                ),
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    )
                )
            return controls

        def render() -> None:
            waveform_holder.content = ft.GestureDetector(
                content=ft.Image(
                    src=beat_grid_editor_svg(state),
                    width=EDITOR_WIDTH,
                    height=EDITOR_WAVEFORM_HEIGHT,
                    fit=ft.BoxFit.FILL,
                    exclude_from_semantics=True,
                ),
                on_tap=set_cursor,
                mouse_cursor=ft.MouseCursor.CLICK,
            )
            cursor_label.value = (
                f"Курсор: {format_position(state.cursor_ms)} "
                f"({state.cursor_ms} мс)"
            )
            view_label.value = (
                f"Окно: {format_position(state.view_start_ms)} — "
                f"{format_position(state.view_end_ms)} · "
                f"{state.view_duration_ms / 1000:.1f} с"
            )
            anchors_column.controls = anchor_controls()
            self.page.update(
                waveform_holder,
                cursor_label,
                view_label,
                anchors_column,
            )

        def set_cursor(event: object) -> None:
            local = getattr(event, "local_position", None)
            if local is None:
                return
            state.set_cursor_fraction(local.x / EDITOR_WIDTH)
            render()

        def apply_fields(_: object | None = None) -> bool:
            try:
                state.square_bars = int(squares.value or 8)
                state.apply_grid(
                    bpm=float((bpm.value or "").replace(",", ".")),
                    first_beat_ms=int(first_beat.value or "0"),
                    downbeat_ms=int(downbeat.value or "0"),
                    beats_per_bar=int(beats_per_bar.value or "4"),
                )
            except (BeatGridError, TypeError, ValueError) as exc:
                show_error(exc)
                return False
            status.value = (
                "Параметры применены к предпросмотру"
            )
            status.color = ft.Colors.PRIMARY
            render()
            self.page.update(status)
            return True

        def set_first(_: object) -> None:
            first_beat.value = str(state.cursor_ms)
            apply_fields()

        def set_downbeat(_: object) -> None:
            downbeat.value = str(state.cursor_ms)
            apply_fields()

        def add_anchor(_: object) -> None:
            if not apply_fields():
                return
            try:
                anchor = state.add_warp_at_cursor()
            except BeatGridError as exc:
                show_error(exc)
                return
            status.value = (
                f"Warp: доля {anchor.beat_number:g} закреплена на "
                f"{format_position(anchor.source_ms)}"
            )
            status.color = ft.Colors.PRIMARY
            render()
            self.page.update(status)

        def remove_anchor(anchor: BeatGridAnchor) -> None:
            try:
                state.remove_warp(anchor)
            except BeatGridError as exc:
                show_error(exc)
                return
            status.value = (
                "Warp-якорь удалён из предпросмотра"
            )
            status.color = ft.Colors.PRIMARY
            render()
            self.page.update(status)

        def clear_anchors(_: object) -> None:
            state.anchors.clear()
            status.value = (
                "Все Warp-якоря удалены из предпросмотра"
            )
            status.color = ft.Colors.PRIMARY
            render()
            self.page.update(status)

        def zoom(factor: float) -> None:
            try:
                state.zoom(factor)
            except BeatGridError as exc:
                show_error(exc)
                return
            render()

        def play(_: object) -> None:
            self.page.run_task(self._play_track, track.id, state.cursor_ms)

        def save(_: object) -> None:
            if not apply_fields():
                return
            try:
                repository.save_editor_state(
                    track.id,
                    state.grid,
                    state.anchors,
                )
            except BeatGridRepositoryError as exc:
                show_error(exc)
                return
            self.page.pop_dialog()
            self._notify("BPM-сетка и Warp-якоря сохранены")
            self._selected_track_id = track.id
            self.show_library(local_update=True)

        def select_square(_: object) -> None:
            state.square_bars = int(squares.value or 8)
            render()

        squares.on_select = select_square
        toolbar = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.SKIP_PREVIOUS,
                    tooltip="Предыдущее окно",
                    on_click=lambda _: (state.pan(-1), render()),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_IN,
                    tooltip="Увеличить",
                    on_click=lambda _: zoom(0.5),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_OUT,
                    tooltip="Уменьшить",
                    on_click=lambda _: zoom(2.0),
                ),
                ft.Button(
                    content="Весь трек",
                    icon=ft.Icons.FIT_SCREEN,
                    on_click=lambda _: (state.show_all(), render()),
                ),
                ft.IconButton(
                    icon=ft.Icons.SKIP_NEXT,
                    tooltip="Следующее окно",
                    on_click=lambda _: (state.pan(1), render()),
                ),
                ft.Button(
                    content="Слушать отсюда",
                    icon=ft.Icons.PLAY_ARROW,
                    on_click=play,
                ),
                cursor_label,
            ],
            spacing=4,
            wrap=True,
        )
        parameter_row = ft.Row(
            controls=[
                bpm,
                first_beat,
                downbeat,
                beats_per_bar,
                squares,
                ft.Button(
                    content="Применить",
                    icon=ft.Icons.TUNE,
                    on_click=apply_fields,
                ),
            ],
            spacing=6,
            wrap=True,
        )
        edit_row = ft.Row(
            controls=[
                ft.Button(
                    content="Первая доля = курсор",
                    icon=ft.Icons.FIRST_PAGE,
                    on_click=set_first,
                ),
                ft.Button(
                    content="Сильная доля = курсор",
                    icon=ft.Icons.MY_LOCATION,
                    on_click=set_downbeat,
                ),
                ft.Button(
                    content="Поставить Warp-якорь",
                    icon=ft.Icons.ADD_LOCATION_ALT,
                    on_click=add_anchor,
                ),
                ft.Button(
                    content="Удалить все Warp",
                    icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                    on_click=clear_anchors,
                ),
            ],
            spacing=6,
            wrap=True,
        )
        confidence = state.grid.downbeat_confidence
        stability = state.grid.tempo_stability
        info = ft.Text(
            f"Detected beats: {len(state.grid.beat_ticks_ms)} · "
            f"стабильность: {stability:.1%} · "
            f"уверенность сильной доли: {confidence:.1%}"
            if stability is not None and confidence is not None
            else f"Detected beats: {len(state.grid.beat_ticks_ms)}",
            size=COMPACT_UI.font_xs,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Редактор BPM-сетки · {title}"),
            content=ft.Container(
                width=1040,
                height=680,
                content=ft.Column(
                    controls=[
                        parameter_row,
                        info,
                        toolbar,
                        waveform_holder,
                        view_label,
                        edit_row,
                        status,
                        ft.Text("Warp-якоря", weight=ft.FontWeight.BOLD),
                        anchors_column,
                    ],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.Button(
                    content="Отмена",
                    on_click=lambda _: self.page.pop_dialog(),
                ),
                ft.Button(
                    content="Сохранить сетку",
                    icon=ft.Icons.SAVE_OUTLINED,
                    on_click=save,
                ),
            ],
        )
        self.page.show_dialog(dialog)
        render()
