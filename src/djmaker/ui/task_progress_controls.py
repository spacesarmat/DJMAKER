"""Фоновые задачи (сканирование/анализ/waveform/обложки) и их UI-карточки.

Выделено из DJMakerUI (god object).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import flet as ft

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.merge import match_confidence, merge_candidates
from djmaker.services.audio_analysis import recommended_analysis_concurrency
from djmaker.services.tasks import TaskCancelled, TaskKind, TaskPaused, TaskSnapshot, TaskStatus
from djmaker.ui.density import COMPACT_UI

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _AudioTaskContext:
    force: bool
    pending_ids: set[int] | None = None
    labels: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class _BatchTaskContext:
    pending_ids: set[int]
    labels: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class _WaveformTaskContext:
    force: bool
    pending_ids: set[int] | None = None
    labels: dict[int, str] = field(default_factory=dict)


class TaskProgressController:
    """Владеет фоновыми задачами и их UI; состояние остаётся в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    def refresh_task_indicator(self) -> None:
        """Синхронизирует компактный индикатор фоновых задач."""
        app = self.app
        active = app.tasks.active_count()
        app.task_count_text.value = f"Задачи: {active}"
        app.busy.visible = active > 0

    async def monitor_tasks(self) -> None:
        """Обновляет экран задач, пока долгие worker-операции меняют состояние."""
        app = self.app
        previous: tuple[object, ...] | None = None
        while True:
            await asyncio.sleep(0.4)
            snapshots = app.tasks.snapshots()
            signature = tuple(
                (
                    item.id,
                    item.status,
                    item.completed,
                    item.succeeded,
                    item.failed,
                    item.detail,
                )
                for item in snapshots
            )
            if signature == previous:
                continue
            previous = signature
            self.refresh_task_indicator()
            if app.navigation.selected_index == 5:
                self.show_tasks()
            else:
                app.page.update()

    def show_tasks(self) -> None:
        """Показывает текущие и завершённые задачи текущего сеанса."""
        app = self.app
        app._set_navigation_index(5)
        snapshots = app.tasks.snapshots()
        active = [item for item in snapshots if item.active]
        history = [item for item in snapshots if not item.active]

        summary = app._surface_card(
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.PENDING_ACTIONS, color=ft.Colors.PRIMARY),
                    ft.Column(
                        controls=[
                            ft.Text(
                                f"Текущих задач: {len(active)}",
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                "Остановка сохраняет очередь для продолжения; "
                                "отмена завершает задачу окончательно.",
                                size=COMPACT_UI.font_xs,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                ]
            )
        )

        items: list[ft.Control] = []
        if not snapshots:
            items.append(
                app._empty_state(
                    ft.Icons.TASK_ALT,
                    "Фоновых задач пока нет",
                    "Сканирование, Drag&Drop, обложки, waveform и полный аудио-анализ появятся здесь.",
                )
            )
        else:
            if active:
                items.append(ft.Text("Текущие", weight=ft.FontWeight.BOLD))
                items.extend(self.task_card(item) for item in active)
            if history:
                items.append(ft.Text("История сеанса", weight=ft.FontWeight.BOLD))
                items.extend(self.task_card(item) for item in history)

        listing = ft.ListView(
            controls=items,
            expand=True,
            spacing=COMPACT_UI.space_sm,
        )
        app._replace_content(
            "Задачи",
            "Фоновые операции DJMAKER",
            summary,
            listing,
        )

    def task_card(self, snapshot: TaskSnapshot) -> ft.Control:
        app = self.app
        state_label, state_icon, state_color = app._task_state_view(snapshot.status)
        if snapshot.total is not None:
            counters = (
                f"{snapshot.completed}/{snapshot.total} · "
                f"успешно: {snapshot.succeeded} · ошибок: {snapshot.failed}"
            )
        else:
            counters = (
                f"обработано: {snapshot.completed} · "
                f"успешно: {snapshot.succeeded} · ошибок: {snapshot.failed}"
            )

        actions: list[ft.Control] = []
        if snapshot.can_stop:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.PAUSE_CIRCLE_OUTLINE,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Остановить с возможностью продолжения",
                    on_click=lambda _, task_id=snapshot.id: self.stop_task(task_id),
                )
            )
        if snapshot.can_resume:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.PLAY_ARROW,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Возобновить",
                    on_click=lambda _, task_id=snapshot.id: self.resume_task(task_id),
                )
            )
        if snapshot.can_cancel:
            actions.append(
                ft.IconButton(
                    icon=ft.Icons.CANCEL_OUTLINED,
                    icon_size=COMPACT_UI.action_icon_size,
                    padding=COMPACT_UI.space_xs,
                    visual_density=ft.VisualDensity.COMPACT,
                    tooltip="Отменить окончательно",
                    on_click=lambda _, task_id=snapshot.id: self.cancel_task(task_id),
                )
            )

        progress_value = snapshot.progress
        if progress_value is None and snapshot.status is not TaskStatus.RUNNING:
            progress_value = 0.0

        details: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Icon(state_icon, size=16, color=state_color),
                    ft.Text(
                        snapshot.title,
                        weight=ft.FontWeight.BOLD,
                        size=COMPACT_UI.font_sm,
                    ),
                    ft.Text(
                        state_label,
                        size=COMPACT_UI.font_xs,
                        color=state_color,
                    ),
                    ft.Container(expand=True),
                    *actions,
                ],
                spacing=COMPACT_UI.space_xs,
            ),
            ft.Text(
                snapshot.detail or "—",
                size=COMPACT_UI.font_xs,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.ProgressBar(value=progress_value),
            ft.Text(
                counters,
                size=COMPACT_UI.font_micro,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if snapshot.error:
            details.append(
                ft.Text(
                    snapshot.error,
                    size=COMPACT_UI.font_xs,
                    color=ft.Colors.ERROR,
                )
            )
        return app._surface_card(ft.Column(controls=details, spacing=3))

    def stop_task(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        if task is None or not task.request_pause():
            return
        self.refresh_task_indicator()
        self.show_tasks()

    def cancel_task(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        if task is None or not task.request_cancel():
            return
        if task.snapshot().status is TaskStatus.CANCELLED:
            self.forget_task_context(task_id)
        self.refresh_task_indicator()
        self.show_tasks()

    def resume_task(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        if task is None:
            return
        snapshot = task.snapshot()
        if not task.resume():
            return

        if snapshot.kind is TaskKind.AUDIO_ANALYSIS and task_id in app._audio_task_contexts:
            app.page.run_task(self.run_audio_analysis, task_id)
        elif snapshot.kind is TaskKind.LIBRARY_SCAN and task_id in app._scan_task_paths:
            app.page.run_task(app._run_scan, app._scan_task_paths[task_id], task_id)
        elif (
            snapshot.kind is TaskKind.LIBRARY_IMPORT
            and task_id in app._drop_task_paths
        ):
            app.page.run_task(
                app._run_drop_import,
                app._drop_task_paths[task_id],
                task_id,
            )
        elif snapshot.kind is TaskKind.ARTWORK_INDEX and task_id in app._artwork_task_contexts:
            app.page.run_task(self.run_embedded_artwork, task_id)
        elif (
            snapshot.kind is TaskKind.WAVEFORM_ANALYSIS
            and task_id in app._waveform_task_contexts
        ):
            app.page.run_task(self.run_waveform_analysis, task_id)
        elif (
            snapshot.kind is TaskKind.PLAYLIST_EXPORT
            and task_id in app._playlist_export_contexts
        ):
            app.page.run_task(app._run_playlist_export, task_id)
        elif (
            snapshot.kind is TaskKind.METADATA_BULK_SEARCH
            and task_id in app._metadata_bulk_task_contexts
        ):
            app.page.run_task(self.run_metadata_bulk_search, task_id)
        else:
            task.mark_failed("Контекст задачи больше недоступен")

        self.refresh_task_indicator()
        self.show_tasks()

    def forget_task_context(self, task_id: str) -> None:
        app = self.app
        app._playlist_export_contexts.pop(task_id, None)
        app._audio_task_contexts.pop(task_id, None)
        app._scan_task_paths.pop(task_id, None)
        app._drop_task_paths.pop(task_id, None)
        app._artwork_task_contexts.pop(task_id, None)
        app._waveform_task_contexts.pop(task_id, None)
        app._metadata_bulk_task_contexts.pop(task_id, None)

    async def run_audio_analysis(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        context = app._audio_task_contexts.get(task_id)
        if task is None or context is None:
            return

        app.analysis_progress.visible = True
        app.analysis_progress.value = task.snapshot().progress or 0
        app.analysis_progress_text.visible = True
        app._set_status("Подготовка FFmpeg и Essentia...")

        try:
            task.checkpoint()
            report = await app.workers.run(app.runtime.ensure_all)
            app.runtime_report = report
            task.checkpoint()
            if not report.ffmpeg.available:
                raise RuntimeError(f"FFmpeg недоступен: {report.ffmpeg.detail}")
            if app.runtime.essentia_analyzer_path() is None:
                raise RuntimeError(
                    "Собственный DJMAKER Essentia runtime недоступен. "
                    "Откройте Настройки → Аудио-компоненты."
                )

            if context.pending_ids is None:
                tracks = await app.workers.run(
                    app.service.tracks_for_analysis,
                    force=context.force,
                )
                context.pending_ids = {track.id for track in tracks}
                context.labels = {track.id: str(track.path) for track in tracks}
                task.set_progress(
                    total=len(tracks),
                    detail="Очередь полного аудио-анализа подготовлена",
                )

            pending_ids = context.pending_ids
            if not pending_ids:
                task.mark_completed("Все треки уже проанализированы")
                self.forget_task_context(task_id)
                app._notify("Все треки уже проанализированы")
                return

            total = task.snapshot().total or len(pending_ids)
            parallelism = min(
                recommended_analysis_concurrency(),
                app.workers.max_workers,
                len(pending_ids),
            )
            task.set_progress(
                detail=f"BPM / сетка / Key · потоков: {parallelism}"
            )
            app.analysis_progress_text.value = (
                f"{task.snapshot().completed}/{total} · потоков: {parallelism}"
            )
            app.page.update()

            def analyze_one(track_id: int) -> TrackRecord:
                task.checkpoint()
                return app.service.analyze_track(track_id, task=task)

            async for outcome in app.workers.run_many_unordered(
                analyze_one,
                list(pending_ids),
                max_concurrency=parallelism,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(outcome.item, f"track_id={outcome.item}")
                pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось проанализировать %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    task.advance(success=True, detail=label)

                snapshot = task.snapshot()
                app.status.value = (
                    f"BPM / сетка / Key: {snapshot.completed}/{total} · "
                    f"потоков: {parallelism}"
                )
                app.analysis_progress.value = snapshot.progress or 0
                app.analysis_progress_text.value = (
                    f"{snapshot.completed}/{total} · потоков: {parallelism}"
                )
                app.page.update()

            if task.cancel_requested:
                task.mark_cancelled()
                self.forget_task_context(task_id)
                app._notify("Полный аудио-анализ отменён")
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
                app._notify("Полный аудио-анализ остановлен")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} успешно, "
                    f"{snapshot.failed} ошибок"
                )
                self.forget_task_context(task_id)
                app._notify(
                    "Аудио-анализ завершён: "
                    f"{snapshot.succeeded} успешно, {snapshot.failed} ошибок"
                )
                if app.navigation.selected_index == 0:
                    app.show_library()
                elif app.navigation.selected_index == 4:
                    app.show_audio_modules()
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            app._notify("Полный аудио-анализ остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self.forget_task_context(task_id)
            app._notify("Полный аудио-анализ отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка пакетного аудио-анализа")
            task.mark_failed(exc)
            self.forget_task_context(task_id)
            app._notify(f"Не удалось выполнить полный аудио-анализ: {exc}")
        finally:
            snapshot = task.snapshot()
            running = snapshot.status in {
                TaskStatus.RUNNING,
                TaskStatus.STOPPING,
                TaskStatus.CANCELLING,
            }
            app.analysis_progress.visible = running
            app.analysis_progress_text.visible = running
            self.refresh_task_indicator()
            app.page.update()

    def start_waveform_analysis(self, _: object, *, force: bool = False) -> None:
        app = self.app
        existing = app.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS)
        if existing is not None:
            app._notify(
                "Waveform уже строится или остановлен. "
                "Откройте «Задачи» для управления."
            )
            return

        task = app.tasks.create(
            kind=TaskKind.WAVEFORM_ANALYSIS,
            title="Анализ Waveform",
            detail="Подготовка FFmpeg...",
        )
        app._waveform_task_contexts[task.id] = _WaveformTaskContext(force=force)
        self.refresh_task_indicator()
        app.page.run_task(self.run_waveform_analysis, task.id)

    async def ensure_waveforms(self) -> None:
        """Автоматически строит отсутствующие waveform после запуска приложения."""
        app = self.app
        existing = app.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS)
        if existing is not None:
            return
        try:
            tracks = await app.workers.run(
                app.service.tracks_for_waveform_analysis,
                force=False,
            )
        except Exception:
            LOGGER.exception("Не удалось получить очередь waveform")
            return
        if not tracks:
            return

        task = app.tasks.create(
            kind=TaskKind.WAVEFORM_ANALYSIS,
            title="Анализ Waveform",
            detail="Подготовка FFmpeg...",
            total=len(tracks),
        )
        app._waveform_task_contexts[task.id] = _WaveformTaskContext(
            force=False,
            pending_ids={track.id for track in tracks},
            labels={track.id: str(track.path) for track in tracks},
        )
        self.refresh_task_indicator()
        await self.run_waveform_analysis(task.id)

    async def run_waveform_analysis(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        context = app._waveform_task_contexts.get(task_id)
        if task is None or context is None:
            return

        changed = False
        try:
            task.checkpoint()
            report = await app.workers.run(app.runtime.ensure_all)
            app.runtime_report = report
            task.checkpoint()
            if not report.ffmpeg.available:
                raise RuntimeError(f"FFmpeg недоступен: {report.ffmpeg.detail}")

            if context.pending_ids is None:
                tracks = await app.workers.run(
                    app.service.tracks_for_waveform_analysis,
                    force=context.force,
                )
                context.pending_ids = {track.id for track in tracks}
                context.labels = {track.id: str(track.path) for track in tracks}
                task.set_progress(
                    total=len(tracks),
                    detail="Очередь waveform подготовлена",
                )

            pending_ids = context.pending_ids
            if not pending_ids:
                task.mark_completed("Все waveform уже построены")
                self.forget_task_context(task_id)
                return

            concurrency = min(1, app.workers.max_workers, len(pending_ids))
            task.set_progress(detail=f"Waveform · потоков: {concurrency}")

            def analyze_one(track_id: int) -> TrackRecord:
                task.checkpoint()
                return app.service.analyze_waveform(track_id, task=task)

            async for outcome in app.workers.run_many_unordered(
                analyze_one,
                list(pending_ids),
                max_concurrency=concurrency,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(outcome.item, f"track_id={outcome.item}")
                pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось построить waveform %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    changed = True
                    task.advance(success=True, detail=label)

            if task.cancel_requested:
                task.mark_cancelled()
                self.forget_task_context(task_id)
                app._notify("Анализ waveform отменён")
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
                app._notify("Анализ waveform остановлен")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} waveform, "
                    f"{snapshot.failed} ошибок"
                )
                self.forget_task_context(task_id)
                app._notify(
                    "Waveform-анализ завершён: "
                    f"{snapshot.succeeded} успешно, {snapshot.failed} ошибок"
                )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            app._notify("Анализ waveform остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self.forget_task_context(task_id)
            app._notify("Анализ waveform отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка пакетного waveform-анализа")
            task.mark_failed(exc)
            self.forget_task_context(task_id)
            app._notify(f"Не удалось выполнить waveform-анализ: {exc}")
        finally:
            self.refresh_task_indicator()
            if changed and app.navigation.selected_index == 0:
                app.show_library()
            elif app.navigation.selected_index == 4:
                app.show_audio_modules()
            else:
                app.page.update()

    def start_metadata_bulk_search(self, tracks: list[TrackRecord]) -> None:
        """Запускает фоновый массовый поиск метаданных по переданным трекам."""
        app = self.app
        existing = app.tasks.active_for_kind(TaskKind.METADATA_BULK_SEARCH)
        if existing is not None:
            app._notify(
                "Массовый поиск метаданных уже выполняется или остановлен. "
                "Откройте «Задачи» для управления."
            )
            return
        if not tracks:
            app._notify("Нет треков для обработки")
            return
        if not app.settings.metadata_providers:
            app._notify(
                "Не выбрано ни одного источника метаданных (Настройки → Источники метаданных)"
            )
            return

        task = app.tasks.create(
            kind=TaskKind.METADATA_BULK_SEARCH,
            title="Массовый поиск метаданных",
            detail="Подготовка очереди...",
            total=len(tracks),
        )
        app._metadata_bulk_task_contexts[task.id] = _BatchTaskContext(
            pending_ids={track.id for track in tracks},
            labels={
                track.id: f"{track.metadata.artist or '—'} - {track.metadata.title or track.path.stem}"
                for track in tracks
            },
        )
        self.refresh_task_indicator()
        app.page.run_task(self.run_metadata_bulk_search, task.id)

    async def run_metadata_bulk_search(self, task_id: str) -> None:
        """Массовый поиск: общий пул воркеров разбирает пары (трек, провайдер).

        Все пары ставятся в очередь сразу — как только какой-то поиск
        завершается, освободившийся воркер берёт следующую пару, независимо
        от того, какому треку она принадлежит (это и даёт выигрыш от
        параллелизма при большом числе треков и провайдеров). Когда для
        трека отчитались все его провайдеры — результаты объединяются и
        применяются/помечаются на основном цикле (см. _finish_metadata_bulk_track).
        """
        app = self.app
        task = app.tasks.get(task_id)
        context = app._metadata_bulk_task_contexts.get(task_id)
        if task is None or context is None:
            return

        provider_ids = app.settings.metadata_providers
        threshold = app.settings.metadata_auto_apply_threshold / 100.0
        track_ids = sorted(context.pending_ids)
        pairs = [
            (track_id, provider_id) for track_id in track_ids for provider_id in provider_ids
        ]

        changed = False
        applied = 0
        flagged = 0
        errors = 0

        per_provider_results: dict[int, dict[str, list[MetadataCandidate]]] = {
            track_id: {} for track_id in track_ids
        }
        failed_provider_counts: dict[int, int] = {track_id: 0 for track_id in track_ids}
        remaining_providers: dict[int, int] = {
            track_id: len(provider_ids) for track_id in track_ids
        }

        def search_pair(
            pair: tuple[int, str],
        ) -> tuple[list[MetadataCandidate] | None, Exception | None]:
            track_id, provider_id = pair
            try:
                return app.service.search_single_provider(track_id, provider_id, 8), None
            except Exception as exc:  # best-effort, как в LibraryService.search_metadata
                return None, exc

        try:
            if not pairs:
                task.mark_completed("Нет треков для обработки")
                self.forget_task_context(task_id)
                return

            task.set_progress(total=len(pairs), detail="Поиск метаданных...")

            async for outcome in app.workers.run_many_unordered(search_pair, pairs):
                task.checkpoint()
                track_id, provider_id = outcome.item
                if outcome.error is not None:
                    candidates, exc = None, outcome.error
                else:
                    candidates, exc = outcome.value

                if exc is not None:
                    LOGGER.warning(
                        "Провайдер %s недоступен для трека %s: %s", provider_id, track_id, exc
                    )
                    failed_provider_counts[track_id] += 1
                else:
                    per_provider_results[track_id][provider_id] = candidates or []

                remaining_providers[track_id] -= 1
                task.advance(success=exc is None, detail=f"{provider_id} · {track_id}")

                if remaining_providers[track_id] > 0:
                    continue

                label = context.labels.get(track_id, f"track_id={track_id}")
                try:
                    outcome_kind = await self._finish_metadata_bulk_track(
                        track_id,
                        per_provider_results.pop(track_id),
                        provider_ids,
                        threshold,
                        all_providers_failed=(
                            failed_provider_counts[track_id] == len(provider_ids)
                        ),
                    )
                except (TaskPaused, TaskCancelled):
                    raise
                except Exception as exc:
                    LOGGER.warning("Не удалось завершить обработку %s: %s", label, exc)
                    errors += 1
                    task.set_progress(detail=f"{label}: ошибка")
                else:
                    if outcome_kind == "applied":
                        applied += 1
                        changed = True
                    else:
                        flagged += 1
                    task.set_progress(detail=f"{label}: {outcome_kind}")
                finally:
                    context.pending_ids.discard(track_id)

            task.mark_completed(
                f"Готово: применено {applied}, на проверку {flagged}, ошибок {errors}"
            )
            self.forget_task_context(task_id)
            app._notify(
                f"Массовый поиск завершён: применено {applied}, "
                f"на проверку {flagged}, ошибок {errors}"
            )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            app._notify("Массовый поиск остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            self.forget_task_context(task_id)
            app._notify("Массовый поиск отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка массового поиска метаданных")
            task.mark_failed(exc)
            self.forget_task_context(task_id)
            app._notify(f"Не удалось выполнить массовый поиск: {exc}")
        finally:
            self.refresh_task_indicator()
            if changed and app.navigation.selected_index == 0:
                app.show_library()
            elif app.navigation.selected_index == 3:
                app.show_plugins()
            else:
                app.page.update()

    async def _finish_metadata_bulk_track(
        self,
        track_id: int,
        per_provider: dict[str, list[MetadataCandidate]],
        provider_ids: tuple[str, ...],
        threshold: float,
        *,
        all_providers_failed: bool,
    ) -> str:
        """Объединяет собранные по всем провайдерам результаты одного трека и
        применяет либо помечает его на проверку.

        Вызывается на основном asyncio-цикле (не в worker-потоке) уже после
        того, как все провайдеры для этого трека отчитались — поэтому
        release_if_current перед записью тегов остаётся safe для UI-состояния
        плеера, как и в одиночном сценарии поиска.
        """
        app = self.app
        track = await app.workers.run(app.service.database.get_track, track_id)
        if track is None:
            return "flagged"

        # Порядок задаёт приоритет при merge — по настройкам, а не по тому,
        # какой провайдер отчитался первым.
        ordered = [per_provider.get(provider_id, []) for provider_id in provider_ids]
        candidates = merge_candidates(ordered)

        if not candidates:
            reason = "search_failed" if all_providers_failed else "not_found"
            await app.workers.run(
                app.service.database.set_metadata_review,
                track_id,
                reason=reason,
                score=None,
            )
            return "flagged"

        best = max(candidates, key=lambda candidate: match_confidence(track, candidate))
        score = match_confidence(track, best)
        if score >= threshold:
            await app.player.release_if_current(track_id)
            await app.workers.run(app.service.apply_candidate, track_id, best)
            return "applied"

        await app.workers.run(
            app.service.database.set_metadata_review,
            track_id,
            reason="low_confidence",
            score=score,
        )
        return "flagged"

    async def ensure_embedded_artwork(self) -> None:
        """Индексирует встроенные обложки как управляемую фоновую задачу."""
        app = self.app
        existing = app.tasks.active_for_kind(TaskKind.ARTWORK_INDEX)
        if existing is not None:
            return
        try:
            tracks = await app.workers.run(app.service.tracks_for_artwork_refresh)
        except Exception:
            LOGGER.exception("Не удалось получить очередь встроенных обложек")
            return
        if not tracks:
            return

        task = app.tasks.create(
            kind=TaskKind.ARTWORK_INDEX,
            title="Индексирование встроенных обложек",
            detail="Подготовка очереди...",
            total=len(tracks),
        )
        app._artwork_task_contexts[task.id] = _BatchTaskContext(
            pending_ids={track.id for track in tracks},
            labels={track.id: str(track.path) for track in tracks},
        )
        self.refresh_task_indicator()
        await self.run_embedded_artwork(task.id)

    async def run_embedded_artwork(self, task_id: str) -> None:
        app = self.app
        task = app.tasks.get(task_id)
        context = app._artwork_task_contexts.get(task_id)
        if task is None or context is None:
            return

        changed = False
        concurrency = min(2, app.workers.max_workers)

        def refresh_one(track_id: int) -> TrackRecord:
            task.checkpoint()
            return app.service.refresh_embedded_artwork(track_id, task=task)

        try:
            async for outcome in app.workers.run_many_unordered(
                refresh_one,
                list(context.pending_ids),
                max_concurrency=concurrency,
            ):
                if isinstance(outcome.error, (TaskPaused, TaskCancelled)):
                    continue

                label = context.labels.get(
                    outcome.item, f"track_id={outcome.item}"
                )
                context.pending_ids.discard(outcome.item)
                if outcome.error is not None:
                    LOGGER.warning(
                        "Не удалось прочитать встроенную обложку %s: %s",
                        label,
                        outcome.error,
                    )
                    task.advance(success=False, detail=label)
                else:
                    changed = True
                    task.advance(success=True, detail=label)

            if task.cancel_requested:
                task.mark_cancelled()
                self.forget_task_context(task_id)
            elif task.pause_requested:
                task.mark_paused("Остановлено · можно продолжить")
            else:
                snapshot = task.snapshot()
                task.mark_completed(
                    f"Готово: {snapshot.succeeded} обложек, "
                    f"{snapshot.failed} ошибок"
                )
                self.forget_task_context(task_id)
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
        except TaskCancelled:
            task.mark_cancelled()
            self.forget_task_context(task_id)
        except Exception as exc:
            LOGGER.exception("Ошибка индексирования встроенных обложек")
            task.mark_failed(exc)
            self.forget_task_context(task_id)
        finally:
            self.refresh_task_indicator()
            if changed and app.navigation.selected_index == 0:
                app.show_library()
            else:
                app.page.update()
