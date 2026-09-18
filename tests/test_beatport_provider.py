"""Регрессия провайдера Beatport: разбор __NEXT_DATA__ (BPM, тональность, жанр)."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.beatport import BeatportProvider


def _track(title: str = "One More Time", artist: str = "Daft Punk") -> TrackRecord:
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


def _track_item(**overrides: object) -> dict:
    item = {
        "track_id": 6014288,
        "track_name": "One More Time",
        "mix_name": "12 Mix",
        "artists": [{"artist_id": 3547, "artist_name": "Daft Punk"}],
        "bpm": 123,
        "key_name": "D Major",
        "genre": [{"genre_id": 5, "genre_name": "House"}],
        "release_date": "2000-12-08T00:00:00",
        "release": {
            "release_id": 1414362,
            "release_name": "One More Time",
            "release_image_dynamic_uri": "https://example.test/{w}x{h}/cover.jpg",
        },
    }
    item.update(overrides)
    return item


def _page(items: list) -> MagicMock:
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["search-all"],
                            "state": {"data": {"tracks": {"data": items}}},
                        }
                    ]
                }
            }
        }
    }
    html = f'<html><script id="__NEXT_DATA__">{json.dumps(payload)}</script></html>'
    response = MagicMock()
    response.read.return_value = html.encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class BeatportProviderTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_search_returns_candidate_with_bpm_and_key(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page([_track_item()])

        candidates = BeatportProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.provider_id, "beatport")
        self.assertEqual(candidate.external_id, "6014288")
        self.assertEqual(candidate.title, "One More Time (12 Mix)")
        self.assertEqual(candidate.artist, "Daft Punk")
        self.assertEqual(candidate.album, "One More Time")
        self.assertEqual(candidate.year, "2000")
        self.assertEqual(candidate.genre, "House")
        self.assertEqual(candidate.bpm, 123.0)
        self.assertEqual(candidate.musical_key, "D Major")
        self.assertEqual(candidate.artwork_url, "https://example.test/1400x1400/cover.jpg")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_original_mix_is_not_appended_to_title(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page([_track_item(mix_name="Original Mix")])

        candidates = BeatportProvider().search(_track())

        self.assertEqual(candidates[0].title, "One More Time")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_empty_results_returns_empty_list(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page([])

        self.assertEqual(BeatportProvider().search(_track()), [])

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_missing_marker_raises_clear_error(self, mock_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = b"<html><body>no data here</body></html>"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response

        with self.assertRaises(MetadataProviderError):
            BeatportProvider().search(_track())

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _page([_track_item()])]

        candidates = BeatportProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
