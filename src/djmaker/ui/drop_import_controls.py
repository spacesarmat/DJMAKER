"""Drag&Drop импорт и сканирование папок: выделено из DJMakerUI (god object)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from djmaker.services.tasks import ManagedTask, TaskCancelled, TaskKind, TaskPaused
from djmaker.ui.density import COMPACT_UI

if TYPE_CHECKING:
    from djmaker.ui.app import DJMakerUI

LOGGER = logging.getLogger(__name__)


class DropImportController:
    """Владеет pick/scan/Drag&Drop-import логикой; состояние остаётся в DJMakerUI."""

    def __init__(self, app: "DJMakerUI") -> None:
        self.app = app

    async def pick_and_scan(self, _: object) -> None:
        app = self.app
        try:
            selected = await ft.FilePicker().get_directory_path(
                dialog_title="Выберите папку с музыкой"
            )
        except Exception as exc:
            LOGGER.exception("Ошибка FilePicker")
            app._notify(f"Не удалось открыть выбор папки: {exc}")
            return
        if not selected:
            return
        await self.run_scan(Path(selected))

    async def run_scan(self, path: Path, task_id: str | None = None) -> None:
        app = self.app
        root = path.expanduser().resolve()
        task: ManagedTask
        if task_id is None:
            task = app.tasks.create(
                kind=TaskKind.LIBRARY_SCAN,
                title=f"Сканирование: {root.name or root}",
                detail=str(root),
            )
            task_id = task.id
            app._scan_task_paths[task_id] = root
        else:
            task = app.tasks.get(task_id)  # type: ignore[assignment]
            if task is None:
                return

        app._refresh_task_indicator()
        app.status.value = f"Сканирование: {root}"
        app.page.update()

        def update_progress(stats: object, current_path: Path) -> None:
            discovered = int(getattr(stats, "discovered", 0))
            updated = int(getattr(stats, "updated", 0))
            unchanged = int(getattr(stats, "unchanged", 0))
            errors = int(getattr(stats, "errors", 0))
            task.set_progress(
                completed=updated + unchanged + errors,
                succeeded=updated + unchanged,
                failed=errors,
                detail=f"{current_path.name} · найдено: {discovered}",
            )

        try:
            stats = await app.workers.run(
                app.service.scan_folder,
                root,
                task=task,
                progress=update_progress,
            )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            app._notify(f"Сканирование остановлено: {root}")
        except TaskCancelled:
            task.mark_cancelled()
            app._forget_task_context(task_id)
            app._notify(f"Сканирование отменено: {root}")
        except Exception as exc:
            LOGGER.exception("Ошибка сканирования")
            task.mark_failed(exc)
            app._forget_task_context(task_id)
            app._notify(f"Ошибка сканирования: {exc}")
        else:
            task.set_progress(
                completed=stats.updated + stats.unchanged + stats.errors,
                succeeded=stats.updated + stats.unchanged,
                failed=stats.errors,
                detail=f"Завершено · найдено: {stats.discovered}",
            )
            task.mark_completed()
            app._forget_task_context(task_id)
            app._notify(
                "Сканирование завершено: "
                f"найдено {stats.discovered}, обновлено {stats.updated}, "
                f"без изменений {stats.unchanged}, удалено {stats.removed}, "
                f"пропущено {stats.ignored}, ошибок {stats.errors}"
            )
            if app.navigation.selected_index == 1:
                app.show_folders()
            if app.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS) is None:
                app.page.run_task(app.ensure_waveforms)
        finally:
            app._refresh_task_indicator()
            app.page.update()

    async def run_drop_import(
        self,
        paths: tuple[Path, ...],
        task_id: str | None = None,
    ) -> None:
        """Импортирует dropped-файлы/папки через общий scanner/task pipeline."""
        app = self.app
        task: ManagedTask
        if task_id is None:
            if (
                app.tasks.active_for_kind(TaskKind.LIBRARY_SCAN) is not None
                or app.tasks.active_for_kind(TaskKind.LIBRARY_IMPORT) is not None
            ):
                app._notify(
                    "Drag&Drop: дождитесь завершения текущего сканирования "
                    "или остановите его в «Задачах»"
                )
                return

            plan = app.service.plan_import_paths(list(paths))
            if plan.accepted_count == 0:
                app._notify(
                    "Drag&Drop: поддерживаемых аудиофайлов или папок не найдено "
                    f"· пропущено: {plan.ignored_count}"
                )
                return

            task = app.tasks.create(
                kind=TaskKind.LIBRARY_IMPORT,
                title="Drag&Drop импорт",
                detail=(
                    f"папок: {len(plan.directories)} · файлов: {len(plan.files)} "
                    f"· сразу пропущено: {plan.ignored_count}"
                ),
            )
            task_id = task.id
            app._drop_task_paths[task_id] = tuple(paths)
        else:
            task = app.tasks.get(task_id)  # type: ignore[assignment]
            if task is None:
                return

        app._refresh_task_indicator()
        app.status.value = "Drag&Drop: импорт файлов и папок"
        app.page.update()

        def update_progress(stats: object, current_path: Path) -> None:
            discovered = int(getattr(stats, "discovered", 0))
            updated = int(getattr(stats, "updated", 0))
            unchanged = int(getattr(stats, "unchanged", 0))
            errors = int(getattr(stats, "errors", 0))
            ignored = int(getattr(stats, "ignored", 0))
            task.set_progress(
                completed=updated + unchanged + errors + ignored,
                succeeded=updated + unchanged,
                failed=errors,
                detail=(
                    f"{current_path.name} · аудио: {discovered} "
                    f"· пропущено: {ignored}"
                ),
            )

        try:
            stats = await app.workers.run(
                app.service.import_paths,
                list(paths),
                task=task,
                progress=update_progress,
            )
        except TaskPaused:
            task.mark_paused("Остановлено · можно продолжить")
            app._notify("Drag&Drop импорт остановлен")
        except TaskCancelled:
            task.mark_cancelled()
            app._forget_task_context(task_id)
            app._notify("Drag&Drop импорт отменён")
        except Exception as exc:
            LOGGER.exception("Ошибка Drag&Drop импорта")
            task.mark_failed(exc)
            app._forget_task_context(task_id)
            app._notify(f"Ошибка Drag&Drop импорта: {exc}")
        else:
            task.set_progress(
                completed=(
                    stats.updated
                    + stats.unchanged
                    + stats.errors
                    + stats.ignored
                ),
                succeeded=stats.updated + stats.unchanged,
                failed=stats.errors,
                detail=(
                    f"Завершено · аудио: {stats.discovered} "
                    f"· пропущено: {stats.ignored}"
                ),
            )
            task.mark_completed()
            app._forget_task_context(task_id)
            app._notify(
                "Drag&Drop завершён: "
                f"аудио {stats.discovered}, обновлено {stats.updated}, "
                f"без изменений {stats.unchanged}, пропущено {stats.ignored}, "
                f"ошибок {stats.errors}"
            )
            if app.navigation.selected_index == 0:
                app.show_library()
            elif app.navigation.selected_index == 1:
                app.show_folders()
            if app.tasks.active_for_kind(TaskKind.WAVEFORM_ANALYSIS) is None:
                app.page.run_task(app.ensure_waveforms)
        finally:
            app._refresh_task_indicator()
            app.page.update()

    @staticmethod
    def path_setting(label: str, path: Path) -> ft.Control:
        return ft.Column(
            controls=[
                ft.Text(label, size=COMPACT_UI.font_micro),
                ft.Text(str(path), selectable=True, size=COMPACT_UI.font_xs),
            ],
            spacing=0,
        )

    def on_paths_dropped(self, event: object) -> None:
        """Передаёт реальные desktop paths в управляемую задачу импорта."""
        app = self.app
        app._drop_overlay.visible = False
        app._drop_overlay.update()

        files = getattr(event, "files", ()) or ()
        paths = tuple(
            Path(path)
            for item in files
            if (path := str(getattr(item, "path", "") or "").strip())
        )
        if not paths:
            app._notify("Drag&Drop: не получено локальных файлов или папок")
            return
        app.page.run_task(self.run_drop_import, paths)
