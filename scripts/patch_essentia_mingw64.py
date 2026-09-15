"""Точечный патч upstream Essentia для 64-битной MinGW cross-сборки.

Upstream-флаг ``--cross-compile-mingw32`` всё ещё жёстко использует i686 toolchain.
DJMAKER не форкает Essentia: CI применяет минимальную проверяемую замену к
зафиксированному upstream commit перед сборкой.
"""

from __future__ import annotations

import argparse
from pathlib import Path


I686 = "i686-w64-mingw32"
X64 = "x86_64-w64-mingw32"
RESET_FLAGS = "ctx.env.CXXFLAGS = ['-static-libgcc', '-static-libstdc++']"
APPEND_FLAGS = "ctx.env.CXXFLAGS += ['-static-libgcc', '-static-libstdc++', '-D_USE_MATH_DEFINES']"


def patch_wscript(path: Path) -> None:
    """Патчит MinGW x64 toolchain и включает математические константы CRT."""
    source = path.read_text(encoding="utf-8")
    compiler_occurrences = source.count(I686)
    reset_occurrences = source.count(RESET_FLAGS)
    if compiler_occurrences != 3:
        raise RuntimeError(
            f"Ожидалось 3 упоминания {I686}, найдено {compiler_occurrences}; "
            "upstream изменился, требуется ревизия патча"
        )
    if reset_occurrences != 2:
        raise RuntimeError(
            "Ожидалось 2 текстовых вхождения присваивания CXXFLAGS "
            "(одно в историческом комментарии и одно активное); "
            f"найдено {reset_occurrences}. Upstream изменился, требуется ревизия патча"
        )

    patched = source.replace(I686, X64)
    active_offset = patched.rfind(RESET_FLAGS)
    if active_offset < 0:
        raise RuntimeError("Не найден активный MinGW CXXFLAGS block")
    patched = (
        patched[:active_offset]
        + APPEND_FLAGS
        + patched[active_offset + len(RESET_FLAGS):]
    )
    path.write_text(patched, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wscript", type=Path)
    args = parser.parse_args()
    patch_wscript(args.wscript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
