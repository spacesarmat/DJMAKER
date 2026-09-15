"""Создаёт минимальный pkg-config файл для закреплённой header-only Eigen."""

from __future__ import annotations

import argparse
from pathlib import Path


def write_eigen_pkgconfig(eigen_root: Path, output: Path, version: str) -> None:
    """Проверяет Eigen и записывает ``eigen3.pc`` с абсолютным include-путём."""
    root = eigen_root.expanduser().resolve()
    required = (
        root / "Eigen" / "Core",
        root / "unsupported" / "Eigen" / "CXX11" / "Tensor",
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        rendered = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"В закреплённой Eigen отсутствуют заголовки: {rendered}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(
            (
                f"prefix={root.as_posix()}",
                "includedir=${prefix}",
                "",
                "Name: Eigen3",
                "Description: DJMAKER pinned Eigen headers",
                f"Version: {version}",
                "Cflags: -I${includedir}",
                "",
            )
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eigen-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    write_eigen_pkgconfig(args.eigen_root, args.output, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
