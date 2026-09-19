from __future__ import annotations

import unittest

from djmaker.domain.energy_color import (
    BUCKET_BLACK,
    BUCKET_BLUE,
    BUCKET_GREEN,
    BUCKET_ORANGE,
    BUCKET_PURPLE,
    BUCKET_RED,
    BUCKET_UNKNOWN,
    BUCKET_YELLOW,
    GENRE_TAG_AMBIENT,
    GENRE_TAG_HEAVY_DARK,
    energy_color_bucket,
)


class GenreTagPriorityTests(unittest.TestCase):
    def test_heavy_dark_genre_tag_wins_regardless_of_energy(self) -> None:
        self.assertEqual(
            BUCKET_BLACK,
            energy_color_bucket(5.0, "major", 0.0, genre_tag=GENRE_TAG_HEAVY_DARK),
        )
        self.assertEqual(
            BUCKET_BLACK,
            energy_color_bucket(95.0, "minor", 0.9, genre_tag=GENRE_TAG_HEAVY_DARK),
        )

    def test_ambient_genre_tag_wins_regardless_of_energy(self) -> None:
        self.assertEqual(
            BUCKET_PURPLE,
            energy_color_bucket(90.0, "major", 0.1, genre_tag=GENRE_TAG_AMBIENT),
        )

    def test_unknown_genre_tag_falls_back_to_dsp_heuristic(self) -> None:
        result = energy_color_bucket(90.0, "major", 0.1, genre_tag="something_else")
        self.assertNotIn(result, {BUCKET_BLACK, BUCKET_PURPLE})


class DspFallbackTests(unittest.TestCase):
    def test_missing_energy_is_unknown(self) -> None:
        self.assertEqual(BUCKET_UNKNOWN, energy_color_bucket(None, "major", None))

    def test_very_low_energy_is_purple(self) -> None:
        self.assertEqual(BUCKET_PURPLE, energy_color_bucket(5.0, "minor", 0.1))

    def test_low_energy_is_blue(self) -> None:
        self.assertEqual(BUCKET_BLUE, energy_color_bucket(20.0, "major", 0.1))

    def test_medium_energy_is_green(self) -> None:
        self.assertEqual(BUCKET_GREEN, energy_color_bucket(45.0, "major", 0.1))

    def test_medium_high_energy_is_orange(self) -> None:
        self.assertEqual(BUCKET_ORANGE, energy_color_bucket(65.0, "major", 0.1))

    def test_high_energy_major_low_noise_is_yellow(self) -> None:
        self.assertEqual(BUCKET_YELLOW, energy_color_bucket(90.0, "major", 0.1))

    def test_high_energy_minor_is_red(self) -> None:
        self.assertEqual(BUCKET_RED, energy_color_bucket(90.0, "minor", 0.1))

    def test_high_energy_major_but_noisy_is_red(self) -> None:
        self.assertEqual(BUCKET_RED, energy_color_bucket(90.0, "major", 0.8))

    def test_missing_noisiness_defaults_to_clean(self) -> None:
        self.assertEqual(BUCKET_YELLOW, energy_color_bucket(90.0, "major", None))

    def test_scale_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(BUCKET_RED, energy_color_bucket(90.0, " MINOR ", 0.1))

    def test_boundaries_are_exclusive_on_the_lower_edge(self) -> None:
        # На стыке порогов значение относится к ВЕРХНЕЙ категории.
        self.assertEqual(BUCKET_BLUE, energy_color_bucket(10.0, "major", 0.0))
        self.assertEqual(BUCKET_GREEN, energy_color_bucket(30.0, "major", 0.0))
        self.assertEqual(BUCKET_ORANGE, energy_color_bucket(55.0, "major", 0.0))
        self.assertEqual(BUCKET_YELLOW, energy_color_bucket(80.0, "major", 0.0))


if __name__ == "__main__":
    unittest.main()
