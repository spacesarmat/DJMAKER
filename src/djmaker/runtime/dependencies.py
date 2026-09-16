"""Проверка и установка внешних аудио-зависимостей DJMAKER.

Модуль специально использует только стандартную библиотеку Python. FFmpeg и
собственная lightweight-сборка Essentia DJMAKER хранятся как управляемые бинарники в
каталоге данных приложения. FFmpeg декодирует аудио, Essentia выполняет DSP-анализ.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


LOGGER = logging.getLogger(__name__)

DOWNLOAD_USER_AGENT = "DJMAKER/0.1 runtime-dependency-manager"
DOWNLOAD_TIMEOUT_SECONDS = 90
COPY_BUFFER_SIZE = 1024 * 1024

FFMPEG_RELEASE_BASE = "https://github.com/binmgr/ffmpeg/releases/latest/download"
FFMPEG_CHECKSUMS_URL = f"{FFMPEG_RELEASE_BASE}/SHA256SUMS.txt"

ESSENTIA_UPSTREAM_SHA = "66a890f285d0e1988155c12d17a2068e406cdd90"
ESSENTIA_RUNTIME_VERSION = "2026.08.27-66a890f2-r6"
ESSENTIA_RELEASE_TAG = f"essentia-runtime-v{ESSENTIA_RUNTIME_VERSION}"
ESSENTIA_RELEASE_BASE = (
    "https://github.com/spacesarmat/DJMAKER/releases/download/"
    f"{ESSENTIA_RELEASE_TAG}"
)
ESSENTIA_CHECKSUMS_URL = f"{ESSENTIA_RELEASE_BASE}/SHA256SUMS.txt"
ESSENTIA_BINARY_BASENAME = "djmaker-essentia"


class RuntimeDependencyError(RuntimeError):
    """Ошибка проверки, загрузки или установки runtime-зависимости."""


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Текущее состояние одной внешней зависимости."""

    name: str
    available: bool
    managed: bool = False
    version: str = ""
    path: str = ""
    backend: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeReport:
    """Снимок состояния внешних аудио-компонентов."""

    ffmpeg: DependencyStatus
    essentia: DependencyStatus


