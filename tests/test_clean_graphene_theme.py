"""Регрессия фирменной палитры «Чистый графен»."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "theme.py"


class CleanGrapheneThemeTests(unittest.TestCase):
    def test_dark_palette_uses_requested_carbon_material_colors(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")

        for color in (
            'surface="#0F0F11"',
            'surface_container="#242529"',
            'surface_container_highest="#383A40"',
            'secondary="#8A8D91"',
            'tertiary="#D1D5DB"',
            'primary="#00F0FF"',
        ):
            self.assertIn(color, source)

    def test_clean_graphene_has_only_laser_cyan_neon_marker(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")
        start = source.index('"clean_graphene": (')
        end = source.index('    "strict": (', start)
        graphene = source[start:end]

        self.assertIn("#00F0FF", graphene)
        self.assertNotIn("#39FF14", graphene)
        self.assertNotIn("#FF9F00", graphene)

    def test_palette_description_mentions_single_cyan_accent(self) -> None:
        source = THEME_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            "Углеродная база, металлические структурные тона и один "
            "лазерно-циановый акцент.",
            source,
        )


if __name__ == "__main__":
    unittest.main()
