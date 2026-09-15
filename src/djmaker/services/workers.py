"""Фоновое выполнение тяжёлых задач вне UI-потока Flet."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar


T = TypeVar("T")


class BackgroundWorkers:
    """Небольшой пул потоков для сканирования, хэширования и файловых операций."""

    def __init__(self, max_workers: int = 4) -> None:
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

    def close(self) -> None:
        """Корректно завершает пул worker-потоков."""
        self._executor.shutdown(wait=False, cancel_futures=True)
