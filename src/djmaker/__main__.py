"""CLI entry point: python -m djmaker."""

from __future__ import annotations

import flet as ft

from djmaker.app import flet_main


def main() -> None:
    """Запускает desktop-приложение DJMAKER."""
    ft.run(flet_main)


if __name__ == "__main__":
    main()
