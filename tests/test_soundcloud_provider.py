"""Регрессия провайдера SoundCloud: разбор честного noscript-списка результатов."""

from __future__ import annotations

import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.soundcloud import SoundCloudProvider

_MENU_NOSCRIPT = (
    "<noscript><ul>"
    '<li><a href="/search">Search for Everything</a></li>'
    '<li><a href="/search/sounds">Search for Tracks</a></li>'
    "</ul></noscript>"
)

_RESULTS_NOSCRIPT = (
    "<noscript><ul>"
    '<li><h2><a href="/daftpunkofficialmusic">Daft Punk</a></h2></li>'
    '<li><h2><a href="/theweeknd/starboy-1">Starboy (feat. Daft Punk)</a></h2></li>'
    '<li><h2><a href="/daftpunkofficialmusic/around-the-world">Around the World</a></h2></li>'
    "</ul></noscript>"
)


def _track(title: str = "Around the World", artist: str = "Daft Punk") -> TrackRecord:
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


def _page(body: str) -> MagicMock:
    response = MagicMock()
    response.read.return_value = f"<html><body>{body}</body></html>".encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class SoundCloudProviderTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_separate_noscript_blocks_are_parsed(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page(_MENU_NOSCRIPT + _RESULTS_NOSCRIPT)

        candidates = SoundCloudProvider().search(_track())

        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0].title, "Daft Punk")
        self.assertEqual(candidates[0].artist, "daftpunkofficialmusic")
        self.assertEqual(candidates[0].external_id, "/daftpunkofficialmusic")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_single_combined_noscript_block_is_parsed(self, mock_urlopen: MagicMock) -> None:
        combined = (
            "<noscript><ul>"
            '<li><a href="/search">Search for Everything</a></li>'
            "</ul>"
            '<ul><li><h2><a href="/daftpunkofficialmusic">Daft Punk</a></h2></li></ul>'
            "</noscript>"
        )
        mock_urlopen.return_value = _page(combined)

        candidates = SoundCloudProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Daft Punk")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_menu_items_without_h2_are_not_matched(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page(_MENU_NOSCRIPT)

        self.assertEqual(SoundCloudProvider().search(_track()), [])

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_html_entities_in_title_are_unescaped(self, mock_urlopen: MagicMock) -> None:
        block = (
            "<noscript><ul>"
            '<li><h2><a href="/user/track">Rock &amp; Roll</a></h2></li>'
            "</ul></noscript>"
        )
        mock_urlopen.return_value = _page(block)

        candidates = SoundCloudProvider().search(_track())

        self.assertEqual(candidates[0].title, "Rock & Roll")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_respects_limit(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page(_RESULTS_NOSCRIPT)

        candidates = SoundCloudProvider().search(_track(), limit=1)

        self.assertEqual(len(candidates), 1)

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_no_noscript_at_all_raises_clear_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _page("no noscript here")

        with self.assertRaises(MetadataProviderError):
            SoundCloudProvider().search(_track())

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _page(_RESULTS_NOSCRIPT)]

        candidates = SoundCloudProvider().search(_track())

        self.assertEqual(len(candidates), 3)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
