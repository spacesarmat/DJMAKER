"""Рендер строки трека в медиатеке (обложка, теги, waveform).

Выделено из DJMakerUI (god object).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import flet as ft

from djmaker.domain.camelot_color import camelot_to_color
from djmaker.domain.energy_color import (
    BUCKET_BLACK,
    BUCKET_BLUE,
    BUCKET_GREEN,
    BUCKET_ORANGE,
    BUCKET_PURPLE,
    BUCKET_RED,
    BUCKET_YELLOW,
    energy_color_bucket,
)
from djmaker.domain.models import TrackRecord
from djmaker.services.file_browser import reveal_file
from djmaker.services.waveform import resample_waveform_peaks
from djmaker.ui.density import COMPACT_UI, scaled_library_size

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)

# Неброский тон подсветки строки: базовый цвет бакета с низкой непрозрачностью
# поверх обычного фона карточки трека.
ENERGY_HIGHLIGHT_OPACITY = 0.16
ENERGY_BUCKET_COLORS: dict[str, str] = {
    BUCKET_RED: ft.Colors.RED,
    BUCKET_YELLOW: ft.Colors.YELLOW,
    BUCKET_ORANGE: ft.Colors.ORANGE,
    BUCKET_GREEN: ft.Colors.GREEN,
    BUCKET_BLUE: ft.Colors.BLUE,
    BUCKET_PURPLE: ft.Colors.PURPLE,
    BUCKET_BLACK: ft.Colors.BLUE_GREY_900,
}


@dataclass(slots=True)
class _WaveformView:
    peaks: tuple[float, ...]
    progress_image: ft.Image
    played_bars: int


class TrackRowController:
    """Владеет рендером строки трека и waveform-геометрией; состояние — в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def library_size(
        self,
        value: float,
        *,
        minimum: float = 1.0,
    ) -> float:
        """Возвращает размер элемента строки с учётом масштаба медиатеки."""
        return scaled_library_size(
            value,
            self.app.settings.library_scale_percent,
            minimum=minimum,
        )

    def track_item_extent(self) -> float:
        """Высота строки медиатеки с учётом пользовательского масштаба."""
        return (
            self.library_size(COMPACT_UI.track_icon_box)
            + self.library_size(COMPACT_UI.card_padding) * 2
            + self.library_size(COMPACT_UI.space_sm)
        )

    def track_row(self, track: TrackRecord) -> ft.Control:
        app = self.app
        metadata_block = ft.Column(
            controls=[
                self.track_title_row(track),
                self.track_details_row(track),
                self.track_path_link(track),
            ],
            expand=True,
            height=self.library_size(COMPACT_UI.track_icon_box),
            spacing=0,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        card = app._surface_card(
            ft.Row(
                controls=[
                    app._library_selection_checkbox(track.id),
                    self.track_artwork(track),
                    metadata_block,
                    self.track_tonality_bar(track),
                    self.track_waveform(track),
                    app._playlist_track_menu(track.id),
                    ft.IconButton(
                        icon=ft.Icons.GRID_ON_OUTLINED,
                        icon_size=self.library_size(COMPACT_UI.action_icon_size),
                        padding=self.library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Редактировать BPM-сетку",
                        disabled=(
                            track.analysis is None
                            or track.analysis.beat_grid is None
                        ),
                        on_click=lambda _, track_id=track.id: (
                            app._open_beat_grid_editor(track_id)
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=self.library_size(COMPACT_UI.action_icon_size),
                        padding=self.library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Редактировать теги",
                        on_click=lambda _, track_id=track.id: app._open_tag_editor(
                            track_id
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
                        icon_size=self.library_size(COMPACT_UI.action_icon_size),
                        padding=self.library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Организовать файл",
                        on_click=lambda _, track_id=track.id: app._open_organizer(
                            track_id
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SEARCH,
                        icon_size=self.library_size(COMPACT_UI.action_icon_size),
                        padding=self.library_size(COMPACT_UI.space_xs),
                        visual_density=ft.VisualDensity.COMPACT,
                        tooltip="Найти метаданные",
                        on_click=lambda _, track_id=track.id: (
                            app._start_metadata_search(track_id)
                        ),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=self.library_size(COMPACT_UI.space_sm),
            ),
            padding=self.library_size(COMPACT_UI.card_padding),
        )
        card.bgcolor = self.track_row_bgcolor(track)
        app._track_row_cards[track.id] = card
        return ft.Container(
            height=self.track_item_extent(),
            padding=ft.Padding.only(
                bottom=self.library_size(COMPACT_UI.space_sm)
            ),
            content=ft.GestureDetector(
                content=card,
                on_tap=lambda _, track_id=track.id: app.page.run_task(
                    app._select_library_track, track_id
                ),
                on_double_tap=lambda _, track_id=track.id: app._open_tag_editor(
                    track_id
                ),
                mouse_cursor=ft.MouseCursor.CLICK,
            ),
        )

    def track_row_bgcolor(self, track: TrackRecord) -> str:
        """Цвет фона карточки трека: выделение важнее подсветки по энергии."""
        app = self.app
        if app._selected_track_id == track.id:
            return ft.Colors.SURFACE_CONTAINER_HIGH
        if app.settings.energy_highlight_enabled and track.analysis is not None:
            analysis = track.analysis
            bucket = energy_color_bucket(
                analysis.energy, analysis.scale, analysis.noisiness, analysis.genre_tag
            )
            base_color = ENERGY_BUCKET_COLORS.get(bucket)
            if base_color is not None:
                return ft.Colors.with_opacity(ENERGY_HIGHLIGHT_OPACITY, base_color)
        return ft.Colors.SURFACE_CONTAINER_LOW

    def track_tonality_bar(self, track: TrackRecord) -> ft.Control:
        """Тонкая вертикальная полоса тональности по цвету колеса Camelot."""
        analysis = track.analysis
        camelot = analysis.camelot if analysis else ""
        color = camelot_to_color(camelot) if camelot else None
        if analysis and camelot:
            key_label = f"{analysis.musical_key} {analysis.scale}".strip()
            tooltip = f"{key_label} · {camelot}" if key_label else camelot
        else:
            tooltip = "Тональность не определена"
        return ft.Container(
            width=6,
            height=self.library_size(COMPACT_UI.track_icon_box),
            bgcolor=color or ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border_radius=3,
            tooltip=tooltip,
        )

    def track_title_row(self, track: TrackRecord) -> ft.Control:
        artist = track.metadata.artist.strip() or "Unknown Artist"
        title = track.metadata.title.strip() or track.path.stem
        return ft.Row(
            controls=[
                ft.Text(
                    f"{artist} - {title}",
                    expand=True,
                    expand_loose=True,
                    weight=ft.FontWeight.BOLD,
                    size=self.library_size(COMPACT_UI.font_sm, minimum=6.0),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                self.track_tag(self.track_key_label(track), accent=True),
                self.track_tag(self.track_bpm_label(track), accent=True),
            ],
            spacing=self.library_size(COMPACT_UI.space_xs),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def track_details_row(self, track: TrackRecord) -> ft.Control:
        app = self.app
        album = track.metadata.album.strip() or "Альбом —"
        controls: list[ft.Control] = [
            ft.Text(
                app._format_duration(track.technical.duration),
                size=self.library_size(COMPACT_UI.font_xs, minimum=5.5),
                color=ft.Colors.ON_SURFACE,
            )
        ]
        controls.extend(
            self.track_tag(label) for label in self.track_detail_tags(track)
        )
        controls.append(
            ft.Text(
                album,
                expand=True,
                size=self.library_size(COMPACT_UI.font_xs, minimum=5.5),
                color=ft.Colors.ON_SURFACE_VARIANT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
        return ft.Row(
            controls=controls,
            spacing=self.library_size(COMPACT_UI.space_xs),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def track_path_link(self, track: TrackRecord) -> ft.Control:
        return ft.GestureDetector(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.FOLDER_OPEN_OUTLINED,
                        size=self.library_size(COMPACT_UI.font_xs, minimum=5.5),
                        color=ft.Colors.PRIMARY,
                    ),
                    ft.Text(
                        str(track.path),
                        expand=True,
                        size=self.library_size(COMPACT_UI.font_micro, minimum=5.0),
                        color=ft.Colors.PRIMARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=self.library_size(COMPACT_UI.space_xs),
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_double_tap=lambda _, current=track: self.reveal_track_file(current),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def track_tag(self, label: str, *, accent: bool = False) -> ft.Control:
        app = self.app
        return ft.Container(
            height=self.library_size(12, minimum=8.0),
            padding=ft.Padding.symmetric(horizontal=self.library_size(4)),
            border_radius=self.library_size(4),
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
                app._on_accent_track_tag_hover
                if accent
                else app._on_neutral_track_tag_hover
            ),
            content=ft.Text(
                label,
                size=self.library_size(COMPACT_UI.font_micro, minimum=5.0),
                color=(
                    ft.Colors.ON_PRIMARY_CONTAINER
                    if accent
                    else ft.Colors.ON_SURFACE_VARIANT
                ),
                max_lines=1,
            ),
        )

    @staticmethod
    def track_bpm_label(track: TrackRecord) -> str:
        bpm = track.analysis.bpm if track.analysis is not None else None
        if bpm is None:
            bpm = track.metadata.bpm
        return f"{bpm:.1f} BPM" if bpm is not None else "BPM —"

    @staticmethod
    def track_key_label(track: TrackRecord) -> str:
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
    def track_detail_tags(track: TrackRecord) -> tuple[str, ...]:
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

    def reveal_track_file(self, track: TrackRecord) -> None:
        try:
            reveal_file(track.path)
        except OSError as exc:
            LOGGER.warning(
                "Не удалось открыть расположение файла %s: %s",
                track.path,
                exc,
            )
            self.app._notify("Не удалось открыть папку с файлом")

    def track_artwork(self, track: TrackRecord) -> ft.Control:
        """Показывает кликабельную обложку с embedded/online fallback."""
        app = self.app
        fallback = ft.Container(
            width=self.library_size(COMPACT_UI.track_icon_box),
            height=self.library_size(COMPACT_UI.track_icon_box),
            border_radius=self.library_size(6),
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(
                ft.Icons.MUSIC_NOTE,
                size=self.library_size(COMPACT_UI.track_icon_size),
                color=ft.Colors.ON_PRIMARY_CONTAINER,
            ),
        )

        def image(source: str, error_content: ft.Control) -> ft.Image:
            return ft.Image(
                src=source,
                width=self.library_size(COMPACT_UI.track_icon_box),
                height=self.library_size(COMPACT_UI.track_icon_box),
                fit=ft.BoxFit.COVER,
                border_radius=self.library_size(6),
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
            on_tap=lambda _, track_id=track.id: app.page.run_task(
                app._play_track, track_id, 0
            ),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def track_waveform(self, track: TrackRecord) -> ft.Control:
        app = self.app
        peaks = track.waveform.peaks if track.waveform is not None else ()
        normalized_peaks = resample_waveform_peaks(
            peaks,
            COMPACT_UI.waveform_bar_count,
        )

        played = 0
        if app._player_track_id == track.id:
            duration = app._player_duration_ms
            fraction = (app._player_position_ms / duration) if duration > 0 else 0.0
            played = min(
                len(normalized_peaks),
                max(0, round(len(normalized_peaks) * fraction)),
            )

        base_image = ft.Image(
            src=self.waveform_svg(normalized_peaks),
            width=self.waveform_width(),
            height=self.library_size(COMPACT_UI.waveform_height),
            fit=ft.BoxFit.FILL,
            color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            exclude_from_semantics=True,
        )
        progress_image = ft.Image(
            src=self.waveform_svg(normalized_peaks, played),
            width=self.waveform_width(),
            height=self.library_size(COMPACT_UI.waveform_height),
            fit=ft.BoxFit.FILL,
            color=ft.Colors.PRIMARY,
            exclude_from_semantics=True,
        )
        app._waveform_views[track.id] = _WaveformView(
            peaks=normalized_peaks,
            progress_image=progress_image,
            played_bars=played,
        )

        waveform = ft.Container(
            width=self.waveform_width(),
            height=self.library_size(COMPACT_UI.track_icon_box),
            alignment=ft.Alignment.CENTER,
            content=ft.Stack(
                controls=[base_image, progress_image],
                width=self.waveform_width(),
                height=self.library_size(COMPACT_UI.waveform_height),
            ),
        )
        return ft.GestureDetector(
            content=waveform,
            on_tap=lambda event, current=track: self.play_from_waveform(
                event, current
            ),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    @staticmethod
    def analysis_label(track: TrackRecord) -> str:
        analysis = track.analysis
        if analysis is None:
            return "BPM / Key: —"
        bpm = f"{analysis.bpm:.1f} BPM" if analysis.bpm is not None else "— BPM"
        key = " ".join(part for part in (analysis.musical_key, analysis.scale) if part)
        parts = [bpm, key or "—"]
        if analysis.camelot:
            parts.append(analysis.camelot)
        if analysis.beat_grid is not None:
            parts.append(f"сетка {len(analysis.beat_grid.beat_ticks_ms)} долей")
        return " · ".join(parts)

    def start_metadata_search(self, track_id: int) -> None:
        self.app.page.run_task(self.app._metadata_search, track_id)

    @staticmethod
    def base_waveform_width() -> int:
        return (
            COMPACT_UI.waveform_bar_count * COMPACT_UI.waveform_bar_width
            + (COMPACT_UI.waveform_bar_count - 1) * COMPACT_UI.waveform_bar_gap
        )

    def waveform_width(self) -> float:
        return self.library_size(self.base_waveform_width())

    @staticmethod
    def waveform_svg(
        peaks: tuple[float, ...],
        played_bars: int | None = None,
    ) -> str:
        """Рисует waveform одним SVG вместо десятков Flet-контролов."""
        width = TrackRowController.base_waveform_width()
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

    def play_from_waveform(self, event: ft.TapEvent, track: TrackRecord) -> None:
        app = self.app
        if event.local_position is None:
            return
        duration = track.technical.duration or 0.0
        if duration <= 0:
            app._notify("Для seek недоступна продолжительность трека")
            return
        fraction = min(
            1.0,
            max(0.0, event.local_position.x / self.waveform_width()),
        )
        position_ms = int(duration * fraction * 1000)
        app.page.run_task(app._play_track, track.id, position_ms)
