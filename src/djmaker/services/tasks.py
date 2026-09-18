"""Потокобезопасное состояние долгих фоновых задач DJMAKER."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol


class TaskStatus(StrEnum):
    """Состояние управляемой фоновой задачи."""

    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskKind(StrEnum):
    """Типы долгих операций, показываемых в менеджере задач."""

    AUDIO_ANALYSIS = "audio_analysis"
    LIBRARY_SCAN = "library_scan"
    LIBRARY_IMPORT = "library_import"
    PLAYLIST_EXPORT = "playlist_export"
    ARTWORK_INDEX = "artwork_index"
    WAVEFORM_ANALYSIS = "waveform_analysis"
    METADATA_BULK_SEARCH = "metadata_bulk_search"


ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.RUNNING,
        TaskStatus.STOPPING,
        TaskStatus.PAUSED,
        TaskStatus.CANCELLING,
    }
)


class TaskInterrupted(Exception):
    """Базовый сигнал кооперативного прерывания worker-операции."""


class TaskPaused(TaskInterrupted):
    """Операция остановлена с возможностью последующего продолжения."""


class TaskCancelled(TaskInterrupted):
    """Операция окончательно отменена пользователем."""


class TaskControl(Protocol):
    """Минимальный контракт для кода, поддерживающего прерывание."""

    def checkpoint(self) -> None:
        """Прерывает текущую операцию, если запрошена остановка/отмена."""


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """Неизменяемое представление задачи для UI."""

    id: str
    kind: TaskKind
    title: str
    status: TaskStatus
    detail: str
    total: int | None
    completed: int
    succeeded: int
    failed: int
    created_at: datetime
    started_at: datetime
    finished_at: datetime | None
    error: str

    @property
    def progress(self) -> float | None:
        """Возвращает прогресс 0..1, если известен общий объём."""
        if self.total is None or self.total <= 0:
            return None
        return min(1.0, max(0.0, self.completed / self.total))

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_TASK_STATUSES

    @property
    def can_stop(self) -> bool:
        return self.status is TaskStatus.RUNNING

    @property
    def can_resume(self) -> bool:
        return self.status is TaskStatus.PAUSED

    @property
    def can_cancel(self) -> bool:
        return self.status in {
            TaskStatus.RUNNING,
            TaskStatus.STOPPING,
            TaskStatus.PAUSED,
        }


class ManagedTask:
    """Изменяемое потокобезопасное состояние одной долгой операции."""

    def __init__(
        self,
        *,
        kind: TaskKind,
        title: str,
        detail: str = "",
        total: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.title = title
        self._status = TaskStatus.RUNNING
        self._detail = detail
        self._total = total
        self._completed = 0
        self._succeeded = 0
        self._failed = 0
        self._created_at = now
        self._started_at = now
        self._finished_at: datetime | None = None
        self._error = ""
        self._pause_requested = threading.Event()
        self._cancel_requested = threading.Event()
        self._lock = threading.RLock()

    def snapshot(self) -> TaskSnapshot:
        """Возвращает согласованный снимок состояния."""
        with self._lock:
            return TaskSnapshot(
                id=self.id,
                kind=self.kind,
                title=self.title,
                status=self._status,
                detail=self._detail,
                total=self._total,
                completed=self._completed,
                succeeded=self._succeeded,
                failed=self._failed,
                created_at=self._created_at,
                started_at=self._started_at,
                finished_at=self._finished_at,
                error=self._error,
            )

    @property
    def pause_requested(self) -> bool:
        return self._pause_requested.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def request_pause(self) -> bool:
        """Просит worker'ы остановиться и сохранить задачу для продолжения."""
        with self._lock:
            if self._status is not TaskStatus.RUNNING:
                return False
            self._pause_requested.set()
            self._status = TaskStatus.STOPPING
            self._detail = self._detail or "Останавливается..."
            return True

    def request_cancel(self) -> bool:
        """Просит окончательно прекратить задачу."""
        with self._lock:
            if self._status not in {
                TaskStatus.RUNNING,
                TaskStatus.STOPPING,
                TaskStatus.PAUSED,
            }:
                return False
            self._cancel_requested.set()
            self._pause_requested.clear()
            if self._status is TaskStatus.PAUSED:
                self._status = TaskStatus.CANCELLED
                self._finished_at = datetime.now(timezone.utc)
                self._detail = "Отменено"
            else:
                self._status = TaskStatus.CANCELLING
                self._detail = "Отменяется..."
            return True

    def resume(self) -> bool:
        """Возвращает остановленную задачу в состояние выполнения."""
        with self._lock:
            if self._status is not TaskStatus.PAUSED:
                return False
            self._pause_requested.clear()
            self._cancel_requested.clear()
            self._status = TaskStatus.RUNNING
            self._finished_at = None
            self._error = ""
            return True

    def checkpoint(self) -> None:
        """Проверяет запрос пользователя из worker-потока."""
        if self._cancel_requested.is_set():
            raise TaskCancelled("Задача отменена")
        if self._pause_requested.is_set():
            raise TaskPaused("Задача остановлена")

    def set_progress(
        self,
        *,
        total: int | None = None,
        completed: int | None = None,
        succeeded: int | None = None,
        failed: int | None = None,
        detail: str | None = None,
    ) -> None:
        """Атомарно обновляет счётчики и описание текущей операции."""
        with self._lock:
            if total is not None:
                self._total = max(0, total)
            if completed is not None:
                self._completed = max(0, completed)
            if succeeded is not None:
                self._succeeded = max(0, succeeded)
            if failed is not None:
                self._failed = max(0, failed)
            if detail is not None:
                self._detail = detail

    def advance(self, *, success: bool, detail: str | None = None) -> None:
        """Засчитывает один полностью обработанный элемент."""
        with self._lock:
            self._completed += 1
            if success:
                self._succeeded += 1
            else:
                self._failed += 1
            if detail is not None:
                self._detail = detail

    def mark_paused(self, detail: str = "Остановлено") -> None:
        with self._lock:
            if self._cancel_requested.is_set():
                self._status = TaskStatus.CANCELLED
                self._finished_at = datetime.now(timezone.utc)
                self._detail = "Отменено"
                return
            self._status = TaskStatus.PAUSED
            self._detail = detail

    def mark_cancelled(self, detail: str = "Отменено") -> None:
        with self._lock:
            self._status = TaskStatus.CANCELLED
            self._detail = detail
            self._finished_at = datetime.now(timezone.utc)

    def mark_completed(self, detail: str = "Завершено") -> None:
        with self._lock:
            self._status = TaskStatus.COMPLETED
            self._detail = detail
            self._finished_at = datetime.now(timezone.utc)

    def mark_failed(self, error: BaseException | str) -> None:
        with self._lock:
            self._status = TaskStatus.FAILED
            self._error = str(error)
            self._detail = "Ошибка"
            self._finished_at = datetime.now(timezone.utc)


