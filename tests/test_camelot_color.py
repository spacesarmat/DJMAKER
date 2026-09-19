from __future__ import annotations

import unittest

from djmaker.domain.camelot_color import camelot_to_color


class CamelotToColorTests(unittest.TestCase):
    def test_unknown_camelot_code_returns_none(self) -> None:
        self.assertIsNone(camelot_to_color(""))
        self.assertIsNone(camelot_to_color("garbage"))

    def test_valid_code_returns_hex_color(self) -> None:
        color = camelot_to_color("8A")
        self.assertIsNotNone(color)
        assert color is not None
        self.assertRegex(color, r"^#[0-9A-F]{6}$")

    def test_same_number_minor_and_major_share_hue_but_differ_in_shade(self) -> None:
        minor = camelot_to_color("5A")
        major = camelot_to_color("5B")
        self.assertIsNotNone(minor)
        self.assertIsNotNone(major)
        self.assertNotEqual(minor, major)

    def test_different_numbers_produce_different_colors(self) -> None:
        colors = {camelot_to_color(f"{n}A") for n in range(1, 13)}
        self.assertEqual(12, len(colors))

    def test_accepts_musical_key_and_scale_notation_too(self) -> None:
        # camelot_order() уже понимает "A minor" и т.п. — функция должна тоже.
        self.assertEqual(camelot_to_color("8A"), camelot_to_color("A minor"))

    def test_wheel_wraps_consistently_at_12_to_1(self) -> None:
        color_12 = camelot_to_color("12A")
        color_1 = camelot_to_color("1A")
        self.assertIsNotNone(color_12)
        self.assertIsNotNone(color_1)
        self.assertNotEqual(color_12, color_1)


if __name__ == "__main__":
    unittest.main()
