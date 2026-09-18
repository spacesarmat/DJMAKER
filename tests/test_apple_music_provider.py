"""Регрессия провайдера Apple Music: разбор serialized-server-data, устойчивость."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.apple_music import AppleMusicProvider


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


def _page(sections: list) -> MagicMock:
    payload = {"data": [{"data": {"sections": sections}}]}
    html = (
        "<html><body>"
        f'<script id="serialized-server-data">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    response = MagicMock()
    response.read.return_value = html.encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


_TRACK_SECTION = {
    "itemKind": "trackLockup",
    "items": [
        {
            "title": "One More Time",
            "subtitleLinks": [{"title": "Daft Punk"}],
            "contentDescriptor": {"identifiers": {"storeAdamID": "697195462"}},
            "artwork": {"dictionary": {"url": "https://example.test/img/{w}x{h}bb.{f}"}},
        }
    ],
}


class AppleMusicProviderTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_search_returns_candidate_with_artwork(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page(
            [{"itemKind": "topSearchLockup", "items": []}, _TRACK_SECTION]
        )

        candidates = AppleMusicProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.provider_id, "apple_music")
        self.assertEqual(candidate.external_id, "697195462")
        self.assertEqual(candidate.title, "One More Time")
        self.assertEqual(candidate.artist, "Daft Punk")
        self.assertEqual(candidate.artwork_url, "https://example.test/img/1200x1200bb.jpg")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_missing_track_section_returns_empty_list(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page([{"itemKind": "topSearchLockup", "items": []}])

        self.assertEqual(AppleMusicProvider().search(_track()), [])

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_missing_marker_raises_clear_error(self, mock_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = b"<html><body>no data here</body></html>"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response

        with self.assertRaises(MetadataProviderError):
            AppleMusicProvider().search(_track())

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _page([_TRACK_SECTION])]

        candidates = AppleMusicProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        mock_sleep.assert_called_once()

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_respects_limit(self, mock_urlopen: MagicMock) -> None:
        section = {
            "itemKind": "trackLockup",
            "items": [
                {"title": f"Track {i}", "subtitleLinks": [{"title": "Artist"}]}
                for i in range(5)
            ],
        }
        mock_urlopen.return_value = _page([section])

        candidates = AppleMusicProvider().search(_track(), limit=2)

        self.assertEqual(len(candidates), 2)


if __name__ == "__main__":
    unittest.main()
