"""Регрессия жанров и устойчивости MusicBrainz (топ-тег через inc=tags, retry на 5xx/DNS)."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
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


class MusicBrainzResilienceTests(unittest.TestCase):
    @patch("djmaker.plugins.providers.musicbrainz.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_503_retries_once_and_succeeds(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        unavailable = urllib.error.HTTPError(
            "url", 503, "Service Unavailable", {"Retry-After": "3"}, None
        )
        mock_urlopen.side_effect = [unavailable, _json_response({"recordings": []})]

        candidates = MusicBrainzProvider().search(_track())

        self.assertEqual(candidates, [])
        self.assertIn(call(3.0), mock_sleep.call_args_list)

    @patch("djmaker.plugins.providers.musicbrainz.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_persistent_503_raises_after_one_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        unavailable = urllib.error.HTTPError(
            "url", 503, "Service Unavailable", None, None
        )
        mock_urlopen.side_effect = [unavailable, unavailable]

        with self.assertRaises(MetadataProviderError) as ctx:
            MusicBrainzProvider().search(_track())

        self.assertIn("MusicBrainz недоступен", str(ctx.exception))
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_client_error_does_not_retry(self, mock_urlopen: MagicMock) -> None:
        bad_request = urllib.error.HTTPError("url", 400, "Bad Request", None, None)
        mock_urlopen.side_effect = [bad_request]

        with self.assertRaises(MetadataProviderError):
            MusicBrainzProvider().search(_track())

        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("djmaker.plugins.providers.musicbrainz.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _json_response({"recordings": []})]

        candidates = MusicBrainzProvider().search(_track())

        self.assertEqual(candidates, [])
        mock_sleep.assert_called_once()

    @patch("djmaker.plugins.providers.musicbrainz.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.musicbrainz.urllib.request.urlopen")
    def test_persistent_connection_error_raises_after_retries(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, dns_error, dns_error]

        with self.assertRaises(MetadataProviderError):
            MusicBrainzProvider().search(_track())

        self.assertEqual(mock_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
