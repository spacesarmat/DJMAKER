"""Кроссплатформенное открытие каталога с выделением аудиофайла."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def reveal_command(path: Path, system: str | None = None) -> list[str]:
    """Возвращает команду для показа файла в системном файловом менеджере."""
    target = path.expanduser()
    if not target.is_absolute():
        target = target.absolute()
    current_system = (system or platform.system()).lower()

    if current_system == "windows":
        return ["explorer.exe", "/select,", str(target)]
    if current_system == "darwin":
        return ["open", "-R", str(target)]
    return ["xdg-open", str(target.parent)]


def reveal_file(path: Path) -> None:
    """Открывает файловый менеджер и, где возможно, выделяет указанный файл."""
    command = reveal_command(path)
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    subprocess.Popen(command, **kwargs)
