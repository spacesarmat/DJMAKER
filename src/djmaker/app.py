"""Композиция зависимостей и точка запуска Flet-приложения."""

from __future__ import annotations

import flet as ft

from djmaker.config import get_app_paths
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.logging_config import configure_logging
from djmaker.plugins.registry import PluginRegistry
from djmaker.runtime.dependencies import RuntimeDependencies
from djmaker.services.audio_analysis import EssentiaAudioAnalyzer
from djmaker.services.artwork import ArtworkCache
from djmaker.services.audio_tags import AudioTagService
from djmaker.services.library import LibraryService
from djmaker.services.organizer import FileOrganizer
from djmaker.services.scanner import LibraryScanner
from djmaker.services.workers import BackgroundWorkers
from djmaker.settings import SettingsStore
from djmaker.ui.app import DJMakerUI


async def flet_main(page: ft.Page) -> None:
    """Создаёт зависимости DJMAKER и запускает главное окно."""
    paths = get_app_paths()
    configure_logging(paths.log_file)

    database = LibraryDatabase(paths.database)
    database.initialize()
    tags = AudioTagService()
    artwork_cache = ArtworkCache(paths.artwork_dir)
    scanner = LibraryScanner(database, tags, artwork_cache)
    organizer = FileOrganizer()
    plugins = PluginRegistry()
    runtime = RuntimeDependencies(paths.data_dir)
    analyzer = EssentiaAudioAnalyzer(runtime)
    service = LibraryService(
        database,
        scanner,
        tags,
        organizer,
        plugins,
        analyzer,
        artwork_cache,
    )
    workers = BackgroundWorkers(max_workers=4)
    settings_store = SettingsStore(paths.settings_file)
    settings = settings_store.load()

    ui = DJMakerUI(
        page,
        service,
        workers,
        paths,
        settings_store,
        settings,
        runtime,
    )
    ui.build()
    page.run_task(ui.ensure_runtime_dependencies)
    page.run_task(ui.ensure_embedded_artwork)

    def on_disconnect(_: object) -> None:
        workers.close()

    page.on_disconnect = on_disconnect
