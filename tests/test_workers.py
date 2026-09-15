from __future__ import annotations

import threading
import unittest

from djmaker.services.workers import BackgroundWorkers


class BackgroundWorkersTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_many_unordered_executes_items_in_parallel(self) -> None:
        workers = BackgroundWorkers(max_workers=2)
        barrier = threading.Barrier(2)
        thread_ids: set[int] = set()
        lock = threading.Lock()

        def work(value: int) -> int:
            with lock:
                thread_ids.add(threading.get_ident())
            barrier.wait(timeout=2.0)
            return value * 10

        try:
            outcomes = [
                outcome
                async for outcome in workers.run_many_unordered(
                    work,
                    [1, 2],
                    max_concurrency=2,
                )
            ]
        finally:
            workers.close()

        self.assertEqual(2, len(thread_ids))
        self.assertTrue(all(outcome.error is None for outcome in outcomes))
        self.assertEqual({10, 20}, {outcome.value for outcome in outcomes})

    async def test_run_many_unordered_keeps_per_item_errors(self) -> None:
        workers = BackgroundWorkers(max_workers=2)

        def work(value: int) -> int:
            if value == 2:
                raise RuntimeError("boom")
            return value

        try:
            outcomes = [
                outcome
                async for outcome in workers.run_many_unordered(work, [1, 2])
            ]
        finally:
            workers.close()

        by_item = {outcome.item: outcome for outcome in outcomes}
        self.assertEqual(1, by_item[1].value)
        self.assertIsNone(by_item[1].error)
        self.assertIsInstance(by_item[2].error, RuntimeError)


if __name__ == "__main__":
    unittest.main()
