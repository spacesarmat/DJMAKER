"""Regression-тесты редактора тегов медиатеки."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from djmaker.domain.models import (
    AudioMetadata,
    AudioTechnicalInfo,
    EmbeddedArtwork,
    InspectedAudio,
    TrackRecord,
)
from djmaker.services.library import LibraryService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "app.py"
TRACK_ROW_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "ui" / "track_row_controls.py"
LIBRARY_SOURCE = PROJECT_ROOT / "src" / "djmaker" / "services" / "library.py"


class TagEditorUITests(unittest.TestCase):
    def test_double_click_on_track_opens_existing_tag_editor(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")
        track_row_source = TRACK_ROW_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            "on_double_tap=lambda _, track_id=track.id: app._open_tag_editor(",
            track_row_source,
        )
        self.assertIn("def _open_tag_editor(self, track_id: int) -> None:", source)
        self.assertIn("self.reveal_track_file(current)", track_row_source)

    def test_editor_contains_full_metadata_fields_and_embedded_artwork(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        for label in (
            'label="Название"',
            'label="Исполнитель"',
            'label="Альбом"',
            'label="Исполнитель альбома"',
            'label="Жанр"',
            'label="Год"',
            'label="Трек"',
            'label="Диск"',
            'label="BPM (тег)"',
            'label="Key (тег)"',
        ):
            self.assertIn(label, source)

        self.assertIn('"Встроенная обложка"', source)
        self.assertIn('content="Заменить"', source)
        self.assertIn('content="Удалить"', source)
        self.assertIn('allowed_extensions=["jpg", "jpeg", "png"]', source)
        self.assertIn("self.service.prepare_artwork(data)", source)
        self.assertIn("replace_artwork=artwork_state.changed", source)
        self.assertIn("artwork=artwork_state.artwork", source)
        self.assertIn('f"DSP-анализ: {analysis_label}"', source)

    def test_library_service_can_replace_artwork_while_updating_tags(self) -> None:
        source = LIBRARY_SOURCE.read_text(encoding="utf-8")

        self.assertIn("replace_artwork: bool = False", source)
        self.assertIn("artwork: EmbeddedArtwork | None = None", source)
        self.assertIn("self.tags.write_artwork(track.path, artwork)", source)
        self.assertIn("refresh_artwork=replace_artwork", source)
        self.assertIn("self.artwork_cache.store(inspected.artwork)", source)
        self.assertIn("self.database.set_embedded_artwork", source)


class TagEditorServiceTests(unittest.TestCase):
    def test_update_tags_can_replace_artwork_and_refresh_cache(self) -> None:
        path = Path("track.mp3")
        track = TrackRecord(
            id=7,
            path=path,
            root_path=Path("."),
            size=100,
            mtime_ns=1,
            extension=".mp3",
            file_hash="old",
            metadata=AudioMetadata(title="Old"),
            technical=AudioTechnicalInfo(duration=180.0),
        )
        updated = TrackRecord(
            id=7,
            path=path,
            root_path=Path("."),
            size=120,
            mtime_ns=2,
            extension=".mp3",
            file_hash="new",
            metadata=AudioMetadata(title="New"),
            technical=AudioTechnicalInfo(duration=180.0),
        )
        cover = EmbeddedArtwork(b"\xff\xd8\xffcover", "image/jpeg")
        inspected = InspectedAudio(
            path=path,
            metadata=updated.metadata,
            technical=updated.technical,
            artwork=cover,
        )
        stat = SimpleNamespace(st_size=120, st_mtime_ns=2)

        database = Mock()
        database.get_track.side_effect = [track, updated]
        scanner = Mock()
        scanner.inspect_and_hash.return_value = (inspected, "new", stat)
        tags = Mock()
        artwork_cache = Mock()
        artwork_cache.store.return_value = Path("cache/cover.jpg")
        service = LibraryService(
            database=database,
            scanner=scanner,
            tags=tags,
            organizer=Mock(),
            plugins=Mock(),
            analyzer=Mock(),
            artwork_cache=artwork_cache,
            waveform_analyzer=Mock(),
        )

        result = service.update_tags(
            7,
            updated.metadata,
            replace_artwork=True,
            artwork=cover,
        )

        self.assertIs(updated, result)
        tags.write.assert_called_once_with(path, updated.metadata)
        tags.write_artwork.assert_called_once_with(path, cover)
        database.update_after_file_change.assert_called_once()
        artwork_cache.store.assert_called_once_with(cover)
        database.set_embedded_artwork.assert_called_once_with(
            7,
            Path("cache/cover.jpg"),
        )


if __name__ == "__main__":
    unittest.main()
