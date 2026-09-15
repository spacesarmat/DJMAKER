"""Source-level regression безопасного обнуления SQLite из настроек."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
SERVICE_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "services" / "library.py"


class DatabaseResetUITests(unittest.TestCase):
    def test_settings_contains_guarded_database_reset(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('content="Обнулить БД"', source)
        self.assertIn("def _open_database_reset_dialog", source)
        self.assertIn("self.tasks.active_count() > 0", source)
        self.assertIn("await self.workers.run(self.service.reset_library)", source)
        self.assertIn("def _clear_library_runtime_state", source)
        self.assertIn("Музыкальные файлы не изменялись", source)

    def test_service_exposes_database_reset(self) -> None:
        source = SERVICE_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def reset_library(self) -> None", source)
        self.assertIn("self.database.reset()", source)


if __name__ == "__main__":
    unittest.main()