class RuntimeDependencies:
    """Проверяет и при необходимости устанавливает FFmpeg и Essentia."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "runtime"
        self.ffmpeg_dir = self.root / "ffmpeg"
        self.essentia_dir = self.root / "essentia"
        self.python_packages_dir = self.essentia_dir / "python"
        self.manifest_path = self.root / "manifest.json"
        self._lock = threading.Lock()

    def probe(self) -> RuntimeReport:
        """Проверяет зависимости без сети и без изменения системы."""
        return RuntimeReport(
            ffmpeg=self._probe_ffmpeg(),
            essentia=self._probe_essentia(),
        )

    def ensure_all(self) -> RuntimeReport:
        """Проверяет и автоматически устанавливает отсутствующие компоненты."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            ffmpeg = self._ensure_ffmpeg()
            essentia = self._ensure_essentia()
            return RuntimeReport(ffmpeg=ffmpeg, essentia=essentia)

    def ffmpeg_path(self) -> Path | None:
        """Возвращает путь к доступному ffmpeg или ``None``."""
        status = self._probe_ffmpeg()
        if not status.available or not status.path:
            return None
        return Path(status.path)

    def ffprobe_path(self) -> Path | None:
        """Возвращает путь к ffprobe из той же установки, что и ffmpeg."""
        managed = self._managed_ffprobe_path()
        if managed.is_file():
            return managed
        system = shutil.which("ffprobe")
        return Path(system) if system else None

    def essentia_analyzer_path(self) -> Path | None:
        """Возвращает путь к управляемому DSP bridge Essentia DJMAKER."""
        status = self._probe_managed_essentia()
        return Path(status.path) if status.available and status.path else None

    def essentia_executable(self, name: str = ESSENTIA_BINARY_BASENAME) -> Path | None:
        """Возвращает управляемый Essentia CLI; сохранено как совместимый API."""
        if name not in {ESSENTIA_BINARY_BASENAME, _exe_name(ESSENTIA_BINARY_BASENAME)}:
            return None
        return self.essentia_analyzer_path()

    def activate_essentia_python(self) -> bool:
        """Активирует Python Essentia, если пользователь установил её отдельно."""
        if self.python_packages_dir.is_dir():
            value = str(self.python_packages_dir)
            if value not in sys.path:
                sys.path.insert(0, value)
            importlib.invalidate_caches()
        return self._import_essentia_version() is not None

    def _ensure_ffmpeg(self) -> DependencyStatus:
        current = self._probe_ffmpeg()
        if current.available:
            return current

        system = platform.system()
        machine = _normalized_machine(platform.machine())
        asset = _ffmpeg_asset_name(system, machine)
        if asset is None:
            return DependencyStatus(
                name="FFmpeg",
                available=False,
                detail=f"Автоустановка не поддерживается: {system}/{machine}",
            )

        LOGGER.info("FFmpeg не найден; загружается %s", asset)
        self.ffmpeg_dir.mkdir(parents=True, exist_ok=True)

        try:
            checksums = _download_text(FFMPEG_CHECKSUMS_URL)
            expected_hash = _checksum_for_asset(checksums, asset)
            if expected_hash is None:
                raise RuntimeDependencyError(
                    f"В SHA256SUMS.txt отсутствует контрольная сумма для {asset}"
                )

            with tempfile.TemporaryDirectory(dir=self.root) as temp_name:
                temp_dir = Path(temp_name)
                archive = temp_dir / asset
                _download_file(f"{FFMPEG_RELEASE_BASE}/{asset}", archive)
                actual_hash = _sha256(archive)
                if actual_hash.lower() != expected_hash.lower():
                    raise RuntimeDependencyError(
                        "Контрольная сумма FFmpeg не совпала: "
                        f"ожидалась {expected_hash}, получена {actual_hash}"
                    )

                unpacked = temp_dir / "unpacked"
                unpacked.mkdir()
                _extract_archive_safely(archive, unpacked)
                ffmpeg_source = _find_binary(unpacked, _exe_name("ffmpeg"))
                ffprobe_source = _find_binary(unpacked, _exe_name("ffprobe"))
                if ffmpeg_source is None or ffprobe_source is None:
                    raise RuntimeDependencyError(
                        "В загруженном архиве не найдены ffmpeg и ffprobe"
                    )

                bin_dir = self.ffmpeg_dir / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                ffmpeg_target = bin_dir / _exe_name("ffmpeg")
                ffprobe_target = bin_dir / _exe_name("ffprobe")
                _atomic_copy(ffmpeg_source, ffmpeg_target)
                _atomic_copy(ffprobe_source, ffprobe_target)
                _make_executable(ffmpeg_target)
                _make_executable(ffprobe_target)

            status = self._probe_ffmpeg()
            if not status.available:
                raise RuntimeDependencyError(
                    f"FFmpeg загружен, но проверка запуска не пройдена: {status.detail}"
                )
            self._update_manifest("ffmpeg", status)
            return status
        except (OSError, urllib.error.URLError, RuntimeDependencyError) as exc:
            LOGGER.exception("Не удалось установить FFmpeg")
            return DependencyStatus(
                name="FFmpeg",
                available=False,
                detail=f"Ошибка автоустановки: {exc}",
            )

    def _probe_ffmpeg(self) -> DependencyStatus:
        managed_path = self._managed_ffmpeg_path()
        candidates: list[tuple[Path, bool]] = []
        if managed_path.is_file():
            candidates.append((managed_path, True))
        system_path = shutil.which("ffmpeg")
        if system_path:
            path = Path(system_path)
            if path != managed_path:
                candidates.append((path, False))

        errors: list[str] = []
        for path, managed in candidates:
            try:
                first_line = _run_version_command(path, "-version")
            except RuntimeDependencyError as exc:
                errors.append(str(exc))
                continue
            version = _parse_ffmpeg_version(first_line)
            return DependencyStatus(
                name="FFmpeg",
                available=True,
                managed=managed,
                version=version,
                path=str(path),
                backend="managed" if managed else "system",
                detail="Готов к использованию",
            )

        detail = "; ".join(errors) if errors else "Не найден"
        return DependencyStatus(name="FFmpeg", available=False, detail=detail)

    def _ensure_essentia(self) -> DependencyStatus:
        managed = self._probe_managed_essentia()
        if managed.available:
            return managed

        installed = self._install_managed_essentia()
        if installed.available:
            return installed

        # Не блокируем приложение, если пользователь уже имеет совместимый Python API.
        fallback = self._probe_python_essentia()
        if fallback.available:
            return fallback
        return installed

    def _probe_essentia(self) -> DependencyStatus:
        managed = self._probe_managed_essentia()
        if managed.available:
            return managed
        fallback = self._probe_python_essentia()
        if fallback.available:
            return fallback
        return managed

    def _probe_managed_essentia(self) -> DependencyStatus:
        path = self._managed_essentia_path()
        if not path.is_file():
            return DependencyStatus(
                name="Essentia",
                available=False,
                managed=True,
                backend="djmaker-cli",
                detail="Управляемая сборка DJMAKER не установлена",
            )
        try:
            first_line = _run_version_command(path, "--version")
        except RuntimeDependencyError as exc:
            return DependencyStatus(
                name="Essentia",
                available=False,
                managed=True,
                path=str(path),
                backend="djmaker-cli",
                detail=str(exc),
            )
        version = _parse_essentia_version(first_line)
        if version != ESSENTIA_RUNTIME_VERSION:
            return DependencyStatus(
                name="Essentia",
                available=False,
                managed=True,
                version=version,
                path=str(path),
                backend="djmaker-cli",
                detail=(
                    f"Требуется обновление runtime: {version or 'неизвестно'} → "
                    f"{ESSENTIA_RUNTIME_VERSION}"
                ),
            )
        return DependencyStatus(
            name="Essentia",
            available=True,
            managed=True,
            version=version,
            path=str(path),
            backend="djmaker-cli",
            detail=f"Собственная сборка · upstream {ESSENTIA_UPSTREAM_SHA[:8]}",
        )

    def _probe_python_essentia(self) -> DependencyStatus:
        version = self._import_essentia_version()
        if version is None:
            return DependencyStatus(name="Essentia", available=False, detail="Не найдена")
        module = sys.modules.get("essentia")
        module_path = getattr(module, "__file__", "") if module else ""
        managed = False
        if module_path:
            try:
                Path(module_path).resolve().relative_to(self.python_packages_dir.resolve())
                managed = True
            except (OSError, ValueError):
                managed = False
        return DependencyStatus(
            name="Essentia",
            available=True,
            managed=managed,
            version=version,
            path=str(module_path or ""),
            backend="python-fallback",
            detail="Используется внешняя Python Essentia как резервный backend",
        )

    def _install_managed_essentia(self) -> DependencyStatus:
        system = platform.system()
        machine = _normalized_machine(platform.machine())
        asset = _essentia_asset_name(system, machine)
        if asset is None:
            return DependencyStatus(
                name="Essentia",
                available=False,
                managed=True,
                backend="djmaker-cli",
                detail=f"Собственная сборка пока не публикуется для {system}/{machine}",
            )

        LOGGER.info("Essentia DJMAKER не найдена; загружается %s", asset)
        self.essentia_dir.mkdir(parents=True, exist_ok=True)
        try:
            checksums = _download_text(ESSENTIA_CHECKSUMS_URL)
            expected_hash = _checksum_for_asset(checksums, asset)
            if expected_hash is None:
                raise RuntimeDependencyError(
                    f"В SHA256SUMS.txt отсутствует контрольная сумма для {asset}"
                )

            with tempfile.TemporaryDirectory(dir=self.root) as temp_name:
                temp_dir = Path(temp_name)
                archive = temp_dir / asset
                _download_file(f"{ESSENTIA_RELEASE_BASE}/{asset}", archive)
                actual_hash = _sha256(archive)
                if actual_hash.lower() != expected_hash.lower():
                    raise RuntimeDependencyError(
                        "Контрольная сумма Essentia не совпала: "
                        f"ожидалась {expected_hash}, получена {actual_hash}"
                    )

                unpacked = temp_dir / "unpacked"
                unpacked.mkdir()
                _extract_archive_safely(archive, unpacked)
                binary = _find_binary(
                    unpacked,
                    _exe_name(ESSENTIA_BINARY_BASENAME),
                )
                if binary is None:
                    raise RuntimeDependencyError(
                        "В runtime-архиве не найден djmaker-essentia"
                    )

                target = self._managed_essentia_path()
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_copy(binary, target)
                _make_executable(target)

            _run_essentia_self_test(self._managed_essentia_path())
            status = self._probe_managed_essentia()
            if not status.available:
                raise RuntimeDependencyError(
                    f"Essentia установлена, но проверка запуска не пройдена: {status.detail}"
                )
            self._update_manifest(
                "essentia",
                status,
                extra={
                    "asset": asset,
                    "archive_sha256": expected_hash,
                    "upstream_sha": ESSENTIA_UPSTREAM_SHA,
                    "runtime_version": ESSENTIA_RUNTIME_VERSION,
                },
            )
            return status
        except (OSError, urllib.error.URLError, RuntimeDependencyError) as exc:
            LOGGER.exception("Не удалось установить собственную Essentia")
            return DependencyStatus(
                name="Essentia",
                available=False,
                managed=True,
                backend="djmaker-cli",
                detail=f"Ошибка автоустановки: {exc}",
            )

    def _load_manifest(self) -> dict[str, object]:
        if not self.manifest_path.is_file():
            return {}
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _import_essentia_version(self) -> str | None:
        if self.python_packages_dir.is_dir():
            value = str(self.python_packages_dir)
            if value not in sys.path:
                sys.path.insert(0, value)
                importlib.invalidate_caches()
        try:
            module = importlib.import_module("essentia")
        except (ImportError, OSError):
            return None
        return str(getattr(module, "__version__", "unknown"))

    def _managed_essentia_path(self) -> Path:
        return self.essentia_dir / "bin" / _exe_name(ESSENTIA_BINARY_BASENAME)

    def _managed_ffmpeg_path(self) -> Path:
        return self.ffmpeg_dir / "bin" / _exe_name("ffmpeg")

    def _managed_ffprobe_path(self) -> Path:
        return self.ffmpeg_dir / "bin" / _exe_name("ffprobe")

    def _update_manifest(
        self,
        key: str,
        status: DependencyStatus,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        manifest = self._load_manifest()

        entry: dict[str, object] = asdict(status)
        if extra:
            entry.update(extra)
        manifest[key] = entry
        temporary = self.manifest_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.manifest_path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    LOGGER.warning("Не удалось удалить %s", temporary)


def _normalized_machine(machine: str) -> str:
    value = machine.strip().lower()
    if value in {"amd64", "x86_64", "x64"}:
        return "amd64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value


def _ffmpeg_asset_name(system: str, machine: str) -> str | None:
    """Возвращает имя архива FFmpeg для платформы binmgr."""
    arch = _normalized_machine(machine)
    if system == "Windows" and arch in {"amd64", "arm64"}:
        return f"ffmpeg-windows-{arch}.zip"
    if system == "Darwin" and arch in {"amd64", "arm64"}:
        return f"ffmpeg-darwin-{arch}.tar.gz"
    return None


def _essentia_asset_name(system: str, machine: str) -> str | None:
    """Возвращает имя собственного runtime-архива Essentia DJMAKER."""
    arch = _normalized_machine(machine)
    if system == "Windows" and arch == "amd64":
        return "djmaker-essentia-windows-amd64.zip"
    if system == "Darwin" and arch in {"amd64", "arm64"}:
        return f"djmaker-essentia-darwin-{arch}.tar.gz"
    return None


def _exe_name(base: str) -> str:
    return f"{base}.exe" if platform.system() == "Windows" else base


def _download_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        data = response.read(4 * 1024 * 1024)
    return data.decode("utf-8")


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=COPY_BUFFER_SIZE)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                LOGGER.warning("Не удалось удалить незавершённую загрузку %s", temporary)
        raise


