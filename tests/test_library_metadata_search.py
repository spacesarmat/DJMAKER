"""Регрессия LibraryService.search_metadata: несколько провайдеров, best-effort."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from djmaker.domain.models import MetadataCandidate
from djmaker.plugins.base import MetadataProviderError
from djmaker.services.library import LibraryService, LibraryServiceError


class LibraryServiceSearchMetadataTests(unittest.TestCase):
    def _service(self) -> LibraryService:
        service = LibraryService.__new__(LibraryService)
        service.database = Mock()
        service.database.get_track.return_value = Mock()
        return service

    def test_merges_results_from_multiple_providers(self) -> None:
        spotify_provider = Mock()
        spotify_provider.search.return_value = [
            MetadataCandidate(
                provider_id="spotify",
                external_id="1",
                title="Song",
                artist="Artist",
                album="Spotify Album",
            )
        ]
        musicbrainz_provider = Mock()
        musicbrainz_provider.search.return_value = [
            MetadataCandidate(
                provider_id="musicbrainz",
                external_id="2",
                title="Song",
                artist="Artist",
                genre="house",
            )
        ]
        registry = Mock()
        registry.get.side_effect = lambda pid: {
            "spotify": spotify_provider,
            "musicbrainz": musicbrainz_provider,
        }[pid]

        service = self._service()
        service.plugins = registry

        result = service.search_metadata(1, ("spotify", "musicbrainz"))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].album, "Spotify Album")
        self.assertEqual(result[0].genre, "house")

    def test_partial_provider_failure_still_returns_other_results(self) -> None:
        working = Mock()
        working.search.return_value = [
            MetadataCandidate(
                provider_id="musicbrainz", external_id="1", title="Song", artist="Artist"
            )
        ]
        broken = Mock()
        broken.search.side_effect = MetadataProviderError("boom")
        registry = Mock()
        registry.get.side_effect = lambda pid: {"musicbrainz": working, "spotify": broken}[pid]

        service = self._service()
        service.plugins = registry

        result = service.search_metadata(1, ("musicbrainz", "spotify"))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].provider_id, "musicbrainz")

    def test_all_providers_failing_raises_library_service_error(self) -> None:
        broken = Mock()
        broken.search.side_effect = MetadataProviderError("boom")
        registry = Mock()
        registry.get.return_value = broken

        service = self._service()
        service.plugins = registry

        with self.assertRaises(LibraryServiceError):
            service.search_metadata(1, ("spotify",))


if __name__ == "__main__":
    unittest.main()
