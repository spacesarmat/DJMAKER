"""Регрессия реального триггера загрузки AST-модели из UI.

Раньше classify() только проверял наличие модели и никогда её не скачивал
(см. фикс в genre_inference.py/library.py) — здесь проверяется, что UI-слой
(тумблер в Настройках, кнопка «Проверить/установить») тоже даёт пользователю
рабочий способ инициировать загрузку, а не полагается только на побочный
эффект внутри фонового анализа трека.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft

from djmaker.runtime.dependencies import DependencyStatus, RuntimeReport
from djmaker.services.workers import BackgroundWorkers
from djmaker.settings import AppSettings
from djmaker.ui.theme_settings_controls import ThemeSettingsController


def _report(ast_available: bool) -> RuntimeReport:
    ready = DependencyStatus(name="FFmpeg", available=True, version="7")
    return RuntimeReport(
        ffmpeg=ready,
        essentia=DependencyStatus(name="Essentia", available=True, version="1"),
        ast=DependencyStatus(name="AST", available=ast_available, managed=True),
    )


class EnsureAstModelDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(energy_genre_refinement_enabled=True)
        self.app.runtime_report = _report(ast_available=False)
        self.app.runtime = Mock()
        self.app.workers = BackgroundWorkers(max_workers=2)
        self.addCleanup(self.app.workers.close)
        self.app._set_status = Mock()
        self.app._notify = Mock()
        self.app.navigation = SimpleNamespace(selected_index=6)
        self.app.show_settings = Mock()
        self.controller = ThemeSettingsController(self.app)

    async def test_skips_download_when_already_available(self) -> None:
        self.app.runtime_report = _report(ast_available=True)
        self.app.runtime.ensure_ast_model = Mock()

        await self.controller.ensure_ast_model_download()

        self.app.runtime.ensure_ast_model.assert_not_called()

    async def test_successful_download_updates_report_and_notifies(self) -> None:
        new_status = DependencyStatus(name="AST", available=True, managed=True, path="/m.onnx")
        self.app.runtime.ensure_ast_model = Mock(return_value=new_status)

        await self.controller.ensure_ast_model_download()

        self.assertTrue(self.app.runtime_report.ast.available)
        self.app._notify.assert_called_once_with("AST-модель жанрового уточнения готова")
        self.app.show_settings.assert_called_once()

    async def test_failed_download_notifies_detail_without_crashing(self) -> None:
        failed_status = DependencyStatus(
            name="AST", available=False, managed=True, detail="нет сети"
        )
        self.app.runtime.ensure_ast_model = Mock(return_value=failed_status)

        await self.controller.ensure_ast_model_download()

        self.assertFalse(self.app.runtime_report.ast.available)
        self.app._notify.assert_called_once_with("AST-модель недоступна: нет сети")

    async def test_unexpected_exception_is_caught_and_reported(self) -> None:
        self.app.runtime.ensure_ast_model = Mock(side_effect=RuntimeError("boom"))

        await self.controller.ensure_ast_model_download()

        self.app._notify.assert_called_once()
        self.assertIn("Не удалось загрузить", self.app._notify.call_args.args[0])


class GenreRefinementToggleTriggersDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(energy_genre_refinement_enabled=False)
        self.app.settings_store = Mock()
        self.app.page = Mock()
        self.app.theme_button = ft.IconButton()
        self.app.status = ft.Text()
        self.app.show_settings = Mock()
        self.app.show_plugins = Mock()
        self.app._notify = Mock()
        self.app._surface_card = lambda content, **kwargs: content
        self.app.navigation = SimpleNamespace(selected_index=6)
        self.app.runtime_report = _report(ast_available=False)
        self.app._theme_editor_active = True
        self.app._theme_editor_fields = {"x": 1}
        self.app._theme_editor_swatches = {"x": 1}
        self.app._theme_editor_status = "editing"
        self.app.ensure_ast_model_download = Mock()
        self.controller = ThemeSettingsController(self.app)

    def test_checking_the_box_schedules_download_when_model_missing(self) -> None:
        event = SimpleNamespace(control=SimpleNamespace(value=True))

        self.controller.on_genre_refinement_toggled(event)

        self.app.page.run_task.assert_called_once_with(self.app.ensure_ast_model_download)

    def test_checking_the_box_skips_download_when_model_already_present(self) -> None:
        self.app.runtime_report = _report(ast_available=True)
        event = SimpleNamespace(control=SimpleNamespace(value=True))

        self.controller.on_genre_refinement_toggled(event)

        self.app.page.run_task.assert_not_called()

    def test_unchecking_the_box_never_schedules_download(self) -> None:
        self.app.settings = AppSettings(energy_genre_refinement_enabled=True)
        event = SimpleNamespace(control=SimpleNamespace(value=False))

        self.controller.on_genre_refinement_toggled(event)

        self.app.page.run_task.assert_not_called()


class RetryButtonAlsoFetchesAstModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_runtime_dependencies_also_downloads_ast_when_enabled(self) -> None:
        app = SimpleNamespace()
        app.settings = AppSettings(energy_genre_refinement_enabled=True)
        app._set_status = Mock()
        app._notify = Mock()
        app.navigation = SimpleNamespace(selected_index=6)
        app.show_settings = Mock()
        app.runtime = Mock()
        app.runtime.ensure_all = Mock(
            return_value=_report(ast_available=False)
        )
        app.workers = BackgroundWorkers(max_workers=2)
        self.addCleanup(app.workers.close)
        controller = ThemeSettingsController(app)
        controller.ensure_ast_model_download = AsyncMock()

        await controller.ensure_runtime_dependencies()

        controller.ensure_ast_model_download.assert_awaited_once()

    async def test_retry_runtime_dependencies_skips_ast_when_disabled(self) -> None:
        app = SimpleNamespace()
        app.settings = AppSettings(energy_genre_refinement_enabled=False)
        app._set_status = Mock()
        app._notify = Mock()
        app.navigation = SimpleNamespace(selected_index=6)
        app.show_settings = Mock()
        app.runtime = Mock()
        app.runtime.ensure_all = Mock(return_value=_report(ast_available=False))
        app.workers = BackgroundWorkers(max_workers=2)
        self.addCleanup(app.workers.close)
        controller = ThemeSettingsController(app)
        controller.ensure_ast_model_download = AsyncMock()

        await controller.ensure_runtime_dependencies()

        controller.ensure_ast_model_download.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
