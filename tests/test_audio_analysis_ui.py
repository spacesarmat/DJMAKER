"""Регрессия пакетного полного BPM/grid/Key анализа в UI."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TASK_PROGRESS_SOURCE = (
    PROJECT_ROOT / "src" / "djmaker" / "ui" / "task_progress_controls.py"
)


class AudioAnalysisUITests(unittest.TestCase):
    def test_batch_analysis_uses_parallel_worker_queue(self) -> None:
        source = TASK_PROGRESS_SOURCE.read_text(encoding="utf-8")

        self.assertIn("recommended_analysis_concurrency()", source)
        self.assertIn("app.workers.run_many_unordered(", source)
        self.assertIn("max_concurrency=parallelism", source)
        self.assertIn("потоков: {parallelism}", source)

    def test_full_analysis_screen_reports_grid_progress(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("beat_grid_counts()", source)
        self.assertIn("Полный анализ: BPM / сетка / Key / Camelot", source)
        self.assertIn("сетка: {grid_analyzed}/{grid_total}", source)


if __name__ == "__main__":
    unittest.main()
