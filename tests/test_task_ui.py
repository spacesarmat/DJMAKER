from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
APP_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "app.py"
ANALYZER_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "services" / "audio_analysis.py"


class TaskUITests(unittest.TestCase):
    def test_navigation_has_task_manager_and_controls(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('label="Задачи"', source)
        self.assertIn("ft.Icons.PAUSE_CIRCLE_OUTLINE", source)
        self.assertIn("ft.Icons.PLAY_ARROW", source)
        self.assertIn("ft.Icons.CANCEL_OUTLINED", source)
        self.assertIn("def _stop_task", source)
        self.assertIn("def _resume_task", source)
        self.assertIn("def _cancel_task", source)

    def test_long_operations_are_registered_as_managed_tasks(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("TaskKind.AUDIO_ANALYSIS", source)
        self.assertIn("TaskKind.LIBRARY_SCAN", source)
        self.assertIn("TaskKind.ARTWORK_INDEX", source)
        self.assertIn("task=task", source)

    def test_task_monitor_is_started_with_application(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        self.assertIn("page.run_task(ui.monitor_tasks)", source)

    def test_audio_analyzer_uses_interruptible_popen_pair(self) -> None:
        source = ANALYZER_SOURCE.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("subprocess.Popen("), 2)
        self.assertIn("task.checkpoint()", source)
        self.assertIn("_terminate_process(analyzer)", source)
        self.assertIn("_terminate_process(decoder)", source)


if __name__ == "__main__":
    unittest.main()
