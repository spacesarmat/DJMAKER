"""Source-level regression нативного desktop Drag&Drop."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
TASK_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "services" / "tasks.py"


class DragDropUITests(unittest.TestCase):
    def test_extension_is_pinned_and_guarded_for_built_desktop_runtime(self) -> None:
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('"flet-dropzone==0.4.0"', pyproject)
        self.assertIn('"flet-cli==0.86.5"', pyproject)
        self.assertIn('[tool.flet.flutter.pubspec.dependency_overrides]', pyproject)
        self.assertIn('flet = "0.86.5"', pyproject)
        self.assertIn('os.getenv("FLET_DART_BRIDGE_PORT")', source)
        self.assertIn("ftd.Dropzone(", source)
        self.assertIn("on_dropped=self._on_paths_dropped", source)

    def test_drop_import_uses_managed_task_pipeline(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        tasks = TASK_SOURCE.read_text(encoding="utf-8")

        self.assertIn('LIBRARY_IMPORT = "library_import"', tasks)
        self.assertIn("async def _run_drop_import", source)
        self.assertIn("self.service.import_paths", source)
        self.assertIn("TaskKind.LIBRARY_IMPORT", source)
        self.assertIn("stats.ignored", source)
        self.assertIn("self.page.run_task(self.ensure_waveforms)", source)

    def test_drop_overlay_explains_filtering(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("Отпустите файлы или папки", source)
        self.assertIn("остальные файлы пропущены", source)
        self.assertIn("def _on_drop_entered", source)
        self.assertIn("def _on_drop_exited", source)


if __name__ == "__main__":
    unittest.main()
