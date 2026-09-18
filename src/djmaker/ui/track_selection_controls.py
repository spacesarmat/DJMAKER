"""Выбор трека в библиотеке и подключение Audio-плеера: выделено из DJMakerUI."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft
import flet_audio as fta

from djmaker.domain.models import TrackRecord
from djmaker.ui.density import COMPACT_UI
from djmaker.ui.player_controls import _PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS
from djmaker.ui.scrolling import centered_scroll_offset

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)


class TrackSelectionController:
    """Владеет выбором трека и подключением Audio service; состояние — в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def ensure_audio_service(
        self,
        track: TrackRecord,
    ) -> tuple[fta.Audio, bool, bool]:
        """Лениво создаёт Audio service и сообщает о смене source."""
        return self.ensure_audio_path(track.path)

    def ensure_audio_path(self, path: Path) -> tuple[fta.Audio, bool, bool]:
        """Подключает обычный трек или созданный локальный preview."""
        app = self.app
        source = path.expanduser().resolve()
        created = app.audio is None
        source_changed = created or app._player_track_path != source
        if app.audio is None:
            app.audio = fta.Audio(
                src=str(source),
                autoplay=False,
                release_mode=fta.ReleaseMode.STOP,
                on_loaded=app._on_player_loaded,
                on_duration_change=app._on_player_duration_change,
                on_position_change=app._on_player_position_change,
                on_state_change=app._on_player_state_change,
            )
            app.page.services.append(app.audio)
            app._player_track_path = source
        elif source_changed:
            app.audio.src = str(source)
            app._player_track_path = source
        return app.audio, source_changed, created

    async def play_track(self, track_id: int, position_ms: int = 0) -> None:
        """Выбирает трек и плавно запускает его с нужной позиции."""
        app = self.app
        app._player_request_revision += 1
        request_revision = app._player_request_revision
        await self.select_library_track(track_id)
        if request_revision != app._player_request_revision:
            return

        started_switch = False
        try:
            track = await app.workers.run(app.service.track, track_id)
            if request_revision != app._player_request_revision:
                return
            if not track.path.is_file():
                raise RuntimeError(f"Файл не найден: {track.path}")

            async with app._player_switch_lock:
                if request_revision != app._player_request_revision:
                    return

                source = track.path.expanduser().resolve()
                source_changed = app.audio is None or app._player_track_path != source
                target_position_ms = max(0, position_ms)

                if source_changed:
                    started_switch = True
                    app._player_switching = True
                    if app.audio is not None:
                        try:
                            await app.audio.pause()
                        except Exception:
                            LOGGER.debug(
                                "Не удалось приостановить предыдущий source",
                                exc_info=True,
                            )
                    if request_revision != app._player_request_revision:
                        return

                    app._player_load_event = asyncio.Event()
                    audio, _, audio_created = self.ensure_audio_service(track)
                    if audio_created:
                        # Первый Audio service нужно смонтировать в дерево страницы.
                        app.page.update()
                    else:
                        audio.update()

                    await asyncio.wait_for(
                        app._player_load_event.wait(),
                        timeout=_PLAYER_SOURCE_LOAD_TIMEOUT_SECONDS,
                    )
                    if request_revision != app._player_request_revision:
                        return
                else:
                    audio, _, _ = self.ensure_audio_service(track)

                waveform_updates = app._commit_player_track(
                    track,
                    target_position_ms,
                )
                app.page.update(
                    app.player_bar,
                    app.player_title,
                    app.player_play_button,
                    app.player_position,
                    app.player_progress,
                    *waveform_updates,
                )
                if started_switch:
                    app._player_switching = False
                    app._player_load_event = None
                    started_switch = False
                await audio.play(
                    position=ft.Duration(milliseconds=target_position_ms)
                )
        except TimeoutError:
            LOGGER.error("Audio source не загрузился вовремя: track_id=%s", track_id)
            app._notify("Плеер не успел загрузить выбранный файл")
        except Exception as exc:
            LOGGER.exception("Не удалось воспроизвести трек %s", track_id)
            app._notify(f"Не удалось воспроизвести файл: {exc}")
        finally:
            if started_switch:
                app._player_switching = False
                app._player_load_event = None

    def estimated_library_viewport_extent(self) -> float:
        """Оценивает viewport до первого scroll-event от Flutter-клиента."""
        app = self.app
        item_extent = app._track_item_extent()
        page_height = float(app.page.height or app.page.window.height or 640)
        reserved = 150 if app.player_bar.visible else 115
        return max(item_extent * 3, page_height - reserved)

    async def select_library_track(self, track_id: int) -> None:
        """Выделяет трек и плавно размещает его по центру списка."""
        app = self.app
        previous_id = app._selected_track_id
        app._selected_track_id = track_id

        listing = app._library_list
        index = app._library_track_indices.get(track_id)
        if listing is None or index is None or app.navigation.selected_index != 0:
            return

        updates: list[ft.Control] = []
        if previous_id is not None and previous_id != track_id:
            previous_card = app._track_row_cards.get(previous_id)
            if previous_card is not None:
                previous_card.bgcolor = ft.Colors.SURFACE_CONTAINER_LOW
                updates.append(previous_card)

        current_card = app._track_row_cards.get(track_id)
        if current_card is not None:
            current_card.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGH
            updates.append(current_card)
        if updates:
            app.page.update(*updates)

        item_extent = app._track_item_extent()
        viewport = (
            app._library_viewport_extent
            if app._library_viewport_extent > 0
            else self.estimated_library_viewport_extent()
        )
        estimated_max = max(
            0.0,
            len(app._library_track_indices) * item_extent - viewport,
        )
        max_scroll = max(app._library_max_scroll_extent, estimated_max)
        offset = centered_scroll_offset(
            index=index,
            item_extent=item_extent,
            viewport_extent=viewport,
            max_scroll_extent=max_scroll,
        )
        await listing.scroll_to(
            offset=offset,
            duration=COMPACT_UI.track_center_scroll_ms,
            curve=ft.AnimationCurve.EASE_IN_OUT_CUBIC,
        )
