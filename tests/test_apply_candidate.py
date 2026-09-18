"""Регрессия LibraryService.apply_candidate: правило замены/сохранения полей."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from djmaker.domain.models import (
    AudioMetadata,
    AudioTechnicalInfo,
    MetadataCandidate,
    TrackRecord,
)
from djmaker.services.library import LibraryService


def _track(**metadata_overrides: object) -> TrackRecord:
    metadata = AudioMetadata(
        title="Old Title",
        artist="Old Artist",
        album="Old Album",
        album_artist="Old Album Artist",
        genre="",
        year="1999",
        track_number=3,
        disc_number=1,
        bpm=None,
        musical_key="",
    )
    for key, value in metadata_overrides.items():
        setattr(metadata, key, value)
    return TrackRecord(
        id=1,
        path=Path("/music/track.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash="hash",
        metadata=metadata,
        technical=AudioTechnicalInfo(),
    )


class ApplyCandidateTests(unittest.TestCase):
    def _service(self, current: TrackRecord) -> LibraryService:
        service = LibraryService.__new__(LibraryService)
        service.database = Mock()
        service.database.get_track.return_value = current
        service.tags = Mock()
        service.scanner = Mock()
        service.scanner.inspect_and_hash.return_value = (Mock(), current.file_hash, Mock())
        return service

    def test_candidate_fields_replace_local_when_present(self) -> None:
        current = _track()
        service = self._service(current)

        candidate = MetadataCandidate(
            provider_id="beatport",
            external_id="1",
            title="New Title",
            artist="New Artist",
            album="New Album",
            year="2024",
            genre="House",
            bpm=123.0,
            musical_key="D Major",
        )
        service.apply_candidate(1, candidate)

        written = service.tags.write.call_args[0][1]
        self.assertEqual(written.title, "New Title")
        self.assertEqual(written.artist, "New Artist")
        self.assertEqual(written.album, "New Album")
        self.assertEqual(written.year, "2024")
        self.assertEqual(written.genre, "House")
        self.assertEqual(written.bpm, 123.0)
        self.assertEqual(written.musical_key, "D Major")

    def test_fields_missing_from_candidate_keep_local_values(self) -> None:
        current = _track(bpm=128.0, musical_key="8A", genre="Techno")
        service = self._service(current)

        candidate = MetadataCandidate(
            provider_id="soundcloud", external_id="1", title="New Title", artist="New Artist"
        )
        service.apply_candidate(1, candidate)

        written = service.tags.write.call_args[0][1]
        self.assertEqual(written.title, "New Title")
        self.assertEqual(written.bpm, 128.0)
        self.assertEqual(written.musical_key, "8A")
        self.assertEqual(written.genre, "Techno")
        self.assertEqual(written.album, "Old Album")

    def test_album_artist_track_number_disc_number_are_never_touched(self) -> None:
        current = _track()
        service = self._service(current)

        candidate = MetadataCandidate(
            provider_id="deezer", external_id="1", title="New Title", artist="New Artist"
        )
        service.apply_candidate(1, candidate)

        written = service.tags.write.call_args[0][1]
        self.assertEqual(written.album_artist, "Old Album Artist")
        self.assertEqual(written.track_number, 3)
        self.assertEqual(written.disc_number, 1)

    def test_artwork_url_is_saved_when_present(self) -> None:
        current = _track()
        service = self._service(current)

        candidate = MetadataCandidate(
            provider_id="deezer",
            external_id="1",
            title="New Title",
            artist="New Artist",
            artwork_url="https://example.test/cover.jpg",
        )
        service.apply_candidate(1, candidate)

        service.database.set_artwork_url.assert_called_once_with(
            1, "https://example.test/cover.jpg"
        )

    def test_no_artwork_url_does_not_touch_database(self) -> None:
        current = _track()
        service = self._service(current)

        candidate = MetadataCandidate(
            provider_id="deezer", external_id="1", title="New Title", artist="New Artist"
        )
        service.apply_candidate(1, candidate)

        service.database.set_artwork_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
