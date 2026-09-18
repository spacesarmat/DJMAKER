"""Регрессия объединения кандидатов метаданных из нескольких провайдеров."""

from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.domain.models import (
    AudioMetadata,
    AudioTechnicalInfo,
    MetadataCandidate,
    TrackRecord,
)
from djmaker.plugins.merge import match_confidence, merge_candidates


def _candidate(provider_id: str, title: str, artist: str, **kwargs: object) -> MetadataCandidate:
    return MetadataCandidate(
        provider_id=provider_id, external_id="x", title=title, artist=artist, **kwargs
    )


def _track(title: str = "Song", artist: str = "Artist") -> TrackRecord:
    return TrackRecord(
        id=1,
        path=Path("/music/track.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash="hash",
        metadata=AudioMetadata(title=title, artist=artist),
        technical=AudioTechnicalInfo(),
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

    def test_bpm_and_musical_key_are_filled_from_other_provider(self) -> None:
        beatport = [
            _candidate("beatport", "Song", "Artist", bpm=123.0, musical_key="D Major")
        ]
        spotify = [_candidate("spotify", "Song", "Artist", album="Spotify Album")]

        merged = merge_candidates([spotify, beatport])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].album, "Spotify Album")
        self.assertEqual(merged[0].bpm, 123.0)
        self.assertEqual(merged[0].musical_key, "D Major")

    def test_zero_bpm_is_treated_as_missing(self) -> None:
        a = [_candidate("a", "Song", "Artist", bpm=0.0)]
        b = [_candidate("b", "Song", "Artist", bpm=140.0)]

        merged = merge_candidates([a, b])

        self.assertEqual(merged[0].bpm, 140.0)


class MatchConfidenceTests(unittest.TestCase):
    def test_exact_title_and_artist_match_scores_high(self) -> None:
        track = _track(title="Shatter and Spin", artist="DataFunk")
        candidate = _candidate("spotify", "Shatter and Spin", "DataFunk")

        self.assertAlmostEqual(1.0, match_confidence(track, candidate))

    def test_case_and_whitespace_do_not_affect_score(self) -> None:
        track = _track(title="  Shatter AND Spin ", artist="datafunk")
        candidate = _candidate("spotify", "shatter and spin", "DataFunk")

        self.assertAlmostEqual(1.0, match_confidence(track, candidate))

    def test_unrelated_candidate_scores_low(self) -> None:
        track = _track(title="Shatter and Spin", artist="DataFunk")
        candidate = _candidate("spotify", "Totally Different Track", "Someone Else")

        self.assertLess(match_confidence(track, candidate), 0.5)

    def test_missing_local_artist_compares_title_only(self) -> None:
        track = _track(title="Shatter and Spin", artist="")
        exact_title = _candidate("spotify", "Shatter and Spin", "Anyone At All")

        self.assertAlmostEqual(1.0, match_confidence(track, exact_title))

    def test_missing_local_title_falls_back_to_filename_stem(self) -> None:
        track = TrackRecord(
            id=1,
            path=Path("/music/Shatter and Spin.mp3"),
            root_path=Path("/music"),
            size=1,
            mtime_ns=0,
            extension=".mp3",
            file_hash="hash",
            metadata=AudioMetadata(title="", artist="DataFunk"),
            technical=AudioTechnicalInfo(),
        )
        candidate = _candidate("spotify", "Shatter and Spin", "DataFunk")

        self.assertAlmostEqual(1.0, match_confidence(track, candidate))


if __name__ == "__main__":
    unittest.main()
