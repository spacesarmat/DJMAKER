"""Регрессия жанров MusicBrainz (топ-тег по count через inc=tags)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.providers.musicbrainz import MusicBrainzProvider


def _track(title: str = "Song", artist: str = "Artist") -> TrackRecord:
    return TrackRecord(
        id=1,
        path=Path("/music/test.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash="hash",
        metadata=AudioMetadata(title=title, artist=artist),
        technical=AudioTechnicalInfo(),
    )


def _json_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class MusicBrainzGenreTests(unittest.TestCase):
    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_top_tag_by_count_becomes_genre(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _json_response(
            {
                "recordings": [
                    {
                        "id": "rec1",
                        "title": "Song",
                        "artist-credit": [{"name": "Artist"}],
                        "tags": [
                            {"name": "pop", "count": 2},
                            {"name": "house", "count": 5},
                        ],
                    }
                ]
            }
        )

        candidates = MusicBrainzProvider().search(_track())

        self.assertEqual(candidates[0].genre, "house")
        request = mock_urlopen.call_args[0][0]
        self.assertIn("inc=tags", request.full_url)

    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_missing_tags_gives_empty_genre(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _json_response(
            {
                "recordings": [
                    {"id": "rec1", "title": "Song", "artist-credit": [{"name": "Artist"}]}
                ]
            }
        )

        candidates = MusicBrainzProvider().search(_track())

        self.assertEqual(candidates[0].genre, "")


if __name__ == "__main__":
    unittest.main()
