"""Регрессия упаковки: сборка Flet не должна портить пакеты, нужные librosa.

Энергия трека считается через librosa. В упакованном приложении она работает
только если serious_python не трогает site-packages:

* ``compile.packages`` — numba (зависимость librosa) использует
  ``@jit(cache=True)`` и требует реальный ``.py`` рядом с модулем; по умолчанию
  исходники удаляются после компиляции в ``.pyc``.
* ``cleanup.packages`` — чистка удаляет все ``*.pyi``, а librosa читает
  ``__init__.pyi`` при импорте (``lazy_loader.attach_stub``).

Обе ошибки проявляются только в релизной сборке (из исходников всё работает),
поэтому настройки охраняются тестом.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class FletPackagingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        self.flet = config["tool"]["flet"]

    def test_site_packages_are_not_compiled_to_pyc_only(self) -> None:
        self.assertIs(
            False,
            self.flet.get("compile", {}).get("packages"),
            "tool.flet.compile.packages должен быть false: numba/librosa требуют .py",
        )

    def test_site_packages_stubs_are_not_stripped(self) -> None:
        self.assertIs(
            False,
            self.flet.get("cleanup", {}).get("packages"),
            "tool.flet.cleanup.packages должен быть false: librosa читает __init__.pyi",
        )

    def test_no_package_cleanup_globs_reenable_cleanup(self) -> None:
        # flet включает чистку обратно, если задан список cleanup.package_files.
        self.assertNotIn("package_files", self.flet.get("cleanup", {}))


if __name__ == "__main__":
    unittest.main()
