"""Фоновое выполнение тяжёлых задач вне UI-потока Flet."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Generic, TypeVar


T = TypeVar("T")
I = TypeVar("I")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class WorkOutcome(Generic[I, R]):
    """Результат одного элемента параллельной worker-очереди."""

    item: I
    value: R | None = None
    error: Exception | None = None


class BackgroundWorkers:
    """Пул потоков для сканирования, анализа и файловых операций."""

    def __init__(self, max_workers: int = 4) -> None:
        if max_workers < 1:
            raise ValueError("max_workers должен быть >= 1")
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="djmaker-worker",
        )

    async def run(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Выполняет синхронную функцию в worker-потоке и возвращает результат."""
        loop = asyncio.get_running_loop()
        if kwargs:
            call = lambda: func(*args, **kwargs)
            return await loop.run_in_executor(self._executor, call)
        return await loop.run_in_executor(self._executor, func, *args)

    async def run_many_unordered(
        self,
        func: Callable[[I], R],
        items: Iterable[I],
        *,
        max_concurrency: int | None = None,
    ) -> AsyncIterator[WorkOutcome[I, R]]:
        """Выполняет элементы параллельно и отдаёт их по мере завершения."""
        queued = list(items)
        if not queued:
            return

        concurrency = self.max_workers if max_concurrency is None else max_concurrency
        if concurrency < 1:
            raise ValueError("max_concurrency должен быть >= 1")
        concurrency = min(concurrency, self.max_workers)
        semaphore = asyncio.Semaphore(concurrency)

        async def execute(item: I) -> WorkOutcome[I, R]:
            async with semaphore:
                try:
                    value = await self.run(func, item)
                except Exception as exc:
                    return WorkOutcome(item=item, error=exc)
                return WorkOutcome(item=item, value=value)

        tasks = [asyncio.create_task(execute(item)) for item in queued]
        try:
            for task in asyncio.as_completed(tasks):
                yield await task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self) -> None:
        """Корректно завершает пул worker-потоков."""
        self._executor.shutdown(wait=False, cancel_futures=True)