def _checksum_for_asset(checksums: str, asset_name: str) -> str | None:
    for raw_line in checksums.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if match and Path(match.group(2)).name == asset_name:
            return match.group(1)
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(COPY_BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive_safely(archive: Path, destination: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            _validate_archive_members(destination, (info.filename for info in handle.infolist()))
            handle.extractall(destination)
        return
    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, mode="r:gz") as handle:
            _validate_archive_members(destination, (member.name for member in handle.getmembers()))
            handle.extractall(destination, filter="data")
        return
    raise RuntimeDependencyError(f"Неизвестный формат архива: {archive.name}")


def _validate_archive_members(destination: Path, names: Iterable[str]) -> None:
    root = destination.resolve()
    for name in names:
        target = (destination / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeDependencyError(
                f"Архив содержит небезопасный путь: {name}"
            ) from exc


def _find_binary(root: Path, filename: str) -> Path | None:
    for candidate in root.rglob(filename):
        if candidate.is_file():
            return candidate
    return None


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                LOGGER.warning("Не удалось удалить временный файл %s", temporary)


def _make_executable(path: Path) -> None:
    if platform.system() != "Windows":
        path.chmod(path.stat().st_mode | 0o755)


def _run_version_command(executable: Path, argument: str) -> str:
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(executable), argument],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeDependencyError(f"Не удалось запустить {executable}: {exc}") from exc
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        raise RuntimeDependencyError(f"{executable} не вернул информацию о версии")
    return output.splitlines()[0]


def _run_essentia_self_test(executable: Path) -> None:
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(executable), "--self-test"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeDependencyError(
            f"Не удалось выполнить self-test {executable}: {exc}"
        ) from exc
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0 or '"ok":true' not in output.replace(" ", "").lower():
        raise RuntimeDependencyError(
            f"Essentia self-test не пройден: {_tail(output or 'нет вывода', 300)}"
        )


def _parse_essentia_version(first_line: str) -> str:
    match = re.search(r"djmaker-essentia\s+([^\s]+)", first_line, flags=re.IGNORECASE)
    return match.group(1) if match else first_line[:80]


def _parse_ffmpeg_version(first_line: str) -> str:
    match = re.search(r"ffmpeg version\s+([^\s]+)", first_line, flags=re.IGNORECASE)
    return match.group(1) if match else first_line[:80]


def _tail(value: str, length: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= length else "…" + compact[-length:]
