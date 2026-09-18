"""Регрессия LibraryService.search_metadata: несколько провайдеров, best-effort."""

from __future__ import annotations

import time
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

    def test_merge_priority_follows_provider_ids_not_completion_order(self) -> None:
        # spotify объявлен первым (выше приоритет), но отвечает медленнее —
        # порядок merge должен зависеть от provider_ids, а не от того, кто
        # из потоков завершился раньше.
        def slow_search(track, limit=10):
            time.sleep(0.05)
            return [
                MetadataCandidate(
                    provider_id="spotify",
                    external_id="1",
                    title="Song",
                    artist="Artist",
                    album="Spotify Album",
                )
            ]

        spotify_provider = Mock()
        spotify_provider.search.side_effect = slow_search
        musicbrainz_provider = Mock()
        musicbrainz_provider.search.return_value = [
            MetadataCandidate(
                provider_id="musicbrainz",
                external_id="2",
                title="Song",
                artist="Artist",
                album="MB Album",
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

        self.assertEqual(result[0].album, "Spotify Album")

    def test_providers_are_queried_concurrently_not_sequentially(self) -> None:
        def slow_search(track, limit=10):
            time.sleep(0.15)
            return []

        provider_a = Mock()
        provider_a.search.side_effect = slow_search
        provider_b = Mock()
        provider_b.search.side_effect = slow_search
        registry = Mock()
        registry.get.side_effect = lambda pid: {"a": provider_a, "b": provider_b}[pid]

        service = self._service()
        service.plugins = registry

        started = time.monotonic()
        service.search_metadata(1, ("a", "b"))
        elapsed = time.monotonic() - started

        # Последовательно это заняло бы ~0.3с; параллельно — ~0.15с.
        self.assertLess(elapsed, 0.25)

    def test_search_single_provider_returns_that_providers_candidates(self) -> None:
        provider = Mock()
        provider.search.return_value = [
            MetadataCandidate(provider_id="deezer", external_id="1", title="Song", artist="Artist")
        ]
        registry = Mock()
        registry.get.side_effect = lambda pid: {"deezer": provider}[pid]

        service = self._service()
        service.plugins = registry

        result = service.search_single_provider(1, "deezer")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].provider_id, "deezer")
        provider.search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
