"""Регрессия провайдера Spotify: токен, поиск, жанры, лимит запросов."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.spotify import SpotifyProvider


def _track(title: str = "Song Name", artist: str = "Artist One") -> TrackRecord:
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


class SpotifyProviderCredentialsTests(unittest.TestCase):
    def test_missing_credentials_raise_clear_error(self) -> None:
        provider = SpotifyProvider()
        with patch.dict(
            "os.environ",
            {"DJMAKER_SPOTIFY_CLIENT_ID": "", "DJMAKER_SPOTIFY_CLIENT_SECRET": ""},
        ):
            with self.assertRaises(MetadataProviderError) as ctx:
                provider.search(_track())
        self.assertIn("не настроен", str(ctx.exception))


class SpotifyProviderSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(
            "os.environ",
            {"DJMAKER_SPOTIFY_CLIENT_ID": "id", "DJMAKER_SPOTIFY_CLIENT_SECRET": "secret"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = SpotifyProvider()

    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_search_returns_candidate_with_artwork_and_genre(self, mock_urlopen: MagicMock) -> None:
        token_response = _json_response({"access_token": "tok123", "expires_in": 3600})
        search_response = _json_response(
            {
                "tracks": {
                    "items": [
                        {
                            "id": "trk1",
                            "name": "Song Name",
                            "artists": [{"id": "art1", "name": "Artist One"}],
                            "album": {
                                "id": "alb1",
                                "name": "Album Name",
                                "release_date": "2021-05-01",
                                "images": [
                                    {"url": "http://img/small.jpg", "width": 64},
                                    {"url": "http://img/big.jpg", "width": 640},
                                ],
                            },
                        }
                    ]
                }
            }
        )
        artists_response = _json_response(
            {"artists": [{"id": "art1", "genres": ["house", "techno"]}]}
        )
        mock_urlopen.side_effect = [token_response, search_response, artists_response]

        candidates = self.provider.search(_track(), limit=5)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.provider_id, "spotify")
        self.assertEqual(candidate.external_id, "trk1")
        self.assertEqual(candidate.title, "Song Name")
        self.assertEqual(candidate.artist, "Artist One")
        self.assertEqual(candidate.album, "Album Name")
        self.assertEqual(candidate.year, "2021")
        self.assertEqual(candidate.artwork_url, "http://img/big.jpg")
        self.assertEqual(candidate.genre, "house")

    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_token_is_cached_across_searches(self, mock_urlopen: MagicMock) -> None:
        token_response = _json_response({"access_token": "tok123", "expires_in": 3600})
        mock_urlopen.side_effect = [
            token_response,
            _json_response({"tracks": {"items": []}}),
            _json_response({"tracks": {"items": []}}),
        ]

        self.provider.search(_track())
        self.provider.search(_track())

        self.assertEqual(mock_urlopen.call_count, 3)

    @patch("djmaker.plugins.providers.spotify.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_rate_limit_retries_once_and_respects_retry_after(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        token_response = _json_response({"access_token": "tok123", "expires_in": 3600})
        too_many_requests = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {"Retry-After": "2"}, None
        )
        mock_urlopen.side_effect = [
            token_response,
            too_many_requests,
            _json_response({"tracks": {"items": []}}),
        ]

        candidates = self.provider.search(_track())

        self.assertEqual(candidates, [])
        mock_sleep.assert_called_once_with(2.0)

    @patch("djmaker.plugins.providers.spotify.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_persistent_network_error_raises_after_retries(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.side_effect = [
            _json_response({"access_token": "tok123", "expires_in": 3600}),
            urllib.error.URLError("no route to host"),
            urllib.error.URLError("no route to host"),
            urllib.error.URLError("no route to host"),
        ]

        with self.assertRaises(MetadataProviderError):
            self.provider.search(_track())

        self.assertEqual(mock_sleep.call_count, 2)

    @patch("djmaker.plugins.providers.spotify.time.sleep", return_value=None)
    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [
            _json_response({"access_token": "tok123", "expires_in": 3600}),
            dns_error,
            _json_response({"tracks": {"items": []}}),
        ]

        candidates = self.provider.search(_track())

        self.assertEqual(candidates, [])
        mock_sleep.assert_called_once()

    @patch("djmaker.plugins.providers.spotify.urllib.request.urlopen")
    def test_dns_failure_message_is_actionable(self, mock_urlopen: MagicMock) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [
            _json_response({"access_token": "tok123", "expires_in": 3600}),
            dns_error,
            dns_error,
            dns_error,
        ]

        with patch("djmaker.plugins.providers.spotify.time.sleep", return_value=None):
            with self.assertRaises(MetadataProviderError) as ctx:
                self.provider.search(_track())

        self.assertIn("DNS", str(ctx.exception))
        self.assertIn("VPN", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
