"""Регрессия провайдера Яндекс Музыки: сборка React state-patches, устойчивость."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.providers.yandex_music import YandexMusicProvider


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


def _patch_script(patches: list) -> str:
    return (
        "<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])"
        f".push({json.dumps(patches)});</script>"
    )


def _page(scripts: list[str]) -> MagicMock:
    html = "<html><body>" + "".join(scripts) + "</body></html>"
    response = MagicMock()
    response.read.return_value = html.encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _track_item(index: int, **overrides: object) -> dict:
    value = {
        "type": "track",
        "data": {
            "id": "113911861",
            "title": "One More Time",
            "artists": [{"id": "1", "name": "Daft Punk"}],
            "genre": "house",
            "coverUri": "avatars.yandex.net/get-music-content/1/2.a.3/%%",
        },
    }
    value["data"].update(overrides)
    return {"op": "add", "path": f"/search/pagesLoader/items/{index}", "value": value}


class YandexMusicProviderTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_search_returns_candidate_with_artwork_and_genre(
        self, mock_urlopen: MagicMock
    ) -> None:
        mock_urlopen.return_value = _page([_patch_script([_track_item(0)])])

        candidates = YandexMusicProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.provider_id, "yandex_music")
        self.assertEqual(candidate.external_id, "113911861")
        self.assertEqual(candidate.title, "One More Time")
        self.assertEqual(candidate.artist, "Daft Punk")
        self.assertEqual(candidate.genre, "house")
        self.assertEqual(
            candidate.artwork_url, "https://avatars.yandex.net/get-music-content/1/2.a.3/400x400"
        )

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_non_track_items_are_ignored(self, mock_urlopen: MagicMock) -> None:
        album_item = {
            "op": "add",
            "path": "/search/pagesLoader/items/0",
            "value": {"type": "album", "data": {"id": "1", "title": "Some Album"}},
        }
        mock_urlopen.return_value = _page([_patch_script([album_item])])

        self.assertEqual(YandexMusicProvider().search(_track()), [])

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_items_split_across_multiple_script_tags_are_collected(
        self, mock_urlopen: MagicMock
    ) -> None:
        mock_urlopen.return_value = _page(
            [
                _patch_script([_track_item(0, title="First")]),
                _patch_script([_track_item(1, title="Second")]),
            ]
        )

        candidates = YandexMusicProvider().search(_track())

        self.assertEqual({c.title for c in candidates}, {"First", "Second"})

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_no_state_patches_raises_clear_error(self, mock_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = b"<html><body>no data here</body></html>"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response

        with self.assertRaises(MetadataProviderError):
            YandexMusicProvider().search(_track())

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_transient_dns_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _page([_patch_script([_track_item(0)])])]

        candidates = YandexMusicProvider().search(_track())

        self.assertEqual(len(candidates), 1)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
