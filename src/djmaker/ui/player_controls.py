"""Плеер и waveform-прогресс: выделено из DJMakerUI (god object)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft
import flet_audio as fta

from djmaker.domain.models import TrackRecord

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)

_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS = 15.0


class PlayerControlsController:
    """Владеет логикой плеера и waveform-прогресса; состояние остаётся в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    async def play_external_audio(self, source, title: str) -> None:
        """Воспроизводит локальный preview без добавления его в медиатеку."""
        app = self.app
        app._player_request_revision += 1
        request_revision = app._player_request_revision
        started_switch = False
        try:
            if not source.is_file():
                raise RuntimeError(f"Файл не найден: {source}")
            async with app._player_switch_lock:
                started_switch = True
                app._player_switching = True
                if app.audio is not None:
                    try:
                        await app.audio.pause()
                    except Exception:
                        LOGGER.debug("Не удалось остановить предыдущий source", exc_info=True)
                if request_revision != app._player_request_revision:
                    return
                app._player_load_event = asyncio.Event()
                audio, source_changed, audio_created = app._ensure_audio_path(source)
                if audio_created:
                    app.page.update()
                elif source_changed:
                    audio.update()
                else:
                    app._player_load_event.set()
                await asyncio.wait_for(
                    app._player_load_event.wait(),
                    timeout=_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS,
                )
                if request_revision != app._player_request_revision:
                    return
                duration = await asyncio.wait_for(
                    audio.get_duration(),
                    timeout=_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS,
                )
                previous_track_id = app._player_track_id
                app._player_track_id = None
                app._player_position_ms = 0
                app._player_duration_ms = (
                    max(0, duration.in_milliseconds) if duration is not None else 0
                )
                app._player_state = fta.AudioState.STOPPED
                app.player_title.value = title
                app.player_bar.visible = True
                self.refresh_player_controls()
                controls: list[ft.Control] = [
                    app.player_bar,
                    app.player_title,
                    app.player_play_button,
                    app.player_position,
                    app.player_progress,
                ]
                if previous_track_id is not None:
                    waveform = self.paint_waveform_progress(previous_track_id, 0.0)
                    if waveform is not None:
                        controls.append(waveform)
                app._player_switching = False
                app._player_load_event = None
                started_switch = False
                app.page.update(*controls)
                await audio.play()
        except TimeoutError:
            LOGGER.error("Preview source не загрузился вовремя: %s", source)
            app._notify("Плеер не успел загрузить preview перехода")
        except Exception as exc:
            LOGGER.exception("Не удалось воспроизвести preview %s", source)
            app._notify(f"Не удалось воспроизвести переход: {exc}")
        finally:
            if started_switch:
                app._player_switching = False
                app._player_load_event = None

    def commit_player_track(
        self,
        track: TrackRecord,
        position_ms: int,
    ) -> list[ft.Control]:
        """Атомарно переключает UI плеера на уже загруженный трек."""
        app = self.app
        previous_track_id = app._player_track_id
        waveform_updates: list[ft.Control] = []
        if previous_track_id is not None and previous_track_id != track.id:
            previous_waveform = self.paint_waveform_progress(previous_track_id, 0.0)
            if previous_waveform is not None:
                waveform_updates.append(previous_waveform)

        app._player_track_id = track.id
        app._player_position_ms = max(0, position_ms)
        app._player_duration_ms = max(
            0, int((track.technical.duration or 0.0) * 1000)
        )
        app._player_state = fta.AudioState.STOPPED
        artist = track.metadata.artist or "Unknown Artist"
        title = track.metadata.title or track.path.stem
        app.player_title.value = f"{artist} - {title}"
        app.player_bar.visible = True
        self.refresh_player_controls()
        current_waveform = self.refresh_waveform_progress()
        if current_waveform is not None:
            waveform_updates.append(current_waveform)
        return waveform_updates

    async def stop_player(self, _: object) -> None:
        app = self.app
        if app.audio is None:
            return
        try:
            await app.audio.pause()
            await app.audio.seek(ft.Duration(milliseconds=0))
            app._player_position_ms = 0
            app._player_state = fta.AudioState.STOPPED
            self.refresh_player_controls()
            controls: list[ft.Control] = [
                app.player_play_button,
                app.player_position,
                app.player_progress,
            ]
            waveform = self.refresh_waveform_progress()
            if waveform is not None:
                controls.append(waveform)
            app.page.update(*controls)
        except Exception as exc:
            LOGGER.exception("Ошибка остановки плеера")
            app._notify(f"Ошибка плеера: {exc}")

    def on_player_duration_change(self, event: fta.AudioDurationChangeEvent) -> None:
        app = self.app
        if app._player_switching:
            return
        app._player_duration_ms = max(0, event.duration.in_milliseconds)
        self.refresh_player_controls()
        controls: list[ft.Control] = [app.player_position, app.player_progress]
        waveform = self.refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        app.page.update(*controls)

    def on_player_position_change(self, event: fta.AudioPositionChangeEvent) -> None:
        app = self.app
        if app._player_switching:
            return
        app._player_position_ms = max(0, int(event.position))
        self.refresh_player_controls()
        controls: list[ft.Control] = [app.player_position, app.player_progress]
        waveform = self.refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        app.page.update(*controls)

    def on_player_state_change(self, event: fta.AudioStateChangeEvent) -> None:
        app = self.app
        if app._player_switching:
            return
        app._player_state = event.state
        if event.state is fta.AudioState.COMPLETED:
            app._player_position_ms = app._player_duration_ms
        self.refresh_player_controls()
        controls: list[ft.Control] = [
            app.player_play_button,
            app.player_position,
            app.player_progress,
        ]
        waveform = self.refresh_waveform_progress()
        if waveform is not None:
            controls.append(waveform)
        app.page.update(*controls)

    def refresh_player_controls(self) -> None:
        app = self.app
        duration = app._player_duration_ms
        position = (
            min(app._player_position_ms, duration)
            if duration
            else app._player_position_ms
        )
        app.player_play_button.icon = (
            ft.Icons.PAUSE
            if app._player_state is fta.AudioState.PLAYING
            else ft.Icons.PLAY_ARROW
        )
        app.player_position.value = (
            f"{self.format_duration(position / 1000)} / "
            f"{self.format_duration(duration / 1000)}"
        )
        app.player_progress.value = (position / duration) if duration > 0 else 0.0

    @staticmethod
    def format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "--:--"
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    def refresh_waveform_progress(self) -> ft.Control | None:
        app = self.app
        track_id = app._player_track_id
        if track_id is None:
            return None
        duration = app._player_duration_ms
        fraction = (app._player_position_ms / duration) if duration > 0 else 0.0
        return self.paint_waveform_progress(track_id, fraction)

    def paint_waveform_progress(
        self,
        track_id: int,
        fraction: float,
    ) -> ft.Control | None:
        app = self.app
        view = app._waveform_views.get(track_id)
        if view is None:
            return None
        played = min(
            len(view.peaks),
            max(0, round(len(view.peaks) * min(1.0, max(0.0, fraction)))),
        )
        if played == view.played_bars:
            return None
        view.played_bars = played
        view.progress_image.src = app._waveform_svg(view.peaks, played)
        return view.progress_image
