"""Регрессия провайдера Deezer: парсинг результатов, ошибки API, устойчивость."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.deezer import DeezerProvider


def _track(title: str = "Starboy", artist: str = "The Weeknd") -> TrackRecord:
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


def _json_response(payload: object) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class DeezerProviderTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_search_returns_candidate_with_artwork(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _json_response(
            {
                "data": [
                    {
                        "id": 136889400,
                        "title": "Starboy",
                        "artist": {"id": 4050205, "name": "The Weeknd"},
                        "album": {
                            "id": 14652356,
                            "title": "Starboy",
                            "cover_xl": "https://cdn/1000x1000.jpg",
                            "cover_big": "https://cdn/500x500.jpg",
                        },
                    }
                ]
            }
        )

        candidates = DeezerProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.provider_id, "deezer")
        self.assertEqual(candidate.external_id, "136889400")
        self.assertEqual(candidate.title, "Starboy")
        self.assertEqual(candidate.artist, "The Weeknd")
        self.assertEqual(candidate.album, "Starboy")
        self.assertEqual(candidate.artwork_url, "https://cdn/1000x1000.jpg")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_empty_results_returns_empty_list(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _json_response({"data": []})

        self.assertEqual(DeezerProvider().search(_track()), [])

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_api_error_payload_raises_clear_message(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _json_response(
            {"error": {"type": "QuotaException", "message": "Too many requests", "code": 4}}
        )

        with self.assertRaises(MetadataProviderError) as ctx:
            DeezerProvider().search(_track())
        self.assertIn("Too many requests", str(ctx.exception))

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _json_response({"data": []})]

        candidates = DeezerProvider().search(_track())

        self.assertEqual(candidates, [])
        mock_sleep.assert_called_once()

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_quotes_in_query_are_escaped(self, mock_urlopen: MagicMock) -> None:
        import urllib.parse

        mock_urlopen.return_value = _json_response({"data": []})

        DeezerProvider().search(_track(title='Song "Remix"', artist="Artist"))

        request = mock_urlopen.call_args[0][0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["q"][0]
        self.assertEqual(query, 'track:"Song \'Remix\'" artist:"Artist"')


if __name__ == "__main__":
    unittest.main()