class TaskManager:
    """Реестр текущих и недавних задач одного сеанса DJMAKER."""

    def __init__(self, history_limit: int = 100) -> None:
        if history_limit < 1:
            raise ValueError("history_limit должен быть >= 1")
        self.history_limit = history_limit
        self._tasks: dict[str, ManagedTask] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def create(
        self,
        *,
        kind: TaskKind,
        title: str,
        detail: str = "",
        total: int | None = None,
    ) -> ManagedTask:
        task = ManagedTask(kind=kind, title=title, detail=detail, total=total)
        with self._lock:
            self._tasks[task.id] = task
            self._order.insert(0, task.id)
            self._trim_finished_locked()
        return task

    def get(self, task_id: str) -> ManagedTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def snapshots(self) -> list[TaskSnapshot]:
        with self._lock:
            tasks = [self._tasks[task_id] for task_id in self._order]
        return [task.snapshot() for task in tasks]

    def active_count(self) -> int:
        return sum(snapshot.active for snapshot in self.snapshots())

    def active_for_kind(self, kind: TaskKind) -> TaskSnapshot | None:
        for snapshot in self.snapshots():
            if snapshot.kind is kind and snapshot.active:
                return snapshot
        return None

    def _trim_finished_locked(self) -> None:
        if len(self._order) <= self.history_limit:
            return
        keep: list[str] = []
        finished_kept = 0
        for task_id in self._order:
            task = self._tasks[task_id]
            if task.snapshot().active:
                keep.append(task_id)
                continue
            if finished_kept < self.history_limit:
                keep.append(task_id)
                finished_kept += 1
            else:
                self._tasks.pop(task_id, None)
        self._order = keep
