"""Регрессия упаковки: сборка Flet не должна удалять исходники пакетов.

librosa -> numba использует ``@jit(cache=True)``, которому нужен реальный ``.py``
рядом с модулем. serious_python по умолчанию компилирует site-packages в ``.pyc``
и удаляет ``.py`` — тогда в упакованном приложении librosa падает на первом
вызове, а BPM/Key/энергия не считаются. Ошибка проявляется только в релизной
сборке, поэтому охраняем настройку тестом.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class FletPackagingConfigTests(unittest.TestCase):
    def test_site_packages_are_not_compiled_to_pyc_only(self) -> None:
        config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        compile_settings = config["tool"]["flet"].get("compile", {})

        self.assertIs(
            False,
            compile_settings.get("packages"),
            "tool.flet.compile.packages должен быть false: numba/librosa требуют .py",
        )


if __name__ == "__main__":
    unittest.main()
