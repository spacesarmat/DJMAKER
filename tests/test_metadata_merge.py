"""Регрессия объединения кандидатов метаданных из нескольких провайдеров."""

from __future__ import annotations

import unittest

from djmaker.domain.models import MetadataCandidate
from djmaker.plugins.merge import merge_candidates


def _candidate(provider_id: str, title: str, artist: str, **kwargs: object) -> MetadataCandidate:
    return MetadataCandidate(
        provider_id=provider_id, external_id="x", title=title, artist=artist, **kwargs
    )


class MergeCandidatesTests(unittest.TestCase):
    def test_matching_candidates_merge_fields_first_non_empty_wins(self) -> None:
        spotify = [
            _candidate(
                "spotify",
                "Song",
                "Artist",
                album="Spotify Album",
                year="2021",
                artwork_url="http://art",
            )
        ]
        musicbrainz = [_candidate("musicbrainz", "Song", "Artist", genre="house")]

        merged = merge_candidates([spotify, musicbrainz])

        self.assertEqual(len(merged), 1)
        result = merged[0]
        self.assertEqual(result.provider_id, "spotify+musicbrainz")
        self.assertEqual(result.album, "Spotify Album")
        self.assertEqual(result.genre, "house")
        self.assertEqual(result.artwork_url, "http://art")

    def test_case_and_whitespace_insensitive_matching(self) -> None:
        spotify = [_candidate("spotify", "  Song Title ", "Artist")]
        musicbrainz = [_candidate("musicbrainz", "song title", "ARTIST", genre="pop")]

        merged = merge_candidates([spotify, musicbrainz])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].genre, "pop")

    def test_non_matching_candidates_stay_separate(self) -> None:
        spotify = [_candidate("spotify", "Song A", "Artist A")]
        musicbrainz = [_candidate("musicbrainz", "Song B", "Artist B")]

        merged = merge_candidates([spotify, musicbrainz])

        self.assertEqual(len(merged), 2)
        self.assertEqual({c.provider_id for c in merged}, {"spotify", "musicbrainz"})

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(merge_candidates([]), [])
        self.assertEqual(merge_candidates([[], []]), [])

    def test_provider_order_determines_priority_on_conflict(self) -> None:
        spotify = [_candidate("spotify", "Song", "Artist", album="Spotify Album")]
        musicbrainz = [_candidate("musicbrainz", "Song", "Artist", album="MB Album")]

        spotify_first = merge_candidates([spotify, musicbrainz])
        musicbrainz_first = merge_candidates([musicbrainz, spotify])

        self.assertEqual(spotify_first[0].album, "Spotify Album")
        self.assertEqual(musicbrainz_first[0].album, "MB Album")


if __name__ == "__main__":
    unittest.main()
