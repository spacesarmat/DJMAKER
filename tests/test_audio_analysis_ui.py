"""Регрессия пакетного многопоточного BPM/Key анализа в UI."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"


class AudioAnalysisUITests(unittest.TestCase):
    def test_batch_analysis_uses_parallel_worker_queue(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("recommended_analysis_concurrency()", source)
        self.assertIn("self.workers.run_many_unordered(", source)
        self.assertIn("max_concurrency=parallelism", source)
        self.assertIn("потоков: {parallelism}", source)


if __name__ == "__main__":
    unittest.main()
